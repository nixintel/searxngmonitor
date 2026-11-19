from django import forms

from monitor.models import AppSettings, SavedSearch
from monitor.services import notify
from monitor.services.searxng import available_engines


def engine_choices(selected=()):
    names = [e["name"] for e in available_engines()]
    for name in selected or ():
        if name not in names:
            names.append(name)
    return [(n, n) for n in sorted(names)]


def _style(fields):
    for field in fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "form-select")
        elif not isinstance(field.widget, forms.CheckboxSelectMultiple):
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
            "name", "query", "engines", "categories", "language", "time_range", "max_pages",
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.engines if self.instance.pk else AppSettings.load().default_engines
        self.fields["engines"].choices = engine_choices(current)
        if not self.instance.pk:
            self.initial["engines"] = current
        _style(self.fields)

    def clean(self):
        data = super().clean()
        if data.get("notify_email") and not (data.get("notify_address") or notify.recipient_for(None)):
            self.add_error("notify_address", "Enter an address, or set a default address in Settings.")
        if data.get("notify_slack") and not (data.get("slack_webhook_url") or notify.slack_webhook_for(None)):
            self.add_error("slack_webhook_url", "Enter a webhook URL, or set a default webhook in Settings.")
        return data


class AppSettingsForm(forms.ModelForm):
    default_engines = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = AppSettings
        fields = ["default_engines", "notify_address", "slack_webhook_url", "retention_days"]
        labels = {"notify_address": "Default email address", "slack_webhook_url": "Default Slack webhook URL"}
        widgets = {
            "slack_webhook_url": forms.URLInput(attrs={"placeholder": "https://hooks.slack.com/services/…"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_engines"].choices = engine_choices(self.instance.default_engines)
        _style(self.fields)
