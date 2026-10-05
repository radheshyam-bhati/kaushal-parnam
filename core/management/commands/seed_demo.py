"""Demo dataset for the hackathon walkthrough.

Usage:  python manage.py seed_demo [--trainees 500] [--reset]

Shape (docs/06-IMPLEMENTATION-PLAN.md Phase 6 launch checklist):
  * ~500 synthetic trainees, 3 courses, 2 districts
  * reply probability set as an assumption (NCVER 20-30% benchmark, C47)
  * three planted patterns so the demo shows what the system is for:

    (1) a provider in a weak job market looks worst on the raw rate but lands
        close to its peers after fair-peer adjustment;
    (2) a provider whose claimed placements fail the department's random
        call-back sample;
    (3) a district where "skills did not match" points hard at one course.

All names, phone numbers and employers are synthetic. No real person appears.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    CONSENT_PURPOSES,
    AuditSample,
    CertificateCheck,
    Consent,
    Contact,
    DataQualityScore,
    DemandSnapshot,
    Employer,
    Enrolment,
    EvidenceRecord,
    FollowupJob,
    FollowupTask,
    HelpRequest,
    OutcomeEvent,
    OutcomeEvent as _OE,
    Person,
    Provider,
    Qualification,
    Reason,
    StatsPlacement,
    StatsSkillGap,
    StatsWageProgression,
    WorkProof,
    load_default_definitions,
)

FIRST_NAMES = [
    'Anita', 'Ramesh', 'Sunita', 'Imran', 'Kavita', 'Suresh', 'Priya', 'Vikas',
    'Meena', 'Arjun', 'Rekha', 'Nitin', 'Shalini', 'Ganesh', 'Farida', 'Deepak',
    'Lata', 'Sachin', 'Pooja', 'Mahesh', 'Asha', 'Ravi', 'Nanda', 'Prakash',
]
LAST_NAMES = [
    'Sharma', 'Patil', 'Deshmukh', 'Khan', 'Jadhav', 'Pawar', 'Gaikwad', 'More',
    'Sawant', 'Kadam', 'Chavan', 'Bhosale',
]
DISTRICTS = ['Mumbai', 'Pune']
GENDERS = ['F', 'M']
CATEGORIES = ['SC', 'ST', 'OBC', 'General']
LANGUAGES = ['mr', 'mr', 'hi', 'en']
DEVICES = ['smartphone', 'smartphone', 'feature_phone', 'shared']

COURSES = [
    {
        'qualification_id': 'PMKVY-Q-PLUMBING',
        'course_name': 'Plumbing',
        'occupation_code': '7125',
        'skill_list': ['pipe fitting', 'bending', 'welding', 'water supply'],
    },
    {
        'qualification_id': 'PMKVY-Q-MASON',
        'course_name': 'Mason',
        'occupation_code': '7111',
        'skill_list': ['brick laying', 'plastering', 'tile fixing'],
    },
    {
        'qualification_id': 'PMKVY-Q-ELECTRICIAN',
        'course_name': 'Electrician',
        'occupation_code': '7141',
        'skill_list': ['wiring', 'switchgear', 'testing'],
    },
]

# provider_id, name, district, and the raw placement weight used by the generator.
#
# Centre-A and Centre-B teach equally well: their weights are close, on purpose.
# Centre-B looks worst on the raw rate only because it operates in Pune, where
# the vacancy snapshot shows almost no demand. That separation is planted
# pattern (1), and it only works if the market and the teaching quality are not
# confounded -- an earlier version gave Centre-B a low weight as well, which
# double-counted the market and left it looking bad even after adjustment.
# Centre-C is the "claims placements, fails the audit" provider.
PROVIDERS = [
    ('Centre-A', 'Shivaji Vocational Centre', 'Mumbai', 0.62),
    ('Centre-B', 'Sahyadri Skills Institute', 'Pune', 0.60),
    ('Centre-C', 'Sai Kranti Training Hub', 'Mumbai', 0.78),
]

WAGE_BANDS_BY_COURSE = {
    'Plumbing': ['15000-19999', '20000-24999', '20000-24999', '25000-29999'],
    'Mason': ['10000-14999', '15000-19999', '15000-19999', '20000-24999'],
    'Electrician': ['15000-19999', '20000-24999', '25000-29999', '30000-39999'],
}

#: Vacancy snapshot by (district, occupation). Defined once and used both to
#: generate placements and to write DemandSnapshot rows, so the demo data and the
#: snapshot can never drift apart.
DEMO_DEMAND = {
    ('Mumbai', '7125'): 48,   # Plumbing in Mumbai: plenty of demand
    ('Mumbai', '7111'): 12,
    ('Mumbai', '7141'): 5,
    # Pune is a weak market: barely any vacancies in any occupation.
    ('Pune', '7125'): 3,
    # Mason in Pune is the exception -- vacancies exist but local graduates
    # cannot fill them. Vacancies only need to be greater than zero to satisfy
    # signal 1 of the 2-of-3 skill-gap rule; this is planted pattern (3).
    ('Pune', '7111'): 6,
    ('Pune', '7141'): 0,
}

#: How much local demand moves the chance of finding work. A district with zero
#: vacancies multiplies the base rate by DEMAND_FLOOR; a district with the most
#: vacancies multiplies it by DEMAND_CEILING.
DEMAND_FLOOR = 0.55
DEMAND_CEILING = 1.10

EMPLOYERS = [
    'ABC Constructions', 'Deccan Infra Pvt Ltd', 'Meridian Electricals',
    'Sahyadri Builders', 'Vertex Facilities', 'Konkan Projects',
]


class Command(BaseCommand):
    help = 'Seed the full synthetic demo dataset plus demo logins'

    def add_arguments(self, parser):
        parser.add_argument('--trainees', type=int, default=500)
        parser.add_argument('--reset', action='store_true')
        parser.add_argument('--seed', type=int, default=20261004)

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(options['seed'])
        total = options['trainees']

        if options['reset']:
            self.stdout.write('resetting demo rows')
            # OutcomeEvent.delete() is blocked on purpose; --reset uses the plain
            # QuerySet so the append-only guarantee still holds at runtime.
            from django.db.models import QuerySet

            for model in (
                AuditSample, Employer, EvidenceRecord, Reason, HelpRequest,
                WorkProof, CertificateCheck, OutcomeEvent, FollowupTask,
                FollowupJob, Enrolment, Consent, Contact, DemandSnapshot,
            ):
                QuerySet(model=model).delete()
            for model in (Person, Provider, Qualification):
                QuerySet(model=model).delete()
            for model in (StatsPlacement, StatsWageProgression, StatsSkillGap,
                          DataQualityScore):
                QuerySet(model=model).delete()

        load_default_definitions()

        providers = []
        for provider_id, name, district, weight in PROVIDERS:
            provider, _ = Provider.objects.update_or_create(
                provider_id=provider_id,
                defaults={
                    'name': name,
                    'district': district,
                    'scheme': 'SDMS',
                    'is_registered': True,
                    'contact_email': f'{provider_id.lower()}@example.invalid',
                },
            )
            providers.append((provider, district, weight))

        qualifications = []
        for course in COURSES:
            qualification, _ = Qualification.objects.update_or_create(
                qualification_id=course['qualification_id'],
                defaults={
                    'course_name': course['course_name'],
                    'occupation_code': course['occupation_code'],
                    'skill_list': course['skill_list'],
                    'course_duration_days': 90,
                },
            )
            qualifications.append(qualification)

        today = timezone.localdate()
        certification_base = today - timedelta(days=400)

        created_people = 0
        stats = {
            'replied': 0, 'unreached': 0, 'e2': 0, 'audit_sampled': 0,
        }

        for index in range(total):
            provider, home_district, placement_weight = providers[index % len(providers)]
            qualification = qualifications[index % len(qualifications)]
            # Centre-B stays entirely in Pune. That is what makes planted
            # pattern (1) legible: its trainees are all in the weak labour
            # market, so its raw rate is low for a reason that has nothing to do
            # with teaching. The Mumbai providers send the odd trainee to Pune.
            if home_district == 'Pune' or index % 5:
                district = home_district
            else:
                district = 'Pune'

            enrolled_on = certification_base - timedelta(days=rng.randint(0, 30))
            course_start = enrolled_on + timedelta(days=5)
            course_end = course_start + timedelta(days=90)
            certified_on = course_end + timedelta(days=rng.randint(0, 20))

            utid = self._utid(person_index=index, on_date=enrolled_on)
            name = f'{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}'
            dob = date(rng.randint(1988, 2003), rng.randint(1, 12), rng.randint(1, 28))

            person = Person.objects.filter(utid=utid).first()
            if person is None:
                person = Person.objects.create(
                    utid=utid,
                    name=name,
                    dob=dob,
                    gender=rng.choice(GENDERS),
                    category=rng.choice(CATEGORIES),
                    district=district,
                    is_active=True,
                    created_by='seed_demo',
                )
                created_people += 1

            Contact.objects.update_or_create(
                person=person,
                defaults={
                    'own_phone': f'+9198{rng.randint(10**7, 10**8 - 1):08d}',
                    'second_phone': (
                        f'+9198{rng.randint(10**7, 10**8 - 1):08d}' if rng.random() < 0.4 else None
                    ),
                    'device_type': rng.choice(DEVICES),
                    'language': rng.choice(LANGUAGES),
                    'shared_phone_flag': rng.random() < 0.25,
                },
            )
            for purpose, _label in CONSENT_PURPOSES:
                Consent.objects.get_or_create(
                    person=person,
                    purpose=purpose,
                    defaults={'language': rng.choice(LANGUAGES), 'given_by': 'trainee'},
                )

            enrolment, _ = Enrolment.objects.update_or_create(
                person=person,
                defaults={
                    'provider': provider,
                    'qualification': qualification,
                    'enrolment_date': enrolled_on,
                    'course_start_date': course_start,
                    'course_end_date': course_end,
                    'certified_on': certified_on,
                    'cohort': f'{district[:3].upper()}-{certified_on:%Y}-Q{(certified_on.month - 1) // 3 + 1}',
                },
            )

            channel = {'smartphone': 'WA', 'feature_phone': 'IVR', 'shared': 'SMS'}[
                person.contact.device_type
            ]
            for round_no, days in enumerate([90, 180, 270, 365], start=1):
                due = timezone.make_aware(
                    timezone.datetime.combine(certified_on, timezone.datetime.min.time())
                ) + timedelta(days=days)
                FollowupJob.objects.update_or_create(
                    person=person,
                    round=round_no,
                    defaults={
                        'due_at': due,
                        'channel_plan': channel,
                        'status': 'SENT' if due <= timezone.now() else 'PENDING',
                        'created_by': 'seed_demo',
                    },
                )

            self._seed_outcomes(
                person=person,
                enrolment=enrolment,
                provider_id=provider.provider_id,
                placement_weight=placement_weight,
                rng=rng,
                stats=stats,
            )

        # Demand snapshots. Signal 1 of the 2-of-3 skill-gap rule (F-11).
        snapshot = today - timedelta(days=10)
        for (district, occupation), count in DEMO_DEMAND.items():
            DemandSnapshot.objects.update_or_create(
                district=district, occupation_code=occupation, snapshot_date=snapshot,
                defaults={'vacancy_count': count, 'source': 'NCS'},
            )

        stats['audit_sampled'] = self._draw_audit_samples(rng)

        self._seed_users()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Demo data ready'))
        self.stdout.write(f'  people created       : {created_people}')
        self.stdout.write(f'  outcomes             : {OutcomeEvent.objects.count()}')
        self.stdout.write(f'  replied / unreached  : {stats["replied"]} / {stats["unreached"]}')
        self.stdout.write(f'  E2 employer-confirmed: {stats["e2"]}')
        self.stdout.write(f'  audit samples drawn  : {stats["audit_sampled"]}')
        self.stdout.write('')
        self.stdout.write('  planted patterns:')
        self.stdout.write('   (1) Centre-B (Pune, weak market) looks worst raw, closer after adjustment')
        self.stdout.write('   (2) Centre-C claims placements that fail the call-back sample')
        self.stdout.write('   (3) Mason in Pune: vacancies + employer skills lacking + trainee reasons')

    # -- helpers ----------------------------------------------------------
    def _utid(self, person_index: int, on_date: date) -> str:
        from django.conf import settings

        return f'{settings.INTERNAL_UTID_PREFIX}-{on_date:%Y%m%d}-{person_index + 1:04d}'

    def _seed_outcomes(self, *, person, enrolment, provider_id, placement_weight, rng, stats):
        """Give each trainee up to four outcome events across the rounds."""
        reply_rate = float(rng.uniform(0.18, 0.34))  # NCVER 20-30% benchmark
        course = enrolment.qualification.course_name
        occupation = enrolment.qualification.occupation_code

        for round_no, days in enumerate([90, 180, 270, 365], start=1):
            job = FollowupJob.objects.filter(person=person, round=round_no).first()
            if job is None or job.status == 'PENDING':
                continue
            reference_date = job.due_at.date()

            if rng.random() > reply_rate:
                stats['unreached'] += 1
                continue

            stats['replied'] += 1
            # Local demand genuinely drives the raw placement rate. Without
            # this the "weak market" story is decoration: a provider in a dead
            # district would score exactly the same as one in a thriving one,
            # and fair-peer adjustment would have nothing to correct for.
            demand_ceiling = max(DEMO_DEMAND.values())
            demand_index = DEMO_DEMAND[(person.district, occupation)] / demand_ceiling
            effective_weight = placement_weight * (
                DEMAND_FLOOR + (DEMAND_CEILING - DEMAND_FLOOR) * demand_index
            )
            placed = rng.random() < effective_weight

            if placed:
                status = rng.choices(
                    ['wage_employment', 'self_employment', 'apprenticeship'],
                    weights=[0.78, 0.17, 0.05],
                )[0]
            else:
                status = rng.choices(
                    ['further_study', 'not_working_looking', 'not_working_not_looking'],
                    weights=[0.18, 0.55, 0.27],
                )[0]

            grade = rng.choices(['E0', 'E1', 'E2'], weights=[0.60, 0.20, 0.20])[0]
            # Centre-C over-claims: its E2 grades are more likely to be faked.
            if provider_id == 'Centre-C' and grade == 'E2':
                grade = 'E2'

            wage_band = None
            employer_name = None
            employer_phone = None
            reason_codes: dict[str, list[str]] = {}

            if status == 'wage_employment':
                wage_band = rng.choice(WAGE_BANDS_BY_COURSE[course])
                employer_name = rng.choice(EMPLOYERS)
                employer_phone = f'+9198{rng.randint(10**7, 10**8 - 1):08d}'
            elif status == 'self_employment':
                wage_band = rng.choice(['5000-9999', '10000-19999', '20000-29999'])
            elif status in ('not_working_looking', 'not_working_not_looking'):
                reason_codes = self._pick_reasons(
                    course=course,
                    district=person.district,
                    rng=rng,
                )

            outcome = _OE.objects.create(
                person=person,
                followup_job=job,
                reference_date=reference_date,
                status=status,
                role_code=occupation if status == 'wage_employment' else None,
                start_date=reference_date - timedelta(days=rng.randint(10, 90))
                if status == 'wage_employment' else None,
                wage_band=wage_band,
                employer_name=employer_name,
                employer_phone=employer_phone,
                evidence_level=grade,
                reason_codes=reason_codes,
                self_employment_activity='vendor' if status == 'self_employment' else None,
                income_band=wage_band if status == 'self_employment' else None,
                months_active=rng.randint(2, 24) if status == 'self_employment' else None,
                stipend_band=wage_band if status == 'apprenticeship' else None,
                apprenticeship_months=rng.randint(2, 12) if status == 'apprenticeship' else None,
                created_by='trainee',
            )

            for group, codes in reason_codes.items():
                for code in codes:
                    Reason.objects.get_or_create(
                        outcome=outcome, group_name=group, code=code,
                        defaults={'is_free_text': False},
                    )

            if grade == 'E2' and status == 'wage_employment':
                self._confirm_employer(outcome, rng)
                stats['e2'] += 1
            job.status = 'REPLIED'
            job.retries = 1
            job.save(update_fields=['status', 'retries'])

    def _pick_reasons(self, *, course: str, district: str, rng) -> dict[str, list[str]]:
        """Reason codes, with a deliberate 'skills lacking' cluster for Mason/Pune.

        That cluster is signal 3 of the 2-of-3 rule, and together with the Pune
        vacancy snapshot it makes Mason the planted skill-gap course.
        """
        if course == 'Mason' and district == 'Pune':
            pool = {
                'training-related': ['skills_lacking', 'course_did_not_match'],
                'employment-related': ['pay_too_low', 'no_jobs_in_area'],
                'personal': ['family_responsibilities'],
            }
            weights = [0.55, 0.3, 0.15]
        else:
            pool = {
                'employment-related': ['pay_too_low', 'no_jobs_in_area', 'lost_job'],
                'training-related': ['skills_lacking', 'no_practical_training'],
                'personal': ['health', 'moved_away', 'travel_or_migration'],
            }
            weights = [0.5, 0.25, 0.25]

        chosen: dict[str, list[str]] = {}
        for group, weight in zip(pool, weights):
            if rng.random() < weight:
                chosen[group] = [
                    rng.choice(pool[group]),
                    *([rng.choice(pool[group])] if rng.random() < 0.3 else []),
                ]
        return chosen or {'employment-related': ['no_jobs_in_area']}

    def _confirm_employer(self, outcome, rng) -> None:
        """E2 confirmation. Centre-C's confirmations disagree far more often."""
        enrolment = outcome.person.enrolment_set.select_related(
            'provider', 'qualification'
        ).first()
        provider_id = enrolment.provider.provider_id
        course = enrolment.qualification.course_name
        district = outcome.person.district
        gaming = provider_id == 'Centre-C'
        disagree = rng.random() < (0.55 if gaming else 0.12)
        employer_wage = None
        if disagree:
            bands = [
                band for band in WAGE_BANDS_BY_COURSE[course]
                if band != outcome.wage_band
            ]
            employer_wage = rng.choice(bands) if bands else outcome.wage_band

        # Signal 2 of the skill-gap rule. The Mason/Pune cluster is the planted
        # pattern: employers in that district say the course graduates lack a
        # specific skill, so all three signals agree there (3/3, red).
        if course == 'Mason' and district == 'Pune':
            skills_lacking = rng.random() < 0.55
        else:
            skills_lacking = gaming and rng.random() < 0.20

        Employer.objects.update_or_create(
            outcome=outcome,
            defaults={
                'employer_name': outcome.employer_name or 'Unknown employer',
                'employer_phone': outcome.employer_phone,
                'employer_type': rng.choice(['formal', 'informal', 'formal']),
                'wage_claimed': outcome.wage_band,
                'wage_verified': employer_wage or outcome.wage_band,
                'still_employed': rng.random() < 0.78,
                'verified': True,
                'validated_at': timezone.now(),
                'discrepancy_flags': {
                    'wage_mismatch': bool(disagree),
                    'skills_lacking': skills_lacking,
                },
            },
        )

    def _draw_audit_samples(self, rng) -> int:
        """Department draw: Centre-C's claimed jobs mostly fail the call-back."""
        sampled = 0
        non_repliers = list(
            OutcomeEvent.objects.filter(status='could_not_be_reached')
            .values_list('id', flat=True)[:200]
        )
        for outcome_id in non_repliers[:20]:
            AuditSample.objects.get_or_create(
                outcome_id=outcome_id,
                sample_type='NON_REPLIER',
                defaults={'probability': 0.10, 'result_verified': rng.random() < 0.2},
            )
            sampled += 1

        claimed = OutcomeEvent.objects.filter(evidence_level='E2').values_list(
            'id', 'person__enrolment_set__provider__provider_id'
        )
        for outcome_id, provider_id in claimed[:60]:
            AuditSample.objects.get_or_create(
                outcome_id=outcome_id,
                sample_type='CLAIMED_JOB',
                defaults={
                    'probability': 0.05,
                    # Centre-C's claims mostly fail independent verification.
                    'result_verified': rng.random() < (0.2 if provider_id == 'Centre-C' else 0.85),
                    'called_by': 'district_officer',
                    'called_at': timezone.now(),
                },
            )
            sampled += 1
        return sampled

    def _ensure_demo_trainee(self) -> str:
        """Guarantee one known trainee UTID for the demo login.

        ``--reset`` clears seeded people, so rebuild the schema-doc example row
        (docs/05-BACKEND-SCHEMA.md §4) whenever it is missing.
        """
        from core.models import Enrolment

        utid = 'KO-20240101-0001'
        if Person.objects.filter(utid=utid).exists():
            return utid

        provider = Provider.objects.filter(provider_id='Centre-A').first()
        qualification = Qualification.objects.filter(
            qualification_id='PMKVY-Q-PLUMBING'
        ).first()
        if provider is None or qualification is None:
            first_person = Person.objects.order_by('utid').first()
            return first_person.pk if first_person else ''

        person = Person.objects.create(
            utid=utid,
            name='Anita Sharma',
            dob=date(1998, 5, 15),
            gender='F',
            category='OBC',
            district='Mumbai',
            is_active=True,
            created_by='seed_demo',
        )
        Contact.objects.create(
            person=person,
            own_phone='+919876543210',
            device_type='smartphone',
            language='hi',
        )
        for purpose, _label in CONSENT_PURPOSES:
            Consent.objects.get_or_create(
                person=person, purpose=purpose, defaults={'language': 'hi'}
            )
        Enrolment.objects.create(
            person=person,
            provider=provider,
            qualification=qualification,
            enrolment_date=date(2024, 1, 10),
            course_start_date=date(2024, 1, 15),
            course_end_date=date(2024, 3, 15),
            certified_on=date(2024, 3, 15),
            cohort='MUM-2024-Q1',
        )
        for round_no, days in enumerate([90, 180, 270, 365], start=1):
            due = timezone.make_aware(
                timezone.datetime.combine(date(2024, 3, 15), timezone.datetime.min.time())
            ) + timedelta(days=days)
            FollowupJob.objects.create(
                person=person,
                round=round_no,
                due_at=due,
                channel_plan='WA',
                status='PENDING',
                created_by='seed_demo',
            )

        # Without an outcome the "trainee" demo login shows an empty dashboard,
        # which makes the E0 -> E2 story impossible to walk through. Seed one
        # wage-employment outcome plus the employer record that will confirm it.
        from core.models import Employer, OutcomeEvent

        outcome = OutcomeEvent.objects.create(
            person=person,
            reference_date=date(2024, 6, 13),
            status='wage_employment',
            role_code=qualification.occupation_code,
            start_date=date(2024, 6, 1),
            wage_band='15000-19999',
            employer_name='Konkan Projects',
            employer_phone='+919876500001',
            evidence_level='E0',
            created_by='trainee',
        )
        Employer.objects.create(
            outcome=outcome,
            employer_name='Konkan Projects',
            employer_phone='+919876500001',
            employer_type='formal',
            wage_claimed='15000-19999',
        )
        return utid

    def _seed_users(self) -> None:
        User = get_user_model()
        demo_trainee_utid = self._ensure_demo_trainee()
        demo_users = [
            ('trainee', 'trainee@example.invalid', 'trainee', None, None, demo_trainee_utid),
            ('provider_a', 'provider.a@example.invalid', 'provider', 'Centre-A', 'Mumbai', None),
            ('provider_b', 'provider.b@example.invalid', 'provider', 'Centre-B', 'Pune', None),
            ('officer_mumbai', 'officer.mumbai@example.invalid', 'district_officer', None, 'Mumbai', None),
            ('officer_pune', 'officer.pune@example.invalid', 'district_officer', None, 'Pune', None),
            ('policy', 'policy@example.invalid', 'policy_officer', None, None, None),
            ('coordinator_a', 'coordinator.a@example.invalid', 'provider_coordinator', 'Centre-A', 'Mumbai', None),
        ]
        for username, email, role, provider_id, district, utid in demo_users:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'role': role,
                    'provider_id': provider_id,
                    'district': district,
                    'utid': utid,
                },
            )
            if created:
                user.set_password('demo-pass-1234')
                user.save(update_fields=['password'])
        self.stdout.write('  demo logins (password: demo-pass-1234):')
        for username, _email, role, provider_id, district, _utid in demo_users:
            self.stdout.write(f'    {username:16s} role={role}')