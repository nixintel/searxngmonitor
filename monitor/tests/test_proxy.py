"""Proxy settings edited in the UI and applied to the searxng-proxy instance."""

import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor import tasks
from monitor.models import AppSettings, SavedSearch, SearchRun
from monitor.services import proxy_config

FAKE_ENGINES = [{"name": n, "categories": ["general"], "enabled": True} for n in ["bing", "brave"]]


def configure(**kw):
    app = AppSettings.load()
    for k, v in {"proxy_enabled": True, "proxy_scheme": "socks5h", "proxy_host": "proxy.example.net",
                 "proxy_port": 1080, **kw}.items():
        setattr(app, k, v)
    app.save()
    return app


def config_response(instance_name):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"instance_name": instance_name}
    return resp


class ProxyUrlTests(TestCase):
    def test_url_building_and_escaping(self):
        app = configure(proxy_username="stu dent", proxy_password="p@ss:w/rd#&")
        self.assertEqual(proxy_config.proxy_url(app),
                         "socks5h://stu%20dent:p%40ss%3Aw%2Frd%23%26@proxy.example.net:1080")
        self.assertEqual(proxy_config.proxy_url(app, redact=True), "socks5h://stu%20dent:****@proxy.example.net:1080")
        app.proxy_password = ""
        self.assertEqual(proxy_config.proxy_url(app), "socks5h://stu%20dent@proxy.example.net:1080")

    def test_off_or_incomplete_means_no_url(self):
        app = configure(proxy_enabled=False)
        self.assertEqual(proxy_config.proxy_url(app), "")
        app = configure(proxy_port=None)
        self.assertEqual(proxy_config.proxy_url(app), "")

    def test_render_uses_blackhole_when_off(self):
        rendered = proxy_config.render(configure(proxy_enabled=False))
        self.assertIn('- "http://127.0.0.1:9"', rendered)
        self.assertIn(f'instance_name: "{proxy_config.expected_instance_name()}"', rendered)
        self.assertNotIn("__PROXY_URL__", rendered)

    def test_render_and_fingerprint_change_with_settings(self):
        a = configure(proxy_username="u", proxy_password="one")
        fp1, out = proxy_config.fingerprint(a), proxy_config.render(a)
        self.assertIn('- "socks5h://u:one@proxy.example.net:1080"', out)
        b = configure(proxy_username="u", proxy_password="two")
        self.assertNotEqual(fp1, proxy_config.fingerprint(b))

    def test_apply_writes_only_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "settings.yml"
            with override_settings(SEARXNG_PROXY_CONFIG_PATH=str(target)):
                app = configure()
                self.assertTrue(proxy_config.apply(app))
                self.assertIn("proxy.example.net", target.read_text())
                self.assertFalse(proxy_config.apply(app))  # unchanged -> no rewrite, no restart
                self.assertTrue(proxy_config.apply(configure(proxy_port=9050)))
                self.assertEqual([p.name for p in Path(d).iterdir()], ["settings.yml"])  # no temp files left


class ProxySafetyTests(TestCase):
    """A proxied search must never run unless the proxy instance runs the current settings."""

    def setUp(self):
        self.search = SavedSearch.objects.create(name="P", query="q", engines=["bing"], use_proxy=True)

    def run_search(self):
        run = SearchRun.objects.create(search=self.search)
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value={"bing"}), \
             mock.patch("monitor.services.searxng.SearxngClient.search",
                        return_value={"results": [{"url": "https://x.example/1"}], "unresponsive_engines": []}) as s:
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        return run, s

    def test_off(self):
        run, searx = self.run_search()
        self.assertEqual(run.status, "failed")
        self.assertIn("turned off in Settings", run.error)
        searx.assert_not_called()

    def test_stale_instance_refused(self):
        configure()
        with mock.patch("monitor.services.proxy_config.requests.get",
                        return_value=config_response("searxngmonitor-proxy-oldsettings")):
            run, searx = self.run_search()
        self.assertEqual(run.status, "failed")
        self.assertIn("being applied", run.error)
        searx.assert_not_called()

    def test_instance_down_refused(self):
        configure()
        with mock.patch("monitor.services.proxy_config.requests.get", side_effect=requests.ConnectionError("x")):
            run, searx = self.run_search()
        self.assertEqual(run.status, "failed")
        searx.assert_not_called()

    def test_runs_when_applied(self):
        configure()
        with mock.patch("monitor.services.proxy_config.requests.get",
                        return_value=config_response(proxy_config.expected_instance_name())):
            run, searx = self.run_search()
        self.assertEqual(run.status, "success")
        searx.assert_called_once()


