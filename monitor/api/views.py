from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from monitor.api.serializers import (
    LogEntrySerializer, SavedSearchSerializer, SearchResultSerializer, SearchRunSerializer, TagSerializer,
)
from monitor.models import LogEntry, SavedSearch, SearchResult, SearchRun, Tag
from monitor.queries import filter_logs, filter_results, filter_runs, reschedule_if_needed, searches_with_stats
from monitor.services import health
from monitor.services.searxng import available_engines
from monitor.tasks import trigger_manual_run

RESULT_FILTERS = [
    OpenApiParameter("view", str, enum=["all", "new", "tagged", "deleted"],
                     description="Which results: all (default, excludes deleted), new, tagged, deleted"),
    OpenApiParameter("tag", str, description="Tag id or name"),
    OpenApiParameter("q", str, description='Keywords matched against title/content/url. Every word must match; '
                                            'use quotes for an exact phrase, e.g. acme "data breach"'),
]


class SavedSearchViewSet(viewsets.ModelViewSet):
    serializer_class = SavedSearchSerializer

    def get_queryset(self):
        return searches_with_stats()

    def perform_update(self, serializer):
        search = serializer.save()
        reschedule_if_needed(search, serializer.validated_data.keys())

    @extend_schema(request=None, responses={202: SearchRunSerializer})
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Queue a manual run now (schedule is unaffected)."""
        run = trigger_manual_run(self.get_object())
        return Response(SearchRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(parameters=RESULT_FILTERS, responses=SearchResultSerializer(many=True))
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        qs = filter_results(request.query_params, self.get_object().results.all())
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(SearchResultSerializer(page, many=True).data)

    @extend_schema(responses=SearchRunSerializer(many=True))
    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        page = self.paginate_queryset(self.get_object().runs.all())
        return self.get_paginated_response(SearchRunSerializer(page, many=True).data)

    @extend_schema(request=None, responses={200: {"type": "object", "properties": {"marked": {"type": "integer"}}}})
    @action(detail=True, methods=["post"], url_path="mark-seen")
    def mark_seen(self, request, pk=None):
        marked = self.get_object().results.visible().filter(is_new=True).update(is_new=False)
        return Response({"marked": marked})


class SearchResultViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Results across all searches.

    PATCH {"is_new": false} marks one seen; PATCH {"tags": ["a", "b"]} sets its tags.
    DELETE soft-deletes (hidden, and never re-added by later runs); POST .../restore/ undoes it.
    """

    serializer_class = SearchResultSerializer

    @extend_schema(parameters=RESULT_FILTERS + [OpenApiParameter("search", int)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        qs = SearchResult.objects.all()
        if self.request.query_params.get("search"):
            qs = qs.filter(search_id=self.request.query_params["search"])
        if self.action == "list":
            return filter_results(self.request.query_params, qs)
        return qs.prefetch_related("tags")

    def perform_destroy(self, instance):
        instance.soft_delete()

    @extend_schema(request=None, responses=SearchResultSerializer)
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        result = self.get_object()
        result.restore()
        return Response(SearchResultSerializer(result).data)


class TagViewSet(viewsets.ReadOnlyModelViewSet):
    """All tags with how many (non-deleted) results carry each."""

    serializer_class = TagSerializer

    def get_queryset(self):
        return Tag.objects.annotate(
            result_count=Count("results", filter=Q(results__deleted_at__isnull=True), distinct=True)
        ).order_by("name")


class SearchRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SearchRunSerializer

    @extend_schema(parameters=[
        OpenApiParameter("search", int), OpenApiParameter("status", str), OpenApiParameter("trigger", str),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        return filter_runs(self.request.query_params)


class LogEntryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LogEntrySerializer

    @extend_schema(parameters=[
        OpenApiParameter("level", str, description="Minimum level"), OpenApiParameter("search", int),
        OpenApiParameter("run", int), OpenApiParameter("q", str),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        return filter_logs(self.request.query_params, LogEntry.objects.all())


@extend_schema(responses={200: dict})
@api_view(["GET"])
def health_view(request):
    checks = health.run_all_checks()
    ok = health.overall_ok(checks)
    return Response({"ok": ok, "checks": checks}, status=200 if ok else 503)


@extend_schema(responses={200: dict})
@api_view(["GET"])
def engines_view(request):
    return Response({"engines": available_engines()})


@extend_schema(responses={200: dict})
@api_view(["GET"])
def stats_view(request):
    runs = SearchRun.objects.aggregate(
        active=Count("id", filter=Q(status__in=SearchRun.ACTIVE_STATUSES)),
        failed=Count("id", filter=Q(status=SearchRun.Status.FAILED)),
        total=Count("id"),
    )
    return Response({
        "searches": SavedSearch.objects.count(),
        "enabled_searches": SavedSearch.objects.filter(enabled=True).count(),
        "results": SearchResult.objects.visible().count(),
        "new_results": SearchResult.objects.visible().filter(is_new=True).count(),
        "tagged_results": SearchResult.objects.visible().tagged().count(),
        "deleted_results": SearchResult.objects.deleted().count(),
        "runs": runs,
    })
