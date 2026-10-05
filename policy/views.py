"""MSInS policy officer views: dashboard, definitions registry, funding report.

Covers F-03 (definitions), F-10 (dashboards with reach), F-11 (skill-gap maps),
S-02 (expected vs actual), S-03 (outcome-funding report), S-05 (data-quality
score).
"""

from __future__ import annotations

import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import role_required
from core.models import (
    AuditSample,
    Definition,
    Definitions,
    DemandSnapshot,
    FollowupJob,
    IdMatchSuggestion,
    OutcomeEvent,
    Provider,
    PLACEMENT_STATUSES,
    StatsPlacement,
    StatsWageProgression,
)
from core.services.identity import (
    RESOLUTION_CHOICES,
    detect_near_matches,
    open_suggestion_count,
    pending_suggestions,
    resolve_suggestion,
)
from core.services.audit import log_event
from core.services.filters import (
    FILTER_KEYS,
    apply_filters,
    filter_options,
    selected_filters,
)
from core.services.evidence import (
    GRADE_FLOOR_LABELS,
    build_placement_rate,
    grade_mix,
)
from core.services.rls import visible_outcomes, visible_people
from core.services.stats import flagged_gaps
from core.services.weights import weighted_rate
from policy.comparison import provider_comparison, weights_note

POLICY_ROLES = ('policy_officer',)


@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def dashboard(request):
    """P-06. Province-wide view. Every rate carries its response rate and mix."""
    floor = (request.GET.get('min_grade') or 'E0').upper()

    from core.services.evidence import effective_grade

    # RLS first, then the cohort filter: a filter narrows the count, never the
    # scope (core.services.filters).
    scoped = visible_people(request.user)
    filters = selected_filters(request.GET)
    people = apply_filters(scoped, filters)
    outcomes = visible_outcomes(request.user).filter(person__in=people)

    graded = [o for o in outcomes if effective_grade(o) in _grades(floor)]
    # "Due" is every outcome round owed to the cohort in view, so the response
    # rate stays honest when a district or course filter is applied.
    due = FollowupJob.objects.filter(
        person__in=people, due_at__lte=timezone.now()
    ).count()
    reached = len(graded)

    rate = build_placement_rate(graded, reached=reached, due=due, label='placement', floor=floor)
    weights = weighted_rate(graded, reached=reached, due=due)

    districts = _district_rollup(graded, people, floor)
    gaps = list(flagged_gaps())
    providers = list(Provider.objects.all())

    return render(request, 'policy/dashboard.html', {
        'floor': floor,
        'grade_floors': GRADE_FLOOR_LABELS,
        'rate': rate,
        'weights': weights,
        'people_count': people.count(),
        'outcome_count': outcomes.count(),
        'e2_plus': sum(1 for o in outcomes if effective_grade(o) in ('E2', 'E3', 'E4')),
        'districts': districts,
        'skill_gaps': gaps,
        'gaps_3of3': [g for g in gaps if g.signals_agree_count == 3],
        'gaps_2of3': [g for g in gaps if g.signals_agree_count == 2],
        'providers': providers,
        'min_group_size': Definitions.min_group_size(),
        'demographics': _demographics(people),
        'definitions': _definition_rows(),
        'filters': filters,
        'filter_options': filter_options(scoped),
        'filter_keys': FILTER_KEYS,
    })


def _grades(floor: str) -> list[str]:
    from core.services.evidence import grade_at_or_above

    return grade_at_or_above(floor)


def _definition_rows() -> list[Definition]:
    return list(Definition.objects.all().order_by('key'))


def _district_rollup(outcomes, people, floor: str) -> list[dict]:
    k = Definitions.min_group_size()
    by_district: dict[str, list] = {}
    for outcome in outcomes:
        by_district.setdefault(outcome.person.district, []).append(outcome)

    total_people = {}
    for person in people:
        total_people[person.district] = total_people.get(person.district, 0) + 1

    rows = []
    for district, rows_in in sorted(by_district.items()):
        reached = len(rows_in)
        placed = sum(1 for o in rows_in if o.status in PLACEMENT_STATUSES)
        headcount = total_people.get(district, 0)
        rows.append({
            'district': district,
            'trainees': headcount,
            'reached': reached,
            'response_rate': round(100.0 * reached / headcount, 1) if headcount else 0.0,
            'placement_rate': round(100.0 * placed / reached, 1) if reached else 0.0,
            'grade_mix': grade_mix(rows_in),
            'withheld': reached < k,
        })
    return rows


