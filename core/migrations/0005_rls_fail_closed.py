"""Make the RLS scope functions fail closed (F-10).

Why this migration exists
-------------------------
``0002_rls_policies`` granted full visibility when ``app.user_role`` was the
empty string:

    current_setting('app.user_role', true) IN ('policy_officer','system','')

That looks like a convenience for "no role set yet" and is the opposite of safe.
``current_setting(..., true)`` returns NULL for an unset variable but ``''`` for
a variable that was set to empty, and both are matched by ``IN`` differently
from what the author intended:

* unset (NULL)      -> every branch false -> **no rows**. Fails closed.
* set to ``''``     -> the first branch true -> **every row**.

``RLSMiddleware`` writes ``app.user_role`` on every request, defaulting to
``'anonymous'``, and ``core/services/rls.py`` never had to care. But the settings
are applied with ``set_config(..., true)``, which is transaction-local: under
autocommit they are discarded the instant the cursor closes, and
``current_setting('app.user_role', true)`` then reads ``''``. Measured on the
demo data, every request would have seen all 501 person rows instead of the one
belonging to the trainee.

Two changes, both needed:

1. Only ``policy_officer`` and ``system`` are privileged. An empty or unset
   role sees nothing. Management commands and migrations are unaffected because
   they connect as the table owner, and the owner bypasses RLS regardless --
   which is why FORCE ROW LEVEL SECURITY must stay off (see 0002's docstring).
2. ``system`` is retained for background work that runs outside a request and so
   has no HTTP role to set. It is a named privilege rather than a default one.

``ATOMIC_REQUESTS = True`` (core/env.py) is the other half of the fix and is not
expressible in SQL: without an enclosing transaction there is nothing for
``set_config(..., true)`` to stay scoped to.
"""

from django.db import migrations

SCOPE_FUNCTIONS = """
-- Which UTIDs may the current session read?
-- RETURNS TABLE names the output column, so policies can say v.utid.
--
-- Fail closed: an unset (NULL) or empty app.user_role matches no branch and so
-- matches no rows. Only these two named roles see broadly. The empty string is
-- deliberately NOT privileged -- see the module docstring.
CREATE OR REPLACE FUNCTION kp_visible_people() RETURNS TABLE(utid text)
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
    SELECT p.utid FROM person p
    WHERE
        -- policy officers see everyone; the table owner bypasses RLS anyway
        current_setting('app.user_role', true) IN ('policy_officer','system')
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
        current_setting('app.user_role', true) IN ('policy_officer','system')
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


def _run(statements, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            if statement.strip():
                cursor.execute(statement)


def apply_scope_functions(apps, schema_editor):
    _run([SCOPE_FUNCTIONS], schema_editor)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_alter_idmatchsuggestion_basis'),
    ]

    operations = [
        migrations.RunPython(apply_scope_functions, noop),
    ]