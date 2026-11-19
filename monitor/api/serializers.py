from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from monitor.models import LogEntry, SavedSearch, SearchResult, SearchRun, Tag
from monitor.services import notify


class SavedSearchSerializer(serializers.ModelSerializer):
    new_count = serializers.IntegerField(read_only=True)
    total_count = serializers.IntegerField(read_only=True)
    tagged_count = serializers.IntegerField(read_only=True)
    last_status = serializers.CharField(read_only=True, allow_null=True)
    engines = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    slack_webhook_set = serializers.SerializerMethodField(help_text="True if this search has its own webhook.")

    class Meta:
        model = SavedSearch
        fields = [
            "id", "name", "source", "query", "feed_url", "engines", "categories", "language", "time_range", "max_pages",
            "interval_value", "interval_unit", "use_proxy", "notify_email", "notify_address",
            "notify_slack", "slack_webhook_url", "slack_webhook_set",
            "baseline_first_run", "enabled", "last_run_at", "next_run_at", "created_at", "updated_at",
            "new_count", "total_count", "tagged_count", "last_status",
        ]
        read_only_fields = ["last_run_at", "next_run_at", "created_at", "updated_at"]
        # The webhook URL is a secret (anyone holding it can post to the channel): accept, never return.
        extra_kwargs = {"slack_webhook_url": {"write_only": True}}

    def get_slack_webhook_set(self, obj) -> bool:
        return bool(obj.slack_webhook_url)

    def validate(self, attrs):
        # Run the model's own clean() so API and UI enforce the same rules.
        merged = {**self._current_values(), **attrs}
        try:
            SavedSearch(**merged).clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
        if merged.get("notify_email") and not (merged.get("notify_address") or notify.recipient_for(None)):
            raise serializers.ValidationError({"notify_address": "No address given and no default address set."})
        if merged.get("notify_slack") and not (merged.get("slack_webhook_url") or notify.slack_webhook_for(None)):
            raise serializers.ValidationError({"slack_webhook_url": "No webhook given and no default webhook set."})
        return attrs

    def _current_values(self):
        if not self.instance:
            return {}
        model_fields = {fld.name for fld in SavedSearch._meta.fields}
        return {f: getattr(self.instance, f) for f in self.Meta.fields if f in model_fields}


class TagListField(serializers.ListField):
    child = serializers.CharField(max_length=50)

    def to_representation(self, manager):
        return sorted(t.name for t in manager.all())


class SearchResultSerializer(serializers.ModelSerializer):
    tags = TagListField(required=False, help_text="Tag names. PATCH replaces the whole list.")

    class Meta:
        model = SearchResult
        fields = [
            "id", "search", "run", "url", "title", "content", "engines",
            "published_date", "first_seen_at", "is_new", "tags", "deleted_at",
        ]
        read_only_fields = [f for f in fields if f not in ("is_new", "tags")]

    def update(self, instance, validated_data):
        names = validated_data.pop("tags", None)
        instance = super().update(instance, validated_data)
        if names is not None:
            instance.tags.set([Tag.get_or_create_named(n) for n in names if Tag.normalize(n)])
        return instance


class TagSerializer(serializers.ModelSerializer):
    result_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tag
        fields = ["id", "name", "result_count"]


class SearchRunSerializer(serializers.ModelSerializer):
    search_name = serializers.CharField(source="search.name", read_only=True)

    class Meta:
        model = SearchRun
        fields = [
            "id", "search", "search_name", "celery_task_id", "trigger", "status", "proxy_used",
            "created_at", "started_at", "finished_at", "results_returned", "new_results",
            "unresponsive_engines", "error",
        ]
        read_only_fields = fields


class LogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LogEntry
        fields = ["id", "created_at", "level", "logger", "message", "search", "run"]
        read_only_fields = fields