def _demographics(people) -> dict:
    """Cohort composition for the filter bar (F-10).

    Counts, not just the set of values: a policy officer needs to see how thin a
    slice is before trusting a rate cut from it.
    """
    from core.services.filters import AGE_BANDS

    rows = list(people.only('gender', 'category', 'dob'))
    gender: dict[str, int] = {}
    category: dict[str, int] = {}
    age_band: dict[str, int] = {band: 0 for band in AGE_BANDS}
    for person in rows:
        gender[person.gender] = gender.get(person.gender, 0) + 1
        category[person.category] = category.get(person.category, 0) + 1
        age_band[person.age_band] = age_band.get(person.age_band, 0) + 1
    return {
        'gender': sorted(gender.items()),
        'category': sorted(category.items()),
        'age_band': [(band, count) for band, count in age_band.items() if count],
        'total': len(rows),
    }


# ===========================================================================
# Definitions registry (F-03, P-09)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def definitions(request):
    """P-09. Edit the retention rule, break rule and flag thresholds."""
    rows = {row.key: row for row in Definition.objects.all()}
    if request.method == 'POST':
        changed = []
        for key, row in rows.items():
            raw = request.POST.get(key, '').strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                messages.error(
                    request,
                    f'{key}: value must be valid JSON, for example '
                    f'{{"break_days": 60}}.',
                )
                return redirect('policy:definitions')
            if value != row.value:
                row.value = value
                row.updated_by = request.user.username
                row.save(update_fields=['value', 'updated_by'])
                changed.append(key)
        if changed:
            log_event(
                component='definitions', event_type='definition_update',
                description=f'Updated definitions: {", ".join(changed)}. '
                            'All dashboards recompute from the new rules.',
                user_role=request.user.role,
            )
            messages.success(
                request,
                f'Updated {", ".join(changed)}. Wage and retention views now use '
                f'the new rule.',
            )
        else:
            messages.info(request, 'No changes to save.')
        return redirect('policy:definitions')

    return render(request, 'policy/definitions.html', {
        'rows': sorted(rows.values(), key=lambda r: r.key),
        'break_days': Definitions.break_days(),
        'min_signals': Definitions.min_signals(),
        'min_group_size': Definitions.min_group_size(),
        'round_intervals': Definitions.round_intervals(),
    })


# ===========================================================================
# Expected vs actual provider comparison (S-02)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def provider_view(request):
    rows = provider_comparison(request.user)
    return render(request, 'policy/provider_comparison.html', {
        'rows': rows,
        'note': weights_note(request.user),
        'min_group_size': Definitions.min_group_size(),
    })


# ===========================================================================
# Skill-gap maps (F-11)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def skill_gap_view(request):
    gaps = list(flagged_gaps())
    # Read the newest snapshot row once. Calling .first() again and dereferencing
    # it is unsafe: with no demand data at all it returns None.
    newest = DemandSnapshot.objects.order_by('-snapshot_date').first()
    return render(request, 'policy/skill_gap.html', {
        'all_gaps': gaps,
        'red': [g for g in gaps if g.signals_agree_count == 3],
        'orange': [g for g in gaps if g.signals_agree_count == 2],
        'snapshot_date': newest.snapshot_date if newest else None,
        'threshold': Definitions.min_signals(),
        'is_stale': newest.is_stale if newest else False,
    })


