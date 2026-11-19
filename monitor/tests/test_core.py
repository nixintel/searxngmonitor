from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from monitor import tasks
from monitor.models import LogEntry, SavedSearch, SearchResult, SearchRun
from monitor.services.searxng import SearxngError, fetch_results, is_usable_url, normalize_url, url_hash, validate_engines
from monitor.templatetags.monitor_tags import safe_href

FAKE_ENGINES = [{"name": n, "categories": ["general"], "enabled": True}
                for n in ["bing", "brave", "duckduckgo", "google", "mojeek", "qwant"]]


def fake_results(*urls):
    return [{"url": u, "title": f"Title {u}", "content": "snippet", "engine": "brave",
             "engines": ["brave"], "publishedDate": None} for u in urls]


class UrlNormalisationTests(TestCase):
    def test_strips_tracking_and_fragment(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM:443/path/?utm_source=x&id=5&fbclid=abc#frag"),
            "https://example.com/path?id=5",
        )

    def test_equivalent_urls_hash_equal(self):
        self.assertEqual(url_hash("https://example.com/a/"), url_hash("https://EXAMPLE.com/a?utm_medium=y"))
        self.assertNotEqual(url_hash("https://example.com/a"), url_hash("https://example.com/b"))

    def test_usable_url(self):
        self.assertTrue(is_usable_url("https://example.com/x"))
        self.assertFalse(is_usable_url("http:///go/v1/AbC123"))
        self.assertFalse(is_usable_url("javascript:alert(1)"))
        self.assertFalse(is_usable_url(None))

    def test_fetch_drops_unusable_urls(self):
        s = SavedSearch.objects.create(name="u", query="q", engines=["brave"])
        page = {"results": fake_results("https://ok.example/1", "http:///go/v1/tok"), "unresponsive_engines": []}
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value={"brave"}),              mock.patch("monitor.services.searxng.SearxngClient.search", return_value=page):
            results, _ = fetch_results(s)
        self.assertEqual([r["url"] for r in results], ["https://ok.example/1"])
        self.assertTrue(LogEntry.objects.filter(message__contains="unusable URLs").exists())

    def test_safe_href(self):
        self.assertEqual(safe_href("https://ok.example/x"), "https://ok.example/x")
        self.assertEqual(safe_href("javascript:alert(1)"), "#")
        self.assertEqual(safe_href("data:text/html,hi"), "#")


@mock.patch("monitor.services.searxng.available_engines", return_value=FAKE_ENGINES)
class RunSearchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.search = SavedSearch.objects.create(name="Test", query="acme corp", engines=["brave"])

    def _run(self, results, trigger=SearchRun.Trigger.MANUAL):
        run = SearchRun.objects.create(search=self.search, trigger=trigger)
        with mock.patch("monitor.tasks.fetch_results", return_value=(results, [])):
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        return run

    def test_new_results_saved_and_duplicates_ignored(self, _):
        r1 = self._run(fake_results("https://a.example/1", "https://a.example/2", "https://a.example/2?utm_source=z"))
        self.assertEqual(r1.status, "success")
        self.assertEqual(r1.results_returned, 3)
        self.assertEqual(r1.new_results, 2)
        self.assertEqual(SearchResult.objects.filter(is_new=True).count(), 2)

        r2 = self._run(fake_results("https://a.example/1", "https://a.example/3"))
        self.assertEqual(r2.new_results, 1)
        self.assertEqual(self.search.results.count(), 3)
        self.search.refresh_from_db()
        self.assertIsNotNone(self.search.last_run_at)

    def test_baseline_first_run_not_marked_new(self, _):
        self.search.baseline_first_run = True
        self.search.save()
        self._run(fake_results("https://b.example/1"))
        self.assertFalse(SearchResult.objects.get().is_new)
        self._run(fake_results("https://b.example/2"))
        self.assertTrue(SearchResult.objects.get(url="https://b.example/2").is_new)

    def test_failure_recorded_and_logged(self, _):
        run = SearchRun.objects.create(search=self.search)
        with mock.patch("monitor.tasks.fetch_results", side_effect=SearxngError("boom")):
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("boom", run.error)
        self.assertTrue(LogEntry.objects.filter(level="ERROR", run=run).exists())

    def test_all_engines_failed_marks_run_failed(self, _):
        run = SearchRun.objects.create(search=self.search)
        with mock.patch("monitor.tasks.fetch_results", return_value=([], [["bing", "timeout"]])):
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("bing (timeout)", run.error)

    def test_overlapping_run_skipped(self, _):
        cache.add(tasks._lock_key(self.search.id), 999, 60)
        run = self._run(fake_results("https://c.example/1"))
        self.assertEqual(run.status, "skipped")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_sent_for_new_results(self, _):
        self.search.notify_email = True
        self.search.notify_address = "alerts@example.com"
        self.search.save()
        with mock.patch.object(tasks.send_new_results_email, "delay",
                               side_effect=lambda run_id: tasks.send_new_results_email.run(run_id)):
            self._run(fake_results("https://d.example/1", "https://d.example/2"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("2 new result", mail.outbox[0].subject)
        self.assertIn("https://d.example/1", mail.outbox[0].body)

    def test_proxy_disabled_fails_cleanly(self, _):
        self.search.use_proxy = True
        self.search.save()
        run = SearchRun.objects.create(search=self.search)
        tasks.run_search.run(run.id)  # real fetch_results -> SearxngClient.proxied() raises
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("proxy", run.error)


class EngineValidationTests(TestCase):
    def test_unknown_engines_dropped_and_logged(self):
        s = SavedSearch.objects.create(name="v", query="q", engines=["brave", "mojeek"])
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value={"brave", "bing"}):
            self.assertEqual(validate_engines(s), ["brave"])
        self.assertTrue(LogEntry.objects.filter(level="WARNING", message__contains="mojeek").exists())

    def test_no_valid_engines_fails(self):
        s = SavedSearch.objects.create(name="v", query="q", engines=["mojeek"])
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value={"brave"}):
            with self.assertRaises(SearxngError):
                validate_engines(s)

    def test_config_unreachable_passes_through(self):
        s = SavedSearch.objects.create(name="v", query="q", engines=["anything"])
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value=None):
            self.assertEqual(validate_engines(s), ["anything"])


