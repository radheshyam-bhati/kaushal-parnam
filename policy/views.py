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
from django.shortcuts import redirect, render

from core.decorators import role_required
from core.models import (
    AuditSample,
    Definition,
    Definitions,
    DemandSnapshot,
    OutcomeEvent,
    Provider,
    PLACEMENT_STATUSES,
    StatsPlacement,
    StatsWageProgression,
)
from core.services.audit import log_event
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
    people = visible_people(request.user)
    outcomes = visible_outcomes(request.user)
    floor = (request.GET.get('min_grade') or 'E0').upper()

    from core.services.evidence import effective_grade

    graded = [o for o in outcomes if effective_grade(o) in _grades(floor)]
    due = OutcomeEvent.objects.count()  # every outcome implies at least one due round
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
    return {
        'gender': list(people.values('gender').order_by('gender')),
        'category': list(people.values('category').order_by('category')),
        'age_band': sorted({p.age_band for p in people}),
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