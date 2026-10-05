"""Drain due follow-up jobs (F-04).

Usage:
    python manage.py run_followup_scheduler              # one pass
    python manage.py run_followup_scheduler --loop       # keep draining
    python manage.py run_followup_scheduler --max-attempts 3

Console output per docs/06-IMPLEMENTATION-PLAN.md Phase 4:
    WhatsApp form sent to +919876543210
    IVR call stub for +919876543210
    SMS nudge sent to +919876543210
"""

import time

from django.core.management.base import BaseCommand

from core.models import FollowupJob
from core.services.channel_router import dispatch, record_unreachable
from core.services.followup import (
    MAX_RETRIES,
    claim_due_jobs,
    claim_escalations,
    schedule_missing_rounds,
)


class Command(BaseCommand):
    help = 'Send due follow-ups through the channel router and escalate what goes unanswered'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--loop', action='store_true', help='keep polling')
        parser.add_argument('--interval', type=int, default=10, help='seconds between passes')
        parser.add_argument(
            '--max-attempts', type=int, default=MAX_RETRIES,
            help='attempts before the round is escalated to a district officer',
        )
        parser.add_argument('--no-schedule', action='store_true', help='skip backfill')

    def handle(self, *args, **options):
        if not options['no_schedule']:
            created = schedule_missing_rounds()
            if created:
                self.stdout.write(f'scheduled {created} missing follow-up rounds')

        while True:
            self._pass(options)
            if not options['loop']:
                break
            time.sleep(options['interval'])

    def _pass(self, options) -> None:
        job_ids = claim_due_jobs(limit=options['limit'])
        if not job_ids:
            self.stdout.write('no jobs due')
        for job_id in job_ids:
            job = FollowupJob.objects.select_related('person').get(pk=job_id)
            self.stdout.write(dispatch(job))

        # The ladder is exhausted and the trainee has stayed silent past the last
        # rung's window: escalate to an officer task.
        escalated = 0
        for job_id in claim_escalations(
            limit=options['limit'], max_attempts=options['max_attempts']
        ):
            job = FollowupJob.objects.select_related('person').get(pk=job_id)
            record_unreachable(job)
            escalated += 1
        if escalated:
            self.stdout.write(
                self.style.WARNING(
                    f'{escalated} rounds expired after {options["max_attempts"]} '
                    'attempts; officer tasks opened'
                )
            )