"""Tor for the ahmia/torch engines: off by default, turned on in Settings -> Tor or from the search form."""

import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor import tasks
from monitor.forms import engine_choices
from monitor.models import AppSettings, SavedSearch, SearchRun
from monitor.services import searxng, tor_config
from monitor.services.searxng import SearxngClient, available_engines

FAKE_ENGINES = [{"name": n, "categories": ["general"], "enabled": True} for n in ["bing", "brave"]]
FAKE_ENGINES_WITH_TOR = FAKE_ENGINES + [{"name": n, "categories": ["onions"], "enabled": True, "tor": True}
                                        for n in ["ahmia", "torch"]]
TOR_URL = "http://searxng-tor:8080"


def tor_on(on=True):
    app = AppSettings.load()
    app.tor_enabled = on
    app.save()
    return app


def tor_config_response(engines=("ahmia", "torch", "bing")):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"instance_name": "searxngmonitor-tor", "engines": [{"name": n} for n in engines]}
    return resp


class TorrcTests(TestCase):
    def test_off_by_default(self):
        self.assertFalse(AppSettings.load().tor_enabled)
        rendered = tor_config.render()
        self.assertIn("DisableNetwork 1", rendered)
        self.assertNotIn("__DISABLE_NETWORK__", rendered)
        self.assertIn("SocksPort 0.0.0.0:9050", rendered)

    def test_on(self):
        self.assertIn("DisableNetwork 0", tor_config.render(tor_on()))

    def test_apply_writes_only_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "torrc"
            with override_settings(TOR_CONFIG_PATH=str(target)):
                self.assertTrue(tor_config.apply(tor_on(False)))
                self.assertIn("DisableNetwork 1", target.read_text())
                self.assertFalse(tor_config.apply())  # unchanged -> no rewrite, no Tor restart
                self.assertTrue(tor_config.apply(tor_on()))
                self.assertIn("DisableNetwork 0", target.read_text())
                self.assertEqual([p.name for p in Path(d).iterdir()], ["torrc"])  # no temp files left


