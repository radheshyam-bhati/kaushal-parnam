"""Row-level security policy definitions (F-10).

Lives outside ``migrations/`` because two migrations need it: ``0009`` installs
these definitions, and a later one revises them. Importing one module from both
keeps a single source of truth, so the SQL in the migration history cannot drift
from the SQL described here.

Why the policies look like this
-------------------------------
Each table's scope is expressed so that **no policy consults a snapshot-limited
function over the table it protects**. That constraint is not stylistic; it is
what makes the policies usable by Django at all:

* Django writes with ``INSERT/UPDATE ... RETURNING id``.
* PostgreSQL checks rows returned by ``RETURNING`` against the **SELECT** policy.
* A ``STABLE`` function reading the table being written has a snapshot from
  before that write, so it cannot see the row -- and the write is rejected with
  ``new row violates row-level security policy``.

Hence the caller's own row is always a **column comparison**, and cross-table
lookups go through small ``SECURITY DEFINER`` helpers that read *other* tables.
Because those helpers are owned by the migration role they bypass RLS, which is
what keeps the policy graph acyclic without the set-returning helper that made
``INSERT ... RETURNING`` impossible.

Two named application scopes are bound by ``core.middleware.bind_scope``:
``app.utid`` re-bound once ``/enrol/`` mints a UTID, and
``app.employer_outcome_id`` for the no-login employer confirmation link, whose
signed token is the authorisation.

Privilege is never defaulted. An unset or empty ``app.user_role`` matches no
branch of any policy and therefore no rows: the policies fail closed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cross-table lookups. SECURITY DEFINER so they bypass RLS and stay acyclic; each
# reads tables other than the one whose policy calls it, so no snapshot is stale.
# ---------------------------------------------------------------------------
HELPERS = """
CREATE OR REPLACE FUNCTION kp_at_provider(p_utid text, p_provider text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM enrolment e
                    WHERE e.person_id = p_utid
                      AND e.provider_id = p_provider);
$$;

CREATE OR REPLACE FUNCTION kp_district_of(p_utid text)
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT p.district FROM person p WHERE p.utid = p_utid;
$$;

CREATE OR REPLACE FUNCTION kp_outcome_utid(p_outcome bigint)
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT o.person_id FROM outcome_event o WHERE o.id = p_outcome;
$$;

CREATE OR REPLACE FUNCTION kp_outcome_at_provider(p_outcome bigint, p_provider text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM outcome_event o
                    JOIN enrolment e ON e.person_id = o.person_id
                   WHERE o.id = p_outcome
                     AND e.provider_id = p_provider);
$$;

CREATE OR REPLACE FUNCTION kp_outcome_district(p_outcome bigint)
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT p.district
      FROM outcome_event o JOIN person p ON p.utid = o.person_id
     WHERE o.id = p_outcome;
$$;

