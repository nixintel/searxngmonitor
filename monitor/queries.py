"""Query helpers shared by the UI and the API."""

import re

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from monitor.models import LogEntry, SavedSearch, SearchResult, SearchRun, Tag

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
RESULT_VIEWS = ["all", "new", "tagged", "deleted"]
TRUTHY = ("1", "true", "True", "yes", "on")
TERM_RE = re.compile(r'"([^"]*)"|(\S+)')


def _count_subquery(qs):
    sub = qs.order_by().values("search").annotate(c=Count("id", distinct=True)).values("c")
    return Coalesce(Subquery(sub, output_field=IntegerField()), Value(0))


def searches_with_stats():
    latest = SearchRun.objects.filter(search=OuterRef("pk")).order_by("-created_at")
    visible = SearchResult.objects.filter(search=OuterRef("pk"), deleted_at__isnull=True)
    return SavedSearch.objects.annotate(
        new_count=_count_subquery(visible.filter(is_new=True)),
        total_count=_count_subquery(visible),
        tagged_count=_count_subquery(visible.filter(tags__isnull=False)),
        last_status=Subquery(latest.values("status")[:1]),
        last_error=Subquery(latest.values("error")[:1]),
    )


def result_view(params):
    """Which result tab is selected. Accepts view=<name> plus legacy/API flags new/tagged/deleted."""
    view = params.get("view")
    if view in RESULT_VIEWS:
        return view
    for flag in ("deleted", "tagged", "new"):
        if params.get(flag) in TRUTHY:
            return flag
    return "all"


def filter_results(params, qs):
    view = result_view(params)
    if view == "deleted":
        qs = qs.deleted()
    else:
        qs = qs.visible()
        if view == "new":
            qs = qs.filter(is_new=True)
        elif view == "tagged":
            qs = qs.tagged()
    tag = params.get("tag")
    if tag:
        qs = qs.filter(tags__id=tag) if str(tag).isdigit() else qs.filter(tags__name=Tag.normalize(tag))
        qs = qs.distinct()
    for term in search_terms(params.get("q")):
        qs = qs.filter(Q(title__icontains=term) | Q(content__icontains=term) | Q(url__icontains=term))
    return qs.prefetch_related("tags")


def search_terms(q, limit=10):
    """Split a keyword query into terms: words, or "quoted phrases". Every term must match.

    e.g. 'acme "data breach"' -> ['acme', 'data breach']. Only double quotes group words, so apostrophes and
    backslashes are kept as typed (e.g. o'brien); a stray unclosed quote is ignored.
    """
    terms = []
    for phrase, word in TERM_RE.findall(q or ""):
        term = (phrase or word).replace('"', "").strip()
        if term:
            terms.append(term)
    return terms[:limit]


def filter_searches(q, qs):
    """Saved searches whose name, query or feed URL contains every term of `q`."""
    for term in search_terms(q):
        qs = qs.filter(Q(name__icontains=term) | Q(query__icontains=term) | Q(feed_url__icontains=term))
    return qs


def result_view_counts(search):
    base = SearchResult.objects.filter(search=search)
    return {
        "all": base.visible().count(),
        "new": base.visible().filter(is_new=True).count(),
        "tagged": base.visible().tagged().count(),
        "deleted": base.deleted().count(),
    }


def tags_for_search(search):
    return (
        Tag.objects.filter(results__search=search, results__deleted_at__isnull=True)
        .annotate(n=Count("results", distinct=True))
        .order_by("name")
    )


def filter_runs(params, qs=None):
    qs = (qs if qs is not None else SearchRun.objects.all()).select_related("search")
    if params.get("search"):
        qs = qs.filter(search_id=params["search"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("trigger"):
        qs = qs.filter(trigger=params["trigger"])
    return qs


def filter_logs(params, qs=None):
    qs = (qs if qs is not None else LogEntry.objects.all()).select_related("search")
    level = (params.get("level") or "").upper()
    if level in LOG_LEVELS:
        qs = qs.filter(level__in=LOG_LEVELS[LOG_LEVELS.index(level):])
    if params.get("search"):
        qs = qs.filter(search_id=params["search"])
    if params.get("run"):
        qs = qs.filter(run_id=params["run"])
    if params.get("q"):
        qs = qs.filter(message__icontains=params["q"])
    return qs


def reschedule_if_needed(search, changed_fields):
    """After an edit: re-anchor next_run_at when the interval changed or the search was re-enabled."""
    from django.utils import timezone

    if not search.enabled:
        return
    if {"interval_value", "interval_unit", "enabled"} & set(changed_fields) or search.next_run_at is None:
        base = search.last_run_at or timezone.now()
        search.next_run_at = max(base + search.interval, timezone.now()) if search.last_run_at else timezone.now()
        search.save(update_fields=["next_run_at"])
