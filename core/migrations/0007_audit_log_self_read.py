"""Let a caller read back the audit rows they just wrote (F-10).

The bug
-------
Every INSERT here is issued by Django as ``INSERT ... RETURNING id``, and
PostgreSQL applies the **SELECT** policies to rows returned by ``RETURNING``. So
a statement that inserts a row must also be permitted to read it.

``audit_log``'s read policy was restricted to policy officers and the system,
which meant every audit write by a trainee, provider or officer failed with::

    new row violates row-level security policy for table "audit_log"

and, because ``core/services/audit.py`` treats a failed audit write as
non-fatal, the request carried on with no audit row at all. Login was the first
thing to break.

The fix
-------
A session may read audit rows for its own UTID, in addition to the
policy-officer and system cases. This is not a loosening to accommodate the
insert: ``trainees/views.py`` already renders exactly this on the "My Data"
page ("Your audit trail ... as required by DPDP s.8(6)") and already filtered on
``utid=person.pk``. Under the old policy that query silently returned nothing
for anyone who was not a policy officer, so the page was empty. The database
policy now agrees with the feature that was already there.

``system`` keeps its unscoped access for background work with no HTTP role.
``''`` and unset are still not privileged; see 0005.
"""

from django.db import migrations

READ_SCOPE = """
DROP POLICY IF EXISTS rls_read ON audit_log;
CREATE POLICY rls_read ON audit_log FOR SELECT USING (
    current_setting('app.user_role', true) IN ('policy_officer','system')
    OR utid = current_setting('app.utid', true)
);
"""


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(READ_SCOPE)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_split_write_policies'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]