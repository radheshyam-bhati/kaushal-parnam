#!/bin/sh
# Run the test suite with row-level security genuinely enforced.
#
# Why this is a separate script
# -----------------------------
# RLS is skipped three ways: by a superuser, by a BYPASSRLS role, and by the
# table's own owner. The third is the trap. Django's test runner creates the test
# database with the same role it then connects as, so that role owns every table
# in it and the policies never apply. A suite that "passes as a non-superuser"
# can therefore have asserted nothing at all.
#
# So: create and migrate the test database as the table owner, then run the suite
# as kp_test -- NOSUPERUSER, NOBYPASSRLS, and not the owner. --keepdb stops
# Django dropping a database it did not create.
#
# Usage:
#   docker/test-rls.sh                                  # local cluster defaults
#   OWNER_URL=... APP_URL=... docker/test-rls.sh
#   docker/test-rls.sh core.tests.RlsEnforcementTests   # a subset
set -eu

OWNER_URL="${OWNER_URL:-postgres://postgres@127.0.0.1:5433/kaushal_parinam}"
APP_URL="${APP_URL:-postgres://kp_test:kp_test@127.0.0.1:5433/kaushal_parinam}"
PYTHON="${PYTHON:-python3}"

OWNER_USER=$(printf '%s' "$OWNER_URL" | sed -E 's#^postgres://([^:@/]*).*#\1#')
OWNER_PASS=$(printf '%s' "$OWNER_URL" | sed -nE 's#^postgres://[^:@/]*:([^@]*)@.*#\1#p')
OWNER_HOST=$(printf '%s' "$OWNER_URL" | sed -E 's#^postgres://[^@/]*@([^:/?]*).*#\1#')
OWNER_PORT=$(printf '%s' "$OWNER_URL" | sed -nE 's#^postgres://[^@/]*@[^:/?]*:([0-9]+)/.*#\1#p')
OWNER_DB=$(printf '%s' "$OWNER_URL" | sed -E 's#^postgres://[^/]*/([^/?]*).*#\1#')
OWNER_PORT="${OWNER_PORT:-5432}"
TEST_DB="test_${OWNER_DB}"
TEST_URL="postgres://${OWNER_USER}:${OWNER_PASS}@${OWNER_HOST}:${OWNER_PORT}/${TEST_DB}"

PGPASSWORD="$OWNER_PASS"; export PGPASSWORD
PSQL="psql -h $OWNER_HOST -p $OWNER_PORT -U $OWNER_USER -v ON_ERROR_STOP=1 -q"

cleanup() { $PSQL -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

echo "==> owner role $OWNER_USER, test database $TEST_DB"
$PSQL -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;" >/dev/null
$PSQL -d postgres -c "CREATE DATABASE $TEST_DB OWNER $OWNER_USER;" >/dev/null
$PSQL -d postgres -c "GRANT CONNECT ON DATABASE $TEST_DB TO kp_test;" >/dev/null
$PSQL -d "$TEST_DB" -c "GRANT USAGE, CREATE ON SCHEMA public TO kp_test;" >/dev/null
$PSQL -d "$TEST_DB" -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO kp_test;" >/dev/null
$PSQL -d "$TEST_DB" -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO kp_test;" >/dev/null
$PSQL -d "$TEST_DB" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO kp_test;" >/dev/null
$PSQL -d "$TEST_DB" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO kp_test;" >/dev/null

echo "==> migrating as the table owner"
DATABASE_URL="$TEST_URL" "$PYTHON" manage.py migrate --noinput >/dev/null

echo "==> table owners (must be $OWNER_USER, never kp_test)"
$PSQL -d "$TEST_DB" -Atc \
  "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class
    WHERE relname IN ('person','outcome_event','audit_log');"

echo "==> suite as kp_test (NOSUPERUSER, NOBYPASSRLS, not the owner)"
DATABASE_URL="$APP_URL" "$PYTHON" manage.py test --keepdb "$@"