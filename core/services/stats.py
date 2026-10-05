"""Nightly statistics batch (docs/06-IMPLEMENTATION-PLAN.md Phase 5).

Reads the identity store and writes the statistics store. The statistics tables
hold no names and no phone numbers, so dashboards can query them freely.

Three aggregates are produced:
    stats_placement        placement rate + response rate + grade mix (F-10)
    stats_wage_progression wage band per round + months retained (F-09)
    stats_skill_gap        2-of-3 signal flag per course x district (F-11)
"""

from __future__ import annotations

from collections import defaultdict
from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    DemandSnapshot,
    Definitions,
    Enrolment,
    OutcomeEvent,
    PLACEMENT_STATUSES,
    StatsPlacement,
    StatsSkillGap,
    StatsWageProgression,
)
from core.services.evidence import grade_at_or_above, grade_mix
from core.services.retention import retention_for
from core.services.weights import weight_for

GROUP_KEYS = ('course', 'provider', 'district')


def _latest_outcomes() -> list[OutcomeEvent]:
    """Current status = latest valid event per (person, reference date)."""
    latest: dict[tuple[str, object], OutcomeEvent] = {}
    for outcome in OutcomeEvent.objects.select_related('person').order_by(
        'person_id', 'reference_date', 'created_at'
    ):
        latest[(outcome.person_id, outcome.reference_date)] = outcome
    return list(latest.values())


def _due_counts(enrolments) -> tuple[dict, dict]:
    """Reached and due follow-ups per enrolment, from the job table (F-10)."""
    from core.models import FollowupJob

    reached: dict[int, int] = defaultdict(int)
    due: dict[int, int] = defaultdict(int)
    jobs = FollowupJob.objects.values(
        'person__enrolment_set__provider_id',
        'person__enrolment_set__qualification_id',
        'person__enrolment_set__person__district',
        'person_id',
        'status',
    )
    for job in jobs:
        key = (
            job['person__enrolment_set__qualification_id'],
            job['person__enrolment_set__provider_id'],
            job['person__enrolment_set__person__district'],
        )
        due[key] += 1
        if job['status'] == 'REPLIED':
            reached[key] += 1
    return reached, due


def compute_placement(min_grade: str = 'E0') -> int:
    """Fill stats_placement. Called once per grade floor so E2+ can be compared."""
    grades = grade_at_or_above(min_grade)
    latest = [o for o in _latest_outcomes() if o.evidence_level in grades]
    reached_map, due_map = _due_counts(None)

    enrolments = {
        (e.qualification_id, e.provider_id, e.person.district): e
        for e in Enrolment.objects.select_related('provider', 'qualification', 'person')
    }

    buckets: dict[tuple, list[OutcomeEvent]] = defaultdict(list)
    for outcome in latest:
        enrolment = outcome.person.enrolment_set.select_related(
            'qualification', 'provider'
        ).first()
        if enrolment is None:
            continue
        key = (enrolment.qualification_id, enrolment.provider_id, outcome.person.district)
        buckets[key].append(outcome)

    written = 0
    for key, rows in buckets.items():
        enrolment = enrolments.get(key)
        if enrolment is None:
            continue
        reached = reached_map.get(key, len(rows))
        due = due_map.get(key, reached)
        placed = sum(1 for row in rows if row.status in PLACEMENT_STATUSES)
        placement_rate = round(100.0 * placed / reached, 1) if reached else 0.0
        response_rate = round(100.0 * reached / due, 1) if due else 0.0
        weight_total = sum(weight_for(row) for row in rows)
        weighted = round(
            100.0 * sum(weight_for(r) for r in rows if r.status in PLACEMENT_STATUSES) / weight_total,
            1,
        ) if weight_total else 0.0

        StatsPlacement.objects.update_or_create(
            course=enrolment.qualification.course_name,
            provider=enrolment.provider.provider_id,
            district=enrolment.person.district,
            min_grade=min_grade,
            defaults={
                'cohort_total': due,
                'reached_total': reached,
                'placed_total': placed,
                'placement_rate': placement_rate,
                'response_rate': response_rate,
                'grade_mix': grade_mix(rows),
                'weighted_rate': weighted,
                'weighted_response_rate': response_rate,
            },
        )
        written += 1
    return written


