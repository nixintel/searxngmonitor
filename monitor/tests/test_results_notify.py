"""Result deletion/tagging, notification config warnings and Slack delivery."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from monitor import tasks
from monitor.models import AppSettings, SavedSearch, SearchResult, SearchRun, Tag
from monitor.services import notify
from monitor.services.searxng import store_results, url_hash

FAKE_ENGINES = [{"name": n, "categories": ["general"], "enabled": True} for n in ["bing", "brave"]]
SMTP = "django.core.mail.backends.smtp.EmailBackend"


def make_results(search, n=3):
    return [
        SearchResult.objects.create(
            search=search, url=f"https://r{i}.example/", url_hash=url_hash(f"https://r{i}.example/"), title=f"R{i}")
        for i in range(n)
    ]


@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES)
class ResultUiTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))
        self.search = SavedSearch.objects.create(name="S", query="q")
        self.r1, self.r2, self.r3 = make_results(self.search)

    def detail(self, **params):
        return self.client.get(reverse("search-detail", args=[self.search.pk]), params)

    def test_soft_delete_hides_and_blocks_readding(self, _):
        resp = self.client.post(reverse("result-action", args=[self.r1.pk, "delete"]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.content, b"")  # card removed from the list
        self.r1.refresh_from_db()
        self.assertTrue(self.r1.is_deleted)
        self.assertFalse(self.r1.is_new)
        self.assertNotContains(self.detail(), "R0<")
        self.assertContains(self.detail(view="deleted"), "R0")

        # A later run returning the same URL must not bring it back as new.
        run = SearchRun.objects.create(search=self.search)
        added = store_results(self.search, run, [{"url": "https://r0.example/", "title": "R0 again"}])
        self.assertEqual(added, 0)

        self.client.post(reverse("result-action", args=[self.r1.pk, "restore"]), {"view": "deleted"})
        self.r1.refresh_from_db()
        self.assertFalse(self.r1.is_deleted)

    def test_star_and_tags(self, _):
        self.client.post(reverse("result-action", args=[self.r1.pk, "star"]))
        self.assertEqual(self.r1.tag_names, {"interesting"})
        self.client.post(reverse("result-tag-add", args=[self.r2.pk]), {"tag": " Lead ,  Follow Up"})
        self.assertEqual(self.r2.tag_names, {"lead", "follow up"})

        tagged = self.detail(view="tagged").content.decode()
        self.assertIn("R0", tagged)
        self.assertIn("R1", tagged)
        self.assertNotIn(">R2<", tagged)
        lead = Tag.objects.get(name="lead")
        only_lead = self.detail(view="tagged", tag=lead.pk).content.decode()
        self.assertIn("R1", only_lead)
        self.assertNotIn(">R0<", only_lead)

        self.client.post(reverse("result-tag-remove", args=[self.r2.pk, lead.pk]))
        self.assertEqual(self.r2.tag_names, {"follow up"})
        # star toggles off again
        self.client.post(reverse("result-action", args=[self.r1.pk, "star"]))
        self.assertEqual(self.r1.tag_names, set())

    def test_bulk_actions(self, _):
        url = reverse("results-bulk", args=[self.search.pk])
        ids = [self.r1.pk, self.r2.pk]
        self.client.post(url, {"ids": ids, "action": "tag", "tag": "Priority"})
        self.client.post(url, {"ids": ids, "action": "tag", "tag": "priority"})  # idempotent
        self.assertEqual(SearchResult.objects.filter(tags__name="priority").count(), 2)
        self.client.post(url, {"ids": [self.r1.pk], "action": "untag", "tag": "priority"})
        self.assertEqual(SearchResult.objects.filter(tags__name="priority").count(), 1)
        resp = self.client.post(url, {"ids": ids, "action": "delete", "next": "https://evil.example/"})
        self.assertEqual(resp.url, reverse("search-detail", args=[self.search.pk]))  # no open redirect
        self.assertEqual(SearchResult.objects.deleted().count(), 2)
        self.client.post(url, {"ids": ids, "action": "restore"})
        self.assertEqual(SearchResult.objects.deleted().count(), 0)

    def test_counts_exclude_deleted(self, _):
        self.r1.soft_delete()
        self.r2.tags.add(Tag.get_or_create_named("x"))
        s = __import__("monitor.queries", fromlist=["x"]).searches_with_stats().get(pk=self.search.pk)
        self.assertEqual((s.total_count, s.new_count, s.tagged_count), (2, 2, 1))
        dash = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("2 new", dash)

    def test_detail_tabs_render(self, _):
        for view in ("all", "new", "tagged", "deleted"):
            self.assertEqual(self.detail(view=view).status_code, 200, view)
        self.assertEqual(self.detail(new="1").status_code, 200)  # legacy link from emails


@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES)
class NotificationConfigTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))

    @override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST="")
    def test_form_highlights_missing_email_server(self, _):
        resp = self.client.get(reverse("search-create"))
        self.assertContains(resp, "Email alerts are not available")
        self.assertContains(resp, "No email server configured")

    @override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST="smtp.invalid", EMAIL_PORT=2525)
    def test_form_highlights_unreachable_server(self, _):
        with mock.patch("monitor.services.notify.socket.create_connection", side_effect=OSError("refused")):
            resp = self.client.get(reverse("search-create"))
        self.assertContains(resp, "Email server unreachable")
        self.assertContains(resp, "smtp.invalid:2525")

    @override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST="mail.example", EMAIL_PORT=25)
    def test_form_ok_when_reachable(self, _):
        with mock.patch("monitor.services.notify.socket.create_connection"):
            resp = self.client.get(reverse("search-create"))
        self.assertNotContains(resp, "Email alerts are not available")

    @override_settings(NOTIFY_EMAIL_TO="", SLACK_WEBHOOK_URL="")
    def test_alerts_need_a_destination(self, _):
        data = {"name": "N", "query": "q", "engines": ["brave"], "categories": "general", "language": "all",
                "max_pages": 1, "interval_value": 1, "interval_unit": "hours", "enabled": "on",
                "notify_email": "on", "notify_slack": "on"}
        resp = self.client.post(reverse("search-create"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "set a default address in Settings")
        self.assertContains(resp, "set a default webhook in Settings")
        AppSettings.objects.update_or_create(pk=1, defaults={
            "notify_address": "a@example.com", "slack_webhook_url": "https://hooks.slack.com/services/T/B/X"})
        self.assertEqual(self.client.post(reverse("search-create"), data).status_code, 302)

    @override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST="")
    def test_dashboard_banner_when_email_broken(self, _):
        SavedSearch.objects.create(name="E", query="q", notify_email=True, notify_address="a@example.com")
        self.assertContains(self.client.get(reverse("dashboard")), "Email alerts are not being delivered")


class SlackTests(TestCase):
    def setUp(self):
        self.search = SavedSearch.objects.create(
            name="Acme <script>", query="acme & co", notify_slack=True,
            slack_webhook_url="https://hooks.slack.com/services/T/B/X",
        )

    def test_payload_escapes_and_truncates(self):
        results = [SearchResult(url=f"https://e.example/{i}?a=1|2", title=f"T<{i}>", content="x & y") for i in range(20)]
        payload = notify.build_slack_payload(self.search, results, 20, "https://app/searches/1/")
        self.assertIn("20 new results", payload["text"])
        sections = [b for b in payload["blocks"] if b["type"] == "section"]
        self.assertEqual(len(sections), notify.MAX_RESULTS_IN_SLACK)
        self.assertIn("<https://e.example/0?a=1%7C2|T&lt;0&gt;>", sections[0]["text"]["text"])
        self.assertIn("x &amp; y", sections[0]["text"]["text"])
        self.assertIn("and 5 more", payload["blocks"][-1]["elements"][0]["text"])
        self.assertIn("acme &amp; co", payload["blocks"][1]["elements"][0]["text"])

    @mock.patch("monitor.services.notify.requests.post")
    def test_run_posts_to_slack(self, post):
        post.return_value.status_code = 200
        cache.clear()
        run = SearchRun.objects.create(search=self.search)
        fake = [{"url": "https://n.example/1", "title": "New one"}]
        with mock.patch("monitor.tasks.fetch_results", return_value=(fake, [])), \
             mock.patch.object(tasks.send_new_results_slack, "delay",
                               side_effect=lambda rid: tasks.send_new_results_slack.run(rid)):
            tasks.run_search.run(run.id)
        post.assert_called_once()
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, "https://hooks.slack.com/services/T/B/X")
        self.assertIn("New one", str(kwargs["json"]["blocks"]))

    @mock.patch("monitor.services.notify.requests.post")
    def test_slack_error_surfaces(self, post):
        post.return_value.status_code = 404
        post.return_value.text = "no_service"
        with self.assertRaisesMessage(notify.NotifyError, "no_service"):
            notify.send_test_slack("https://hooks.slack.com/services/T/B/X")

    @override_settings(SLACK_ALLOW_HTTP=False)
    def test_rejects_non_https_webhook(self):
        with self.assertRaisesMessage(notify.NotifyError, "https://"):
            notify.send_test_slack("http://169.254.169.254/latest")


class ResultApiTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(get_user_model().objects.create_user("api", password="pw-123456"))
        self.search = SavedSearch.objects.create(name="S", query="q")
        self.r1, self.r2, _ = make_results(self.search)

    def test_tag_delete_restore_filters(self):
        resp = self.client.patch(f"/api/v1/results/{self.r1.pk}/", {"tags": ["Lead", "x"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["tags"], ["lead", "x"])
        self.assertEqual(self.client.get("/api/v1/results/?view=tagged").data["count"], 1)
        self.assertEqual(self.client.get("/api/v1/results/?tag=lead").data["count"], 1)
        self.assertEqual(self.client.get("/api/v1/tags/").data["results"][0]["result_count"], 1)

        self.assertEqual(self.client.delete(f"/api/v1/results/{self.r2.pk}/").status_code, 204)
        self.assertTrue(SearchResult.objects.filter(pk=self.r2.pk).exists())  # soft delete
        self.assertEqual(self.client.get("/api/v1/results/").data["count"], 2)
        self.assertEqual(self.client.get("/api/v1/results/?view=deleted").data["count"], 1)
        self.client.post(f"/api/v1/results/{self.r2.pk}/restore/")
        self.assertEqual(self.client.get("/api/v1/results/").data["count"], 3)

    def test_webhook_is_write_only(self):
        resp = self.client.post("/api/v1/searches/", {
            "name": "A", "query": "q", "engines": ["brave"], "interval_value": 1, "interval_unit": "hours",
            "notify_slack": True, "slack_webhook_url": "https://hooks.slack.com/services/T/B/SECRET",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertNotIn("slack_webhook_url", resp.data)
        self.assertTrue(resp.data["slack_webhook_set"])
        self.assertNotIn("SECRET", str(self.client.get("/api/v1/searches/").data))
