"""SearXNG update check: compares the running version with Docker Hub and shows a notice. Never updates itself."""

from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor import tasks
from monitor.services import health, updates

URLS = {"direct": "http://searxng:8080", "proxy": "http://searxng-proxy:8080", "tor": "http://searxng-tor:8080"}
HUB = "https://hub.example/tags"


def response(data):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


def hub_tags(latest="2026.11.30-cd6d28091", digest_match=True):
    return {"results": [
        {"name": "latest", "digest": "sha256:new" if digest_match else "sha256:other",
         "last_updated": "2026-11-30T18:59:41Z"},
        {"name": latest, "digest": "sha256:new", "last_updated": "2026-11-30T18:59:38Z"},
        {"name": "2026.11.20-1ebea4a07", "digest": "sha256:mid", "last_updated": "2026-11-20T11:47:09Z"},
        {"name": "2026.11.19-368baa340", "digest": "sha256:old", "last_updated": "2026-11-19T06:24:24Z"},
    ]}


def fake_get(versions=None, hub=None, hub_error=None):
    """requests.get stand-in: SearXNG /config per instance (None = down) and the Docker Hub tags API."""
    versions = versions if versions is not None else {"direct": "2026.11.19+368baa340"}

    def get(url, timeout=None, headers=None):
        if url == HUB:
            if hub_error:
                raise hub_error
            return response(hub if hub is not None else hub_tags())
        for name, base in URLS.items():
            if url == f"{base}/config":
                if versions.get(name) is None:
                    raise requests.ConnectionError("down")
                return response({"version": versions[name]})
        raise AssertionError(url)

    return get


@override_settings(SEARXNG_UPDATE_CHECK=True, SEARXNG_UPDATE_CHECK_URL=HUB, SEARXNG_UPDATE_MIN_AGE_DAYS=7,
                   SEARXNG_URL=URLS["direct"], SEARXNG_PROXY_URL=URLS["proxy"], SEARXNG_TOR_URL=URLS["tor"])
class UpdateCheckTests(TestCase):
    def setUp(self):
        cache.clear()

    def check(self, **kw):
        with mock.patch("monitor.services.updates.requests.get", side_effect=fake_get(**kw)) as get:
            return updates.check(force=True), get

    def test_version_helpers(self):
        self.assertEqual(updates.normalize("2026.11.19+368baa340"), "2026.11.19-368baa340")
        self.assertEqual(str(updates.version_date("2026.11.9+368baa340")), "2026-11-09")
        self.assertIsNone(updates.version_date("latest"))
        self.assertIsNone(updates.version_date(None))

    def test_update_available_when_a_week_behind(self):
        result, _ = self.check()
        self.assertEqual(result["running"], "2026.11.19-368baa340")
        self.assertEqual(result["latest"], "2026.11.30-cd6d28091")  # found via the 'latest' digest
        self.assertEqual(result["days_behind"], 11)
        self.assertTrue(result["update_available"])

    def test_recent_builds_dont_nag(self):
        result, _ = self.check(hub=hub_tags(latest="2026.11.24-cd6d28091"))
        self.assertEqual(result["days_behind"], 5)
        self.assertFalse(result["update_available"])

    def test_up_to_date(self):
        result, _ = self.check(versions={"direct": "2026.11.30+cd6d28091"})
        self.assertEqual(result["days_behind"], 0)
        self.assertFalse(result["update_available"])

    def test_latest_falls_back_to_newest_tag_without_digest_match(self):
        result, _ = self.check(hub=hub_tags(digest_match=False))
        self.assertEqual(result["latest"], "2026.11.30-cd6d28091")

    def test_oldest_running_instance_wins_and_down_ones_are_skipped(self):
        result, _ = self.check(versions={"direct": "2026.11.30+cd6d28091", "proxy": "2026.11.19+368baa340",
                                         "tor": None})
        self.assertEqual(result["running"], "2026.11.19-368baa340")

    def test_offline_never_raises(self):
        result, _ = self.check(hub_error=requests.ConnectionError("no network"))
        self.assertIn("no network", result["error"])
        self.assertNotIn("update_available", result)
        result, _ = self.check(versions={})
        self.assertIn("no SearXNG instance answered", result["error"])

    def test_cached_until_forced(self):
        first, _ = self.check()
        with mock.patch("monitor.services.updates.requests.get") as get:
            self.assertEqual(updates.check(), first)
            self.assertEqual(updates.cached(), first)
        get.assert_not_called()

    @override_settings(SEARXNG_UPDATE_CHECK=False)
    def test_turned_off(self):
        with mock.patch("monitor.services.updates.requests.get") as get:
            self.assertIsNone(updates.check(force=True))
            self.assertIsNone(updates.cached())
        get.assert_not_called()

    def test_task_and_health_line(self):
        with mock.patch("monitor.services.updates.requests.get", side_effect=fake_get()):
            self.assertEqual(tasks.check_searxng_update.run(), "update available")
        ok, detail = health.check_searxng_version()  # served from the cache
        self.assertTrue(ok)  # an update is a notice, not a failure
        self.assertIn("Update available: 2026.11.30-cd6d28091 is 11 days newer", detail)
        self.assertIn("docker compose pull searxng searxng-proxy searxng-tor && docker compose up -d", detail)

    def test_health_line_when_offline_or_off(self):
        self.check(hub_error=requests.ConnectionError("no network"))
        ok, detail = health.check_searxng_version()
        self.assertIsNone(ok)
        self.assertIn("Couldn't check for updates", detail)
        with override_settings(SEARXNG_UPDATE_CHECK=False):
            self.assertEqual(health.check_searxng_version(), (None, "Update check turned off (SEARXNG_UPDATE_CHECK=false)"))

    def test_dispatch_queues_first_check_once(self):
        with mock.patch.object(tasks.check_searxng_update, "delay") as delay:
            tasks.dispatch_due_searches.run()
            tasks.dispatch_due_searches.run()
        delay.assert_called_once()


@override_settings(SEARXNG_UPDATE_CHECK=True, SEARXNG_UPDATE_CHECK_URL=HUB, SEARXNG_URL=URLS["direct"],
                   SEARXNG_PROXY_URL=URLS["proxy"], SEARXNG_TOR_URL=URLS["tor"])
class UpdateBannerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))

    def test_no_banner_without_result_and_no_network_on_page_load(self):
        with mock.patch("monitor.services.updates.requests.get") as get:
            self.assertNotContains(self.client.get(reverse("dashboard")), "SearXNG update is available")
        get.assert_not_called()

    def test_banner_when_update_available(self):
        with mock.patch("monitor.services.updates.requests.get", side_effect=fake_get()):
            updates.check(force=True)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "A SearXNG update is available")
        self.assertContains(resp, "docker compose pull searxng searxng-proxy searxng-tor")
        self.assertContains(resp, "2026.11.30-cd6d28091")

    def test_no_banner_when_up_to_date(self):
        with mock.patch("monitor.services.updates.requests.get",
                        side_effect=fake_get(versions={"direct": "2026.11.30+cd6d28091"})):
            updates.check(force=True)
        self.assertNotContains(self.client.get(reverse("dashboard")), "SearXNG update is available")
