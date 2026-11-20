"""Trigram indexes so keyword search over results stays fast with very many results (PostgreSQL only).

Keyword search uses Django's `icontains`, which PostgreSQL runs as `UPPER(col) LIKE UPPER('%term%')`. A pg_trgm
GIN index on `UPPER(col)` serves exactly that, so no query code changes. On SQLite (local dev/tests) this is a no-op.
"""

from django.db import migrations

COLUMNS = ("title", "content", "url")


def index_name(column):
    return f"monitor_result_{column}_trgm"


def create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for column in COLUMNS:
        schema_editor.execute(
            f'CREATE INDEX IF NOT EXISTS {index_name(column)} ON monitor_searchresult '
            f'USING gin (UPPER("{column}") gin_trgm_ops)'
        )


def drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for column in COLUMNS:
        schema_editor.execute(f"DROP INDEX IF EXISTS {index_name(column)}")


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0005_appsettings_tor_enabled"),
    ]

    operations = [
        migrations.RunPython(create, drop),
    ]