CREATE OR REPLACE FUNCTION kp_job_utid(p_job bigint)
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT f.person_id FROM followup_job f WHERE f.id = p_job;
$$;
"""

PRIV = "current_setting('app.user_role', true) IN ('policy_officer','system')"
ROLE = "current_setting('app.user_role', true)"
UTID = "current_setting('app.utid', true)"
PROVIDER = "current_setting('app.provider_id', true)"
DISTRICT = "current_setting('app.district', true)"
REQUEST_ID = "current_setting('app.request_id', true)"
# NULLIF guards the cast: this is only ever set by the employer confirm flow, and
# an empty string would abort the cast rather than compare false.
EMPLOYEE_OUTCOME = (
    "NULLIF(current_setting('app.employer_outcome_id', true), '')::bigint"
)
IS_PROVIDER_SIDE = f"{ROLE} IN ('provider','provider_coordinator')"
# The trainee named by the employer link this session presented.
EMPLOYEE_PERSON = f"kp_outcome_utid({EMPLOYEE_OUTCOME})"


def _person_scope(own_utid_expr: str, *, own_provider_expr: str | None = None) -> str:
    """Scope for a table that reaches a person through a UTID column.

    ``own_utid_expr`` stays a column comparison for the caller's own row, which is
    what lets ``INSERT ... RETURNING`` succeed.
    """
    provider_branch = (
        f"({own_provider_expr} = {PROVIDER})"
        if own_provider_expr
        else f"(kp_at_provider({own_utid_expr}, {PROVIDER}))"
    )
    employer_branch = (
        f"OR ({ROLE} = 'employer' AND {own_utid_expr} = {EMPLOYEE_PERSON})"
    )
    return (
        f"({PRIV}"
        f" OR ({ROLE} = 'trainee' AND {own_utid_expr} = {UTID})"
        f" OR ({IS_PROVIDER_SIDE} AND {provider_branch})"
        f" OR ({ROLE} = 'district_officer'"
        f"     AND kp_district_of({own_utid_expr}) = {DISTRICT})"
        f" {employer_branch})"
    )


def _outcome_scope() -> str:
    """Scope for a table keyed by outcome_id."""
    return (
        f"({PRIV}"
        f" OR ({ROLE} = 'trainee' AND kp_outcome_utid(outcome_id) = {UTID})"
        f" OR ({IS_PROVIDER_SIDE} AND kp_outcome_at_provider(outcome_id, {PROVIDER}))"
        f" OR ({ROLE} = 'district_officer'"
        f"     AND kp_outcome_district(outcome_id) = {DISTRICT})"
        f" OR ({ROLE} = 'employer' AND outcome_id = {EMPLOYEE_OUTCOME}))"
    )


SCOPES = {
    # A district officer's own-district test is a column comparison here, which is
    # also what lets /enrol/ insert the new person row once bind_scope has run.
    'person': (
        f"({PRIV}"
        f" OR ({ROLE} = 'trainee' AND utid = {UTID})"
        f" OR ({IS_PROVIDER_SIDE} AND kp_at_provider(utid, {PROVIDER}))"
        f" OR ({ROLE} = 'district_officer' AND district = {DISTRICT})"
        f" OR ({ROLE} = 'employer' AND utid = {EMPLOYEE_PERSON}))",
    ),
    'contact': (_person_scope('person_id'),),
    'consent': (_person_scope('person_id'),),
    'id_crosswalk': (_person_scope('person_id'),),
    'followup_job': (_person_scope('person_id'),),
    'followup_task': (_person_scope('person_id'),),
    # A provider already knows its own provider_id from the row.
    'enrolment': (_person_scope('person_id', own_provider_expr='provider_id'),),
    'outcome_event': (
        f"({PRIV}"
        f" OR ({ROLE} = 'trainee' AND person_id = {UTID})"
        f" OR ({IS_PROVIDER_SIDE} AND kp_at_provider(person_id, {PROVIDER}))"
        f" OR ({ROLE} = 'district_officer'"
        f"     AND kp_district_of(person_id) = {DISTRICT})"
        f" OR ({ROLE} = 'employer' AND id = {EMPLOYEE_OUTCOME}))",
    ),
    'reason': (_outcome_scope(),),
    'employer': (_outcome_scope(),),
    # The worker logs every attempt; a trainee may read their own round.
    'contact_attempt': (
        f"({PRIV}"
        f" OR ({ROLE} = 'district_officer')"
        f" OR ({ROLE} = 'trainee' AND kp_job_utid(followup_job_id) = {UTID}))",
    ),
    # Readable by policy officers, by the trainee concerned, and by the session
    # that just wrote the row. The last clause is not a convenience:
    # PostgreSQL checks RETURNING against the SELECT policy, so without it no
    # session could write an audit row at all.
    'audit_log': (
        f"({PRIV}"
        f" OR utid = {UTID}"
        f" OR (request_id IS NOT NULL AND request_id = {REQUEST_ID}))",
    ),
}

TABLES = list(SCOPES)

DROP_POLICIES = """
DO $$
DECLARE t TEXT; p TEXT;
BEGIN
  FOR t IN SELECT unnest(ARRAY[
      'person','contact','consent','id_crosswalk','enrolment','followup_job',
      'contact_attempt','followup_task','outcome_event','reason','employer','audit_log'])
  LOOP
    FOR p IN SELECT policyname FROM pg_policies WHERE tablename = t LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON %I', p, t);
    END LOOP;
  END LOOP;
END $$;
"""


def statements(*, drop_helpers: bool = False) -> list[str]:
    """Full SQL to (re)install the helpers and every policy.

    Policies are dropped before the helpers, never the other way round: the
    policies reference the helpers, so dropping a helper first fails with
    "cannot drop function ... because other objects depend on it".
    """
    parts: list[str] = [DROP_POLICIES]
    if drop_helpers:
        parts.append(
            'DROP FUNCTION IF EXISTS kp_visible_people();\n'
            'DROP FUNCTION IF EXISTS kp_visible_outcomes();'
        )
    parts.append(HELPERS)
    for table, (scope,) in SCOPES.items():
        parts.append(
            f'CREATE POLICY rls_read ON {table} FOR SELECT USING ({scope});\n'
            f'CREATE POLICY rls_insert ON {table} FOR INSERT WITH CHECK ({scope});\n'
            f'CREATE POLICY rls_update ON {table} FOR UPDATE'
            f' USING ({scope}) WITH CHECK ({scope});\n'
            f'CREATE POLICY rls_delete ON {table} FOR DELETE USING ({scope});'
        )
    return parts