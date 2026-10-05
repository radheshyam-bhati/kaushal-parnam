"""Make the RLS policies usable by the Django ORM (F-10).

The blocker this migration removes
---------------------------------
``0002_rls_policies`` scoped every read through a set-returning helper that
reads the table being protected::

    CREATE POLICY rls_read ON outcome_event FOR SELECT
        USING (id IN (SELECT v.id FROM kp_visible_outcomes() AS v));

That design cannot be used with Django's ORM, for two reasons that only
interact:

1. Django issues every insert and most updates as
   ``INSERT/UPDATE ... RETURNING id``.
2. PostgreSQL checks the rows returned by ``RETURNING`` against the **SELECT**
   policy.
3. ``kp_visible_outcomes()`` is ``STABLE`` and reads ``outcome_event`` itself, so
   its snapshot cannot contain the row being written.

So the writing session is not allowed to read back its own row, and the write is
rejected: ``new row violates row-level security policy for table "outcome_event"``.
Measured on the demo data, 30 of 49 tests failed on fixture inserts and a real
enrolment would fail identically. Marking the helper ``VOLATILE`` fixes the
snapshot and immediately produces ``infinite recursion detected in policy``,
because the helper reads the table whose policy calls it. That is the trade-off
``0002`` was reaching for when it chose a helper in the first place, and neither
arm of it works.

The fix
-------
Express each policy so that it never consults a snapshot-limited function over
the table it protects:

* the caller's own row is a **direct column comparison** --
  ``person_id = current_setting('app.utid', true)``. A plain column comparison
  has no snapshot, so the row being written satisfies it and ``RETURNING``
  works;
* the cross-table cases go through small ``SECURITY DEFINER`` scalar helpers that
  read **other** tables (``enrolment``, ``person``, ``followup_job``). They are
  owned by the migration role and therefore bypass RLS, which is what keeps the
  policy graph acyclic -- the reason ``0002`` needed a set-returning helper in
  the first place, without the snapshot problem.

``kp_visible_people()`` and ``kp_visible_outcomes()`` are dropped. Nothing
references them after this migration.

Writes are scoped like reads rather than restricted to policy officers: you may
insert, change or remove exactly the rows you could read. That is both tighter
and less surprising than a role whitelist, and it is what lets the trainee
perform their own erasure (``OutcomeEvent.objects.anonymise()`` is an UPDATE on
one of their own rows) without granting officers a monopoly on writes.

Two application-side scopes, both bound by ``core.middleware.bind_scope``:

``app.utid`` re-bound after a UTID is generated
    ``/enrol/`` mints the trainee's UTID partway through the request. Until the
    scope is re-bound the new rows would fall outside the trainee's own scope and
    the insert would be refused.

``app.employer_outcome_id`` for the no-login employer link
    ``/employer/confirm/<token>/`` has no ``request.user``; the signed token is
    the authorisation. After the token is validated the view binds the one
    outcome that token covers, and the policy admits exactly that row and its
    employer, reason and audit rows. Nothing else about the request's identity
    changes.

Unset and empty roles still match nothing (see ``0005``), and the write policies
are still split per command (see ``0006``) so a write policy can never widen a
read.
"""

from django.db import migrations

from django.db import migrations

from core.rls_policies import statements as _statements


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in _statements(drop_helpers=True):
            if statement.strip():
                cursor.execute(statement)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_audit_log_request_scope'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]
