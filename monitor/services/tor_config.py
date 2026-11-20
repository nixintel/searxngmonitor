"""Tor for the .onion engines (ahmia, torch), turned on and off in Settings -> Tor.

Tor is bundled but OFF by default:

1. The web app renders torrc (from tor/torrc.template) into a volume shared with the `tor` container. With
   Tor off it contains `DisableNetwork 1`, so Tor runs but never connects to the Tor network.
2. The tor container restarts Tor when torrc changes, and flags when Tor has fully connected.
3. The `searxng-tor` instance only starts once that flag is set (SearXNG refuses to start with
   `using_tor_proxy` unless Tor works), and all its traffic goes through Tor. So if it answers, Tor works.

Searches that include a Tor engine run entirely on searxng-tor. This is separate from the generic proxy
(Settings -> Proxy).
"""

import logging
from pathlib import Path

import requests
from django.conf import settings

from monitor.services.proxy_config import write_if_changed

logger = logging.getLogger("monitor.tor")


class TorError(Exception):
    pass


def _app():
    from monitor.models import AppSettings

    return AppSettings.load()


def render(app=None):
    app = app or _app()
    template = Path(settings.TOR_TEMPLATE).read_text(encoding="utf-8")
    return template.replace("__DISABLE_NETWORK__", "0" if app.tor_enabled else "1")


def apply(app=None):
    """Write torrc if it changed. Returns True if a new file was written."""
    app = app or _app()
    if not write_if_changed(settings.TOR_CONFIG_PATH, render(app), "Tor config"):
        return False
    logger.info("Tor turned %s; the tor service restarts to apply it.", "on" if app.tor_enabled else "off")
    return True


def instance_engines():
    """Engine names loaded in the searxng-tor instance, or None if it isn't running."""
    try:
        resp = requests.get(f"{settings.SEARXNG_TOR_URL}/config", timeout=4)
        resp.raise_for_status()
        return {e["name"] for e in resp.json().get("engines", [])}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def status(app=None):
    """("off" | "ready" | "connecting", detail) for Tor and the searxng-tor instance."""
    app = app or _app()
    if not app.tor_enabled:
        return "off", "Tor is turned off. Nothing connects to the Tor network."
    engines = instance_engines()
    if engines is None:
        return "connecting", ("Connecting to the Tor network. This usually takes 10-60 seconds; "
                              "the Tor search engine starts once Tor is connected.")
    from monitor.models import TOR_ENGINES

    loaded = [e for e in TOR_ENGINES if e in engines]
    if not loaded:
        return "connecting", "The Tor search engine is running but hasn't loaded ahmia or torch yet."
    return "ready", f"Connected. Searches using {' and '.join(loaded)} run through Tor."


def ensure_ready():
    """Raise unless a Tor search can run now."""
    state, detail = status()
    if state == "off":
        raise TorError("This search uses a Tor engine (ahmia/torch), but Tor is turned off in Settings.")
    if state != "ready":
        raise TorError(f"Tor isn't ready: {detail} The search was not run.")


def test_tor(app=None):
    """Ask check.torproject.org, through the tor service, whether traffic really goes through Tor."""
    app = app or _app()
    if not app.tor_enabled:
        raise TorError("Tor is off. Click Turn on Tor first, wait for Connected, then test.")
    proxies = {"http": settings.TOR_SOCKS_URL, "https": settings.TOR_SOCKS_URL}
    # Try twice: right after Tor connects, or on a bad exit relay, the first circuit can drop the connection
    # (e.g. SSL "UNEXPECTED_EOF"); the retry gets a fresh circuit.
    for attempt in range(2):
        try:
            resp = requests.get(settings.TOR_CHECK_URL, proxies=proxies, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt == 0:
                continue
            msg = str(exc)
            if "connection refused" in msg.lower() or "timed out" in msg.lower() or isinstance(exc, requests.Timeout):
                raise TorError("Tor isn't connected yet. Wait a minute after turning it on, then test again.") from exc
            if isinstance(exc, requests.exceptions.SSLError) or "eof" in msg.lower():
                raise TorError("The connection through Tor was dropped. This happens now and then on Tor, "
                               "especially just after it connects. Wait a few seconds and test again.") from exc
            raise TorError(f"Could not reach the Tor check service through Tor: {msg[:200]}") from exc
    state, detail = status(app)
    return {"is_tor": bool(data.get("IsTor")), "ip": data.get("IP", "?"), "instance": state, "detail": detail}