def compute_wage_progression() -> int:
    rows: dict[tuple, dict] = defaultdict(
        lambda: {'t3': [], 't6': [], 't12': [], 'retention': [], 'people': 0}
    )
    enrolments = Enrolment.objects.select_related('provider', 'qualification', 'person')

    for enrolment in enrolments:
        key = (enrolment.qualification.course_name, enrolment.provider.provider_id,
               enrolment.person.district)
        stats = retention_for(enrolment.person)
        if not stats.employed_rounds:
            continue
        bucket = rows[key]
        bucket['people'] += 1
        bucket['retention'].append(stats.months_retained)
        for months, band in stats.wage_bands.items():
            if band and months in bucket:
                bucket[{'3': 't3', '6': 't6', '12': 't12'}[str(months)]].append(band)

    from collections import Counter

    for key, bucket in rows.items():
        course, provider, district = key
        StatsWageProgression.objects.update_or_create(
            course=course, provider=provider, district=district,
            defaults={
                'wage_t3': _modal(bucket['t3']),
                'wage_t6': _modal(bucket['t6']),
                'wage_t12': _modal(bucket['t12']),
                'retention_months': round(sum(bucket['retention']) / len(bucket['retention'])),
                'respondents': bucket['people'],
            },
        )
    return len(rows)


def _modal(values: list[str]) -> str | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def compute_skill_gap() -> int:
    """Flag a course only where at least 2 of the 3 signals agree (F-11)."""
    threshold = Definitions.min_signals()
    today = timezone.localdate()
    snapshot_date = DemandSnapshot.objects.order_by('-snapshot_date').values_list(
        'snapshot_date', flat=True
    ).first() or today

    latest = _latest_outcomes()

    # Signal 1: vacancies for the course occupation in the district.
    vacancies = {
        (row.district, row.occupation_code): row.vacancy_count
        for row in DemandSnapshot.objects.filter(snapshot_date=snapshot_date)
    }
    # Signal 3: trainee reason codes containing "skills lacking".
    trainee_signals: dict[tuple, int] = defaultdict(int)
    # Signal 2: employer "skills lacking" answers for course graduates.
    employer_signals: dict[tuple, int] = defaultdict(int)

    for outcome in latest:
        enrolment = outcome.person.enrolment_set.select_related(
            'qualification', 'provider'
        ).first()
        if enrolment is None:
            continue
        key = (enrolment.qualification.course_name, outcome.person.district)
        occupation = enrolment.qualification.occupation_code
        for codes in (outcome.reason_codes or {}).values():
            if 'skills_lacking' in codes:
                trainee_signals[key] += 1
        employer = getattr(outcome, 'employer', None)
        if employer is not None and employer.discrepancy_flags.get('skills_lacking'):
            employer_signals[key] += 1
        vacancies.setdefault((outcome.person.district, occupation), 0)

    courses: dict[tuple, dict] = {}
    for outcome in latest:
        enrolment = outcome.person.enrolment_set.select_related(
            'qualification', 'provider'
        ).first()
        if enrolment is None:
            continue
        key = (enrolment.qualification.course_name, outcome.person.district)
        entry = courses.setdefault(
            key,
            {
                'occupation': enrolment.qualification.occupation_code,
                'vacancy': vacancies.get((outcome.person.district, enrolment.qualification.occupation_code), 0),
                'employer': employer_signals.get(key, 0),
                'trainee': trainee_signals.get(key, 0),
            },
        )

    for (course, district), entry in courses.items():
        StatsSkillGap.objects.update_or_create(
            course=course,
            district=district,
            defaults={
                'occupation_code': entry['occupation'],
                'vacancy_count': entry['vacancy'],
                'employer_skills_lacking_count': entry['employer'],
                'trainee_skills_lacking_count': entry['trainee'],
                'demand_snapshot_date': snapshot_date,
            },
        )
        gap = StatsSkillGap.objects.get(course=course, district=district)
        gap.apply_threshold(threshold)
        gap.save()
    return len(courses)


def flagged_gaps():
    """Only courses where the signals agree are listed (F-11)."""
    return StatsSkillGap.objects.filter(gap_flag=True).order_by(
        '-signals_agree_count', 'course'
    )


def run_all(grades=('E0', 'E1', 'E2')) -> dict:
    result = {'placement_rows': 0, 'wage_rows': 0, 'skill_gap_rows': 0}
    for grade in grades:
        result['placement_rows'] += compute_placement(grade)
    result['wage_rows'] = compute_wage_progression()
    result['skill_gap_rows'] = compute_skill_gap()
    return result


def placement_cells(min_grade: str = 'E0'):
    """Dashboard rows, hiding any cell below the minimum group size k (F-10)."""
    k = Definitions.min_group_size()
    rows = StatsPlacement.objects.filter(min_grade=min_grade)
    return [row for row in rows if row.reached_total >= k], [
        row for row in rows if 0 < row.reached_total < k
    ]


def district_rows():
    """District roll-up for the policy dashboard (F-10)."""
    k = Definitions.min_group_size()
    out = []
    rows = (
        StatsPlacement.objects.filter(min_grade='E0')
        .values('district')
        .annotate(
            reached=Count('id'),
            placed=Count('id', filter=Q(placed_total__gt=0)),
        )
        .order_by('district')
    )
    for row in rows:
        if row['reached'] < k:
            continue
        out.append(row)
    return out