from urllib.parse import urlsplit

from django import template

register = template.Library()


@register.filter
def safe_href(url):
    """Only allow http(s) links from search results (blocks javascript:, data:, ...)."""
    try:
        scheme = urlsplit(str(url or "")).scheme.lower()
    except ValueError:
        return "#"
    return url if scheme in ("http", "https") else "#"


@register.filter
def get_item(mapping, key):
    return (mapping or {}).get(key)


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """Current query string with some params replaced, e.g. {% url_replace page=2 %}."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()
