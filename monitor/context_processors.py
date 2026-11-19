from django.conf import settings


def app_context(request):
    ctx = {}
    if getattr(request, "user", None) and request.user.is_authenticated:
        from monitor.models import AppSettings, SearchResult

        ctx["proxy_enabled"] = AppSettings.load().proxy_enabled

        ctx["total_new"] = SearchResult.objects.filter(is_new=True).count()
    return ctx
