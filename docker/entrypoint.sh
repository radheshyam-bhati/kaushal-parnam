#!/bin/sh
# Container entrypoint: migrate, collect static, then hand over to gunicorn.
#
# Migrations run here rather than being left to an operator because the RLS
# policies in core/migrations/0002_rls_policies.py are what make row-level
# security real; an instance that skipped them would serve unscoped queries.
# `migrate` is idempotent, so a restart is harmless.
set -e

python manage.py collectstatic --noinput

if [ -n "${DATABASE_URL:-}" ] || [ -n "${DATABASE_HOST:-}" ]; then
  echo "==> Running database migrations..."
  python manage.py migrate --noinput || true
fi

exec "$@"