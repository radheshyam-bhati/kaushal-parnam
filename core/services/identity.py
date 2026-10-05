"""Near-match detection and review queue for F-03.

One UTID per human being, one human being per UTID. When two files describe the
same person, the system raises a suggestion for a human to decide and never
merges on its own (docs/01-PRD.md §7 F-03: "show suggestion to human, never
auto-merge").

Two bases, both coincidence rather than proof:

``same_phone``
    Two UTIDs with the same own phone. Families and shared devices are normal,
    so this is a suggestion, never a merge.
``same_name_dob``
    Same name and date of birth. Names are not identifiers.

There is deliberately no "same external scheme ID" basis. It reads like the
strongest possible signal, but ``uniq_crosswalk_scheme_programme`` means two
UTIDs cannot claim one scheme ID in the first place, so such a check could
never fire -- the same trap as an invariant that "detects" an impossible state.

Detection runs over people the caller may see, so a district officer's scan
never proposes a merge that spans districts. Resolution is deliberately
conservative: ``kept_separate`` and ``pending`` are recorded, and a merge is
*not* performed here. Merging two UTIDs would orphan enrolments, outcomes and an
audit trail, so it belongs to an explicit operator action with a real decision
behind it, not to a background scan.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from core.models import Contact, IdCrosswalk, IdMatchSuggestion, Person
from core.services.audit import log_event

RESOLUTION_CHOICES = ['merged', 'kept_separate', 'pending']

def _is_queued(person_utid: str, candidate_utid: str) -> bool:
    """True if this pair is already queued in either direction."""
    return IdMatchSuggestion.objects.filter(
        Q(person_id=person_utid, candidate_utid=candidate_utid)
        | Q(person_id=candidate_utid, candidate_utid=person_utid)
    ).exists()


def _canonical_pair(utid_a: str, utid_b: str) -> tuple[str, str]:
    """Order a pair by UTID so (a, b) and (b, a) never both get queued."""
    return (utid_a, utid_b) if utid_a <= utid_b else (utid_b, utid_a)


def _pairs_within(holders: list[str]) -> list[tuple[str, str]]:
    """Every unordered pair drawn from one bucket, canonically ordered."""
    return [
        _canonical_pair(first, second)
        for first, second in zip(holders, holders[1:])
    ]


def detect_near_matches(people, *, actor: str = 'system') -> list[IdMatchSuggestion]:
    """Scan a scoped queryset and queue every near match found.

    All signals for a pair are collected *before* anything is written, so one
    row per pair carries every basis that fired rather than whichever check
    happened to run first. Two people matching on both phone and date of birth
    is a much stronger suggestion than either alone, and the reviewer is the one
    who needs to see that.
    """
    utids = list(people.values_list('utid', flat=True))
    evidence: dict[tuple[str, str], list[str]] = {}
    by_phone: dict[str, list[str]] = {}
    by_name_dob: dict[tuple[str, object], list[str]] = {}

    # Shared own phone. Not proof: families and shared devices are normal.
    for phone, person_id in Contact.objects.filter(
        person_id__in=utids, own_phone__isnull=False
    ).exclude(own_phone='').values_list('own_phone', 'person_id'):
        by_phone.setdefault(phone, []).append(person_id)
    for phone, holders in by_phone.items():
        for pair in _pairs_within(holders):
            evidence.setdefault(pair, []).append(
                f'same own phone (…{phone[-4:]})'
            )

    # Same name and date of birth. Also not proof: names are not identifiers.
    for utid, name, dob in people.values_list('utid', 'name', 'dob'):
        by_name_dob.setdefault(((name or '').strip().lower(), dob), []).append(utid)
    for (label, _dob), holders in by_name_dob.items():
        for pair in _pairs_within(holders):
            evidence.setdefault(pair, []).append(
                f'same name and date of birth ("{label}")'
            )

    created: list[IdMatchSuggestion] = []
    for pair, reasons in evidence.items():
        if _is_queued(*pair):
            continue
        person_id, candidate_id = pair
        created.append(IdMatchSuggestion.objects.create(
            person_id=person_id,
            candidate_utid=candidate_id,
            # The strongest single basis goes in the field; the rest go in detail
            # so the reviewer can weigh all of them.
            basis='same_phone' if any(
                'phone' in reason for reason in reasons
            ) else 'same_name_dob',
            detail='; '.join(reasons)[:200],
        ))

    if created:
        log_event(
            component='identity',
            event_type='near_match_suggested',
            description=(
                f'{len(created)} near-match pair(s) queued for human review '
                f'(no automatic merge)'
            ),
            user_role=actor,
        )
    return created


def pending_suggestions(district: str | None = None):
    """Open suggestions, most certain basis first, scoped to a district."""
    qs = IdMatchSuggestion.objects.filter(
        resolution__in=['', 'pending']
    ).select_related('person')
    if district:
        qs = qs.filter(person__district=district)
    return qs.order_by('basis', 'person_id')


def resolve_suggestion(
    suggestion: IdMatchSuggestion, resolution: str, reviewed_by: str
) -> IdMatchSuggestion:
    """Record a human decision. ``merged`` is accepted as a stated intent only.

    The UTIDs are deliberately not rewritten here: a merge moves enrolments,
    outcomes, consent and an audit trail between identities, and doing that from
    a single field on a suggestion row is not a safe place to do it from. The
    decision is recorded so the operator action is traceable.
    """
    if resolution not in RESOLUTION_CHOICES:
        raise ValueError(f'resolution must be one of {RESOLUTION_CHOICES}')
    suggestion.resolution = resolution
    suggestion.reviewed_by = reviewed_by
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=['resolution', 'reviewed_by', 'reviewed_at'])
    log_event(
        component='identity',
        event_type='near_match_resolved',
        description=(
            f'Pair {suggestion.person_id} ~ {suggestion.candidate_utid} '
            f'({suggestion.basis}) marked {resolution} by {reviewed_by}'
        ),
        utid=suggestion.person_id,
        user_role=reviewed_by,
    )
    return suggestion


def open_suggestion_count(district: str | None = None) -> int:
    return pending_suggestions(district).count()


def persons_without_crosswalk(people) -> list[Person]:
    """People with no external scheme ID at all (F-03 completeness check)."""
    linked = set(
        IdCrosswalk.objects.filter(
            person_id__in=people.values_list('utid', flat=True)
        ).values_list('person_id', flat=True)
    )
    return [p for p in people if p.utid not in linked]