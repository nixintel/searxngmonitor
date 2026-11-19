"""Google Alerts RSS (Atom) feeds as a search source.

Entries are returned in the same shape as SearXNG results, so storage, de-duplication,
"new" flags, tags and notifications all work unchanged.
"""

import html
import logging
import re
from datetime import datetime, timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit

import feedparser
import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from monitor.services.searxng import SearxngError

logger = logging.getLogger("monitor.feeds")

MAX_FEED_BYTES = 5 * 1024 * 1024
ENGINE_LABEL = "google alerts"
TITLE_PREFIX = "Google Alert - "
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_text(value):
    """Strip HTML tags (Google wraps matched terms in <b>) and unescape entities."""
    text = _TAG_RE.sub("", value or "")
    return _SPACE_RE.sub(" ", html.unescape(text)).strip()


def unwrap_google_url(url):
    """Google Alerts links are redirects (google.com/url?...&url=<real>&usg=<changes>).

    Return the real article URL so links go straight to it and de-duplication is stable.
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return url
    if parts.hostname and parts.hostname.endswith("google.com") and parts.path == "/url":
        params = parse_qs(parts.query)
        for key in ("url", "q"):
            if params.get(key):
                return params[key][0]
    return url


def _session():
    retry = Retry(
        total=settings.SEARXNG_MAX_RETRIES, backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; searxng-monitor/1.0; +rss)"
    return session


def download_feed(url):
    try:
        resp = _session().get(url, timeout=settings.SEARXNG_TIMEOUT, stream=True, allow_redirects=False)
    except requests.RequestException as exc:
        raise SearxngError(f"Could not reach Google Alerts: {exc}") from exc
    if resp.status_code == 404:
        raise SearxngError("Google Alerts feed not found (404). Was the alert deleted, or the URL mistyped?")
    if resp.status_code != 200:
        raise SearxngError(f"Google Alerts returned HTTP {resp.status_code}.")
    body = b""
    for chunk in resp.iter_content(64 * 1024):
        body += chunk
        if len(body) > MAX_FEED_BYTES:
            raise SearxngError("Feed is larger than 5 MB; refusing to process it.")
    return body


def _published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=dt_timezone.utc).isoformat()


def parse_feed(body):
    """Parse feed bytes into (feed_title, results)."""
    feed = feedparser.parse(body)
    is_feed = feed.get("version") or feed.feed.get("title") or feed.entries
    if not is_feed:
        raise SearxngError("The URL did not return an RSS/Atom feed. Check it's the alert's RSS feed URL.")
    results = []
    for entry in feed.entries:
        link = entry.get("link") or ""
        url = unwrap_google_url(link)
        if not url:
            continue
        results.append({
            "url": url,
            "title": clean_text(entry.get("title")),
            "content": clean_text(entry.get("summary") or entry.get("description")),
            "engine": ENGINE_LABEL,
            "engines": [ENGINE_LABEL],
            "publishedDate": _published(entry),
        })
    return clean_text(feed.feed.get("title")), results


def alert_query_from_title(title):
    title = title or ""
    return title[len(TITLE_PREFIX):] if title.startswith(TITLE_PREFIX) else title


def fetch_google_alert(search):
    """Fetch a Google Alert feed. Returns (results, unresponsive) like searxng.fetch_results()."""
    feed_title, results = parse_feed(download_feed(search.feed_url))
    query = alert_query_from_title(feed_title)[:500]
    if query and query != search.query:
        search.query = query
        type(search).objects.filter(pk=search.pk).update(query=query)
    logger.info(
        "'%s': Google Alert feed has %d entr%s", search.name, len(results), "y" if len(results) == 1 else "ies",
        extra={"search_id": search.id},
    )
    return results, []