@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES)
class ProxySettingsPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(SEARXNG_PROXY_CONFIG_PATH=str(Path(self.tmp.name) / "settings.yml"))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def post(self, **proxy):
        data = {"retention_days": 30, "proxy_scheme": "http", **proxy}
        with mock.patch("monitor.services.proxy_config.requests.get", side_effect=requests.ConnectionError("x")):
            return self.client.post(reverse("settings"), data, follow=True)

    def test_save_keep_and_clear_password(self, _):
        resp = self.post(proxy_enabled="on", proxy_host="10.0.0.5", proxy_port=3128,
                         proxy_username="student", proxy_password="s3cret!")
        self.assertContains(resp, "Proxy settings saved")
        app = AppSettings.load()
        self.assertEqual((app.proxy_host, app.proxy_port, app.proxy_password), ("10.0.0.5", 3128, "s3cret!"))
        self.assertIn("student:s3cret%21@10.0.0.5:3128", (Path(self.tmp.name) / "settings.yml").read_text())
        self.assertNotContains(resp, "s3cret")  # never echoed back to the page
        self.assertContains(resp, "A password is saved")

        # Saving again with the password box empty keeps the saved password.
        self.post(proxy_enabled="on", proxy_host="10.0.0.5", proxy_port=3128, proxy_username="student")
        self.assertEqual(AppSettings.load().proxy_password, "s3cret!")
        # Ticking "remove" clears it.
        self.post(proxy_enabled="on", proxy_host="10.0.0.5", proxy_port=3128, proxy_username="student",
                  clear_proxy_password="on")
        self.assertEqual(AppSettings.load().proxy_password, "")

    def test_validation(self, _):
        resp = self.post(proxy_enabled="on", proxy_host="http://proxy.example.net", proxy_port=99999)
        self.assertContains(resp, "Enter just the hostname")
        self.assertContains(resp, "between 1 and 65535")
        resp = self.post(proxy_enabled="on", proxy_host="p.example", proxy_port=8080, proxy_password="x")
        self.assertContains(resp, "A password needs a username")
        self.assertFalse(AppSettings.load().proxy_enabled)

    def test_status_and_test_button(self, _):
        configure(proxy_scheme="http")
        with mock.patch("monitor.services.proxy_config.requests.get",
                        return_value=config_response(proxy_config.expected_instance_name())):
            self.assertContains(self.client.get(reverse("proxy-status")), "Active")

        def fake_get(url, proxies=None, timeout=None):
            if url.endswith("/config"):
                return config_response(proxy_config.expected_instance_name())
            resp = mock.Mock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"ip": "203.0.113.9" if proxies else "198.51.100.1"}
            return resp

        with mock.patch("monitor.services.proxy_config.requests.get", side_effect=fake_get):
            resp = self.client.post(reverse("proxy-test"))
        self.assertContains(resp, "198.51.100.1")
        self.assertContains(resp, "203.0.113.9")
        self.assertContains(resp, "hides your IP")

        with mock.patch("monitor.services.proxy_config.requests.get",
                        side_effect=lambda url, proxies=None, timeout=None: (_ for _ in ()).throw(
                            requests.exceptions.ProxyError("407 Proxy Authentication Required")) if proxies
                        else fake_get(url)):
            self.assertContains(self.client.post(reverse("proxy-test")), "rejected the username or password")


class ProxyErrorMessageTests(TestCase):
    def test_messages(self):
        app = configure()
        cases = {
            "Failed to establish a new connection: SOCKS5 authentication failed": "rejected the username or password",
            "407 Proxy Authentication Required": "rejected the username or password",
            "[Errno -2] Name or service not known": "could not be found",
            "[Errno 111] Connection refused": "Check the port",
        }
        for raw, expected in cases.items():
            self.assertIn(expected, proxy_config.explain_proxy_error(requests.ConnectionError(raw), app), raw)
        self.assertIn("did not respond", proxy_config.explain_proxy_error(requests.ConnectTimeout("x"), app))
