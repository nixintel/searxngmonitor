import os
import sys
from pathlib import Path

import dj_database_url
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

# ---------------------------------------------------------------- core
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") + ["web"]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "monitor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "monitor.context_processors.app_context",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FORMS_URLFIELD_ASSUME_HTTPS = True  # Django 6 default; webhook URLs without a scheme become https://

REDIS_URL = env("REDIS_URL", "")
if REDIS_URL and not TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

APP_BASE_URL = env("APP_BASE_URL", "http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------- SearXNG / monitor
SEARXNG_URL = env("SEARXNG_URL", "http://searxng:8080").rstrip("/")
SEARXNG_PROXY_URL = env("SEARXNG_PROXY_URL", "http://searxng-proxy:8080").rstrip("/")
SEARXNG_PROXY_ENABLED = env_bool("SEARXNG_PROXY_ENABLED", False)
SEARXNG_TIMEOUT = float(env("SEARXNG_TIMEOUT", "20"))
SEARXNG_MAX_RETRIES = env_int("SEARXNG_MAX_RETRIES", 3)
DEFAULT_ENGINES = env_list("DEFAULT_ENGINES", "brave,bing,yahoo,mwmbl")
MIN_INTERVAL_MINUTES = env_int("MIN_INTERVAL_MINUTES", 5)
MAX_PAGES_PER_SEARCH = env_int("MAX_PAGES_PER_SEARCH", 5)

DISPATCH_INTERVAL_SECONDS = env_int("DISPATCH_INTERVAL_SECONDS", 60)
DISPATCH_BATCH_SIZE = env_int("DISPATCH_BATCH_SIZE", 200)
SEARCH_LOCK_TIMEOUT = env_int("SEARCH_LOCK_TIMEOUT", 900)
STALE_RUN_MINUTES = env_int("STALE_RUN_MINUTES", 60)

# ---------------------------------------------------------------- Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL or "cache+memory://")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TASK_TIME_LIMIT = 600
CELERY_RESULT_EXPIRES = 60 * 60 * 24
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_BEAT_SCHEDULE = {
    "dispatch-due-searches": {
        "task": "monitor.tasks.dispatch_due_searches",
        "schedule": DISPATCH_INTERVAL_SECONDS,
    },
    "reap-stale-runs": {
        "task": "monitor.tasks.reap_stale_runs",
        "schedule": 600,
    },
    "prune-old-records": {
        "task": "monitor.tasks.prune_old_records",
        "schedule": crontab(hour=3, minute=15),
    },
}

# ---------------------------------------------------------------- Email
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "searxng-monitor@localhost")
NOTIFY_EMAIL_TO = env("NOTIFY_EMAIL_TO", "")
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL", "")
SLACK_ALLOW_HTTP = env_bool("SLACK_ALLOW_HTTP", False)  # only for local testing with a fake receiver

# ---------------------------------------------------------------- API
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "SearXNG Monitor API",
    "DESCRIPTION": "Manage saved searches, results, runs, logs and health.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------- Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
        "db": {"class": "monitor.logging_handler.DatabaseLogHandler", "level": "INFO"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "monitor": {
            "handlers": ["console", "db"],
            "level": env("MONITOR_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
