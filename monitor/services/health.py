"""Health probes for every component of the stack (no Docker socket needed)."""

import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

BEAT_HEARTBEAT_KEY = "monitor:beat:last_dispatch"


def _timed(name, fn):
    start = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:300]
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
        "latency_ms": round((time.monotonic() - start) * 1000),
    }


def check_database():
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return True, connection.vendor


def _redis():
    import redis

    return redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=3, socket_connect_timeout=3)


def check_redis():
    if not settings.CELERY_BROKER_URL.startswith("redis"):
        return False, "broker is not redis"
    r = _redis()
    r.ping()
    return True, f"queue length: {r.llen('celery')}"


def check_searxng(base_url):
    def probe():
        from monitor.services.searxng import SearxngClient

        SearxngClient(base_url, timeout=5, retries=0).healthy()
        return True, base_url

    return probe


def check_proxy_instance():
    if not settings.SEARXNG_PROXY_ENABLED:
        return None, "disabled (SEARXNG_PROXY_ENABLED=false)"
    return check_searxng(settings.SEARXNG_PROXY_URL)()


def check_workers():
    from config.celery import app

    replies = app.control.inspect(timeout=1.5).ping() or {}
    if not replies:
        return False, "no workers responded"
    return True, f"{len(replies)} worker(s): {', '.join(sorted(replies))}"


def check_beat():
    last = cache.get(BEAT_HEARTBEAT_KEY)
    if not last:
        return False, "no dispatch recorded yet"
    age = (timezone.now() - last).total_seconds()
    limit = settings.DISPATCH_INTERVAL_SECONDS * 3
    return age < limit, f"last dispatch {int(age)}s ago"


def check_email():
    from monitor.services.notify import email_status

    status = email_status()
    if not status["configured"]:
        return None, status["message"]
    return status["ok"], status["message"]


def run_all_checks():
    checks = [
        ("Database", check_database),
        ("Redis broker", check_redis),
        ("SearXNG (direct)", check_searxng(settings.SEARXNG_URL)),
        ("SearXNG (proxy)", check_proxy_instance),
        ("Celery workers", check_workers),
        ("Scheduler (beat)", check_beat),
        ("Email (SMTP)", check_email),
    ]
    # Checks that touch the DB run on this thread (Django connections are per-thread,
    # and a pool thread would leave its connection open); network probes run in parallel.
    on_main = {"Database", "Email (SMTP)"}
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {name: pool.submit(_timed, name, fn) for name, fn in checks if name not in on_main}
        local = {name: _timed(name, fn) for name, fn in checks if name in on_main}
        return [local[name] if name in local else futures[name].result() for name, _ in checks]


def overall_ok(results):
    return all(r["ok"] is not False for r in results)