class TorStatusTests(TestCase):
    def test_off(self):
        self.assertEqual(tor_config.status()[0], "off")

    def test_connecting_while_instance_down(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=requests.ConnectionError("x")):
            self.assertEqual(tor_config.status()[0], "connecting")

    def test_ready(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", return_value=tor_config_response()):
            state, detail = tor_config.status()
        self.assertEqual(state, "ready")
        self.assertIn("ahmia and torch", detail)


@override_settings(SEARXNG_TOR_URL=TOR_URL)
class TorRoutingTests(TestCase):
    def setUp(self):
        cache.clear()

    def make(self, engines, use_proxy=False):
        return SavedSearch.objects.create(name="T", query="q", engines=engines, use_proxy=use_proxy)

    def test_model_helpers(self):
        both = self.make(["ahmia", "torch", "bing"], use_proxy=True)
        self.assertEqual(both.tor_engines, ["ahmia", "torch"])
        self.assertTrue(both.uses_tor)
        self.assertFalse(both.runs_via_proxy)  # Tor wins over the generic proxy
        self.assertFalse(self.make(["bing"]).uses_tor)

    def test_for_search_routes_ahmia_torch_or_both_to_tor(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", return_value=tor_config_response()):
            for engines in (["ahmia"], ["torch"], ["ahmia", "torch"], ["ahmia", "bing"]):
                self.assertEqual(SearxngClient.for_search(self.make(engines)).base_url, TOR_URL, engines)

    def test_non_tor_search_unaffected(self):
        client = SearxngClient.for_search(self.make(["bing"]))
        self.assertNotEqual(client.base_url, TOR_URL)
        self.assertEqual(client.timeout, 20)

    @override_settings(SEARXNG_TOR_TIMEOUT=60)
    def test_tor_client_waits_longer_than_engine_timeouts(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", return_value=tor_config_response()):
            client = SearxngClient.for_search(self.make(["ahmia"]))
        self.assertEqual(client.timeout, 60)
        self.assertGreater(client.timeout, 30)  # ahmia/torch timeout in searxng/tor/settings.yml

    def test_validate_engines_uses_tor_instance_list(self):
        search = self.make(["ahmia", "torch", "bing"])
        with mock.patch.object(SearxngClient, "config",
                               return_value={"engines": [{"name": n} for n in ["ahmia", "torch", "bing"]]}) as cfg:
            self.assertEqual(searxng.validate_engines(search), ["ahmia", "torch", "bing"])
        cfg.assert_called_once()
        self.assertIsNotNone(cache.get(f"searxng:engines:{TOR_URL}"))

    def test_available_engines_always_lists_tor_engines(self):
        with mock.patch.object(SearxngClient, "config", side_effect=searxng.SearxngError("down")):
            names = {e["name"]: e for e in available_engines()}
        self.assertTrue(names["ahmia"]["tor"])
        self.assertTrue(names["torch"]["tor"])
        self.assertIn("brave", names)  # fallback default engines still offered

    @mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES_WITH_TOR)
    def test_choices_label_tor_engines(self, _):
        self.assertIn(("ahmia", "ahmia (Tor)"), engine_choices())
        self.assertIn(("torch", "torch (Tor)"), engine_choices())


class TorRunTests(TestCase):
    """A Tor search must never run unless Tor is on and the Tor instance is up."""

    def setUp(self):
        cache.clear()
        self.search = SavedSearch.objects.create(name="Onion", query="q", engines=["ahmia", "torch"],
                                                 use_proxy=True)

    def run_search(self):
        run = SearchRun.objects.create(search=self.search)
        with mock.patch("monitor.services.searxng.loaded_engine_names", return_value={"ahmia", "torch"}), \
             mock.patch("monitor.services.searxng.SearxngClient.search",
                        return_value={"results": [{"url": "http://abc.onion/1", "engines": ["ahmia", "torch"]}],
                                      "unresponsive_engines": []}) as s:
            tasks.run_search.run(run.id)
        run.refresh_from_db()
        return run, s

    def test_off_refused(self):
        run, searx = self.run_search()
        self.assertEqual(run.status, "failed")
        self.assertIn("Tor is turned off in Settings", run.error)
        searx.assert_not_called()

    def test_connecting_refused(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=requests.ConnectionError("x")):
            run, searx = self.run_search()
        self.assertEqual(run.status, "failed")
        self.assertIn("isn't ready", run.error)
        searx.assert_not_called()

    def test_runs_when_ready(self):
        tor_on()
        with mock.patch("monitor.services.tor_config.requests.get", return_value=tor_config_response()):
            run, searx = self.run_search()
        self.assertEqual(run.status, "success")
        self.assertFalse(run.proxy_used)  # went through Tor, not the generic proxy
        searx.assert_called_once()
        self.assertEqual(self.search.results.get().engines, ["ahmia", "torch"])


@mock.patch("monitor.forms.available_engines", return_value=FAKE_ENGINES_WITH_TOR)
class TorFormTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(TOR_CONFIG_PATH=str(Path(self.tmp.name) / "torrc"))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def create(self, engines, **extra):
        data = {"name": "Onion", "source": "searxng", "query": "acme", "engines": engines, "categories": "general",
                "language": "all", "max_pages": 1, "interval_value": 60, "interval_unit": "minutes",
                "enabled": "on", **extra}
        return self.client.post(reverse("search-create"), data)

    def test_rejected_while_tor_off(self, _):
        resp = self.create(["ahmia", "torch"])
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ahmia and torch search .onion sites and need Tor")
        self.assertContains(resp, "Turn on Tor")  # the prompt is on the page
        self.assertFalse(SavedSearch.objects.exists())

    def test_names_only_selected_engine(self, _):
        resp = self.create(["torch"])
        self.assertContains(resp, "torch search .onion sites")
        self.assertNotContains(resp, "ahmia and torch search")

    def test_accepted_while_tor_on_and_proxy_cleared(self, _):
        tor_on()
        resp = self.create(["ahmia", "torch", "bing"], use_proxy="on")
        self.assertEqual(resp.status_code, 302)
        search = SavedSearch.objects.get()
        self.assertEqual(search.engines, ["ahmia", "torch", "bing"])
        self.assertFalse(search.use_proxy)

    def test_non_tor_search_unaffected(self, _):
        self.assertEqual(self.create(["bing"]).status_code, 302)

    def test_prompt_turns_tor_on(self, _):
        self.assertEqual(self.client.get(reverse("tor-enable")).status_code, 405)  # POST only
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=requests.ConnectionError("x")):
            resp = self.client.post(reverse("tor-enable"))
        self.assertContains(resp, "Tor is on")
        self.assertContains(resp, "Connecting")
        self.assertTrue(AppSettings.load().tor_enabled)
        self.assertIn("DisableNetwork 0", (Path(self.tmp.name) / "torrc").read_text())

    def test_prompt_requires_login(self, _):
        self.client.logout()
        resp = self.client.post(reverse("tor-enable"))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(AppSettings.load().tor_enabled)

    def test_settings_page_button(self, _):
        torrc = Path(self.tmp.name) / "torrc"
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=requests.ConnectionError("x")),              mock.patch("monitor.services.proxy_config.requests.get", side_effect=requests.ConnectionError("x")):
            page = self.client.get(reverse("settings"))
            self.assertContains(page, "off by default")
            self.assertContains(page, "Turn on Tor")
            # The button works immediately, with no Save.
            resp = self.client.post(reverse("tor-set"), {"enabled": "1"})
            self.assertContains(resp, "Turn off Tor")
            self.assertContains(resp, "Connecting")
            self.assertTrue(AppSettings.load().tor_enabled)
            self.assertIn("DisableNetwork 0", torrc.read_text())
            # The page's main Save leaves Tor alone.
            self.client.post(reverse("settings"), {"retention_days": 30, "proxy_scheme": "http"})
            self.assertTrue(AppSettings.load().tor_enabled)
            resp = self.client.post(reverse("tor-set"), {"enabled": "0"})
            self.assertContains(resp, "Turn on Tor")
        self.assertFalse(AppSettings.load().tor_enabled)
        self.assertIn("DisableNetwork 1", torrc.read_text())

    def test_tor_test_button(self, _):
        tor_on()

        def fake_get(url, proxies=None, timeout=None):
            if url.endswith("/config"):
                return tor_config_response()
            self.assertEqual(proxies["https"], "socks5h://tor:9050")
            resp = mock.Mock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"IsTor": True, "IP": "185.220.101.1"}
            return resp

        with mock.patch("monitor.services.tor_config.requests.get", side_effect=fake_get):
            resp = self.client.post(reverse("tor-test"))
        self.assertContains(resp, "185.220.101.1")
        self.assertContains(resp, "Tor works")

    def test_tor_test_retries_dropped_connection(self, _):
        tor_on()
        ok = mock.Mock()
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"IsTor": True, "IP": "185.220.101.1"}
        eof = requests.exceptions.SSLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=[eof, ok, tor_config_response()]):
            self.assertTrue(tor_config.test_tor()["is_tor"])
        with mock.patch("monitor.services.tor_config.requests.get", side_effect=[eof, eof]):
            with self.assertRaisesRegex(tor_config.TorError, "dropped"):
                tor_config.test_tor()
