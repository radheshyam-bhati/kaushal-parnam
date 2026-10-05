"""Trust grades E0-E4 (F-07) and trust-grade filtering.

A rate is only meaningful next to how it was checked. Every helper here returns
the reach context (response rate) and the grade mix so the dashboard can never
render a bare percentage (docs/01-PRD.md F-10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from core.models import EVIDENCE_ORDER, PLACEMENT_STATUSES

GRADE_FLOOR_LABELS = {
    'E0': 'All self-reports (E0+)',
    'E1': 'Officer confirmed and above (E1+)',
    'E2': 'Employer confirmed and above (E2+)',
    'E3': 'Document seen and above (E3+)',
    'E4': 'Record matched only (E4)',
}


def grade_at_or_above(floor: str) -> list[str]:
    """Grades included when the dashboard filter is set to ``floor``."""
    floor = (floor or 'E0').upper()
    if floor not in EVIDENCE_ORDER:
        floor = 'E0'
    return EVIDENCE_ORDER[EVIDENCE_ORDER.index(floor):]


def filter_by_grade(queryset, floor: str = 'E0'):
    return queryset.filter(evidence_level__in=grade_at_or_above(floor))


def grade_mix(outcomes: Iterable) -> dict[str, int]:
    """Count of outcomes per grade. Always returns all five keys."""
    mix = {grade: 0 for grade in EVIDENCE_ORDER}
    for outcome in outcomes:
        if outcome.evidence_level in mix:
            mix[outcome.evidence_level] += 1
    return mix


def grade_mix_percent(mix: dict[str, int]) -> dict[str, float]:
    total = sum(mix.values())
    if not total:
        return {grade: 0.0 for grade in mix}
    return {grade: round(100.0 * count / total, 1) for grade, count in mix.items()}


@dataclass
class ReachRate:
    """A rate plus everything needed to judge it honestly.

    Never render ``value`` on its own: pass the whole object to
    templates/dashboard/_rate.html.
    """

    label: str
    numerator: int
    denominator: int
    response_numerator: int
    response_denominator: int
    mix: dict[str, int] = field(default_factory=dict)
    floor: str = 'E0'
    note: str = ''

    @property
    def value(self) -> float:
        if not self.denominator:
            return 0.0
        return round(100.0 * self.numerator / self.denominator, 1)

    @property
    def response_rate(self) -> float:
        if not self.response_denominator:
            return 0.0
        return round(100.0 * self.response_numerator / self.response_denominator, 1)

    @property
    def mix_percent(self) -> dict[str, float]:
        return grade_mix_percent(self.mix)

    @property
    def is_low_reach(self) -> bool:
        return self.response_denominator > 0 and self.response_rate < 30.0

    @property
    def display(self) -> str:
        return f'{self.value:.0f}% ({self.label})'

    @property
    def display_with_reach(self) -> str:
        return f'{self.value:.0f}% {self.label} (response rate: {self.response_rate:.0f}%)'

    @property
    def mix_display(self) -> str:
        pct = self.mix_percent
        return ', '.join(f'{grade}: {pct[grade]:.0f}%' for grade in EVIDENCE_ORDER)


def build_placement_rate(
    outcomes,
    reached: int,
    due: int,
    label: str = 'placement',
    floor: str = 'E0',
    note: str = '',
) -> ReachRate:
    """Assemble a placement rate from outcome rows plus the due/reached counts.

    ``reached`` is the number of trainees who replied to a follow-up that was due;
    ``due`` is the number of follow-ups that were due. Reporting both means a
    42% placement rate can never be mistaken for 42% of everybody.
    """
    rows = list(outcomes)
    placed = sum(1 for outcome in rows if outcome.status in PLACEMENT_STATUSES)
    return ReachRate(
        label=label,
        numerator=placed,
        denominator=reached,
        response_numerator=reached,
        response_denominator=due,
        mix=grade_mix(rows),
        floor=floor,
        note=note,
    )


def raise_grade(outcome, level: str) -> None:
    """Append-only friendly grade promotion.

    An outcome row is never rewritten. Promotion means inserting an
    ``EvidenceRecord`` that documents the new grade, which the analytics batch
    reads as the effective grade.
    """
    from core.models import EVIDENCE_ORDER, EvidenceRecord

    if level not in EVIDENCE_ORDER:
        raise ValueError(f'Unknown evidence level: {level}')
    if EVIDENCE_ORDER.index(level) <= EVIDENCE_ORDER.index(outcome.evidence_level):
        return
    kind = 'RECORD_MATCH' if level == 'E4' else 'DOCUMENT'
    EvidenceRecord.objects.create(
        outcome=outcome,
        kind=kind,
        level=level,
        result_verified=True,
        reviewer='system',
        detail=f'Grade promoted from {outcome.evidence_level} to {level}',
        created_by='system',
    )


def effective_grade(outcome) -> str:
    """The highest grade on record for an outcome, including evidence rows."""
    best = outcome.evidence_level
    for record in outcome.evidence_set.all():
        if record.result_verified and record.level:
            if EVIDENCE_ORDER.index(record.level) > EVIDENCE_ORDER.index(best):
                best = record.level
    return best