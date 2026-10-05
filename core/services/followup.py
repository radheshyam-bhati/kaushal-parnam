"""Follow-up scheduling and draining (F-04).

Rounds are created at certification + 90 / 180 / 270 / 365 days, read from the
RETENTION_SCHEDULE definition so the policy officer can change the cadence.
Draining uses ``FOR UPDATE SKIP LOCKED`` so several workers can run at once
without double-sending (docs/02-TRD.md §1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Contact,
    Definitions,
    FollowupJob,
    OutcomeEvent,
    Person,
)

#: F-04 retry plan (docs/01-PRD.md §7): after the first attempt on the declared
#: device channel, wait RETRY_HOURS[channel] before trying that channel again.
RETRY_HOURS = {'SMS': 48, 'IVR': 72}
#: Order the fallback channels are tried in. Single source of truth: the router
#: picks the rung, the scheduler decides when that rung is due.
RETRY_LADDER = ['SMS', 'IVR']
MAX_RETRIES = len(RETRY_LADDER) + 1


def _retry_cutoffs(now):
    """``(retries, cutoff)`` pairs: when each retry rung becomes due.

    ``retries`` is the attempt count already made, so the rung for index ``i``
    of the ladder fires after ``RETRY_HOURS[RETRY_LADDER[i]]`` hours of silence.
    """
    return [
        (index + 1, now - timedelta(hours=RETRY_HOURS[channel]))
        for index, channel in enumerate(RETRY_LADDER)
    ]


def escalation_cutoff(now, max_attempts: int = MAX_RETRIES):
    """Silence required before a fully-attempted job is escalated to an officer."""
    return now - timedelta(hours=RETRY_HOURS[RETRY_LADDER[-1]])


def _as_date(value):
    """Accept a date, a datetime, or the ISO string a caller just assigned."""
    if value is None or isinstance(value, datetime):
        return value.date() if isinstance(value, datetime) else value
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


def schedule_rounds_for_enrolment(enrolment, *, now=None) -> list[FollowupJob]:
    """Create the 4 rounds for one enrolment, skipping cancelled consent.

    Only purposes with live consent get a follow-up (docs/03-APP-FLOW.md
    "Schedule — only for purposes with live consent").
    """
    now = now or timezone.now()
    person = enrolment.person

    if not person.consent_active('follow-up'):
        return []

    contact = Contact.objects.filter(person=person).first()
    channel = contact.preferred_channel if contact else 'SMS'
    anchor = _as_date(enrolment.reference_date)
    jobs: list[FollowupJob] = []

    for index, days in enumerate(Definitions.round_intervals(), start=1):
        due_at = timezone.make_aware(
            timezone.datetime.combine(anchor, timezone.datetime.min.time())
        ) + timedelta(days=days)
        # Every round is created, including one whose due date has already
        # passed: a retroactive enrolment must not silently lose round 2 just
        # because the certificate was issued last month. Overdue rounds stay
        # PENDING and the scheduler drains them on its next pass.
        job, _created = FollowupJob.objects.get_or_create(
            person=person,
            round=index,
            defaults={
                'due_at': due_at,
                'channel_plan': channel,
                'status': 'PENDING',
                'created_by': 'scheduler',
            },
        )
        jobs.append(job)
    return jobs


def schedule_missing_rounds(now=None) -> int:
    """Backfill any enrolment that does not have all its rounds.

    Checking only for round 1 is not enough: a registration whose round-1 job
    was created before a middle round was added to the schedule would keep a
    permanent hole.
    """
    from core.models import Enrolment

    now = now or timezone.now()
    expected = len(Definitions.round_intervals())
    created = 0
    enrolments = Enrolment.objects.select_related('person')
    for enrolment in enrolments:
        have = FollowupJob.objects.filter(
            person=enrolment.person
        ).values_list('round', flat=True)
        missing = set(range(1, expected + 1)) - set(have)
        if missing:
            created += len(schedule_rounds_for_enrolment(enrolment, now=now))
    return created


def claim_due_jobs(limit: int = 100, now=None) -> list[int]:
    """Atomically claim every job that owes an attempt, and mark them SENT.

    Two populations qualify, and both are needed for the F-04 ladder to run:

    * a ``PENDING`` job whose ``due_at`` has arrived -- the first attempt, sent
      on the channel the trainee's device type declared;
    * a ``SENT`` job that has been silent long enough for the next rung of
      :data:`RETRY_LADDER` -- the 48h SMS step, then the 72h IVR step.

    Claiming only PENDING jobs would leave every job at one attempt forever, so
    the retry rungs and the escalation below them could never be reached.

    ``FOR UPDATE SKIP LOCKED`` is what lets two workers drain the same table.
    On SQLite the same work is done inside a transaction with a compare-and-set
    update, which is sufficient for the single-process demo.
    """
    now = now or timezone.now()
    cutoffs = _retry_cutoffs(now)
    if connection.vendor == 'postgresql':
        retry_clause = ' OR '.join(
            ["(status = 'SENT' AND retries = %s AND last_attempt_at <= %s)"] * len(cutoffs)
        )
        params = [now]
        for retries, cutoff in cutoffs:
            params.extend([retries, cutoff])
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE followup_job
                   SET status = 'SENT', updated_at = NOW(), updated_by = 'scheduler'
                 WHERE id IN (
                       SELECT id FROM followup_job
                        WHERE (status = 'PENDING' AND due_at <= %s)
                           OR {retry_clause}
                        ORDER BY due_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                 )
                RETURNING id
                """,
                params,
            )
            return [row[0] for row in cursor.fetchall()]

    due = Q(status='PENDING', due_at__lte=now)
    for retries, cutoff in cutoffs:
        due |= Q(status='SENT', retries=retries, last_attempt_at__lte=cutoff)
    with transaction.atomic():
        rows = list(
            FollowupJob.objects.select_for_update()
            .filter(due)
            .order_by('due_at')[:limit]
            .values_list('id', flat=True)
        )
        FollowupJob.objects.filter(id__in=rows).update(
            status='SENT', updated_at=now, updated_by='scheduler'
        )
        return rows


