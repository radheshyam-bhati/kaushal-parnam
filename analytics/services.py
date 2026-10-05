"""S-05 per-provider data-quality score.

Four penalties, each traceable to a number the officer can inspect:

    missing provenance   outcomes with no evidence record at all
    inconsistent IDs     trainees with several external scheme IDs and no crosswalk note
    stale demand         demand snapshots older than the staleness window
    unreached share      share of due follow-ups with no reply

The score is 100 minus a weighted penalty, capped at 0-100, then banded
green / amber / red.
"""

from __future__ import annotations

from core.models import (
    DataQualityScore,
    Definitions,
    DemandSnapshot,
    Enrolment,
    FollowupJob,
    IdCrosswalk,
    OutcomeEvent,
    Provider,
)

WEIGHTS = {
    'missing_provenance': 40,
    'inconsistent_ids': 15,
    'stale_demand_rows': 15,
    'unreached_share': 30,
}


def _metrics_for(provider: Provider) -> dict:
    people = Enrolment.objects.filter(provider=provider).values_list('person_id', flat=True)
    outcomes = OutcomeEvent.objects.filter(person_id__in=list(people))

    total_outcomes = outcomes.count()
    with_provenance = outcomes.exclude(evidence_set__isnull=True).distinct().count()
    missing_provenance = total_outcomes - with_provenance

    # "Inconsistent identifiers": a trainee holding two IDs from the same scheme,
    # or an outcome recorded with no crosswalk entry at all.
    inconsistent = 0
    for person_id in people:
        rows = IdCrosswalk.objects.filter(person_id=person_id)
        schemes = [row.scheme for row in rows]
        if len(schemes) != len(set(schemes)):
            inconsistent += 1

    stale_window = Definitions.get('FLAG_SETTINGS').get('stale_demand_days', 90)
    stale_rows = sum(
        1 for snapshot in DemandSnapshot.objects.all() if snapshot.is_stale
    )

    due = FollowupJob.objects.filter(person_id__in=list(people)).count()
    replied = FollowupJob.objects.filter(
        person_id__in=list(people), status='REPLIED'
    ).count()
    unreached_share = round(1.0 - (replied / due), 2) if due else 0.0
    response_rate = round(100.0 * replied / due, 1) if due else 0.0

    return {
        'missing_provenance': missing_provenance,
        'inconsistent_ids': inconsistent,
        'stale_demand_rows': stale_rows,
        'unreached_share': unreached_share,
        'response_rate': response_rate,
        'total_outcomes': total_outcomes,
    }


def score_for(metrics: dict) -> float:
    penalty = 0.0
    # Provenance: every 10% of outcomes without evidence costs 40 points.
    if metrics['total_outcomes']:
        share = metrics['missing_provenance'] / metrics['total_outcomes']
        penalty += WEIGHTS['missing_provenance'] * min(1.0, share * 4)
    penalty += min(1.0, metrics['inconsistent_ids'] / 10) * WEIGHTS['inconsistent_ids']
    penalty += min(1.0, metrics['stale_demand_rows'] / 4) * WEIGHTS['stale_demand_rows']
    penalty += metrics['unreached_share'] * WEIGHTS['unreached_share']
    return int(max(0, min(100, round(100 - penalty))))


def compute_quality_scores() -> list[DataQualityScore]:
    scores = []
    for provider in Provider.objects.all():
        metrics = _metrics_for(provider)
        score, _ = DataQualityScore.objects.update_or_create(
            provider=provider,
            defaults={
                'score': score_for(metrics),
                'missing_provenance': metrics['missing_provenance'],
                'inconsistent_ids': metrics['inconsistent_ids'],
                'stale_demand_rows': metrics['stale_demand_rows'],
                'unreached_share': metrics['unreached_share'],
                'response_rate': metrics['response_rate'],
            },
        )
        scores.append(score)
    return sorted(scores, key=lambda s: -s.score)