import json

from django.conf import settings
from datetime import timedelta
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from monitor.forms import AppSettingsForm, SearchForm
from monitor.models import AppSettings, SavedSearch, SearchResult, SearchRun, Tag
from monitor.queries import (
    LOG_LEVELS, filter_logs, filter_results, filter_runs, reschedule_if_needed, result_view,
    result_view_counts, searches_with_stats, tags_for_search,
)
from monitor.services import health, notify
from monitor.tasks import trigger_manual_run

logger = logging.getLogger("monitor.views")


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _htmx_response(message, level="success", events=("refreshTable", "refreshRuns", "refreshBadge")):
    payload = {e: True for e in events}
    payload["showToast"] = {"message": message, "level": level}
    resp = HttpResponse(status=204)
    resp["HX-Trigger"] = json.dumps(payload)
    return resp


# ---------------------------------------------------------------- public
@require_GET
def healthz(request):
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


# ---------------------------------------------------------------- dashboard
class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "monitor/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["searches"] = searches_with_stats()
        since = timezone.now() - timedelta(hours=24)
        recent = SearchRun.objects.filter(created_at__gte=since)
        ctx["stats"] = {
            "searches": SavedSearch.objects.count(),
            "enabled": SavedSearch.objects.filter(enabled=True).count(),
            "runs_24h": recent.count(),
            "failed_24h": recent.filter(status=SearchRun.Status.FAILED).count(),
            "active": SearchRun.objects.filter(status__in=SearchRun.ACTIVE_STATUSES).count(),
        }
        if SavedSearch.objects.filter(notify_email=True, enabled=True).exists():
            status = notify.email_status()
            if not status["ok"]:
                ctx["email_problem"] = status["message"]
            elif status["mode"] == "test":
                ctx["email_test_mode"] = status
        return ctx


@login_required
def dashboard_table(request):
    return render(request, "monitor/partials/search_table.html", {"searches": searches_with_stats()})


@login_required
def new_badge(request):
    return render(request, "monitor/partials/new_badge.html")


# ---------------------------------------------------------------- search CRUD
class NotificationContextMixin:
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["email_status"] = notify.email_status()
        ctx["slack_default"] = bool(notify.slack_webhook_for(None))
        return ctx


class SearchCreateView(LoginRequiredMixin, NotificationContextMixin, CreateView):
    model = SavedSearch
    form_class = SearchForm
    template_name = "monitor/search_form.html"

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get("source") == SavedSearch.Source.GOOGLE_ALERT:
            initial["source"] = SavedSearch.Source.GOOGLE_ALERT
        return initial

    def form_valid(self, form):
        resp = super().form_valid(form)
        logger.info("Search '%s' created", self.object.name, extra={"search_id": self.object.id})
        kind = "Google Alert" if self.object.is_google_alert else "Search"
        messages.success(self.request, f"{kind} '{self.object.name}' created. It will be checked within a minute.")
        return resp

    def get_success_url(self):
        return reverse("search-detail", args=[self.object.pk])


class SearchUpdateView(LoginRequiredMixin, NotificationContextMixin, UpdateView):
    model = SavedSearch
    form_class = SearchForm
    template_name = "monitor/search_form.html"

    def form_valid(self, form):
        resp = super().form_valid(form)
        reschedule_if_needed(self.object, form.changed_data)
        logger.info("Search '%s' updated", self.object.name, extra={"search_id": self.object.id})
        messages.success(self.request, f"Search '{self.object.name}' saved.")
        return resp

    def get_success_url(self):
        return reverse("search-detail", args=[self.object.pk])


class SearchDeleteView(LoginRequiredMixin, DeleteView):
    model = SavedSearch
    template_name = "monitor/search_confirm_delete.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        name = self.object.name
        resp = super().form_valid(form)
        logger.info("Search '%s' deleted", name)
        messages.success(self.request, f"Search '{name}' deleted.")
        return resp


class SearchDetailView(LoginRequiredMixin, DetailView):
    template_name = "monitor/search_detail.html"
    context_object_name = "search"

    def get_queryset(self):
        return searches_with_stats()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        results = filter_results(params, self.object.results.all())
        ctx["page"] = Paginator(results, 50).get_page(params.get("page"))
        ctx["filters"] = params
        ctx["view"] = result_view(params)
        ctx["counts"] = result_view_counts(self.object)
        ctx["search_tags"] = tags_for_search(self.object)
        ctx["all_tags"] = Tag.objects.all()
        ctx["active_tag"] = params.get("tag", "")
        ctx["email_test_mode"] = self.object.notify_email and notify.is_test_inbox()
        ctx["result_tabs"] = [
            ("all", "All", "bi-list-ul"), ("new", "New", "bi-bell"),
            ("tagged", "Tagged", "bi-tags"), ("deleted", "Deleted", "bi-trash"),
        ]
        return ctx


