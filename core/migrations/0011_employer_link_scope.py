"""Re-install the RLS policies with the employer-link scope (F-10).

Follows ``0009_orm_compatible_policies``, which made the policies usable by the
Django ORM. That first cut gave the no-login employer confirmation link access to
the ``employer`` row only, which was not enough: the page renders the trainee's
name, the course they took, the centre and the wage band they quoted, so the
view loads ``outcome_event``, ``person`` and ``enrolment`` as well. With
``select_related`` the join to ``outcome_event`` came back empty and the
confirmation link 404'd.

So the ``employer`` branch is now on ``person``, ``enrolment`` and
``outcome_event`` too. It is the same narrow scope throughout -- the one outcome
named in the token this session presented, and the trainee it belongs to -- which
is exactly the set of facts the page already shows the employer. Nothing about it
is derived from anything the caller can choose beyond possessing the token.

The definitions live in ``core.rls_policies`` so this migration and ``0009``
cannot drift apart.
"""

from django.db import migrations

from core.rls_policies import statements


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in statements():
            if statement.strip():
                cursor.execute(statement)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_audit_trigger_request_id'),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]