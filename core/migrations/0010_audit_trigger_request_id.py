"""Let the audit trigger write audit rows (F-10).

The problem
-----------
``audit_log_trigger`` writes its row without a ``request_id``. Under the policies
from ``0009`` a session may insert an audit row when it is a policy officer, when
the row concerns its own UTID, or when the row carries the current
``app.request_id``. A trigger row has none of those in the common case: an officer
recording an outcome for a trainee in their district has no UTID of their own,
and the trigger left ``request_id`` NULL. So the very statement the trigger exists
to record -- ``INSERT`` or ``UPDATE`` on an identity table -- failed with::

    new row violates row-level security policy for table "audit_log"

raised from inside ``PL/pgSQL function audit_log_trigger``, which surfaced as a
500 on an otherwise valid request.

The fix
-------
Stamp the current ``app.request_id`` on trigger-written rows, the same way
``core.services.audit`` does for application-written ones. The row genuinely
belongs to the request that caused it, and that is exactly the relationship the
policy's third clause is expressing -- it is not a way around the check, because
the value is read from the session and cannot be chosen by the caller.

The trigger also defaults the role to ``'system'`` when ``app.user_role`` is
unset. That default stays: an unbound session writing an audit row still has to
satisfy the policy, and it now does so through the request id.
"""

from django.db import migrations

TRIGGER = """
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
    INSERT INTO audit_log (
        component, user_role, event_type, description, utid, timestamp, request_id
    )
    VALUES (
        TG_TABLE_NAME,
        COALESCE(NULLIF(current_setting('app.user_role', true), ''), 'system'),
        TG_OP,
        TG_OP || ' on ' || TG_TABLE_NAME
            || CASE WHEN row_utid IS NULL THEN '' ELSE ' for ' || row_utid END,
        row_utid,
        NOW(),
        NULLIF(current_setting('app.request_id', true), '')
    );
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(TRIGGER)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_orm_compatible_policies'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]