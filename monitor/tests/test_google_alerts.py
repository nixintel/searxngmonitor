"""Google Alerts RSS feeds as a search source."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from monitor import tasks
from monitor.models import SavedSearch, SearchResult, SearchRun
from monitor.services.feeds import clean_text, parse_feed, unwrap_google_url
from monitor.services.searxng import SearxngError

FEED_URL = "https://www.google.com/alerts/feeds/10518234095857874996/10859786811638003985"
FAKE_ENGINES = [{"name": n, "categories": ["general"], "enabled": True} for n in ["bing", "brave"]]


def entry(n, usg="AOvVaw0abc"):
    return f"""
  <entry>
    <id>tag:google.com,2013:googlealerts/feed:{n}</id>
    <title type="html">&lt;b&gt;Fancy Bear&lt;/b&gt; story {n} &amp;amp; more</title>
    <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://news{n}.example/a%3Fid%3D{n}&amp;ct=ga&amp;cd=CAIyGj&amp;usg={usg}"/>
    <published>2026-11-1{n}T10:00:00Z</published>
    <updated>2026-11-1{n}T10:00:00Z</updated>
    <content type="html">Report says &lt;b&gt;APT 28&lt;/b&gt; targeted item {n}</content>
    <author><name/></author>
  </entry>"""


def feed(*entries):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:idx="urn:atom-extension:indexing">
  <id>tag:google.com,2005:reader/user/1/state/com.google/alerts/2</id>
  <title>Google Alert - "apt 28" OR "fancy bear"</title>
  <link href="{FEED_URL}" rel="self"></link>
  {''.join(entries)}
</feed>""".encode()


class FeedParsingTests(TestCase):
    def test_unwrap_and_clean(self):
        self.assertEqual(
            unwrap_google_url("https://www.google.com/url?rct=j&sa=t&url=https://x.example/a%3Fb%3D1&usg=Z"),
            "https://x.example/a?b=1",
        )
        self.assertEqual(unwrap_google_url("https://direct.example/"), "https://direct.example/")
        self.assertEqual(clean_text("<b>APT 28</b> &amp; co"), "APT 28 & co")

    def test_parse_feed(self):
        title, results = parse_feed(feed(entry(1), entry(2)))
        self.assertEqual(title, 'Google Alert - "apt 28" OR "fancy bear"')
        self.assertEqual(len(results), 2)
        r = results[0]
        self.assertEqual(r["url"], "https://news1.example/a?id=1")
        self.assertEqual(r["title"], "Fancy Bear story 1 & more")
        self.assertEqual(r["content"], "Report says APT 28 targeted item 1")
        self.assertEqual(r["engines"], ["google alerts"])
        self.assertTrue(r["publishedDate"].startswith("2026-11-11T10:00:00"))

    def test_empty_feed_ok_and_html_rejected(self):
        title, results = parse_feed(feed())
        self.assertEqual(results, [])
        with self.assertRaises(SearxngError):
            parse_feed(b"<html><body>Sign in</body></html>")


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def iter_content(self, size):
        yield self.body


