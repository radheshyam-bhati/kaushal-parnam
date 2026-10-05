"""Split the RLS write policies per command (F-10).

The bug
-------
``0002_rls_policies`` created one write policy per table as::

    CREATE POLICY rls_write ON person FOR ALL
        USING (current_setting('app.user_role', true) IN ('policy_officer','system',''))
        WITH CHECK (...);

``FOR ALL`` means *every* command, SELECT included. Permissive policies are
OR-ed together, so for a SELECT the read policy and this write policy were both
consulted, and any session whose role was in that list matched ``rls_read OR
rls_write`` and therefore saw **every row in the table**.

Measured on the demo data as ``kp_app``: the read helper
``kp_visible_people()`` correctly returned 0 rows for a session with no role,
while ``SELECT count(*) FROM person`` returned all 501. The identity scoping was
being computed and then thrown away by a policy that was only ever meant to
govern writes.

The fix
-------
One policy per command, so a write policy cannot contribute to SELECT:

* ``rls_read``     ``FOR SELECT``      -- the scoped expression, unchanged
* ``rls_insert``   ``FOR INSERT``      -- ``WITH CHECK (true)``, as before
* ``rls_update``   ``FOR UPDATE``      -- ``USING``/``WITH CHECK`` on the role
* ``rls_delete``   ``FOR DELETE``      -- ``USING`` on the role

The role lists drop ``''``. Together with ``0005_rls_fail_closed`` (which
removed ``''`` from the two scope functions) an unset or anonymous session now
sees nothing at all, which is the correct direction to fail.

Write behaviour is otherwise unchanged: the table owner bypasses RLS, so
migrations and management commands are unaffected, and FORCE ROW LEVEL SECURITY
stays off because the scope helpers are SECURITY DEFINER and rely on that bypass.
"""

from django.db import migrations

#: table -> the SELECT expression, which also becomes WITH CHECK on INSERT.
READ_SCOPE = {
    'person': 'utid IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'contact': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'consent': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'id_crosswalk': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'enrolment': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'followup_job': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'followup_task': 'person_id IN (SELECT v.utid FROM kp_visible_people() AS v)',
    'outcome_event': 'id IN (SELECT v.id FROM kp_visible_outcomes() AS v)',
    'reason': 'outcome_id IN (SELECT v.id FROM kp_visible_outcomes() AS v)',
    'employer': 'outcome_id IN (SELECT v.id FROM kp_visible_outcomes() AS v)',
    # Attempts are logged by the worker; a trainee may see their own round's log.
    'contact_attempt': (
        "current_setting('app.user_role', true) IN ('policy_officer','district_officer','system')"
        ' OR followup_job_id IN (SELECT f.id FROM followup_job f'
        " WHERE f.person_id = current_setting('app.utid', true))"
    ),
    # Audit trail is visible to policy officers only.
    'audit_log': "current_setting('app.user_role', true) IN ('policy_officer','system')",
}

#: Roles permitted to change or remove an existing row. Insert is deliberately
#: open: a trainee enrolling creates their own person, contact, consent,
#: enrolment and outcome rows, and requiring a privileged role there would make
#: the enrolment flow impossible.
WRITE_ROLES = "('policy_officer','system')"
WRITE_ROLE_EXPR = f"current_setting('app.user_role', true) IN {WRITE_ROLES}"

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


def _statements():
    parts = [DROP_POLICIES]
    for table, scope in READ_SCOPE.items():
        parts.append(
            f'CREATE POLICY rls_read ON {table} FOR SELECT USING ({scope});\n'
            f'CREATE POLICY rls_insert ON {table} FOR INSERT WITH CHECK (true);\n'
            f'CREATE POLICY rls_update ON {table} FOR UPDATE'
            f' USING ({WRITE_ROLE_EXPR}) WITH CHECK ({WRITE_ROLE_EXPR});\n'
            f'CREATE POLICY rls_delete ON {table} FOR DELETE USING ({WRITE_ROLE_EXPR});'
        )
    return parts


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in _statements():
            if statement.strip():
                cursor.execute(statement)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_rls_fail_closed'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]