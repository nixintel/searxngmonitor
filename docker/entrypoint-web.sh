#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py ensure_admin
python manage.py apply_proxy_config
python manage.py apply_tor_config

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
