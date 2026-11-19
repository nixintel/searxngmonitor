from django.contrib import admin

from monitor.models import AppSettings, LogEntry, SavedSearch, SearchResult, SearchRun, Tag


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("name", "query", "enabled", "interval_value", "interval_unit", "use_proxy", "last_run_at", "next_run_at")
    list_filter = ("enabled", "use_proxy", "notify_email")
    search_fields = ("name", "query")


@admin.register(SearchResult)
class SearchResultAdmin(admin.ModelAdmin):
    list_display = ("title", "search", "is_new", "first_seen_at", "deleted_at")
    list_filter = ("is_new", "search", "tags")
    filter_horizontal = ("tags",)
    search_fields = ("title", "url", "content")
    raw_id_fields = ("run",)


@admin.register(SearchRun)
class SearchRunAdmin(admin.ModelAdmin):
    list_display = ("id", "search", "trigger", "status", "created_at", "results_returned", "new_results")
    list_filter = ("status", "trigger")


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "level", "logger", "message")
    list_filter = ("level",)
    search_fields = ("message",)


admin.site.register(AppSettings)
admin.site.register(Tag)
