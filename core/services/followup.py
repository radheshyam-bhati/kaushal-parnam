"""Follow-up scheduling and draining (F-04).

Rounds are created at certification + 90 / 180 / 270 / 365 days, read from the
RETENTION_SCHEDULE definition so the policy officer can change the cadence.
Draining uses ``FOR UPDATE SKIP LOCKED`` so several workers can run at once
without double-sending (docs/02-TRD.md §1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone

from core.models import (
    Contact,
    Definitions,
    FollowupJob,
    OutcomeEvent,
    Person,
)

RETRY_HOURS = {'SMS': 48, 'IVR': 72}  # F-04 retry plan
MAX_RETRIES = 3


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


def claim_due_jobs(limit: int = 100) -> list[int]:
    """Atomically claim due PENDING jobs and mark them SENT.

    ``FOR UPDATE SKIP LOCKED`` is what lets two workers drain the same table.
    On SQLite the same work is done inside a transaction with a compare-and-set
    update, which is sufficient for the single-process demo.
    """
    now = timezone.now()
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE followup_job
                   SET status = 'SENT', updated_at = NOW(), updated_by = 'scheduler'
                 WHERE id IN (
                       SELECT id FROM followup_job
                        WHERE status = 'PENDING' AND due_at <= %s
                        ORDER BY due_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                 )
                RETURNING id
                """,
                [now, limit],
            )
            return [row[0] for row in cursor.fetchall()]

    with transaction.atomic():
        rows = list(
            FollowupJob.objects.select_for_update()
            .filter(status='PENDING', due_at__lte=now)
            .order_by('due_at')[:limit]
            .values_list('id', flat=True)
        )
        FollowupJob.objects.filter(id__in=rows).update(
            status='SENT', updated_at=now, updated_by='scheduler'
        )
        return rows


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