@login_required
def search_runs(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk)
    return render(request, "monitor/partials/run_table.html", {
        "runs": search.runs.all()[:20], "show_search": False,
    })


@require_POST
@login_required
def run_now(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk)
    trigger_manual_run(search)
    msg = f"Run queued for '{search.name}'."
    if _is_htmx(request):
        return _htmx_response(msg)
    messages.success(request, msg)
    return redirect(request.POST.get("next") or "dashboard")


@require_POST
@login_required
def toggle_enabled(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk)
    search.enabled = not search.enabled
    search.save(update_fields=["enabled", "updated_at"])
    reschedule_if_needed(search, ["enabled"])
    state = "resumed" if search.enabled else "paused"
    logger.info("Search '%s' %s", search.name, state, extra={"search_id": search.id})
    msg = f"'{search.name}' {state}."
    if _is_htmx(request):
        return _htmx_response(msg, level="info")
    messages.info(request, msg)
    return redirect(request.POST.get("next") or "dashboard")


@require_POST
@login_required
def mark_seen(request, pk=None):
    qs = SearchResult.objects.filter(is_new=True)
    if pk is not None:
        qs = qs.filter(search_id=pk)
    count = qs.update(is_new=False)
    msg = f"Marked {count} result(s) as seen."
    if _is_htmx(request):
        return _htmx_response(msg, level="info", events=("refreshTable", "refreshBadge", "refreshResults"))
    messages.info(request, msg)
    return redirect(request.POST.get("next") or "dashboard")


# ---------------------------------------------------------------- result actions
def _card_response(request, result, toast=None, events=("refreshBadge",)):
    """Re-render a result card (or nothing, which removes it) plus htmx events."""
    if result is None:
        resp = HttpResponse("")
    else:
        result = SearchResult.objects.prefetch_related("tags").get(pk=result.pk)
        resp = render(request, "monitor/partials/result_card.html", {
            "r": result, "view": request.POST.get("view"), "all_tags": Tag.objects.all(),
        })
    payload = {e: True for e in events}
    if toast:
        payload["showToast"] = {"message": toast, "level": "info"}
    resp["HX-Trigger"] = json.dumps(payload)
    return resp


@require_POST
@login_required
def result_action(request, pk, action):
    """Per-result actions from the result card (htmx)."""
    result = get_object_or_404(SearchResult, pk=pk)
    view = request.POST.get("view")
    if action == "seen":
        result.is_new = False
        result.save(update_fields=["is_new"])
        return _card_response(request, result)
    if action == "delete":
        result.soft_delete()
        logger.info("Result deleted: %s", result.url, extra={"search_id": result.search_id})
        # Drop the card from the list unless we're looking at the "deleted" view.
        return _card_response(request, result if view == "deleted" else None, toast="Result deleted.")
    if action == "restore":
        result.restore()
        return _card_response(request, None if view == "deleted" else result, toast="Result restored.")
    if action == "star":
        tag = Tag.get_or_create_named(Tag.STAR)
        if result.tags.filter(pk=tag.pk).exists():
            result.tags.remove(tag)
        else:
            result.tags.add(tag)
        return _card_response(request, result)
    return HttpResponse("Unknown action", status=400)


@require_POST
@login_required
def result_tag_add(request, pk):
    result = get_object_or_404(SearchResult, pk=pk)
    for name in (request.POST.get("tag") or "").split(","):
        if Tag.normalize(name):
            result.tags.add(Tag.get_or_create_named(name))
    return _card_response(request, result)


@require_POST
@login_required
def result_tag_remove(request, pk, tag_id):
    result = get_object_or_404(SearchResult, pk=pk)
    result.tags.remove(tag_id)
    return _card_response(request, result)


@require_POST
@login_required
def bulk_results(request, pk):
    """Apply an action to the results selected with checkboxes on the search page."""
    search = get_object_or_404(SavedSearch, pk=pk)
    ids = request.POST.getlist("ids")
    action = request.POST.get("action")
    back = request.POST.get("next") or reverse("search-detail", args=[pk])
    if not back.startswith("/"):
        back = reverse("search-detail", args=[pk])
    if not ids:
        messages.warning(request, "No results selected.")
        return redirect(back)
    qs = SearchResult.objects.filter(search=search, pk__in=ids)
    count = qs.count()
    if action == "seen":
        qs.update(is_new=False)
        msg = f"Marked {count} result(s) as seen."
    elif action == "delete":
        qs.update(deleted_at=timezone.now(), is_new=False)
        logger.info("Deleted %d result(s)", count, extra={"search_id": search.id})
        msg = f"Deleted {count} result(s)."
    elif action == "restore":
        qs.update(deleted_at=None)
        msg = f"Restored {count} result(s)."
    elif action in ("tag", "untag"):
        name = Tag.normalize(request.POST.get("tag"))
        if not name:
            messages.warning(request, "Enter a tag name first.")
            return redirect(back)
        tag = Tag.get_or_create_named(name)
        through = SearchResult.tags.through
        if action == "tag":
            through.objects.bulk_create(
                [through(searchresult_id=r, tag_id=tag.pk) for r in qs.values_list("pk", flat=True)],
                ignore_conflicts=True,
            )
            msg = f"Tagged {count} result(s) '{name}'."
        else:
            through.objects.filter(searchresult__in=qs, tag=tag).delete()
            msg = f"Removed tag '{name}' from {count} result(s)."
    else:
        return HttpResponse("Unknown action", status=400)
    messages.success(request, msg)
    return redirect(back)


