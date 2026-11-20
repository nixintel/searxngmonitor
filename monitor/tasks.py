import logging
import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from monitor.models import AppSettings, LogEntry, SavedSearch, SearchRun
from monitor.services import notify
from monitor.services.feeds import fetch_google_alert
from monitor.services.health import BEAT_HEARTBEAT_KEY
from monitor.services.searxng import SearxngError, fetch_results, store_results

logger = logging.getLogger("monitor.tasks")


def _lock_key(search_id):
    return f"monitor:searchlock:{search_id}"


def enqueue_run(run):
    """Send a SearchRun to the worker queue. The task id is stored before sending."""
    run.celery_task_id = str(uuid.uuid4())
    run.save(update_fields=["celery_task_id"])
    run_search.apply_async(args=[run.id], task_id=run.celery_task_id)
    return run


def trigger_manual_run(search):
    run = SearchRun.objects.create(search=search, trigger=SearchRun.Trigger.MANUAL, proxy_used=search.runs_via_proxy)
    logger.info("Manual run requested for '%s'", search.name, extra={"search_id": search.id, "run_id": run.id})
    return enqueue_run(run)


@shared_task
def dispatch_due_searches():
    """Beat tick: queue every enabled search whose next_run_at has passed."""
    now = timezone.now()
    cache.set(BEAT_HEARTBEAT_KEY, now, timeout=None)
    with transaction.atomic():
        due = list(
            SavedSearch.objects.select_for_update(skip_locked=True)
            .filter(enabled=True, next_run_at__lte=now)
            .order_by("next_run_at")[: settings.DISPATCH_BATCH_SIZE]
        )
        runs = []
        for search in due:
            search.next_run_at = search.compute_next_run(now)
            search.save(update_fields=["next_run_at"])
            runs.append(
                SearchRun.objects.create(
                    search=search, trigger=SearchRun.Trigger.SCHEDULED, proxy_used=search.runs_via_proxy
                )
            )
    for run in runs:
        enqueue_run(run)
    if runs:
        logger.info("Dispatched %d due search(es)", len(runs))
    return len(runs)


@shared_task(bind=True)
def run_search(self, run_id):
    try:
        run = SearchRun.objects.select_related("search").get(pk=run_id)
    except SearchRun.DoesNotExist:
        return "missing"
    search = run.search
    extra = {"search_id": search.id, "run_id": run.id}

    lock_key = _lock_key(search.id)
    if not cache.add(lock_key, run.id, timeout=settings.SEARCH_LOCK_TIMEOUT):
        run.status = SearchRun.Status.SKIPPED
        run.error = "Another run of this search was still in progress."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        logger.warning("Skipped run for '%s': previous run still in progress", search.name, extra=extra)
        return "skipped"

    try:
        run.status = SearchRun.Status.RUNNING
        run.started_at = timezone.now()
        run.proxy_used = search.runs_via_proxy
        run.save(update_fields=["status", "started_at", "proxy_used"])

        is_first = not search.runs.filter(status=SearchRun.Status.SUCCESS).exists()
        mark_new = not (is_first and search.baseline_first_run)

        fetch = fetch_google_alert if search.is_google_alert else fetch_results
        results, unresponsive = fetch(search)
        if not results and unresponsive:
            raise SearxngError(
                "No results: all engines failed - "
                + ", ".join(f"{e[0]} ({e[1]})" if len(e) > 1 else e[0] for e in unresponsive)
            )
        with transaction.atomic():
            new_count = store_results(search, run, results, mark_new=mark_new)

        run.status = SearchRun.Status.SUCCESS
        run.results_returned = len(results)
        run.new_results = new_count
        run.unresponsive_engines = unresponsive
        run.finished_at = timezone.now()
        run.save()
        logger.info(
            "'%s': %d result(s) returned, %d new%s",
            search.name, len(results), new_count,
            f"; unresponsive engines: {', '.join(e[0] for e in unresponsive)}" if unresponsive else "",
            extra=extra,
        )
        if new_count and mark_new:
            if search.notify_email:
                send_new_results_email.delay(run.id)
            if search.notify_slack:
                send_new_results_slack.delay(run.id)
    except Exception as exc:
        run.status = SearchRun.Status.FAILED
        run.error = str(exc)[:4000]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        logger.error("Run failed for '%s': %s", search.name, exc, extra=extra)
    finally:
        SavedSearch.objects.filter(pk=search.pk).update(last_run_at=timezone.now())
        if cache.get(lock_key) == run.id:
            cache.delete(lock_key)
    return run.status


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def send_new_results_email(self, run_id):
    run = SearchRun.objects.select_related("search").get(pk=run_id)
    try:
        notify.send_new_results_email(run)
    except Exception as exc:
        logger.error(
            "Email for '%s' failed (attempt %d): %s", run.search.name, self.request.retries + 1, exc,
            extra={"search_id": run.search_id, "run_id": run.id},
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_new_results_slack(self, run_id):
    run = SearchRun.objects.select_related("search").get(pk=run_id)
    try:
        notify.send_new_results_slack(run)
    except Exception as exc:
        logger.error(
            "Slack post for '%s' failed (attempt %d): %s", run.search.name, self.request.retries + 1, exc,
            extra={"search_id": run.search_id, "run_id": run.id},
        )
        raise self.retry(exc=exc)


@shared_task
def reap_stale_runs():
    """Mark runs stuck in queued/running (e.g. worker killed) as failed."""
    cutoff = timezone.now() - timedelta(minutes=settings.STALE_RUN_MINUTES)
    count = SearchRun.objects.filter(status__in=SearchRun.ACTIVE_STATUSES, created_at__lt=cutoff).update(
        status=SearchRun.Status.FAILED,
        error=f"Marked failed: no completion after {settings.STALE_RUN_MINUTES} minutes.",
        finished_at=timezone.now(),
    )
    if count:
        logger.warning("Marked %d stale run(s) as failed", count)
    return count


@shared_task
def prune_old_records():
    days = AppSettings.load().retention_days
    cutoff = timezone.now() - timedelta(days=days)
    logs, _ = LogEntry.objects.filter(created_at__lt=cutoff).delete()
    runs, _ = SearchRun.objects.filter(created_at__lt=cutoff).exclude(status__in=SearchRun.ACTIVE_STATUSES).delete()
    logger.info("Pruned %d log entries and %d run records older than %d days", logs, runs, days)
    return {"logs": logs, "runs": runs}
