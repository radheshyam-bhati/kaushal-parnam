"""Wage progression and retention (F-09).

Break rule comes from the definitions registry (F-03): default "employed for at
least 365 days with any gap no longer than 60 days counts as retained". Change
``BREAK_RULE.break_days`` in the registry and every retention figure updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import Definitions, OutcomeEvent, PLACEMENT_STATUSES

ROUND_MONTHS = {1: 3, 2: 6, 3: 9, 4: 12}


@dataclass
class Retention:
    utid: str
    employed_rounds: list[int] = field(default_factory=list)
    months_retained: int = 0
    longest_employment_months: int = 0
    gaps: list[str] = field(default_factory=list)
    wage_bands: dict[int, str] = field(default_factory=dict)
    break_days_allowed: int = 60

    @property
    def is_retained(self) -> bool:
        """Retained = enough months employed with no gap longer than the rule."""
        minimum_days = Definitions.min_employment_days()
        return (
            self.longest_employment_months * 30 >= minimum_days
            and not self.gaps
        )


def _round_for_months(months: int) -> int | None:
    for round_no, round_months in ROUND_MONTHS.items():
        if round_months == months:
            return round_no
    return None


def retention_for(person, rounds=(1, 2, 3, 4)) -> Retention:
    """Compute consecutive employment and gaps across follow-up rounds."""
    break_days = Definitions.break_days()
    result = Retention(utid=person.pk, break_days_allowed=break_days)

    # Current status = latest valid event per (person, reference date).
    latest: dict = {}
    for outcome in OutcomeEvent.objects.filter(person=person).order_by(
        'reference_date', 'created_at'
    ):
        latest[outcome.reference_date] = outcome

    by_round: dict[int, object] = {}
    for outcome in latest.values():
        if outcome.status == 'could_not_be_reached':
            continue
        job = outcome.followup_job
        if job is not None:
            by_round[job.round] = outcome
        else:
            # Manual / officer-entered events map to the nearest round.
            months = _approx_months_after(person, outcome.reference_date)
            round_no = _round_for_months(months)
            if round_no:
                by_round.setdefault(round_no, outcome)

    previous_employed = False
    for round_no in sorted(rounds):
        outcome = by_round.get(round_no)
        if outcome is None:
            continue
        if outcome.status in PLACEMENT_STATUSES:
            result.employed_rounds.append(round_no)
            result.wage_bands[round_no] = outcome.wage_band or ''
            if previous_employed:
                pass
            else:
                result.longest_employment_months = ROUND_MONTHS[round_no]
            previous_employed = True
        else:
            if previous_employed and result.employed_rounds:
                start_months = ROUND_MONTHS[result.employed_rounds[-1]]
                gap_months = ROUND_MONTHS[round_no] - start_months
                if gap_months * 30 > break_days:
                    result.gaps.append(
                        f'Not working at round {round_no} '
                        f'(T+{ROUND_MONTHS[round_no]}m) after T+{start_months}m'
                    )
                else:
                    result.longest_employment_months = max(
                        result.longest_employment_months, ROUND_MONTHS[round_no]
                    )
            previous_employed = False

    result.months_retained = result.longest_employment_months
    return result


def _approx_months_after(person, reference_date) -> int:
    enrolment = person.enrolment_set.order_by('-course_start_date').first()
    if enrolment is None:
        return 12
    start = enrolment.reference_date or enrolment.course_start_date
    days = (reference_date - start).days
    return max(1, round(days / 30.4))


def cohort_retention(people) -> dict:
    """Aggregate months retained over a cohort, for the provider dashboard."""
    total = 0
    count = 0
    for person in people:
        stats = retention_for(person)
        if stats.employed_rounds:
            count += 1
            total += stats.months_retained
    return {
        'respondents': count,
        'average_months_retained': round(total / count, 1) if count else 0.0,
        'break_days_allowed': Definitions.break_days(),
    }


def wage_progression(person) -> dict[int, str | None]:
    """Wage band per round: T+3, T+6, T+12 (F-09 acceptance criteria)."""
    out: dict[int, str | None] = {3: None, 6: None, 12: None}
    for outcome in OutcomeEvent.objects.filter(person=person).order_by('reference_date'):
        job = outcome.followup_job
        if job is None:
            continue
        months = ROUND_MONTHS.get(job.round)
        if months in out and outcome.status in PLACEMENT_STATUSES:
            out[months] = outcome.wage_band
    return out