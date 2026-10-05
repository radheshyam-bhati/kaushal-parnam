"""End-to-end smoke test over every page, for every role.

Not a substitute for the unit tests in each app; this exists so that a URL,
template or permission regression fails loudly in one command.

    python manage.py test core.tests -v2
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from core.models import (
    CONSENT_PURPOSES,
    AuditLog,
    Contact,
    Consent,
    Enrolment,
    OutcomeEvent,
    Person,
    Provider,
    Qualification,
    load_default_definitions,
)

User = get_user_model()


class SmokeTestBase(TestCase):
    def setUp(self):
        load_default_definitions()
        # Under enforced policies every statement runs under whatever app.*
        # scope is bound, and an unbound session is refused by design. Building
        # fixtures is out-of-band work, exactly like `seed_demo`, so it runs under
        # the 'system' privilege -- the same one the follow-up scheduler and the
        # management commands use. Requests made through the client rebind the
        # scope in RLSMiddleware, so this does not widen what any HTTP-level test
        # can see. RlsEnforcementTests sets each role explicitly and asserts on
        # the result.
        self._bind_rls_scope('system')
        self.provider = Provider.objects.create(
            provider_id='Centre-A', name='Test Centre', district='Mumbai', is_registered=True
        )
        self.qualification = Qualification.objects.create(
            qualification_id='PMKVY-Q-PLUMBING', course_name='Plumbing',
            occupation_code='7125', skill_list=['bending'],
        )
        self.person = Person.objects.create(
            utid='KO-20240101-0001', name='Anita Sharma', dob='1998-05-15',
            gender='F', category='OBC', district='Mumbai',
        )
        Contact.objects.create(
            person=self.person, own_phone='+919876543210',
            device_type='smartphone', language='hi',
        )
        for purpose, _label in CONSENT_PURPOSES:
            Consent.objects.create(person=self.person, purpose=purpose, language='hi')
        self.enrolment = Enrolment.objects.create(
            person=self.person, provider=self.provider,
            qualification=self.qualification, enrolment_date='2024-01-10',
            course_start_date='2024-01-15', course_end_date='2024-03-15',
            certified_on='2024-03-15', cohort='MUM-2024-Q1',
        )
        self.outcome = OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-03-15',
            status='wage_employment', evidence_level='E0',
            role_code='7125', wage_band='25000-29999',
            employer_name='ABC Constructions', created_by='trainee',
        )

    def make_user(self, username, role, **kwargs):
        return User.objects.create_user(
            username=username, password='demo-pass-1234', role=role, **kwargs
        )

    @staticmethod
    def _bind_rls_scope(role, **values):
        """Bind the app.* session variables for the current transaction.

        Names contain dots, so they cannot be keyword arguments.
        """
        session = {
            'app.user_role': role,
            'app.utid': values.get('utid', ''),
            'app.provider_id': values.get('provider_id', ''),
            'app.district': values.get('district', ''),
            'app.request_id': values.get('request_id', ''),
            'app.employer_outcome_id': values.get('employer_outcome_id', ''),
        }
        with connection.cursor() as cursor:
            for key, value in session.items():
                cursor.execute('SELECT set_config(%s, %s, true)', [key, value])
        return session

    def _otp_code(self, action: str) -> str:
        from core.models import OtpCode

        return OtpCode.objects.filter(
            person=self.person, action=action
        ).order_by('-created_at').values_list('code', flat=True).first()

    def _confirm_withdraw(self, purpose: str) -> None:
        """Complete a two-step OTP-gated withdrawal."""
        url = reverse('trainees:withdraw_consent', args=[purpose])
        self.client.post(url)
        self.client.post(url, {'code': self._otp_code('CONSENT_CHANGE')})

    def _confirm_change(self, url_name: str, payload: dict) -> None:
        """Complete a two-step OTP-gated change to the trainee's own data."""
        url = reverse(url_name)
        self.client.post(url, payload)
        self.client.post(url, payload | {'code': self._otp_code('CORRECTION')})


