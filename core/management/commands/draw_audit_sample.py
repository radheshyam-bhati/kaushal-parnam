"""Department-drawn random call-back sample (F-08).

Usage:
    python manage.py draw_audit_sample
    python manage.py draw_audit_sample --non-replier-rate 0.10 --claimed-job-rate 0.05

The independent calls themselves are a Pilot-phase activity (docs/01-PRD.md §4).
This command draws the sample, stores the probability p, and recomputes the
weighted placement rate (S-07).
"""

from django.core.management.base import BaseCommand

from core.services.weights import draw_claimed_job_sample, draw_non_replier_sample


class Command(BaseCommand):
    help = 'Draw the department random sample of non-repliers and claimed jobs'

    def add_arguments(self, parser):
        parser.add_argument('--non-replier-rate', type=float, default=None)
        parser.add_argument('--claimed-job-rate', type=float, default=None)
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        non_repliers = draw_non_replier_sample(
            options['non_replier_rate'], limit=options['limit']
        )
        claimed = draw_claimed_job_sample(
            options['claimed_job_rate'], limit=options['limit']
        )
        self.stdout.write(
            f'drew {len(non_repliers)} non-repliers and {len(claimed)} claimed jobs'
        )
        for sample in list(non_repliers) + list(claimed):
            self.stdout.write(
                f'  {sample.outcome_id} {sample.sample_type} p={sample.probability:.2f} '
                f'weight={sample.weight:.1f}'
            )