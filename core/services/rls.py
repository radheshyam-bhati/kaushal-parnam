"""Query-layer row isolation.

On PostgreSQL the database enforces isolation through the policies installed in
``core/migrations/0002_rls_policies.py``. These helpers apply the same scope
in the ORM so the behaviour is identical on SQLite and so that a provider can
never widen their own view by hand-editing a query (F-10).

Scopes (docs/08-Architecture.md §5.3):
  trainee         -> own UTID only
  provider        -> trainees enrolled at their own provider_id
  provider_coord  -> same as provider
  district_officer-> all trainees in their district
  policy_officer  -> everyone
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from accounts.models import Role

SCOPE_ALL = 'all'
SCOPE_OWN_UTID = 'own_utid'
SCOPE_PROVIDER = 'provider'
SCOPE_DISTRICT = 'district'


def scope_for(user) -> str:
    if user is None or not getattr(user, 'is_authenticated', False):
        return SCOPE_OWN_UTID
    if user.role == Role.POLICY_OFFICER:
        return SCOPE_ALL
    if user.role == Role.DISTRICT_OFFICER:
        return SCOPE_DISTRICT
    if user.is_provider_side:
        return SCOPE_PROVIDER
    return SCOPE_OWN_UTID


def visible_people(user) -> QuerySet:
    """People the signed-in user may read."""
    from core.models import Person

    qs = Person.objects.all()
    scope = scope_for(user)
    if scope == SCOPE_ALL:
        return qs
    if scope == SCOPE_OWN_UTID:
        return qs.filter(utid=user.utid) if user.utid else qs.none()
    if scope == SCOPE_PROVIDER:
        if not user.provider_id:
            return qs.none()
        return qs.filter(enrolment_set__provider_id=user.provider_id).distinct()
    if not user.district:
        return qs.none()
    return qs.filter(district=user.district)


def visible_outcomes(user) -> QuerySet:
    """Outcome events the signed-in user may read."""
    from core.models import OutcomeEvent

    return OutcomeEvent.objects.filter(person__in=visible_people(user))


def can_view_person(user, person) -> bool:
    return visible_people(user).filter(pk=person.pk).exists()


def record_hidden_access(user, kind: str, reference: object) -> None:
    """Note that a session asked for a row it is not allowed to see.

    With the database policies enforced, an out-of-scope row is invisible rather
    than forbidden, so the honest answer to "give me this record" is 404 -- it
    does not confirm the record exists. That is better than the 403 this used to
    return, but it would otherwise mean a cross-tenant attempt left no trace,
    and DPDP s.8(6) wants those recorded. The attempt is logged here instead.
    """
    from core.services.audit import log_event

    log_event(
        component='rls',
        event_type='rls_violation_attempt',
        description=(
            f'{getattr(user, "role", "anonymous")} asked for {kind} {reference}, '
            f'which is outside its scope'
        ),
        user_role=getattr(user, 'role', 'anonymous'),
    )


def assert_can_view_person(user, person) -> None:
    from django.core.exceptions import PermissionDenied

    if not can_view_person(user, person):
        from core.services.audit import log_event

        log_event(
            component='rls',
            event_type='rls_violation_attempt',
            description=(
                f'{getattr(user, "role", "anonymous")} tried to read {person.pk}'
            ),
            utid=person.pk,
            user_role=getattr(user, 'role', 'anonymous'),
        )
        raise PermissionDenied(
            f'Access denied: you cannot see trainee {person.pk}.'
        )


def k_anonymous(value: int, minimum: int) -> bool:
    """True when a group is big enough to publish (F-10, k >= 5 by default)."""
    return value >= minimum


def suppressed_groups(values: dict[str, int], minimum: int) -> list[str]:
    """Keys whose cell size is below k and must be shown as withheld."""
    return [key for key, count in values.items() if count < minimum]


def own_or_all(user, queryset, provider_field: str = 'provider__provider_id'):
    """Utility for provider-scoped manager querysets."""
    scope = scope_for(user)
    if scope == SCOPE_ALL:
        return queryset
    if scope == SCOPE_PROVIDER and user.provider_id:
        return queryset.filter(**{provider_field: user.provider_id})
    if scope == SCOPE_DISTRICT and user.district:
        return queryset.filter(district=user.district)
    if scope == SCOPE_OWN_UTID and user.utid:
        return queryset.filter(Q(person__utid=user.utid) | Q(person=None))
    return queryset.none()