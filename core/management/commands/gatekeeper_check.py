"""Validate a trainee intake CSV against the four gatekeeper rules (F-02).

Usage:
    python manage.py gatekeeper_check path/to/enrolment.csv
    python manage.py gatekeeper_check --template > enrolment_template.csv
"""

import csv
import sys

from django.core.management.base import BaseCommand

from core.services.gatekeeper import (
    CSV_TEMPLATE_HEADERS,
    FLAG_LABELS,
    check_csv,
)


class Command(BaseCommand):
    help = 'Run the intake gatekeeper over a CSV and print the flag summary'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', nargs='?')
        parser.add_argument(
            '--template', action='store_true',
            help='print a blank CSV template with the required headers',
        )

    def handle(self, *args, **options):
        if options['template']:
            writer = csv.writer(sys.stdout)
            writer.writerow(CSV_TEMPLATE_HEADERS)
            writer.writerow([
                'Anita Sharma', '1998-05-15', 'F', 'OBC', '+919876543210',
                'Plumbing', 'Centre-A', '2024-01-10', '2024-01-15',
                '', '', '', 'smartphone', 'mr', 'N', '2024-03-15', 'MUM-2024-Q1',
            ])
            return

        if not options['csv_path']:
            self.stderr.write('Give a CSV path, or use --template.')
            return

        with open(options['csv_path'], 'rb') as handle:
            report = check_csv(handle.read())

        if report.header_error:
            self.stderr.write(self.style.ERROR(report.header_error))
            return

        self.stdout.write(report.summary())
        self.stdout.write(f'flag rate: {report.flag_rate}%')
        self.stdout.write('')
        for result in report.results:
            if not result.is_flagged:
                continue
            self.stdout.write(self.style.WARNING(f'Row {result.row_number}:'))
            for code, message in zip(result.flags, result.messages):
                self.stdout.write(f'  [{FLAG_LABELS[code]}] {message}')

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'{report.accepted_rows} of {report.total_rows} rows cleared for enrolment'
            )
        )