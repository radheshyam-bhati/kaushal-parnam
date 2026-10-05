"""Row-Level Security policies, skill-gap trigger and audit trigger.

PostgreSQL only. On SQLite the whole migration is a no-op and isolation is
enforced in the query layer by ``core.rls`` (docs/10-BUILD-PLAN.md A-01).

How isolation works
-------------------
Two SECURITY DEFINER helper functions resolve "what may this session see":

    kp_visible_people()  -> UTIDs
    kp_visible_outcomes() -> outcome_event ids

They read the session variables that ``core.middleware.RLSMiddleware`` sets
per request (app.user_role, app.utid, app.provider_id, app.district) and are
owned by the migration role, so the table owner they run as bypasses RLS. Every
policy then reduces to a membership test against one of those functions.

That indirection is what stops the classic failure: a ``person`` policy that
sub-selects ``enrolment`` while ``enrolment``'s policy sub-selects ``person``
makes PostgreSQL raise "infinite recursion detected in policy".

The sub-selects are written as ``SELECT v.utid FROM kp_visible_people() AS v``
rather than with a bare ``utid`` for a second reason: inside a policy an
unqualified column name resolves against the policy's own table first, so
``IN (SELECT utid FROM ...)`` on ``contact`` fails with "column utid does not
exist".

Because the helpers rely on the owner bypass, FORCE ROW LEVEL SECURITY must stay
OFF on these tables. The application role must not be the table owner and must
not be SUPERUSER or BYPASSRLS -- see the ``kp_app`` role note below and
docs/06-IMPLEMENTATION-PLAN.md Phase 2.

Reference: docs/05-BACKEND-SCHEMA.md §1.1 and §5, docs/08-Architecture.md §5.3.
"""

from django.db import migrations

DROP_EVERYTHING = """
DROP FUNCTION IF EXISTS kp_visible_people();
DROP FUNCTION IF EXISTS kp_visible_outcomes();
DROP FUNCTION IF EXISTS kp_drop_policies();
"""

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

# Role predicates shared by both scope functions.
SCOPE_FUNCTIONS = """
-- Which UTIDs may the current session read?
-- RETURNS TABLE names the output column, so policies can say v.utid.
CREATE OR REPLACE FUNCTION kp_visible_people() RETURNS TABLE(utid text)
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT p.utid FROM person p
    WHERE
        -- policy officers see everyone; system/owner writes bypass RLS anyway
        current_setting('app.user_role', true) IN ('policy_officer','system','')
        OR (current_setting('app.user_role', true) = 'trainee'
            AND p.utid = current_setting('app.utid', true))
        OR (current_setting('app.user_role', true) IN ('provider','provider_coordinator')
            AND EXISTS (SELECT 1 FROM enrolment e
                        WHERE e.person_id = p.utid
                          AND e.provider_id = current_setting('app.provider_id', true)))
        OR (current_setting('app.user_role', true) = 'district_officer'
            AND p.district = current_setting('app.district', true));
$$;

-- Which outcome rows may the current session read?
CREATE OR REPLACE FUNCTION kp_visible_outcomes() RETURNS TABLE(id bigint)
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT o.id FROM outcome_event o
    WHERE
        current_setting('app.user_role', true) IN ('policy_officer','system','')
        OR (current_setting('app.user_role', true) = 'trainee'
            AND o.person_id = current_setting('app.utid', true))
        OR (current_setting('app.user_role', true) IN ('provider','provider_coordinator')
            AND EXISTS (SELECT 1 FROM enrolment e
                        WHERE e.person_id = o.person_id
                          AND e.provider_id = current_setting('app.provider_id', true)))
        OR (current_setting('app.user_role', true) = 'district_officer'
            AND EXISTS (SELECT 1 FROM person p
                        WHERE p.utid = o.person_id
                          AND p.district = current_setting('app.district', true)));
$$;
"""

ENABLE_RLS = """
ALTER TABLE person          ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact         ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent         ENABLE ROW LEVEL SECURITY;
ALTER TABLE id_crosswalk    ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrolment       ENABLE ROW LEVEL SECURITY;
ALTER TABLE followup_job    ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE followup_task   ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcome_event   ENABLE ROW LEVEL SECURITY;
ALTER TABLE reason          ENABLE ROW LEVEL SECURITY;
ALTER TABLE employer        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log       ENABLE ROW LEVEL SECURITY;
"""

