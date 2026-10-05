"""Seed the editable definitions registry with its four rows (F-03).

Usage: python manage.py load_definitions
"""

from django.core.management.base import BaseCommand

from core.models import load_default_definitions


class Command(BaseCommand):
    help = 'Seed RETENTION_SCHEDULE, BREAK_RULE, SKILL_GAP_THRESHOLD and FLAG_SETTINGS'

    def handle(self, *args, **options):
        written = load_default_definitions()
        for key, value in written.items():
            self.stdout.write(f'  {key} = {value}')
        self.stdout.write(self.style.SUCCESS(f'{len(written)} definitions loaded'))