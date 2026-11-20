from django.conf import settings


def app_context(request):
    ctx = {}
    if getattr(request, "user", None) and request.user.is_authenticated:
        from monitor.models import AppSettings, SearchResult

        app = AppSettings.load()
        ctx["proxy_enabled"] = app.proxy_enabled
        ctx["tor_enabled"] = app.tor_enabled

        ctx["total_new"] = SearchResult.objects.filter(is_new=True).count()
    return ctx
