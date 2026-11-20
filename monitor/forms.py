from django import forms

from monitor.models import TOR_ENGINES, AppSettings, SavedSearch
from monitor.services import notify
from monitor.services.searxng import available_engines


def engine_choices(selected=()):
    names = [e["name"] for e in available_engines()]
    for name in selected or ():
        if name not in names:
            names.append(name)
    return [(n, f"{n} (Tor)" if n in TOR_ENGINES else n) for n in sorted(names)]


def _style(fields):
    for field in fields.values():
        if isinstance(field.widget, forms.RadioSelect):
            continue
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "form-select")
        elif isinstance(field.widget, forms.CheckboxSelectMultiple):
            field.widget.attrs.setdefault("class", "form-check-input")
        else:
            field.widget.attrs.setdefault("class", "form-control")


class SearchForm(forms.ModelForm):
    engines = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Leave empty to search by category instead.",
    )

    class Meta:
        model = SavedSearch
        fields = [
            "name", "source", "query", "feed_url", "engines", "categories", "language", "time_range", "max_pages",
            "interval_value", "interval_unit", "use_proxy", "notify_email", "notify_address",
            "notify_slack", "slack_webhook_url", "baseline_first_run", "enabled",
        ]
        labels = {
            "interval_value": "Run every",
            "interval_unit": "Unit",
            "use_proxy": "Run through proxy",
            "notify_email": "Email me about new results",
            "notify_address": "Email address",
            "notify_slack": "Post new results to Slack",
            "slack_webhook_url": "Slack webhook URL",
            "baseline_first_run": "Baseline first run",
        }
        widgets = {
            "slack_webhook_url": forms.URLInput(attrs={"placeholder": "https://hooks.slack.com/services/…"}),
            "source": forms.RadioSelect,
            "feed_url": forms.URLInput(attrs={"placeholder": "https://www.google.com/alerts/feeds/…/…"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].widget.attrs["class"] = "source-radio"
        self.fields["source"].required = False  # missing = SearXNG (older forms/clients)
        self.fields["feed_url"].label = "Google Alert RSS feed URL"
        current = self.instance.engines if self.instance.pk else AppSettings.load().default_engines
        self.fields["engines"].choices = engine_choices(current)
        if not self.instance.pk:
            self.initial["engines"] = current
        _style(self.fields)

    def clean_source(self):
        return self.cleaned_data.get("source") or SavedSearch.Source.SEARXNG

    def clean(self):
        data = super().clean()
        if data.get("source") == SavedSearch.Source.GOOGLE_ALERT:
            data["use_proxy"] = False  # the proxy only applies to SearXNG
            self.instance.use_proxy = False
        else:
            tor = [e for e in data.get("engines") or [] if e in TOR_ENGINES]
            if tor:
                if not AppSettings.load().tor_enabled:
                    self.add_error("engines", f"{' and '.join(tor)} search .onion sites and need Tor, which is "
                                              "turned off. Turn on Tor (Settings → Tor), or untick "
                                              f"{'them' if len(tor) > 1 else 'it'}.")
                data["use_proxy"] = False  # Tor searches always run through Tor, never the generic proxy
                self.instance.use_proxy = False
        if data.get("notify_email") and not (data.get("notify_address") or notify.recipient_for(None)):
            self.add_error("notify_address", "Enter an address, or set a default address in Settings.")
        if data.get("notify_slack") and not (data.get("slack_webhook_url") or notify.slack_webhook_for(None)):
            self.add_error("slack_webhook_url", "Enter a webhook URL, or set a default webhook in Settings.")
        return data


class AppSettingsForm(forms.ModelForm):
    default_engines = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    proxy_password = forms.CharField(
        required=False, label="Password", strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the saved password.",
    )
    clear_proxy_password = forms.BooleanField(required=False, label="Remove the saved password")

    class Meta:
        model = AppSettings
        fields = [
            "default_engines", "notify_address", "slack_webhook_url", "retention_days",
            "proxy_enabled", "proxy_scheme", "proxy_host", "proxy_port", "proxy_username", "proxy_password",
        ]
        labels = {
            "notify_address": "Default email address", "slack_webhook_url": "Default Slack webhook URL",
            "proxy_enabled": "Use a proxy for searches marked \"Run through proxy\"",
            "proxy_scheme": "Type", "proxy_host": "Host", "proxy_port": "Port", "proxy_username": "Username",
        }
        widgets = {
            "slack_webhook_url": forms.URLInput(attrs={"placeholder": "https://hooks.slack.com/services/…"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_engines"].choices = engine_choices(self.instance.default_engines)
        self.fields["proxy_username"].widget.attrs["autocomplete"] = "off"
        self.fields["proxy_host"].widget.attrs["placeholder"] = "proxy.example.net"
        _style(self.fields)

    def clean_proxy_host(self):
        host = (self.cleaned_data.get("proxy_host") or "").strip()
        if "://" in host or "/" in host or "@" in host or " " in host:
            raise forms.ValidationError("Enter just the hostname or IP address, e.g. proxy.example.net")
        return host

    def clean_proxy_password(self):
        # Keep the saved password unless a new one is typed or "remove" is ticked.
        new = self.cleaned_data.get("proxy_password") or ""
        if self.data.get("clear_proxy_password"):
            return ""
        return new or self.instance.proxy_password

    def clean(self):
        data = super().clean()
        if data.get("proxy_enabled"):
            if not data.get("proxy_host"):
                self.add_error("proxy_host", "Enter the proxy host to turn the proxy on.")
            port = data.get("proxy_port")
            if not port or not 1 <= port <= 65535:
                self.add_error("proxy_port", "Enter a port between 1 and 65535.")
        if data.get("proxy_password") and not data.get("proxy_username"):
            self.add_error("proxy_username", "A password needs a username.")
        return data
