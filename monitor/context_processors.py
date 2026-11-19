from django.conf import settings


def app_context(request):
    ctx = {"proxy_enabled": settings.SEARXNG_PROXY_ENABLED}
    if getattr(request, "user", None) and request.user.is_authenticated:
        from monitor.models import SearchResult

        ctx["total_new"] = SearchResult.objects.filter(is_new=True).count()
    return ctx