class AlertRunTests(TestCase):
    def setUp(self):
        cache.clear()
        self.search = SavedSearch.objects.create(
            name="APT28", source=SavedSearch.Source.GOOGLE_ALERT, feed_url=FEED_URL, query="",
        )

    def run_with(self, body, status=200):
        run = SearchRun.objects.create(search=self.search)
        with mock.patch("monitor.services.feeds.requests.Session.get", return_value=FakeResponse(body, status)):
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        return run

    def test_runs_dedupe_and_query_from_title(self):
        r1 = self.run_with(feed(entry(1, "a"), entry(2, "a"), entry(3, "a")))
        self.assertEqual((r1.status, r1.results_returned, r1.new_results), ("success", 3, 3))
        self.search.refresh_from_db()
        self.assertEqual(self.search.query, '"apt 28" OR "fancy bear"')
        # Same articles, different Google tracking tokens: nothing new.
        r2 = self.run_with(feed(entry(1, "b"), entry(2, "b"), entry(3, "b")))
        self.assertEqual(r2.new_results, 0)
        r3 = self.run_with(feed(entry(4), entry(1), entry(2)))
        self.assertEqual(r3.new_results, 1)
        self.assertEqual(SearchResult.objects.filter(search=self.search).count(), 4)

    def test_empty_feed_is_success(self):
        run = self.run_with(feed())
        self.assertEqual((run.status, run.results_returned), ("success", 0))

    def test_errors_fail_the_run(self):
        run = self.run_with(b"", status=404)
        self.assertEqual(run.status, "failed")
        self.assertIn("not found", run.error)
        run = self.run_with(b"<html>nope</html>")
        self.assertEqual(run.status, "failed")
        self.assertIn("RSS/Atom", run.error)

    def test_never_calls_searxng(self):
        with mock.patch("monitor.tasks.fetch_results") as searx:
            self.run_with(feed(entry(1)))
        searx.assert_not_called()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_for_new_alert_results(self):
        self.search.notify_email = True
        self.search.notify_address = "a@example.com"
        self.search.save()
        with mock.patch.object(tasks.send_new_results_email, "delay",
                               side_effect=lambda rid: tasks.send_new_results_email.run(rid)):
            self.run_with(feed(entry(1), entry(2)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://news1.example/a?id=1", mail.outbox[0].body)


@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES)
class AlertFormTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))

    def base(self, **extra):
        data = {"name": "Alert", "source": "google_alert", "categories": "general", "language": "all",
                "max_pages": 1, "interval_value": 1, "interval_unit": "hours", "enabled": "on"}
        data.update(extra)
        return data

    def test_create_alert_without_query_or_engines(self, _):
        resp = self.client.post(reverse("search-create"), self.base(feed_url=FEED_URL, use_proxy="on"))
        self.assertEqual(resp.status_code, 302, resp.content[:2000])
        s = SavedSearch.objects.get()
        self.assertTrue(s.is_google_alert)
        self.assertFalse(s.use_proxy)  # proxy doesn't apply to alerts

    def test_rejects_non_google_urls(self, _):
        for bad in ["", "https://evil.example/alerts/feeds/1/2", "http://www.google.com/alerts/feeds/1/2",
                    "https://www.google.com/alerts/feeds/abc"]:
            resp = self.client.post(reverse("search-create"), self.base(feed_url=bad))
            self.assertEqual(resp.status_code, 200, bad)
        self.assertFalse(SavedSearch.objects.exists())

    def test_searxng_still_requires_query(self, _):
        resp = self.client.post(reverse("search-create"), self.base(source="searxng", engines=["brave"]))
        self.assertContains(resp, "Enter a keyword or phrase")

    def test_pages_render(self, _):
        resp = self.client.get(reverse("search-create") + "?source=google_alert")
        self.assertContains(resp, 'value="google_alert"')
        self.assertContains(resp, "How to get the feed URL")
        s = SavedSearch.objects.create(name="A", source="google_alert", feed_url=FEED_URL, query="x")
        self.assertContains(self.client.get(reverse("dashboard")), "bi-google")
        self.assertContains(self.client.get(reverse("search-detail", args=[s.pk])), "Google Alerts RSS")
        self.assertEqual(self.client.get(reverse("search-edit", args=[s.pk])).status_code, 200)


class AlertApiTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(get_user_model().objects.create_user("api", password="pw-123456"))

    def test_create_via_api(self):
        resp = self.client.post("/api/v1/searches/", {
            "name": "API alert", "source": "google_alert", "feed_url": FEED_URL,
            "interval_value": 1, "interval_unit": "hours",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["source"], "google_alert")
        bad = self.client.post("/api/v1/searches/", {
            "name": "Bad", "source": "google_alert", "feed_url": "https://example.com/feed",
            "interval_value": 1, "interval_unit": "hours",
        }, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("feed_url", bad.data)
