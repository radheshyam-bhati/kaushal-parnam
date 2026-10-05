"""Let a session read back audit rows it wrote in this request (F-10).

Follows ``0007_audit_log_self_read``, which was not sufficient on its own.

Why the extra clause
-------------------
Django issues every insert as ``INSERT ... RETURNING id``, and PostgreSQL checks
the rows returned by ``RETURNING`` against the table's **SELECT** policy. So the
writing session must also be allowed to *read* the row. Verified directly::

    INSERT INTO audit_log (...) VALUES (...);              -- passes RLS
    INSERT INTO audit_log (...) VALUES (...) RETURNING id; -- rejected

``0007`` allowed reading your own ``utid``, which covers the "Your audit trail"
card on the "My Data" page but not rows with a NULL ``utid`` -- and login events,
which are exactly the rows written before a session exists. Login was therefore
the first thing to fail.

The clause added here scopes on ``app.request_id``. ``RLSMiddleware`` now
generates a request id when the client did not send ``X-Request-Id`` and writes it
into ``request.META``, so ``core.services.audit`` stamps the same value on every
row and a session can read back only what it wrote *in this request*. Nothing
here widens who can read whose history.

Background work (the follow-up scheduler, management commands) connects as
``kp_worker``, which bypasses RLS, so its audit rows need no such allowance.
"""

from django.db import migrations

READ_SCOPE = """
DROP POLICY IF EXISTS rls_read ON audit_log;
CREATE POLICY rls_read ON audit_log FOR SELECT USING (
    current_setting('app.user_role', true) IN ('policy_officer','system')
    -- the trainee's own trail on the "My Data" page
    OR utid = current_setting('app.utid', true)
    -- rows this session just wrote; required because PostgreSQL applies the
    -- SELECT policy to the RETURNING clause of an INSERT
    OR (request_id IS NOT NULL
        AND request_id = current_setting('app.request_id', true))
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
        ('core', '0007_audit_log_self_read'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]