"""Checks whether a newer SearXNG image is available, so someone can update it.

SearXNG is never updated automatically: that would need the Docker socket (root-equivalent on the host).
Instead the app compares the version the SearXNG instances report at /config with the newest image on Docker
Hub, and shows a notice once the running version is SEARXNG_UPDATE_MIN_AGE_DAYS behind. Updating is one
documented command (README -> Keeping SearXNG up to date).

The only outbound request is an anonymous GET to Docker Hub's public tags API.
"""

import logging
import re
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("monitor.updates")

CACHE_KEY = "monitor:searxng:update"
CACHE_TTL = 3 * 24 * 3600
VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-[0-9a-f]+$")
UPDATE_COMMANDS = ["docker compose pull searxng searxng-proxy searxng-tor", "docker compose up -d"]


def normalize(version):
    """SearXNG reports '2026.11.19+368baa340'; Docker Hub tags it '2026.11.19-368baa340'."""
    return (version or "").strip().replace("+", "-")


def version_date(version):
    """The build date in a version string, or None if it isn't a dated version."""
    m = VERSION_RE.match(normalize(version))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _instance_urls():
    return [settings.SEARXNG_URL, settings.SEARXNG_PROXY_URL, settings.SEARXNG_TOR_URL]


def running_version():
    """The oldest dated version reported by the SearXNG instances that are up, or None if none answered.

    The oldest matters: an instance that wasn't recreated after a pull is still on the old version.
    The Tor and proxy instances are skipped when they are down (e.g. Tor turned off).
    """
    versions = []
    for url in _instance_urls():
        try:
            resp = requests.get(f"{url}/config", timeout=5)
            resp.raise_for_status()
            v = normalize(resp.json().get("version"))
        except (requests.RequestException, ValueError, AttributeError):
            continue
        if version_date(v):
            versions.append(v)
    return min(versions, key=lambda v: (version_date(v), v)) if versions else None


def latest_version():
    """The version tag of the newest searxng/searxng image on Docker Hub (raises on network errors)."""
    resp = requests.get(settings.SEARXNG_UPDATE_CHECK_URL, timeout=10,
                        headers={"User-Agent": "searxng-monitor/1.0"})
    resp.raise_for_status()
    tags = resp.json().get("results") or []
    dated = [t for t in tags if version_date(t.get("name"))]
    if not dated:
        raise ValueError("Docker Hub returned no dated SearXNG tags")
    latest_digest = next((t.get("digest") for t in tags if t.get("name") == "latest"), None)
    if latest_digest:
        for t in dated:
            if t.get("digest") == latest_digest:
                return t["name"]
    # No match (e.g. 'latest' is on a later page): use the newest dated tag.
    return max(dated, key=lambda t: (t.get("last_updated") or "", t["name"]))["name"]


def check(force=False):
    """Compare the running SearXNG with Docker Hub. Cached; never raises.

    Returns None if the check is turned off, else a dict with either `error`, or `running`, `latest`,
    `days_behind` and `update_available`. Always includes `checked_at`.
    """
    if not settings.SEARXNG_UPDATE_CHECK:
        return None
    if not force:
        cached = cache.get(CACHE_KEY)
        if cached:
            return cached
    result = {"checked_at": timezone.now()}
    try:
        running = running_version()
        if not running:
            raise ValueError("no SearXNG instance answered")
        latest = latest_version()
        behind = max(0, (version_date(latest) - version_date(running)).days)
        result.update(
            running=running, latest=latest, days_behind=behind,
            update_available=running != latest and behind >= settings.SEARXNG_UPDATE_MIN_AGE_DAYS,
        )
        if result["update_available"]:
            logger.warning("SearXNG update available: running %s, latest %s (%d day%s newer). Run: %s",
                           running, latest, behind, "" if behind == 1 else "s", " && ".join(UPDATE_COMMANDS))
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        result["error"] = str(exc)[:200]
        logger.info("Could not check for SearXNG updates: %s", result["error"])
    cache.set(CACHE_KEY, result, CACHE_TTL)
    return result


def cached():
    """The last check result without any network call (for page loads), or None."""
    if not settings.SEARXNG_UPDATE_CHECK:
        return None
    return cache.get(CACHE_KEY)
