#!/bin/sh
# Container entrypoint: migrate, collect static, then hand over to gunicorn.
#
# Migrations run here rather than being left to an operator because the RLS
# policies in core/migrations/0002_rls_policies.py are what make row-level
# security real; an instance that skipped them would serve unscoped queries.
# `migrate` is idempotent, so a restart is harmless.
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"