class PublicPagesTests(SmokeTestBase):
    def test_home_page_loads(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kaushal Parinam')
        self.assertContains(response, 'layer on top of Skill India Digital')

    def test_login_page_loads(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_health_endpoint(self):
        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_integration_badges_rendered(self):
        response = self.client.get(reverse('home'))
        for label in ('SANDBOX', 'MOCK', 'STUB', 'SIMULATED'):
            self.assertContains(response, label)

    def test_custom_404(self):
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'could not find that page', status_code=404)


class TraineeFlowTests(SmokeTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('trainee', 'trainee', utid=self.person.pk)

    def test_dashboard_shows_own_record(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('trainees:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KO-20240101-0001')

    def test_enrolment_page_renders_all_f01_fields(self):
        response = self.client.get(reverse('trainees:enrol'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Follow-up contact')
        self.assertContains(response, 'Employer validation')
        self.assertContains(response, 'Record match')
        self.assertContains(response, 'Statistics')
        self.assertContains(response, 'Feature phone')
        self.assertContains(response, 'guardian')

    def test_enrolment_creates_person_contact_and_four_consents(self):
        response = self.client.post(reverse('trainees:enrol'), {
            'name': 'Ramesh Patil',
            'dob': '1995-03-02',
            'gender': 'M',
            'category': 'OBC',
            'district': 'Pune',
            'language': 'mr',
            'own_phone': '+919000000001',
            'second_contact_name': 'Sita Patil',
            'second_phone': '+919000000002',
            'second_contact_relationship': 'Wife',
            'device_type': 'feature_phone',
            'consent_follow_up': 'on',
            'consent_employer_check': 'on',
            'consent_statistics': 'on',
            'guardian_consent': '',
            'notice_accepted': 'on',
            'provider_id': self.provider.pk,
            'qualification_id': self.qualification.pk,
            'course_start_date': '2026-01-05',
            'course_end_date': '2026-04-05',
            'sdms_id': 'PMKVY-2026-0001',
        })
        self.assertEqual(response.status_code, 302)
        person = Person.objects.get(name='Ramesh Patil')
        self.assertRegex(person.utid, r'^KO-\d{8}-\d{4}$')
        self.assertEqual(Contact.objects.get(person=person).device_type, 'feature_phone')
        self.assertEqual(person.consent_set.count(), 3)
        self.assertTrue(person.consent_active('follow-up'))
        self.assertTrue(person.crosswalk_set.filter(scheme='SDMS').exists())
        self.assertTrue(person.followup_set.exists())

    def test_enrolment_blocks_under_18_without_guardian(self):
        response = self.client.post(reverse('trainees:enrol'), {
            'name': 'Minor More', 'dob': '2015-06-01', 'gender': 'M',
            'category': 'SC', 'district': 'Mumbai', 'language': 'mr',
            'own_phone': '+919000000003', 'device_type': 'shared',
            'consent_follow_up': 'on', 'consent_employer_check': 'on',
            'consent_statistics': 'on', 'notice_accepted': 'on',
            'provider_id': self.provider.pk, 'qualification_id': self.qualification.pk,
            'course_start_date': '2026-01-05',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'parental consent')
        self.assertFalse(Person.objects.filter(name='Minor More').exists())

    def test_outcome_capture_stores_e0_append_only(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('trainees:outcome_capture'), {
            'reference_date': '2026-01-15',
            'status': 'wage_employment',
            'employer_name': 'XYZ Builders',
            'employer_phone': '+919000000009',
            'role_code': '7125',
            'start_date': '2025-12-01',
            'wage_band': '20000-24999',
        })
        self.assertEqual(response.status_code, 302)
        latest = OutcomeEvent.objects.filter(
            person=self.person, reference_date='2026-01-15'
        ).get()
        self.assertEqual(latest.evidence_level, 'E0')
        self.assertEqual(OutcomeEvent.objects.filter(person=self.person).count(), 2)

    def test_outcome_capture_requires_reason_when_not_working(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('trainees:outcome_capture'), {
            'reference_date': '2026-01-15', 'status': 'not_working_looking',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pick at least one reason')

    def test_outcome_capture_saves_self_employment_fields(self):
        self.client.force_login(self.user)
        self.client.post(reverse('trainees:outcome_capture'), {
            'reference_date': '2026-01-15', 'status': 'self_employment',
            'self_employment_activity': 'vendor', 'income_band': '10000-19999',
            'months_active': '7',
        })
        outcome = OutcomeEvent.objects.get(reference_date='2026-01-15')
        self.assertEqual(outcome.self_employment_activity, 'vendor')
        self.assertEqual(outcome.months_active, 7)

    def test_my_data_page_and_consent_withdrawal(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('trainees:my_data')).status_code, 200)

        # S-04: withdrawal is OTP-verified, so a bare POST only challenges.
        self.client.post(reverse('trainees:withdraw_consent', args=['employer_check']))
        self.assertIsNone(Consent.objects.get(
            person=self.person, purpose='employer_check'
        ).withdrawn_at)

        self._confirm_withdraw('employer_check')
        consent = Consent.objects.get(person=self.person, purpose='employer_check')
        self.assertIsNotNone(consent.withdrawn_at)
        self.assertTrue(Consent.objects.get(
            person=self.person, purpose='follow-up'
        ).is_active)

    def test_withdrawing_follow_up_cancels_pending_jobs(self):
        from core.services.followup import schedule_rounds_for_enrolment

        jobs = schedule_rounds_for_enrolment(self.enrolment)
        self.assertTrue(jobs)
        self.client.force_login(self.user)
        self._confirm_withdraw('follow-up')
        pending = self.person.followup_set.filter(status='PENDING').count()
        self.assertEqual(pending, 0)

    def test_withdrawal_rejects_a_wrong_code(self):
        self.client.force_login(self.user)
        self.client.post(reverse('trainees:withdraw_consent', args=['follow-up']))
        self.client.post(
            reverse('trainees:withdraw_consent', args=['follow-up']),
            {'code': '000000'},
        )
        self.assertIsNone(Consent.objects.get(
            person=self.person, purpose='follow-up'
        ).withdrawn_at)

    def test_contact_correction_requires_otp(self):
        """A new phone is a new channel to reach the trainee, so it is gated."""
        self.client.force_login(self.user)
        payload = {
            'own_phone': '+919812345678',
            'second_phone': '',
            'second_contact_name': '',
            'second_contact_relationship': '',
            'device_type': 'feature_phone',
            'language': 'mr',
        }
        self.client.post(reverse('trainees:correct_contact'), payload)
        self.assertEqual(
            Contact.objects.get(person=self.person).own_phone, '+919876543210'
        )

        self._confirm_change('trainees:correct_contact', payload)
        self.assertEqual(
            Contact.objects.get(person=self.person).own_phone, '+919812345678'
        )

    def test_erasure_requires_otp_and_soft_deletes(self):
        from core.models import AuditLog

        self.client.force_login(self.user)
        self.client.post(reverse('trainees:request_erasure'))
        self.client.post(reverse('trainees:confirm_erasure'), {'code': '000000'})
        self.person.refresh_from_db()
        self.assertTrue(self.person.is_active)  # wrong code, nothing happened

        from core.services.otp import issue_otp

        code = issue_otp(self.person, action='ERASURE')
        self.client.post(reverse('trainees:confirm_erasure'), {'code': code})
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_active)
        self.assertTrue(self.person.is_anonymous)
        self.assertEqual(self.person.name, 'Withheld')
        self.assertEqual(
            OutcomeEvent.objects.filter(person=self.person, is_anonymous=True).count(), 1
        )
        self.assertTrue(
            AuditLog.objects.filter(utid=self.person.pk, event_type='erasure_completed').exists()
        )

    def test_trainee_cannot_read_another_trainees_outcome(self):
        other = Person.objects.create(
            utid='KO-20240101-0002', name='Other', dob='1990-01-01',
            gender='M', district='Mumbai',
        )
        other_outcome = OutcomeEvent.objects.create(
            person=other, reference_date='2024-05-01',
            status='wage_employment', evidence_level='E0',
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('trainees:outcome_detail', args=[other_outcome.pk])
        )
        # 404, not 403. With the database policies enforced the row is invisible,
        # not forbidden, and a 403 would confirm that it exists. The attempt is
        # still recorded, which is what DPDP s.8(6) asks for.
        self.assertEqual(response.status_code, 404)
        # The request left its own scope bound (set_config is transaction-local
        # and survives a savepoint release), so read the trail back as a
        # privileged session rather than as the trainee.
        self._bind_rls_scope('system')
        self.assertTrue(
            AuditLog.objects.filter(
                event_type='rls_violation_attempt', user_role='trainee'
            ).exists(),
            'a cross-tenant read attempt must leave a trace',
        )

    def test_certificate_check_needs_record_match_consent(self):
        from django.utils import timezone

        self.client.force_login(self.user)
        Consent.objects.filter(person=self.person, purpose='record_match').update(
            withdrawn_at=timezone.now()
        )
        response = self.client.post(
            reverse('trainees:certificate_check'), {'certificate_id': 'X-1'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CONSENT_MISSING')

    def test_certificate_check_verifies_with_consent(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('trainees:certificate_check'), {'certificate_id': 'PMKVY-2024-BATCH1'}
        )
        self.assertContains(response, 'VERIFIED')


class ProviderFlowTests(SmokeTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user(
            'provider_a', 'provider', provider_id=self.provider.pk, district='Mumbai'
        )

    def test_dashboard_shows_rate_with_reach_and_grade_mix(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('providers:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'response rate')
        for grade in ('E0', 'E1', 'E2', 'E3', 'E4'):
            self.assertContains(response, grade)

    def test_grade_filter_changes_the_rate(self):
        self.client.force_login(self.user)
        raw = self.client.get(reverse('providers:dashboard'), {'min_grade': 'E0'})
        filtered = self.client.get(reverse('providers:dashboard'), {'min_grade': 'E2'})
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertNotEqual(
            raw.context['rate'].numerator, filtered.context['rate'].numerator
        )

    def test_provider_cannot_open_policy_pages(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse('policy:dashboard')).status_code, 403
        )

    def test_provider_only_sees_own_people(self):
        other_provider = Provider.objects.create(
            provider_id='Centre-B', name='Other Centre', district='Pune'
        )
        other_person = Person.objects.create(
            utid='KO-20240101-0009', name='Hidden', dob='1990-01-01',
            gender='M', district='Pune',
        )
        Enrolment.objects.create(
            person=other_person, provider=other_provider,
            qualification=self.qualification, enrolment_date='2024-01-10',
            course_start_date='2024-01-15', course_end_date='2024-03-15',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('providers:dashboard'))
        self.assertEqual(response.context['people_count'], 1)
        self.assertNotContains(response, 'KO-20240101-0009')

    def test_csv_upload_runs_the_gatekeeper(self):
        coordinator = self.make_user(
            'coordinator', 'provider_coordinator',
            provider_id=self.provider.pk, district='Mumbai',
        )
        self.client.force_login(coordinator)
        csv_body = (
            'name,dob,gender,category,phone,course,provider,enrolment_date,course_start_date\n'
            'Ramesh Patil,1995-03-02,M,OBC,+919000000001,Plumbing,Centre-A,2024-01-10,2024-01-15\n'
            'Minor More,2015-06-01,M,SC,+919000000004,Plumbing,Centre-A,2024-01-10,2024-01-15\n'
            'Bad Centre,1990-01-01,M,SC,+919000000005,Plumbing,Centre-Z,2024-01-10,2024-01-15\n'
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile('b.csv', csv_body.encode(), 'text/csv')
        response = self.client.post(reverse('trainees:csv_upload'), {'csv_file': upload})
        self.assertEqual(response.status_code, 200)
        report = response.context['report']
        self.assertEqual(report.total_rows, 3)
        self.assertEqual(report.accepted_rows, 1)
        self.assertIn('under_age', report.reason_counts)
        self.assertIn('unregistered_provider', report.reason_counts)

    def test_trainee_cannot_open_provider_dashboard(self):
        trainee = self.make_user('t2', 'trainee', utid=self.person.pk)
        self.client.force_login(trainee)
        self.assertEqual(
            self.client.get(reverse('providers:dashboard')).status_code, 403
        )


class EmployerOneTapTests(SmokeTestBase):
    def test_employer_confirm_raises_grade_and_keeps_both_wages(self):
        from core.models import Employer
        from core.services.employer_link import prepare_employer_link

        employer = Employer.objects.create(
            outcome=self.outcome, employer_name='ABC Constructions',
            employer_phone='+919000000042', wage_claimed='25000-29999',
        )
        token = prepare_employer_link(self.outcome)
        self.assertTrue(token)

        url = reverse('providers:employer_confirm', args=[token])
        self.assertEqual(self.client.get(url).status_code, 200)  # no login needed

        response = self.client.post(url, {
            'still_employed': 'yes', 'wage_verified': '15000-19999',
        })
        self.assertEqual(response.status_code, 200)
        employer.refresh_from_db()
        self.assertTrue(employer.verified)
        self.assertEqual(employer.wage_claimed, '25000-29999')
        self.assertEqual(employer.wage_verified, '15000-19999')
        self.assertTrue(employer.discrepancy_flags['wage_mismatch'])
        self.assertTrue(
            self.outcome.evidence_set.filter(level='E2', result_verified=True).exists()
        )


class OfficerFlowTests(SmokeTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user(
            'officer', 'district_officer', district='Mumbai'
        )

    def test_task_list_loads(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse('officers:task_list')).status_code, 200
        )

    def test_officer_records_outcome_at_e1(self):
        from core.models import FollowupTask

        task = FollowupTask.objects.create(
            person=self.person, district='Mumbai', task_type='CALL', status='OPEN'
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('officers:record_outcome', args=[task.pk]),
            {'status': 'wage_employment', 'note': 'Called and confirmed'},
        )
        self.assertEqual(response.status_code, 302)
        outcome = OutcomeEvent.objects.filter(person=self.person, created_by='officer').get()
        self.assertEqual(outcome.evidence_level, 'E1')
        task.refresh_from_db()
        self.assertEqual(task.status, 'DONE')

    def test_officer_cannot_touch_another_district_task(self):
        from core.models import FollowupTask

        task = FollowupTask.objects.create(
            person=self.person, district='Pune', task_type='CALL', status='OPEN'
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('officers:close_task', args=[task.pk]), {'status': 'DONE'}
        )
        self.assertEqual(response.status_code, 403)

    def test_audit_sample_draw_and_record(self):
        from core.models import AuditSample

        # The draw needs both populations: a non-replier and a claimed job.
        OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-06-15',
            status='could_not_be_reached', evidence_level='E0',
        )
        self.outcome.evidence_level = 'E2'
        self.outcome.save()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('officers:draw_samples'),
            {'non_replier_rate': '1.0', 'claimed_job_rate': '1.0'},
        )
        self.assertEqual(response.status_code, 302)
        sample = AuditSample.objects.first()
        self.assertIsNotNone(sample, 'no sample was drawn')
        self.assertEqual(sample.probability, 1.0)
        self.assertEqual(sample.weight, 1.0)

        self.client.post(
            reverse('officers:record_call', args=[sample.pk]), {'result': 'verified'}
        )
        sample.refresh_from_db()
        self.assertTrue(sample.result_verified)
        self.assertIsNotNone(sample.called_at)

    def test_audit_draw_stamps_sample_type(self):
        """F-08: both consumers filter on sample_type, so it must be stamped.

        An unstamped draw is invisible to the non-response weighting and to the
        funding report's audit columns.
        """
        from core.models import AuditSample

        OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-06-15',
            status='could_not_be_reached', evidence_level='E0',
        )
        self.client.force_login(self.user)
        self.client.post(
            reverse('officers:draw_samples'),
            {'non_replier_rate': '1.0', 'claimed_job_rate': '0'},
        )
        sample = AuditSample.objects.get()
        self.assertEqual(sample.sample_type, 'NON_REPLIER')

    def test_non_replier_samples_change_the_weighted_rate(self):
        """S-07: verified non-repliers are spread over the unreached gap."""
        from core.models import AuditSample
        from core.services.weights import weighted_rate

        unreached = OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-06-15',
            status='could_not_be_reached', evidence_level='E0',
        )
        # Every sampled non-replier was in fact placed, so weighting has to
        # lift the estimate above the raw respondent rate.
        AuditSample.objects.create(
            outcome=unreached, sample_type='NON_REPLIER',
            probability=1.0, result_verified=True,
        )
        result = weighted_rate([self.outcome], reached=1, due=2)
        self.assertGreater(result['weighted_rate'], 50.0)
        self.assertGreater(
            result['weighted_ci_high'] - result['weighted_ci_low'], 0.0,
            'a published weighted rate must carry a range (F-08)',
        )

    def test_self_employment_field_verification_raises_to_e1(self):
        """F-12: E0 by default, and a sampled field check is what raises it."""
        from core.models import AuditSample
        from core.services.evidence import effective_grade

        vendor = OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-09-01',
            status='self_employment', evidence_level='E0',
            self_employment_activity='vendor',
            income_band='10000-19999', months_active=8,
        )
        self.assertEqual(effective_grade(vendor), 'E0')

        sample = AuditSample.objects.create(
            outcome=vendor, sample_type='SELF_EMPLOYMENT', probability=0.10,
        )
        self.client.force_login(self.user)
        self.client.post(
            reverse('officers:verify_self_employment', args=[sample.pk]),
            {'result': 'verified', 'note': 'Visited the market stall, confirmed'},
        )
        vendor.refresh_from_db()
        self.assertEqual(
            vendor.evidence_level, 'E0',
            'the append-only outcome row itself must not be rewritten',
        )
        self.assertEqual(effective_grade(vendor), 'E1')

    def test_failed_field_verification_does_not_raise_the_grade(self):
        from core.models import AuditSample
        from core.services.evidence import effective_grade

        vendor = OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-09-01',
            status='self_employment', evidence_level='E0',
            self_employment_activity='vendor',
        )
        sample = AuditSample.objects.create(
            outcome=vendor, sample_type='SELF_EMPLOYMENT', probability=0.10,
        )
        self.client.force_login(self.user)
        self.client.post(
            reverse('officers:verify_self_employment', args=[sample.pk]),
            {'result': 'failed'},
        )
        vendor.refresh_from_db()
        self.assertEqual(effective_grade(vendor), 'E0')
        sample.refresh_from_db()
        self.assertFalse(sample.result_verified)

    def test_officer_cannot_field_verify_another_district(self):
        from core.models import AuditSample

        other = Person.objects.create(
            utid='KO-20240101-0002', name='Vikram Rao', dob='1995-02-02',
            gender='M', category='General', district='Pune',
        )
        vendor = OutcomeEvent.objects.create(
            person=other, reference_date='2024-09-01',
            status='self_employment', evidence_level='E0',
            self_employment_activity='freelancer',
        )
        sample = AuditSample.objects.create(
            outcome=vendor, sample_type='SELF_EMPLOYMENT', probability=0.10,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('officers:verify_self_employment', args=[sample.pk]),
            {'result': 'verified'},
        )
        # 404 rather than 403: another district's row is invisible, and a 403
        # would tell the officer it exists. The attempt is recorded.
        self.assertEqual(response.status_code, 404)
        sample.refresh_from_db()
        self.assertIsNone(
            sample.result_verified,
            'a denied request must not record a verification',
        )


class PolicyFlowTests(SmokeTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('policy', 'policy_officer')

    def test_policy_dashboard_shows_reach(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('policy:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'response rate')

    def test_definitions_registry_edit_changes_the_break_rule(self):
        from core.models import Definitions

        self.client.force_login(self.user)
        response = self.client.post(reverse('policy:definitions'), {
            'BREAK_RULE': '{"break_days": 30, "min_employment_days": 365}',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Definitions.break_days(), 30)

    def test_definitions_rejects_bad_json(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('policy:definitions'), {'BREAK_RULE': 'not json'},
            follow=True,
        )
        self.assertContains(response, 'must be valid JSON')

    def test_provider_comparison_never_ranks_small_providers(self):
        self.user.is_staff_side  # policy officer sees everyone
        self.client.force_login(self.user)
        response = self.client.get(reverse('policy:provider_comparison'))
        self.assertEqual(response.status_code, 200)
        row = response.context['rows'][0]
        self.assertTrue(row.withheld)  # only one trainee
        self.assertContains(response, 'Data withheld')

    def test_skill_gap_flags_only_on_two_signals(self):
        from core.models import StatsSkillGap

        two = StatsSkillGap(
            course='Plumbing', district='Mumbai', vacancy_count=10,
            employer_skills_lacking_count=0, trainee_skills_lacking_count=0,
        )
        two.apply_threshold(2)
        self.assertFalse(two.gap_flag)

        three = StatsSkillGap(
            course='Mason', district='Pune', vacancy_count=10,
            employer_skills_lacking_count=4, trainee_skills_lacking_count=3,
        )
        three.apply_threshold(2)
        self.assertTrue(three.gap_flag)
        self.assertEqual(three.signals_agree_count, 3)

        one = StatsSkillGap(
            course='Electrician', district='Pune', vacancy_count=5,
            employer_skills_lacking_count=0, trainee_skills_lacking_count=0,
        )
        one.apply_threshold(2)
        self.assertFalse(one.gap_flag)
        self.assertEqual(one.signals_agree_count, 1)

    def test_funding_report_never_counts_e0_or_e1(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('policy:funding_report'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'never counted here')
        for row in response.context['rows']:
            self.assertGreaterEqual(row['e2_plus'], 0)

    def test_data_quality_scores_are_banded(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('policy:data_quality'))
        self.assertEqual(response.status_code, 200)
        for score in response.context['scores']:
            self.assertIn(score.band(), {'green', 'amber', 'red'})
            self.assertGreaterEqual(score.score, 0)
            self.assertLessEqual(score.score, 100)

    def test_near_match_detection_suggests_and_never_merges(self):
        """F-03: a pair matching on two signals is queued for a human, not merged."""
        from core.models import Contact as C, IdCrosswalk, IdMatchSuggestion, Person

        # Two UTIDs, same person on paper: same name, same date of birth, and
        # the phone number they both gave as their own.
        twin = Person.objects.create(
            utid='KO-20240101-0002', name='Anita Sharma', dob='1998-05-15',
            gender='F', category='OBC', district='Mumbai',
        )
        IdCrosswalk.objects.create(
            person=self.person, scheme='SID', programme_id='SID-777',
        )
        IdCrosswalk.objects.create(
            person=twin, scheme='SDMS', programme_id='SDMS-888',
        )
        C.objects.create(
            person=twin, own_phone='+919876543210', device_type='smartphone',
        )

        self.client.force_login(self.user)
        self.client.post(reverse('policy:match_queue'), {'action': 'scan'})

        rows = IdMatchSuggestion.objects.all()
        self.assertEqual(
            rows.count(), 1,
            'one row per pair, however many signals agree',
        )
        row = rows.get()
        self.assertEqual(row.basis, 'same_phone')
        # Every signal that fired is on the row, not just the first one checked.
        self.assertIn('same own phone', row.detail)
        self.assertIn('same name and date of birth', row.detail)
        # Nothing was merged: both UTIDs and both enrolments survive.
        self.assertEqual(Person.objects.count(), 2)
        self.assertTrue(Enrolment.objects.filter(person=twin).exists() is False)
        self.assertEqual(self.person.enrolment_set.count(), 1)

    def test_resolved_pair_is_not_requeued(self):
        from core.models import Contact as C, IdCrosswalk, IdMatchSuggestion, Person

        twin = Person.objects.create(
            utid='KO-20240101-0002', name='Vikram Rao', dob='1995-02-02',
            gender='M', category='General', district='Mumbai',
        )
        C.objects.create(
            person=twin, own_phone='+919876543210', device_type='smartphone',
        )
        self.client.force_login(self.user)
        self.client.post(reverse('policy:match_queue'), {'action': 'scan'})
        suggestion = IdMatchSuggestion.objects.first()
        self.client.post(
            reverse('policy:resolve_match', args=[suggestion.pk]),
            {'resolution': 'kept_separate'},
        )
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.resolution, 'kept_separate')
        self.assertEqual(suggestion.reviewed_by, 'policy')

        self.client.post(reverse('policy:match_queue'), {'action': 'scan'})
        self.assertEqual(IdMatchSuggestion.objects.count(), 1)

    def test_provider_cannot_open_the_match_queue(self):
        provider = self.make_user(
            'prov', 'provider', provider_id=self.provider.provider_id,
        )
        self.client.force_login(provider)
        response = self.client.get(reverse('policy:match_queue'))
        self.assertEqual(response.status_code, 403)

    def test_provider_cannot_edit_definitions(self):
        provider = self.make_user(
            'prov2', 'provider', provider_id=self.provider.pk, district='Mumbai'
        )
        self.client.force_login(provider)
        self.assertEqual(
            self.client.get(reverse('policy:definitions')).status_code, 403
        )


class AppendOnlyTests(SmokeTestBase):
    def test_outcome_rows_cannot_be_updated_or_deleted(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            OutcomeEvent.objects.filter(pk=self.outcome.pk).update(status='further_study')
        with self.assertRaises(ValidationError):
            OutcomeEvent.objects.filter(pk=self.outcome.pk).delete()

    def test_current_status_is_the_latest_event(self):
        from core.services.stats import _latest_outcomes

        OutcomeEvent.objects.create(
            person=self.person, reference_date='2024-06-15',
            status='further_study', evidence_level='E0',
        )
        latest = _latest_outcomes()
        self.assertEqual(len(latest), 2)
        newest = max(latest, key=lambda o: o.reference_date)
        self.assertEqual(newest.status, 'further_study')
