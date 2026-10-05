"""Seed the 6-row starter data set from docs/05-BACKEND-SCHEMA.md §4.

Usage: python manage.py seed_demo_data
Idempotent: re-running updates the same UTID rather than duplicating it.
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    CONSENT_PURPOSES,
    Consent,
    Contact,
    Enrolment,
    Employer,
    EvidenceRecord,
    FollowupJob,
    OutcomeEvent,
    Person,
    Provider,
    Qualification,
    load_default_definitions,
)


class Command(BaseCommand):
    help = 'Insert the 6-row minimal starter data set plus the definitions registry'

    @transaction.atomic
    def handle(self, *args, **options):
        load_default_definitions()
        self.stdout.write('definitions registry: 4 rows')

        provider, _ = Provider.objects.update_or_create(
            provider_id='Centre-A',
            defaults={
                'name': 'Anganwadi Vocational Centre (synthetic)',
                'district': 'Mumbai',
                'scheme': 'SDMS',
                'is_registered': True,
            },
        )
        qualification, _ = Qualification.objects.update_or_create(
            qualification_id='PMKVY-Q-2024-PLUMBING',
            defaults={
                'course_name': 'Plumbing',
                'occupation_code': '7125',
                'skill_list': ['pipe fitting', 'bending', 'welding', 'water supply'],
                'course_duration_days': 90,
            },
        )

        person, _ = Person.objects.update_or_create(
            utid='KO-20240101-0001',
            defaults={
                'name': 'Anita Sharma',
                'dob': date(1998, 5, 15),
                'gender': 'F',
                'category': 'OBC',
                'district': 'Mumbai',
                'is_active': True,
                'created_by': 'system',
            },
        )
        Contact.objects.update_or_create(
            person=person,
            defaults={
                'own_phone': '+919876543210',
                'device_type': 'smartphone',
                'language': 'hi',
                'shared_phone_flag': False,
            },
        )
        for purpose, _label in CONSENT_PURPOSES:
            Consent.objects.get_or_create(
                person=person,
                purpose=purpose,
                defaults={'language': 'hi', 'given_by': 'trainee'},
            )
        self.stdout.write(f'person + contact + 4 consent rows: {person.pk}')

        from core.models import IdCrosswalk

        IdCrosswalk.objects.get_or_create(
            person=person, scheme='SDMS',
            defaults={'programme_id': 'PMKVY-2024-BATCH1', 'linked_by': 'system'},
        )

        enrolment, _ = Enrolment.objects.update_or_create(
            person=person,
            defaults={
                'provider': provider,
                'qualification': qualification,
                'enrolment_date': date(2024, 1, 10),
                'course_start_date': date(2024, 1, 15),
                'course_end_date': date(2024, 3, 15),
                'certified_on': date(2024, 3, 15),
                'cohort': 'MUM-2024-Q1',
            },
        )

        due = timezone.make_aware(
            timezone.datetime.combine(date(2024, 4, 15), timezone.datetime.min.time())
        )
        FollowupJob.objects.update_or_create(
            person=person,
            round=1,
            defaults={
                'due_at': due,
                'channel_plan': 'WA',
                'status': 'PENDING',
                'created_by': 'system',
            },
        )
        self.stdout.write('follow-up job: round 1 due 2024-04-15')

        outcome, created = OutcomeEvent.objects.get_or_create(
            person=person,
            reference_date=date(2024, 3, 15),
            defaults={
                'status': 'wage_employment',
                'evidence_level': 'E0',
                'role_code': '7125',
                'start_date': date(2024, 3, 20),
                'wage_band': '25000-29999',
                'employer_name': 'ABC Constructions',
                'created_by': 'trainee',
            },
        )
        if created:
            self.stdout.write(f'outcome event #{outcome.pk} at E0')

        from core.models import Reason

        Reason.objects.get_or_create(
            outcome=outcome,
            group_name='employment-related',
            code='started_job',
            defaults={'text': 'Trainee started a new job'},
        )
        Employer.objects.update_or_create(
            outcome=outcome,
            defaults={
                'employer_name': 'ABC Constructions',
                'employer_type': 'formal',
                'wage_claimed': '25000-29999',
                'wage_verified': '25000-29999',
                'verified': True,
                'validated_at': timezone.now(),
            },
        )
        EvidenceRecord.objects.get_or_create(
            outcome=outcome,
            kind='DOCUMENT',
            defaults={'reviewer': 'officer', 'result_verified': True, 'level': 'E3'},
        )

        self.stdout.write(
            self.style.SUCCESS(
                'Seed data ready: 1 person, 1 enrolment, 1 outcome (E0), '
                'verified employer, E3 document, 4 definitions'
            )
        )