POLICIES = """
CREATE POLICY rls_read ON person
    FOR SELECT USING (utid IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON person
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    );

CREATE POLICY rls_read ON contact
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON contact
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON consent
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON consent
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON id_crosswalk
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON id_crosswalk
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON enrolment
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON enrolment
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON followup_job
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON followup_job
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON followup_task
    FOR SELECT USING (person_id IN (SELECT v.utid FROM kp_visible_people() AS v));
CREATE POLICY rls_write ON followup_task
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON outcome_event
    FOR SELECT USING (id IN (SELECT v.id FROM kp_visible_outcomes() AS v));
CREATE POLICY rls_write ON outcome_event
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON reason
    FOR SELECT USING (outcome_id IN (SELECT v.id FROM kp_visible_outcomes() AS v));
CREATE POLICY rls_write ON reason
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

CREATE POLICY rls_read ON employer
    FOR SELECT USING (outcome_id IN (SELECT v.id FROM kp_visible_outcomes() AS v));
CREATE POLICY rls_write ON employer
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

-- Attempts are logged by the worker; a trainee may see their own round's log.
CREATE POLICY rls_read ON contact_attempt
    FOR SELECT USING (
        current_setting('app.user_role', true) IN ('policy_officer','district_officer','system','')
        OR followup_job_id IN (SELECT f.id FROM followup_job f
                               WHERE f.person_id = current_setting('app.utid', true))
    );
CREATE POLICY rls_write ON contact_attempt
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);

-- Audit trail is visible to policy officers only.
CREATE POLICY rls_read ON audit_log
    FOR SELECT USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    );
CREATE POLICY rls_write ON audit_log
    FOR ALL USING (
        current_setting('app.user_role', true) IN ('policy_officer','system','')
    ) WITH CHECK (true);
"""

# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
# Note: docs/05-BACKEND-SCHEMA.md §1.1 writes this as "AFTER ... UPDATE OF (...)".
# PostgreSQL only accepts a column list on UPDATE for row-level *constraint*
# triggers, not for plain triggers, so this fires BEFORE INSERT OR UPDATE with
# no column list and recomputes from the three counters.
SKILL_GAP_TRIGGER = """
CREATE OR REPLACE FUNCTION compute_skill_gap_flag() RETURNS TRIGGER AS $$
BEGIN
    NEW.signals_agree_count :=
        (CASE WHEN NEW.vacancy_count > 0 THEN 1 ELSE 0 END)
      + (CASE WHEN NEW.employer_skills_lacking_count > 0 THEN 1 ELSE 0 END)
      + (CASE WHEN NEW.trainee_skills_lacking_count > 0 THEN 1 ELSE 0 END);
    NEW.gap_flag := (NEW.signals_agree_count >= 2);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_skill_gap ON stats_skill_gap;
CREATE TRIGGER trigger_skill_gap
BEFORE INSERT OR UPDATE ON stats_skill_gap
FOR EACH ROW EXECUTE FUNCTION compute_skill_gap_flag();
"""

# `timestamp` is set here because Django's auto_now_add is application-side and
# would leave the column NULL on a trigger-driven insert.
AUDIT_TRIGGER = """
CREATE OR REPLACE FUNCTION audit_log_trigger() RETURNS TRIGGER AS $$
DECLARE
    -- The UTID column is named person_id on child tables but utid on person
    -- itself, so read it from the row JSON rather than naming a column that
    -- does not exist on the current table.
    row_utid TEXT;
BEGIN
    row_utid := COALESCE(
        to_jsonb(NEW) ->> 'person_id',
        to_jsonb(NEW) ->> 'utid',
        to_jsonb(OLD) ->> 'person_id',
        to_jsonb(OLD) ->> 'utid'
    );
    INSERT INTO audit_log (component, user_role, event_type, description, utid, timestamp)
    VALUES (
        TG_TABLE_NAME,
        COALESCE(NULLIF(current_setting('app.user_role', true), ''), 'system'),
        TG_OP,
        TG_OP || ' on ' || TG_TABLE_NAME || CASE WHEN row_utid IS NULL THEN '' ELSE ' for ' || row_utid END,
        row_utid,
        NOW()
    );
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_outcome_event ON outcome_event;
CREATE TRIGGER audit_outcome_event
AFTER INSERT ON outcome_event
FOR EACH ROW EXECUTE FUNCTION audit_log_trigger();

DROP TRIGGER IF EXISTS audit_consent ON consent;
CREATE TRIGGER audit_consent
AFTER INSERT OR UPDATE ON consent
FOR EACH ROW EXECUTE FUNCTION audit_log_trigger();

DROP TRIGGER IF EXISTS audit_person ON person;
CREATE TRIGGER audit_person
AFTER UPDATE ON person
FOR EACH ROW WHEN (OLD.is_active IS DISTINCT FROM NEW.is_active)
EXECUTE FUNCTION audit_log_trigger();
"""

APP_ROLE = """
-- The application must connect as a role that is NOT the table owner, NOT a
-- superuser and NOT BYPASSRLS, or these policies are silently ignored.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kp_app') THEN
        CREATE ROLE kp_app LOGIN PASSWORD 'kp_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO kp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kp_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kp_app;
"""


def _run(statements, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            if statement.strip():
                cursor.execute(statement)


def apply_policies(apps, schema_editor):
    _run(
        [
            DROP_EVERYTHING,
            DROP_POLICIES,
            SCOPE_FUNCTIONS,
            ENABLE_RLS,
            POLICIES,
            SKILL_GAP_TRIGGER,
            AUDIT_TRIGGER,
            APP_ROLE,
        ],
        schema_editor,
    )


def drop_policies(apps, schema_editor):
    _run([DROP_POLICIES, DROP_EVERYTHING], schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(apply_policies, drop_policies),
    ]