def claim_escalations(limit: int = 100, max_attempts: int = MAX_RETRIES, now=None) -> list[int]:
    """Claim fully-attempted jobs that have stayed silent long enough to escalate.

    Separate from :func:`claim_due_jobs` because the outcome is not another
    message: the ladder is exhausted, so the round becomes an officer task.
    """
    now = now or timezone.now()
    silent_since = escalation_cutoff(now)
    return list(
        FollowupJob.objects.select_for_update()
        .filter(
            status='SENT',
            retries__gte=max_attempts,
            last_attempt_at__lte=silent_since,
        )
        .order_by('last_attempt_at')[:limit]
        .values_list('id', flat=True)
    )


def record_attempt(job: FollowupJob, channel: str, result: str, **kwargs) -> None:
    """Append one contact attempt (F-04: all attempts are logged)."""
    from core.models import ContactAttempt

    next_number = (
        ContactAttempt.objects.filter(followup_job=job).count() + 1
    )
    ContactAttempt.objects.create(
        followup_job=job,
        attempt_number=next_number,
        channel=channel,
        result=result,
        demo_label=kwargs.pop('demo_label', 'STUB'),
        **kwargs,
    )
    job.retries = next_number
    job.last_attempt_at = timezone.now()
    job.updated_at = timezone.now()
    job.updated_by = 'scheduler'
    job.save(update_fields=['retries', 'last_attempt_at', 'updated_at', 'updated_by'])


def mark_replied(job: FollowupJob) -> None:
    job.status = 'REPLIED'
    job.updated_at = timezone.now()
    job.updated_by = 'trainee'
    job.save(update_fields=['status', 'updated_at', 'updated_by'])


def mark_cancelled_jobs_for(person: Person, now=None) -> int:
    """Stop scheduling when the trainee withdraws follow-up consent (S-04)."""
    now = now or timezone.now()
    return FollowupJob.objects.filter(
        person=person, status='PENDING'
    ).update(status='CANCELLED', updated_at=now, updated_by='consent_withdrawal')


def escalate_expired(job: FollowupJob, reason: str = 'No reply after three channels') -> FollowupJob | None:
    """Turn an expired job into an officer task (F-04 assisted follow-up)."""
    from core.models import FollowupTask
    from core.services.audit import log_event

    now = timezone.now()
    job.status = 'EXPIRED'
    job.updated_at = now
    job.updated_by = 'scheduler'
    job.save(update_fields=['status', 'updated_at', 'updated_by'])

    task, _created = FollowupTask.objects.get_or_create(
        person=job.person,
        followup_job=job,
        defaults={
            'district': job.person.district,
            'task_type': 'CALL',
            'priority': max(1, 6 - job.round),
            'note': reason,
            'due_at': now + timedelta(days=7),
        },
    )
    log_event(
        component='followup',
        event_type='followup_escalated',
        description=f'Round {job.round} expired for {job.person_id}; officer task {task.pk}',
        utid=job.person_id,
        user_role='system',
    )
    return task


def mark_unreachable_outcome(job: FollowupJob) -> OutcomeEvent:
    """Record "could not be reached" as an append-only event (F-05 status 7)."""
    outcome = OutcomeEvent.objects.create(
        person=job.person,
        followup_job=job,
        reference_date=timezone.localdate(),
        status='could_not_be_reached',
        evidence_level='E0',
        created_by='system',
    )
    mark_replied(job)
    return outcome


def open_task_count(district: str | None = None) -> int:
    from core.models import FollowupTask

    qs = FollowupTask.objects.filter(status='OPEN')
    if district:
        qs = qs.filter(district=district)
    return qs.count()