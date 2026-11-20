"""Proxy settings from the UI, applied to the dedicated `searxng-proxy` SearXNG instance.

SearXNG only reads its outgoing proxy from settings.yml at start-up, so:

1. The web app renders settings.yml (from searxng/proxy/settings.yml.template) into a volume shared with the
   searxng-proxy container.
2. That container watches the file and restarts itself when it changes (see docker-compose.yml).
3. The rendered file carries a fingerprint of the proxy settings in `general.instance_name`, which SearXNG
   reports at /config. Before every proxied search the app checks the running instance reports the *current*
   fingerprint, so a search can never run with stale settings, or without the proxy.

When the proxy is turned off, the instance is pointed at an address that refuses connections, so even a
misrouted request can't reach the internet directly.
"""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger("monitor.proxy")

BLACKHOLE_PROXY = "http://127.0.0.1:9"  # nothing listens here: proxied traffic fails instead of going direct
INSTANCE_PREFIX = "searxngmonitor-proxy-"


class ProxyError(Exception):
    pass


def _app():
    from monitor.models import AppSettings

    return AppSettings.load()


def proxy_url(app=None, redact=False):
    """Proxy URL built from Settings, or "" when the proxy is off or incomplete."""
    app = app or _app()
    if not (app.proxy_enabled and app.proxy_host and app.proxy_port):
        return ""
    auth = ""
    if app.proxy_username:
        user = quote(app.proxy_username, safe="")
        pwd = "****" if redact and app.proxy_password else quote(app.proxy_password or "", safe="")
        auth = f"{user}:{pwd}@" if app.proxy_password else f"{user}@"
    return f"{app.proxy_scheme}://{auth}{app.proxy_host.strip()}:{app.proxy_port}"


def fingerprint(app=None):
    effective = proxy_url(app) or "disabled"
    return hashlib.sha256(effective.encode()).hexdigest()[:12]


def expected_instance_name(app=None):
    return INSTANCE_PREFIX + fingerprint(app)


def render(app=None):
    app = app or _app()
    template = Path(settings.SEARXNG_PROXY_TEMPLATE).read_text(encoding="utf-8")
    url = proxy_url(app) or BLACKHOLE_PROXY
    # json.dumps gives a double-quoted string, which is valid YAML and safely escapes the URL.
    return (template
            .replace("__INSTANCE_NAME__", json.dumps(expected_instance_name(app)))
            .replace("__PROXY_URL__", json.dumps(url)))


def write_if_changed(target, content, what):
    """Atomically write `content` to `target` (a file on a volume shared with another container).

    Returns True if a new file was written, False if it was unchanged or the directory is missing.
    """
    target = Path(target)
    if not target.parent.is_dir():
        logger.warning("%s directory %s not found; settings not applied.", what, target.parent)
        return False
    try:
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    # Atomic replace: needs write access to the directory only, and the reader never sees a half-written file.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.stem}-", suffix=target.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def apply(app=None):
    """Write the proxy instance's settings.yml if it changed. Returns True if a new file was written."""
    if not write_if_changed(settings.SEARXNG_PROXY_CONFIG_PATH, render(app), "Proxy config"):
        return False
    logger.info("Proxy settings written for the proxy SearXNG instance (%s); it will restart to apply them.",
                proxy_url(app, redact=True) or "proxy off")
    return True


def instance_status(app=None):
    """("off" | "applied" | "applying" | "down", detail) for the searxng-proxy instance."""
    app = app or _app()
    if not app.proxy_enabled:
        return "off", "Proxy is turned off in Settings."
    if not proxy_url(app):
        return "down", "Proxy host and port are not set."
    try:
        resp = requests.get(f"{settings.SEARXNG_PROXY_URL}/config", timeout=4)
        resp.raise_for_status()
        running = resp.json().get("instance_name", "")
    except (requests.RequestException, ValueError):
        return "applying", "The proxy SearXNG instance is not responding (it restarts for about 20 seconds after a change)."
    if running == expected_instance_name(app):
        return "applied", f"Searches marked 'Run through proxy' use {proxy_url(app, redact=True)}."
    return "applying", "New proxy settings are being applied (the proxy SearXNG instance is restarting)."


def ensure_ready():
    """Raise unless proxied searches would really go through the current proxy settings."""
    app = _app()
    state, detail = instance_status(app)
    if state == "off":
        raise ProxyError("This search is set to run through the proxy, but the proxy is turned off in Settings.")
    if state != "applied":
        raise ProxyError(f"The proxy isn't ready: {detail} The search was not run, to avoid going out unproxied.")


def explain_proxy_error(exc, app):
    """Turn requests/PySocks errors into a plain-English reason."""
    msg = str(exc)
    low = msg.lower()
    where = f"{app.proxy_host}:{app.proxy_port}"
    if "407" in msg or "proxy authentication required" in low or "authentication failed" in low:
        return "The proxy rejected the username or password."
    if "name or service not known" in low or "getaddrinfo" in low or "nodename nor servname" in low:
        return f"The proxy host '{app.proxy_host}' could not be found. Check the hostname."
    if "connection refused" in low:
        return f"Nothing accepted the connection at {where}. Check the port and that the proxy is running."
    if isinstance(exc, requests.Timeout) or "timed out" in low:
        return f"The proxy at {where} did not respond in time."
    if "socks" in low and app.proxy_scheme.startswith("http"):
        return f"The connection failed. Is this a SOCKS proxy? Try changing Type to SOCKS5h. ({msg[:120]})"
    return f"Could not connect through the proxy: {msg[:200]}"


def test_proxy(app=None):
    """Compare the public IP seen directly and through the proxy (uses settings.IP_ECHO_URL)."""
    app = app or _app()
    url = proxy_url(app)
    if not url:
        raise ProxyError("Turn the proxy on and enter a host and port first.")

    def ip(proxies=None):
        resp = requests.get(settings.IP_ECHO_URL, proxies=proxies, timeout=15)
        resp.raise_for_status()
        return resp.json().get("ip", "?")

    try:
        direct = ip()
    except requests.RequestException:
        direct = "unknown"
    try:
        via = ip({"http": url, "https": url})
    except requests.RequestException as exc:
        raise ProxyError(explain_proxy_error(exc, app)) from exc
    state, detail = instance_status(app)
    return {"direct_ip": direct, "proxy_ip": via, "different": via != direct, "instance": state, "detail": detail}