# ---------------------------------------------------------------- ops
OPS_TABS = ["health", "tasks", "history", "logs"]


class OpsView(LoginRequiredMixin, TemplateView):
    template_name = "monitor/ops.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tab = self.request.GET.get("tab", "health")
        ctx["tab"] = tab if tab in OPS_TABS else "health"
        ctx["tabs"] = OPS_TABS
        ctx["query"] = self.request.GET.urlencode()
        return ctx


@login_required
def ops_health(request):
    checks = health.run_all_checks()
    return render(request, "monitor/partials/ops_health.html", {
        "checks": checks, "overall_ok": health.overall_ok(checks), "checked_at": timezone.now(),
    })


def _celery_snapshot():
    from config.celery import app

    from concurrent.futures import ThreadPoolExecutor

    # Each broadcast waits for its full timeout, so run the three in parallel.
    def call(method):
        return getattr(app.control.inspect(timeout=1.5), method)() or {}

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            active, reserved, stats = pool.map(call, ["active", "reserved", "stats"])
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    workers = []
    for name in sorted(set(active) | set(reserved) | set(stats)):
        pool = (stats.get(name) or {}).get("pool", {})
        workers.append({
            "name": name,
            "active": active.get(name, []),
            "reserved": len(reserved.get(name, [])),
            "concurrency": pool.get("max-concurrency"),
        })
    return {"workers": workers}


@login_required
def ops_tasks(request):
    active_runs = SearchRun.objects.filter(status__in=SearchRun.ACTIVE_STATUSES).select_related("search")
    upcoming = SavedSearch.objects.filter(enabled=True).order_by("next_run_at")[:20]
    return render(request, "monitor/partials/ops_tasks.html", {
        "active_runs": active_runs[:200],
        "upcoming": upcoming,
        "celery": _celery_snapshot(),
    })


@login_required
def ops_history(request):
    runs = filter_runs(request.GET)
    return render(request, "monitor/partials/ops_history.html", {
        "page": Paginator(runs, 50).get_page(request.GET.get("page")),
        "filters": request.GET,
        "searches": SavedSearch.objects.only("id", "name"),
        "statuses": SearchRun.Status.choices,
        "triggers": SearchRun.Trigger.choices,
    })


@login_required
def ops_logs(request):
    logs = filter_logs(request.GET)
    return render(request, "monitor/partials/ops_logs.html", {
        "page": Paginator(logs, 100).get_page(request.GET.get("page")),
        "filters": request.GET,
        "levels": LOG_LEVELS,
        "searches": SavedSearch.objects.only("id", "name"),
    })


# ---------------------------------------------------------------- settings
class SettingsView(LoginRequiredMixin, UpdateView):
    form_class = AppSettingsForm
    template_name = "monitor/settings.html"
    success_url = reverse_lazy("settings")

    def get_object(self, queryset=None):
        return AppSettings.load()

    def form_valid(self, form):
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["email_status"] = notify.email_status()
        return ctx


@require_POST
@login_required
def test_notification(request, channel):
    try:
        if channel == "email":
            to = notify.send_test_email(request.POST.get("to") or None)
            if notify.is_test_inbox():
                msg, level = (f"Test email caught by the Mailpit test inbox (not delivered to {to}). "
                              f"Open {settings.MAILPIT_URL} to view it."), "info"
            else:
                msg, level = f"Test email sent to {to}. Check that inbox (and its spam folder).", "success"
        elif channel == "slack":
            notify.send_test_slack(request.POST.get("webhook") or None)
            msg, level = "Test message posted to Slack.", "success"
        else:
            return HttpResponse(status=400)
    except Exception as exc:  # noqa: BLE001 - report any delivery failure to the user
        msg, level = f"{channel.title()} test failed: {exc}", "danger"
        logger.warning("%s test failed: %s", channel, exc)
    return _htmx_response(msg, level=level, events=())


@login_required
def engines_json(request):
    from monitor.services.searxng import available_engines

    return JsonResponse({"engines": available_engines()})
