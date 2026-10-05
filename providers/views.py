"""Provider views: own-cohort dashboard (F-10) and employer one-tap (F-06).

Every queryset is built from ``core.services.rls`` scope helpers, so a
provider who hand-crafts a URL still cannot read another provider's rows
(docs/01-PRD.md F-10 acceptance criteria).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Employer, OutcomeEvent, StatsPlacement, WAGE_BAND_LABELS
from core.services.audit import log_event
from core.middleware import bind_scope
from core.services.employer_link import read_confirm_token, record_confirmation
from core.services.evidence import (
    GRADE_FLOOR_LABELS,
    build_placement_rate,
    effective_grade,
    filter_by_grade,
)
from core.services.filters import apply_filters, filter_options, selected_filters
from core.services.rls import visible_outcomes, visible_people
from core.services.retention import cohort_retention
from core.services.stats import flagged_gaps
from core.services.weights import weighted_rate


def _min_grade(request) -> str:
    return (request.GET.get('min_grade') or 'E0').upper()


def _k() -> int:
    from core.models import Definitions

    return Definitions.min_group_size()


@login_required
def dashboard(request):
    """P-04. Provider dashboard, own cohorts only."""
    if not request.user.is_provider_side:
        raise PermissionDenied('This dashboard is for training providers.')

    scoped = visible_people(request.user)
    filters = selected_filters(request.GET)
    people = apply_filters(scoped, filters)
    floor = _min_grade(request)
    outcomes = list(filter_by_grade(
        visible_outcomes(request.user).filter(person__in=people), floor
    ))
    placements = [
        o for o in outcomes if effective_grade(o) in _grades(floor)
    ]

    due = _due_count(people)
    rate = build_placement_rate(
        placements, reached=len(placements), due=due, label='placement', floor=floor
    )
    weights = weighted_rate(placements, reached=len(placements), due=due)

    reasons = _reason_counts(request.user, floor)
    skill_gaps = [
        gap for gap in flagged_gaps()
        if gap.district in _districts(people)
    ]

    return render(request, 'providers/dashboard.html', {
        'floor': floor,
        'grade_floors': GRADE_FLOOR_LABELS,
        'rate': rate,
        'weights': weights,
        'people_count': people.count(),
        'due_count': due,
        'retention': cohort_retention(people[:80]),
        'reasons': reasons,
        'skill_gaps': skill_gaps,
        'min_group_size': _k(),
        'withheld_groups': _withheld(request.user, floor),
        'wage_labels': WAGE_BAND_LABELS,
        'filters': filters,
        'filter_options': filter_options(scoped),
    })


def _grades(floor: str) -> list[str]:
    from core.services.evidence import grade_at_or_above

    return grade_at_or_above(floor)


def _due_count(people) -> int:
    from core.models import FollowupJob

    jobs = FollowupJob.objects.filter(
        person__in=people, due_at__lte=_now()
    )
    return jobs.count()


def _now():
    from django.utils import timezone

    return timezone.now()


def _districts(people) -> set[str]:
    return set(people.values_list('district', flat=True).distinct())


def _reason_counts(user, floor: str) -> list[dict]:
    from core.models import REASON_LABELS

    counts: dict[tuple[str, str], int] = {}
    for outcome in filter_by_grade(visible_outcomes(user), floor):
        for group, codes in (outcome.reason_codes or {}).items():
            for code in codes:
                key = (group, code)
                counts[key] = counts.get(key, 0) + 1
    return sorted(
        [
            {
                'group': group,
                'code': code,
                'label': REASON_LABELS.get(code, code),
                'count': count,
            }
            for (group, code), count in counts.items()
        ],
        key=lambda row: -row['count'],
    )


def _withheld(user, floor: str) -> list[dict]:
    """Cells below the minimum group size k are never published (F-10)."""
    k = _k()
    rows = (
        StatsPlacement.objects.filter(min_grade=floor)
        .exclude(provider=user.provider_id)
    )
    return [
        {'course': row.course, 'district': row.district, 'reached': row.reached_total}
        for row in rows
        if 0 < row.reached_total < k
    ]





# ===========================================================================
# Employer one-tap confirm (F-06) -- no login required
# ===========================================================================
def employer_confirm(request, token):
    """P-10 style pre-filled page. The employer taps Confirm; no account needed.

    There is no ``request.user`` here, so the RLS scope is established from the
    token itself: it is signed and carries the outcome id, so the view can bind
    the scope to that one outcome before reading anything, and the policies admit
    exactly that row and the employer, reason and audit rows attached to it.
    """
    outcome_id = read_confirm_token(token)
    if outcome_id is None:
        raise Http404('This confirmation link is not valid.')

    bind_scope(user_role='employer', employer_outcome_id=outcome_id)

    employer = get_object_or_404(
        Employer.objects.select_related('outcome'), confirm_token=token
    )
    outcome = employer.outcome
    if request.method == 'POST':
        still_employed = request.POST.get('still_employed') == 'yes'
        wage_verified = request.POST.get('wage_verified') or None
        result = record_confirmation(
            employer,
            still_employed=still_employed,
            wage_verified=wage_verified,
            skills_lacking=request.POST.get('skills_lacking') == 'yes',
            note=request.POST.get('note', '')[:300],
        )
        messages.success(request, 'Thank you. Your confirmation is recorded.')
        return render(request, 'providers/employer_confirmed.html', {
            'employer': employer, 'outcome': outcome, 'result': result,
        })

    return render(request, 'providers/employer_confirm.html', {
        'employer': employer,
        'outcome': outcome,
        'person': outcome.person,
        'enrolment': outcome.person.enrolment_set.select_related(
            'provider', 'qualification'
        ).first(),
        'wage_labels': WAGE_BAND_LABELS,
    })


@login_required
def send_employer_link(request, event_id):
    """Officer or coordinator nudges the employer link (still a stub send)."""
    outcome = get_object_or_404(OutcomeEvent, pk=event_id)
    employer, _created = Employer.objects.get_or_create(
        outcome=outcome,
        defaults={
            'employer_name': outcome.employer_name or 'Unnamed employer',
            'wage_claimed': outcome.wage_band,
        },
    )
    from core.services.employer_link import employer_sms_body, prepare_employer_link

    prepare_employer_link(outcome)
    body = employer_sms_body(employer, outcome)
    log_event(
        component='employer', event_type='employer_check_sent',
        description=f'Employer confirm link sent for outcome {outcome.pk} (SANDBOX)',
        utid=outcome.person_id, user_role=request.user.role,
    )
    messages.info(request, f'Demo send to {employer.employer_phone}: {body}')
    return redirect(request.META.get('HTTP_REFERER', '/'))