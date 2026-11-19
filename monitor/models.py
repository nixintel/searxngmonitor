from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def default_engines():
    return list(settings.DEFAULT_ENGINES)


class AppSettings(models.Model):
    """Singleton row holding UI-editable defaults."""

    default_engines = models.JSONField(default=default_engines, blank=True)
    notify_address = models.EmailField(
        blank=True, help_text="Fallback recipient when a search has no address of its own."
    )
    slack_webhook_url = models.URLField(
        max_length=500, blank=True,
        help_text="Default Slack incoming-webhook URL, used when a search has none of its own.",
    )
    retention_days = models.PositiveIntegerField(
        default=30, help_text="Logs and run history older than this are pruned nightly."
    )

    class Meta:
        verbose_name = "app settings"
        verbose_name_plural = "app settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "App settings"


class SavedSearch(models.Model):
    class IntervalUnit(models.TextChoices):
        MINUTES = "minutes", "Minutes"
        HOURS = "hours", "Hours"

    class TimeRange(models.TextChoices):
        ANY = "", "Any time"
        DAY = "day", "Past day"
        WEEK = "week", "Past week"
        MONTH = "month", "Past month"
        YEAR = "year", "Past year"

    name = models.CharField(max_length=200)
    query = models.CharField(max_length=500, help_text="Keyword or phrase. SearXNG syntax is allowed, e.g. \"exact phrase\".")
    engines = models.JSONField(default=default_engines, blank=True)
    categories = models.CharField(
        max_length=200, blank=True, default="general",
        help_text="Used only when no engines are selected.",
    )
    language = models.CharField(max_length=20, default="all")
    time_range = models.CharField(max_length=10, blank=True, choices=TimeRange.choices, default=TimeRange.ANY)
    max_pages = models.PositiveSmallIntegerField(default=1, help_text="Result pages to fetch per run.")
    interval_value = models.PositiveIntegerField(default=60)
    interval_unit = models.CharField(max_length=10, choices=IntervalUnit.choices, default=IntervalUnit.MINUTES)
    use_proxy = models.BooleanField(default=False, help_text="Run through the proxied SearXNG instance.")
    notify_email = models.BooleanField(default=False)
    notify_address = models.EmailField(blank=True, help_text="Blank = use the default address.")
    notify_slack = models.BooleanField(default=False)
    slack_webhook_url = models.URLField(
        max_length=500, blank=True, help_text="Blank = use the default webhook from Settings.",
    )
    baseline_first_run = models.BooleanField(
        default=False,
        help_text="Treat results from the very first run as already seen (no 'new' flag, no email).",
    )
    enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["enabled", "next_run_at"])]

    def __str__(self):
        return self.name

    @property
    def interval(self) -> timedelta:
        if self.interval_unit == self.IntervalUnit.HOURS:
            return timedelta(hours=self.interval_value)
        return timedelta(minutes=self.interval_value)

    @property
    def interval_display(self):
        unit = self.get_interval_unit_display().lower()
        if self.interval_value == 1:
            unit = unit.rstrip("s")
        return f"every {self.interval_value} {unit}"

    def compute_next_run(self, from_time=None):
        return (from_time or timezone.now()) + self.interval

    def clean(self):
        minimum = timedelta(minutes=settings.MIN_INTERVAL_MINUTES)
        if self.interval_value and self.interval < minimum:
            raise ValidationError(
                {"interval_value": f"Interval must be at least {settings.MIN_INTERVAL_MINUTES} minutes."}
            )
        if not 1 <= (self.max_pages or 0) <= settings.MAX_PAGES_PER_SEARCH:
            raise ValidationError({"max_pages": f"Must be between 1 and {settings.MAX_PAGES_PER_SEARCH}."})
        if not self.engines and not self.categories:
            raise ValidationError("Select at least one engine or a category.")

    def save(self, *args, **kwargs):
        if self.enabled and self.next_run_at is None:
            self.next_run_at = timezone.now()
        super().save(*args, **kwargs)


class SearchRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    search = models.ForeignKey(SavedSearch, on_delete=models.CASCADE, related_name="runs")
    celery_task_id = models.CharField(max_length=64, blank=True, db_index=True)
    trigger = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.SCHEDULED)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    proxy_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    results_returned = models.PositiveIntegerField(default=0)
    new_results = models.PositiveIntegerField(default=0)
    unresponsive_engines = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["search", "-created_at"])]

    def __str__(self):
        return f"Run {self.pk} of {self.search_id} ({self.status})"

    @property
    def duration(self):
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    STAR = "interesting"

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def normalize(cls, name):
        return " ".join((name or "").split())[:50].lower()

    @classmethod
    def get_or_create_named(cls, name):
        name = cls.normalize(name)
        if not name:
            raise ValueError("Tag name is empty.")
        tag, _ = cls.objects.get_or_create(name=name)
        return tag


class SearchResultQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def tagged(self):
        return self.filter(tags__isnull=False).distinct()


class SearchResult(models.Model):
    search = models.ForeignKey(SavedSearch, on_delete=models.CASCADE, related_name="results")
    run = models.ForeignKey(SearchRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="found_results")
    url = models.TextField()
    url_hash = models.CharField(max_length=64)
    title = models.TextField(blank=True)
    content = models.TextField(blank=True)
    engines = models.JSONField(default=list, blank=True)
    published_date = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_new = models.BooleanField(default=True, db_index=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="results")
    # Soft delete: the row is kept so its url_hash still blocks the result from
    # being re-added as "new" on the next run.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    raw = models.JSONField(default=dict, blank=True)

    objects = SearchResultQuerySet.as_manager()

    class Meta:
        ordering = ["-first_seen_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["search", "url_hash"], name="uniq_result_per_search"),
        ]
        indexes = [models.Index(fields=["search", "is_new"])]

    def __str__(self):
        return self.title or self.url

    @property
    def tag_names(self):
        return {t.name for t in self.tags.all()}

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.is_new = False
        self.save(update_fields=["deleted_at", "is_new"])

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])


class LogEntry(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    level = models.CharField(max_length=10, db_index=True)
    logger = models.CharField(max_length=100)
    message = models.TextField()
    search = models.ForeignKey(SavedSearch, on_delete=models.SET_NULL, null=True, blank=True, related_name="logs")
    run = models.ForeignKey(SearchRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="logs")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "log entries"

    def __str__(self):
        return f"[{self.level}] {self.message[:80]}"