class DispatcherTests(TestCase):
    @mock.patch("monitor.tasks.run_search.apply_async")
    def test_dispatches_only_due_enabled_searches(self, apply_async):
        now = timezone.now()
        due = SavedSearch.objects.create(name="due", query="x", next_run_at=now - timedelta(minutes=1))
        SavedSearch.objects.create(name="later", query="y", next_run_at=now + timedelta(hours=1))
        SavedSearch.objects.create(name="off", query="z", enabled=False, next_run_at=now - timedelta(hours=1))

        self.assertEqual(tasks.dispatch_due_searches.run(), 1)
        self.assertEqual(apply_async.call_count, 1)
        due.refresh_from_db()
        self.assertGreater(due.next_run_at, now)
        run = SearchRun.objects.get()
        self.assertEqual((run.search, run.trigger), (due, "scheduled"))
        self.assertTrue(run.celery_task_id)

        self.assertEqual(tasks.dispatch_due_searches.run(), 0)  # not due again yet

    def test_new_search_due_immediately(self):
        s = SavedSearch.objects.create(name="n", query="q")
        self.assertLessEqual(s.next_run_at, timezone.now())

    def test_stale_runs_reaped(self):
        s = SavedSearch.objects.create(name="n", query="q")
        old = SearchRun.objects.create(search=s, status="running", created_at=timezone.now() - timedelta(hours=3))
        fresh = SearchRun.objects.create(search=s, status="queued")
        self.assertEqual(tasks.reap_stale_runs.run(), 1)
        old.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual((old.status, fresh.status), ("failed", "queued"))


