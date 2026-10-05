"""Channel router: declared device type decides the first channel (F-04).

Retry plan from docs/01-PRD.md §7 (F-04):
    first attempt -> device channel
    no reply in 48h -> SMS
    no reply in 72h -> IVR
    still nothing  -> EXPIRED, and an assisted task for the district officer
"""

from __future__ import annotations

from django.utils import timezone

from core.adapters.channels import CHANNELS
from core.services.followup import (
    MAX_RETRIES,
    escalate_expired,
    mark_unreachable_outcome,
    record_attempt,
)

#: Escalation ladder: after the first attempt, try SMS then IVR then an officer.
RETRY_LADDER = ['SMS', 'IVR']


def next_channel(job) -> str:
    """Channel to use for the next attempt on ``job``."""
    if job.retries == 0:
        return job.channel_plan
    index = min(job.retries - 1, len(RETRY_LADDER) - 1)
    return RETRY_LADDER[index]


def template_context(job) -> dict:
    from core.models import Contact

    person = job.person
    enrolment = person.enrolment_set.select_related('provider', 'qualification').first()
    contact = Contact.objects.filter(person=person).first()
    return {
        'utid': person.pk,
        'name': person.display_name,
        'centre': enrolment.provider.name if enrolment else 'your training centre',
        'course': enrolment.qualification.course_name if enrolment else 'your course',
        'language': contact.language if contact else 'en',
        'link': f'/outcome/reply/{job.pk}/',
        'round': job.round,
    }


def dispatch(job) -> str:
    """Send one attempt for ``job`` and log it. Returns a human-readable line."""
    channel_code = next_channel(job)
    adapter = CHANNELS[channel_code]
    context = template_context(job)

    if channel_code == 'OFFICER' or job.retries >= MAX_RETRIES:
        escalate_expired(job)
        return f'Officer task opened for {job.person_id} (round {job.round})'

    result = adapter.send(
        recipient=job.person.contact.own_phone,
        template=adapter.TEMPLATE if hasattr(adapter, 'TEMPLATE') else '{link}',
        context=context,
    )
    record_attempt(
        job,
        channel=result.channel,
        result='SENT' if result.ok else 'ERROR',
        reply_json=result.payload,
        demo_label=result.demo_label,
    )
    return result.detail


def record_reply(job, outcome) -> None:
    """A reply arrived: close the job and append the outcome event (F-04 -> F-05)."""
    from core.services.followup import mark_replied

    record_attempt(
        job,
        channel=job.channel_plan,
        result='FORM_REPLY',
        reply_time=timezone.now(),
        reply_json={'outcome_id': outcome.pk, 'status': outcome.status},
        demo_label='SANDBOX',
    )
    mark_replied(job)


def record_unreachable(job) -> None:
    """Three channels tried and no reply: record status 7 and escalate."""
    from core.services.followup import mark_unreachable_outcome

    if not job.person.outcome_set.filter(
        reference_date=timezone.localdate(), status='could_not_be_reached'
    ).exists():
        mark_unreachable_outcome(job)
    escalate_expired(job)