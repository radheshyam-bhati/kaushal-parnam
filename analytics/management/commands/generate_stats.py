"""Run the nightly statistics batch (F-09, F-10, F-11).

Usage:
    python manage.py generate_stats
    python manage.py generate_stats --grades E0 E2
"""

from django.core.management.base import BaseCommand

from core.services.stats import run_all


class Command(BaseCommand):
    help = 'Rebuild stats_placement, stats_wage_progression and stats_skill_gap'

    def add_arguments(self, parser):
        parser.add_argument(
            '--grades', nargs='+', default=['E0', 'E1', 'E2'],
            help='grade floors to publish, so E2+ can be compared with the raw rate',
        )

    def handle(self, *args, **options):
        result = run_all(grades=tuple(options['grades']))
        self.stdout.write(
            self.style.SUCCESS(
                f'stats_placement rows (incl. grade variants): {result["placement_rows"]}'
            )
        )
        self.stdout.write(f'stats_wage_progression rows: {result["wage_rows"]}')
        self.stdout.write(f'stats_skill_gap rows:         {result["skill_gap_rows"]}')

        from core.services.stats import flagged_gaps

        gaps = flagged_gaps()
        self.stdout.write('')
        self.stdout.write(f'courses flagged for a skill gap (2 of 3 signals agree): {gaps.count()}')
        for gap in gaps:
            self.stdout.write(
                f'  {gap.course} / {gap.district}: {gap.signals_agree_count}/3 signals '
                f'(vacancies {gap.vacancy_count}, employer {gap.employer_skills_lacking_count}, '
                f'trainee {gap.trainee_skills_lacking_count})'
            )