"""SearXNG HTTP client, URL normalisation and result storage."""

import hashlib
from datetime import timezone as dt_timezone
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("monitor.searxng")

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid",
    "igshid", "ref_src", "_hsenc", "_hsmi", "spm",
}
DEFAULT_PORTS = {"http": "80", "https": "443"}


class SearxngError(Exception):
    pass


# ------------------------------------------------------------------ URL helpers
def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication."""
    url = (url or "").strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    if port and str(port) != DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/") or ""
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def is_usable_url(url) -> bool:
    """http(s) URL with a hostname. Some engines emit relative redirect links
    (e.g. 'http:///go/v1/<token>') with a per-request token - unusable and never deduplicable."""
    try:
        parts = urlsplit(url or "")
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ client
class SearxngClient:
    def __init__(self, base_url: str, timeout: float | None = None, retries: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or settings.SEARXNG_TIMEOUT
        retry = Retry(
            total=settings.SEARXNG_MAX_RETRIES if retries is None else retries,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session = requests.Session()
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers["User-Agent"] = "searxng-monitor/1.0"

    @classmethod
    def direct(cls, **kw):
        return cls(settings.SEARXNG_URL, **kw)

    @classmethod
    def proxied(cls, **kw):
        if not settings.SEARXNG_PROXY_ENABLED:
            raise SearxngError(
                "This search is set to use the proxy, but the proxy instance is disabled "
                "(set SEARXNG_PROXY_ENABLED=true and add 'proxy' to COMPOSE_PROFILES)."
            )
        return cls(settings.SEARXNG_PROXY_URL, **kw)

    @classmethod
    def for_search(cls, search):
        return cls.proxied() if search.use_proxy else cls.direct()

    def _get(self, path, params=None):
        try:
            resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SearxngError(f"Request to {self.base_url}{path} failed: {exc}") from exc
        if resp.status_code != 200:
            hint = " (is the json format enabled in settings.yml?)" if resp.status_code == 403 else ""
            raise SearxngError(f"SearXNG returned HTTP {resp.status_code}{hint}")
        return resp

    def search(self, query, *, engines=None, categories=None, language="all", time_range="", pageno=1):
        params = {"q": query, "format": "json", "pageno": pageno, "language": language or "all"}
        if engines:
            params["engines"] = ",".join(engines)
        elif categories:
            params["categories"] = categories
        if time_range:
            params["time_range"] = time_range
        resp = self._get("/search", params)
        try:
            return resp.json()
        except ValueError as exc:
            raise SearxngError("SearXNG returned a non-JSON response") from exc

    def config(self):
        return self._get("/config").json()

    def healthy(self):
        self._get("/healthz")
        return True


def fetch_results(search):
    """Run a saved search across its configured pages.

    Returns (results, unresponsive_engines).
    """
    client = SearxngClient.for_search(search)
    engines = validate_engines(search)
    results, unresponsive, dropped = [], [], {}
    for page in range(1, max(1, search.max_pages) + 1):
        data = client.search(
            search.query,
            engines=engines,
            categories=search.categories,
            language=search.language,
            time_range=search.time_range,
            pageno=page,
        )
        page_results = data.get("results") or []
        results.extend(r for r in page_results if is_usable_url(r.get("url")))
        for r in page_results:
            if not is_usable_url(r.get("url")):
                bad = r.get("engine") or "?"
                dropped[bad] = dropped.get(bad, 0) + 1
        for item in data.get("unresponsive_engines") or []:
            entry = list(item) if isinstance(item, (list, tuple)) else [str(item)]
            if entry not in unresponsive:
                unresponsive.append(entry)
        if not page_results:
            break
    if dropped:
        logger.warning(
            "'%s': dropped %d result(s) with unusable URLs (engine: %s)", search.name, sum(dropped.values()),
            ", ".join(f"{k}={v}" for k, v in dropped.items()), extra={"search_id": search.id},
        )
    return results, unresponsive


def validate_engines(search):
    """Drop engine names SearXNG doesn't have loaded.

    SearXNG silently ignores unknown engine names and falls back to its default
    engines, so an unknown name would quietly search somewhere else.
    """
    if not search.engines:
        return search.engines
    loaded = loaded_engine_names()
    if loaded is None:  # couldn't fetch /config - send as-is
        return search.engines
    valid = [e for e in search.engines if e in loaded]
    unknown = [e for e in search.engines if e not in loaded]
    if unknown:
        logger.warning(
            "'%s': ignoring engine(s) not loaded in SearXNG: %s", search.name, ", ".join(unknown),
            extra={"search_id": search.id},
        )
    if not valid:
        raise SearxngError(
            f"None of the selected engines are available in SearXNG ({', '.join(unknown)}). Edit the search."
        )
    return valid


def _parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = parse_datetime(value)
    except ValueError:
        return None
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def store_results(search, run, results, *, mark_new=True):
    """Insert results that this search hasn't seen yet. Returns the number inserted."""
    from monitor.models import SearchResult

    by_hash = {}
    for item in results:
        url = item.get("url")
        if not url:
            continue
        h = url_hash(url)
        engines = item.get("engines") or ([item["engine"]] if item.get("engine") else [])
        if h in by_hash:
            merged = by_hash[h].engines
            merged.extend(e for e in engines if e not in merged)
            continue
        by_hash[h] = SearchResult(
            search=search,
            run=run,
            url=url,
            url_hash=h,
            title=(item.get("title") or "")[:2000],
            content=item.get("content") or "",
            engines=list(engines),
            published_date=_parse_date(item.get("publishedDate")),
            is_new=mark_new,
            raw=item,
        )
    if not by_hash:
        return 0

    existing = set(
        SearchResult.objects.filter(search=search, url_hash__in=list(by_hash)).values_list("url_hash", flat=True)
    )
    fresh = [obj for h, obj in by_hash.items() if h not in existing]
    # ignore_conflicts guards against any concurrent insert slipping past the lock.
    SearchResult.objects.bulk_create(fresh, ignore_conflicts=True, batch_size=500)
    return len(fresh)


def loaded_engine_names():
    """Set of engine names loaded in SearXNG, or None if it can't be determined."""
    engines = _fetch_engines()
    return {e["name"] for e in engines} if engines is not None else None


def available_engines():
    """Engines loaded in the direct SearXNG instance (cached), with a fallback list.

    Disabled engines are included: naming an engine explicitly in a query works
    even when it is disabled by default in SearXNG.
    """
    engines = _fetch_engines()
    if engines is not None:
        return engines
    return [{"name": n, "categories": [], "enabled": True} for n in settings.DEFAULT_ENGINES]


def _fetch_engines():
    cached = cache.get("searxng:engines")
    if cached:
        return cached
    try:
        cfg = SearxngClient.direct(timeout=5, retries=0).config()
        engines = sorted(
            (
                {"name": e["name"], "categories": e.get("categories", []), "enabled": e.get("enabled", True)}
                for e in cfg.get("engines", [])
            ),
            key=lambda e: e["name"],
        )
        cache.set("searxng:engines", engines, 3600)
        return engines
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load engine list from SearXNG: %s", exc)
        return None