@mock.patch("monitor.services.searxng.available_engines", return_value=FAKE_ENGINES)
@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES)
class UiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("admin", password="pw-123456")
        self.client.force_login(self.user)

    def test_login_required(self, *_):
        self.client.logout()
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_crud_and_pages_render(self, *_):
        resp = self.client.post(reverse("search-create"), {
            "name": "Acme", "query": '"acme corp"', "engines": ["brave", "bing"], "categories": "general",
            "language": "all", "time_range": "", "max_pages": 1, "interval_value": 2,
            "interval_unit": "hours", "enabled": "on",
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", {}) and resp.context["form"].errors)
        s = SavedSearch.objects.get()
        self.assertEqual(s.engines, ["brave", "bing"])
        SearchResult.objects.create(search=s, url="javascript:alert(1)", url_hash="h", title="evil")

        for name, args in [("dashboard", []), ("dashboard-table", []), ("search-detail", [s.pk]),
                           ("search-edit", [s.pk]), ("search-delete", [s.pk]), ("ops", []),
                           ("ops-tasks", []), ("ops-history", []), ("ops-logs", []), ("settings", []),
                           ("search-runs", [s.pk]), ("new-badge", [])]:
            with mock.patch("monitor.views._celery_snapshot", return_value={"workers": []}):
                r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)
        detail = self.client.get(reverse("search-detail", args=[s.pk])).content.decode()
        self.assertNotIn('href="javascript:', detail)
        self.assertIn("1 new", self.client.get(reverse("dashboard")).content.decode())

        resp = self.client.post(reverse("search-edit", args=[s.pk]), {
            "name": "Acme 2", "query": "acme", "engines": ["brave"], "categories": "general", "language": "all",
            "time_range": "week", "max_pages": 2, "interval_value": 30, "interval_unit": "minutes",
        })
        self.assertEqual(resp.status_code, 302)
        s.refresh_from_db()
        self.assertEqual((s.name, s.enabled, s.time_range), ("Acme 2", False, "week"))

        self.client.post(reverse("search-delete", args=[s.pk]))
        self.assertFalse(SavedSearch.objects.exists())

    def test_interval_minimum_enforced(self, *_):
        resp = self.client.post(reverse("search-create"), {
            "name": "Fast", "query": "x", "engines": ["brave"], "categories": "general", "language": "all",
            "max_pages": 1, "interval_value": 1, "interval_unit": "minutes", "enabled": "on",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("at least", resp.content.decode())

    @mock.patch("monitor.tasks.run_search.apply_async")
    def test_run_now_htmx(self, apply_async, *_):
        s = SavedSearch.objects.create(name="n", query="q")
        resp = self.client.post(reverse("search-run", args=[s.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.assertIn("showToast", resp["HX-Trigger"])
        self.assertEqual(SearchRun.objects.get().trigger, "manual")
        apply_async.assert_called_once()

    def test_mark_seen(self, *_):
        s = SavedSearch.objects.create(name="n", query="q")
        r = SearchResult.objects.create(search=s, url="https://x.example", url_hash="1")
        SearchResult.objects.create(search=s, url="https://y.example", url_hash="2")
        resp = self.client.post(reverse("result-action", args=[r.pk, "seen"]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.client.post(reverse("search-mark-seen", args=[s.pk]))
        self.assertFalse(SearchResult.objects.filter(is_new=True).exists())

    def test_health_page(self, *_):
        fake = [{"name": "Database", "ok": True, "detail": "x", "latency_ms": 1},
                {"name": "SearXNG (direct)", "ok": False, "detail": "down", "latency_ms": 5}]
        with mock.patch("monitor.views.health.run_all_checks", return_value=fake):
            resp = self.client.get(reverse("ops-health"))
        self.assertContains(resp, "One or more components are unhealthy")

    def test_public_healthz(self, *_):
        self.client.logout()
        self.assertEqual(self.client.get("/healthz").status_code, 200)


class ApiTests(APITestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("api", password="pw-123456")
        self.client.force_authenticate(user)

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/v1/searches/").status_code, 401)

    @mock.patch("monitor.tasks.run_search.apply_async")
    def test_search_crud_and_actions(self, apply_async):
        resp = self.client.post("/api/v1/searches/", {
            "name": "API", "query": "foo", "engines": ["brave"], "interval_value": 1, "interval_unit": "hours",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        sid = resp.data["id"]

        bad = self.client.patch(f"/api/v1/searches/{sid}/", {"interval_value": 1, "interval_unit": "minutes"}, format="json")
        self.assertEqual(bad.status_code, 400)

        self.assertEqual(self.client.patch(f"/api/v1/searches/{sid}/", {"enabled": False}, format="json").status_code, 200)
        listing = self.client.get("/api/v1/searches/").data
        self.assertEqual(listing["results"][0]["new_count"], 0)

        run = self.client.post(f"/api/v1/searches/{sid}/run/")
        self.assertEqual(run.status_code, 202)
        apply_async.assert_called_once()

        SearchResult.objects.create(search_id=sid, url="https://x.example", url_hash="1")
        self.assertEqual(self.client.get(f"/api/v1/searches/{sid}/results/?new=true").data["count"], 1)
        self.assertEqual(self.client.post(f"/api/v1/searches/{sid}/mark-seen/").data["marked"], 1)
        self.assertEqual(self.client.get("/api/v1/stats/").data["new_results"], 0)
        self.assertEqual(self.client.get(f"/api/v1/runs/?search={sid}").data["count"], 1)
        self.assertEqual(self.client.get("/api/v1/logs/?level=INFO").status_code, 200)

        self.assertEqual(self.client.delete(f"/api/v1/searches/{sid}/").status_code, 204)

    def test_schema(self):
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)


class AdminShortcutTests(TestCase):
    def test_only_staff_see_admin_link(self):
        admin = get_user_model().objects.create_superuser("boss", password="pw-123456")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("dashboard")), 'href="/admin/" title="Admin panel"')

        normal = get_user_model().objects.create_user("student", password="pw-123456")
        self.client.force_login(normal)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Admin panel")
