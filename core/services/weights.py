"""Random call-back sample and non-response weighting (F-08, S-07).

The department draws the sample, not the provider: a provider cannot choose who
gets audited. Each sampled record stores the probability p of being drawn, and
the weight 1/p corrects a reported rate so it represents every trainee rather
than only the ones who replied (docs/01-PRD.md §7 F-08).

Per docs/01-PRD.md §4, the MVP builds the simplified form: the sample is drawn
and the weights are computed here; the independent telephone calls themselves
are a Pilot-phase activity.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db.models import Q

from core.models import AuditSample, OutcomeEvent, PLACEMENT_STATUSES
from core.services.audit import log_event


def _sample(queryset, probability: float, limit: int = 500):
    """Draw a simple random sample; store p so the weight is reproducible."""
    candidate_ids = list(queryset.values_list('id', flat=True)[:limit])
    if not candidate_ids or probability <= 0:
        return []
    size = max(1, round(len(candidate_ids) * probability))
    chosen = secrets.SystemRandom().sample(candidate_ids, min(size, len(candidate_ids)))
    return [
        AuditSample.objects.create(
            outcome_id=outcome_id,
            probability=probability,
            note=f'Department draw at p={probability:.2f}',
        )
        for outcome_id in chosen
    ]


def draw_non_replier_sample(probability: float | None = None, limit: int = 500):
    """Sample rounds that expired with no reply (F-08 clause 1)."""
    probability = probability or settings.NON_REPLIER_SAMPLE_RATE
    queryset = OutcomeEvent.objects.filter(
        status='could_not_be_reached'
    ).filter(Q(is_anonymous=False))
    samples = _sample(queryset, probability, limit)
    log_event(
        component='audit',
        event_type='audit_sample_non_replier',
        description=f'Drew {len(samples)} non-repliers at p={probability:.2f}',
        user_role='system',
    )
    return samples


def draw_claimed_job_sample(probability: float | None = None, limit: int = 500):
    """Sample employer-confirmed jobs for independent verification (F-08 clause 2)."""
    probability = probability or settings.CLAIMED_JOB_SAMPLE_RATE
    queryset = OutcomeEvent.objects.filter(
        evidence_level__in=('E2', 'E3', 'E4'), status__in=PLACEMENT_STATUSES
    )
    samples = _sample(queryset, probability, limit)
    log_event(
        component='audit',
        event_type='audit_sample_claimed_job',
        description=f'Drew {len(samples)} claimed jobs at p={probability:.2f}',
        user_role='system',
    )
    return samples


def record_call_result(sample: AuditSample, verified: bool, called_by: str) -> AuditSample:
    from django.utils import timezone

    sample.result_verified = verified
    sample.called_by = called_by
    sample.called_at = timezone.now()
    sample.save(update_fields=['result_verified', 'called_by', 'called_at'])
    return sample


def weight_for(outcome) -> float:
    """Inverse-probability weight for one outcome: 1/p, or 1 when unsampled."""
    sample = outcome.audit_samples.order_by('id').first()
    return sample.weight if sample else 1.0


def weighted_rate(outcomes, reached: int, due: int) -> dict:
    """Placement rate after non-response weighting (S-07).

    Unsampled respondents keep weight 1. Sampled rows carry 1/p, so an
    unrepresentative sample of hard-to-reach trainees pulls the estimate down
    instead of letting a small responsive group speak for everyone.
    """
    rows = list(outcomes)
    weight_total = sum(weight_for(outcome) for outcome in rows)
    placed_weight = sum(
        weight_for(outcome)
        for outcome in rows
        if outcome.status in PLACEMENT_STATUSES
    )
    weighted_rate = round(100.0 * placed_weight / weight_total, 1) if weight_total else 0.0

    # Non-repliers answer at their sampled rate; spread their weight over the
    # gap between those who replied and those who were due.
    missed = max(0, due - reached)
    if missed:
        non_replier_samples = AuditSample.objects.filter(
            sample_type='NON_REPLIER', result_verified__isnull=False
        )
        verified_non_repliers = non_replier_samples.filter(result_verified=True).count()
        called = non_replier_samples.count()
        if called:
            non_replier_rate = verified_non_repliers / called
            combined = (placed_weight + missed * non_replier_rate) / (weight_total + missed)
            weighted_rate = round(100.0 * combined, 1)

    return {
        'weighted_rate': weighted_rate,
        'weighted_response_rate': round(100.0 * reached / due, 1) if due else 0.0,
        'weight_total': round(weight_total, 2),
        'sampled': AuditSample.objects.filter(outcome__in=rows).count(),
    }


def confidence_interval(rate: float, denominator: int, z: float = 1.96) -> tuple[float, float]:
    """Normal-approximation 95% interval, used by the S-02 provider view."""
    if denominator <= 0:
        return (0.0, 0.0)
    proportion = rate / 100.0
    margin = z * ((proportion * (1 - proportion) / denominator) ** 0.5)
    return (
        round(max(0.0, proportion - margin) * 100, 1),
        round(min(1.0, proportion + margin) * 100, 1),
    )