# ===========================================================================
# Outcome-funding report (S-03) -- E2+ only, never E0/E1
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def funding_report(request):
    """S-03. E2+ outcomes with reach and audit result. Never pays on E0/E1."""
    rows = []
    for cell in StatsPlacement.objects.filter(min_grade='E2').order_by('-placed_total'):
        samples = AuditSample.objects.filter(
            outcome__person__enrolment_set__provider__provider_id=cell.provider,
            sample_type='CLAIMED_JOB',
        )
        called = samples.count()
        verified = samples.filter(result_verified=True).count()
        rows.append({
            'course': cell.course,
            'provider': cell.provider,
            'district': cell.district,
            'e2_plus': cell.placed_total,
            'reached': cell.reached_total,
            'response_rate': cell.response_rate,
            'weighted_rate': cell.weighted_rate,
            'audit_called': called,
            'audit_verified': verified,
            'audit_rate': round(100.0 * verified / called, 1) if called else None,
        })
    return render(request, 'policy/funding_report.html', {
        'rows': rows,
        'total_e2': sum(row['e2_plus'] for row in rows),
        'never_included': 'E0 self-reports and E1 officer calls are never counted here.',
    })


@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def funding_export(request):
    """CSV export for a Skills Outcomes Fund-style proposal."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="outcome-funding-report.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'course', 'provider', 'district', 'e2_plus_outcomes', 'response_rate',
        'weighted_placement_rate', 'audit_called', 'audit_verified',
    ])
    for cell in StatsPlacement.objects.filter(min_grade='E2').order_by('-placed_total'):
        samples = AuditSample.objects.filter(
            outcome__person__enrolment_set__provider__provider_id=cell.provider,
            sample_type='CLAIMED_JOB',
        )
        writer.writerow([
            cell.course, cell.provider, cell.district, cell.placed_total,
            cell.response_rate, cell.weighted_rate,
            samples.count(), samples.filter(result_verified=True).count(),
        ])
    log_event(
        component='funding', event_type='funding_report_export',
        description='Outcome-funding report exported as CSV',
        user_role=request.user.role,
    )
    return response


# ===========================================================================
# Data-quality score (S-05)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def data_quality(request):
    from analytics.services import compute_quality_scores

    scores = compute_quality_scores()
    return render(request, 'policy/data_quality.html', {
        'scores': scores,
        'green': [s for s in scores if s.band() == 'green'],
        'amber': [s for s in scores if s.band() == 'amber'],
        'red': [s for s in scores if s.band() == 'red'],
    })


# ===========================================================================
# Wage and retention view (F-09)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def wage_retention(request):
    rows = StatsWageProgression.objects.all().order_by('-retention_months')
    return render(request, 'policy/wage_retention.html', {
        'rows': rows,
        'break_days': Definitions.break_days(),
        'min_employment_days': Definitions.min_employment_days(),
    })


# ===========================================================================
# Identity near-match review queue (F-03)
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def match_queue(request):
    """F-03: suggest, never auto-merge. A human decides every pair."""
    queued = 0
    if request.method == 'POST' and request.POST.get('action') == 'scan':
        queued = len(detect_near_matches(
            visible_people(request.user), actor=request.user.role
        ))
        messages.success(
            request,
            f'Scan complete: {queued} new near-match pair(s) queued for review.'
            if queued else
            'Scan complete: no new near matches. Existing pairs are untouched.',
        )
        return redirect('policy:match_queue')

    return render(request, 'policy/match_queue.html', {
        'rows': pending_suggestions(),
        'open_count': open_suggestion_count(),
        'resolutions': RESOLUTION_CHOICES,
        'queued_now': queued,
    })


@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
@require_POST
def resolve_match(request, suggestion_id):
    suggestion = get_object_or_404(IdMatchSuggestion, pk=suggestion_id)
    resolution = request.POST.get('resolution', '')
    if resolution not in RESOLUTION_CHOICES:
        messages.error(request, 'Choose a resolution.')
        return redirect('policy:match_queue')
    resolve_suggestion(suggestion, resolution, request.user.username)
    messages.success(
        request,
        f'{suggestion.person_id} ~ {suggestion.candidate_utid} marked '
        f'{resolution.replace("_", " ")}.',
    )
    return redirect('policy:match_queue')


# ===========================================================================
# Audit trail
# ===========================================================================
@login_required
@role_required(*POLICY_ROLES, message='Only MSInS policy officers can open this page.')
def audit_trail(request):
    from core.models import AuditLog

    rows = AuditLog.objects.all()[:200]
    return render(request, 'policy/audit_trail.html', {
        'rows': rows,
        'total': AuditLog.objects.count(),
    })