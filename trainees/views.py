"""Trainee-facing views: enrolment (F-01), outcome reply (F-05), My Data (S-04).

Every query here goes through ``core.services.rls`` so a trainee can only ever
reach their own UTID, whatever the URL says.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.views import PasswordResetView
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from core.decorators import role_required
from core.middleware import bind_scope
from core.models import (
    AuditLog,
    Consent,
    Contact,
    Enrolment,
    IdCrosswalk,
    OutcomeEvent,
    Person,
    Provider,
    Qualification,
)
from core.services import followup
from core.services.audit import log_event
from core.services.channel_router import record_reply
from core.services.evidence import effective_grade
from core.services.gatekeeper import check_csv
from core.services.otp import apply_erasure, issue_otp, verify_otp
from core.services.rls import assert_can_view_person, record_hidden_access
from core.services.retention import retention_for, wage_progression
from core.services.utid import generate_utid
from trainees.forms import (
    ConsentPassportForm,
    ContactCorrectionForm,
    OutcomeCaptureForm,
)

User = get_user_model()


# ===========================================================================
# Authentication (docs/03-APP-FLOW.md J-09)
# ===========================================================================
def home(request):
    """P-01 landing page."""
    from core.models import FollowupTask, OutcomeEvent, Person

    context = {
        'person_count': Person.objects.filter(is_active=True).count(),
        'outcome_count': OutcomeEvent.objects.count(),
        'open_tasks': FollowupTask.objects.filter(status='OPEN').count(),
        'dashboard_url': request.user.dashboard_url if request.user.is_authenticated else None,
    }
    return render(request, 'home.html', context)


def login_view(request):
    """P-13. Routes each role to its own dashboard."""
    if request.user.is_authenticated:
        return redirect(request.user.dashboard_url)
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        auth_login(request, user)
        request.session['utid'] = user.utid
        log_event(
            component='auth', event_type='login',
            description=f'{user.username} signed in as {user.role}',
            utid=user.utid, user_role=user.role,
        )
        return redirect(user.dashboard_url)
    return render(request, 'registration/login.html', {'form': form})


def logout_view(request):
    """P-14."""
    if request.user.is_authenticated:
        log_event(
            component='auth', event_type='logout',
            description=f'{request.user.username} signed out',
            utid=request.user.utid, user_role=request.user.role,
        )
    auth_logout(request)
    return redirect('/')


# ===========================================================================
# Enrolment — consent passport (F-01) and UTID generation (F-03)
# ===========================================================================
@transaction.atomic
def enrol(request):
    """P-02. Creates person, contact, four consent rows, crosswalk, enrolment."""
    form = ConsentPassportForm(request.POST or None)

    providers = Provider.objects.filter(is_registered=True).values_list(
        'provider_id', 'name'
    )
    qualifications = Qualification.objects.values_list('qualification_id', 'course_name')

    if request.method == 'POST' and form.is_valid():
        provider = Provider.objects.filter(
            provider_id=form.cleaned_data['provider_id'], is_registered=True
        ).first()
        qualification = Qualification.objects.filter(
            qualification_id=form.cleaned_data['qualification_id']
        ).first()
        if provider is None or qualification is None:
            form.add_error(
                None,
                'That training centre or course code is not on the registered list. '
                'Ask your coordinator to add it first.',
            )
        else:
            utid = generate_utid()
            # This form is public, so the request began with no role at all and
            # the policies would refuse the insert. Bind the scope the applicant
            # is acting under: they are a trainee creating their own record.
            # The UTID is minted here, so it is not a value the client chose --
            # they can only ever write rows keyed to this one id, and read back
            # only those (core/migrations/0009).
            bind_scope(user_role=Role.TRAINEE, utid=utid)
            person = Person.objects.create(
                utid=utid,
                name=form.cleaned_data['name'],
                dob=form.cleaned_data['dob'],
                gender=form.cleaned_data['gender'],
                category=form.cleaned_data['category'],
                district=form.cleaned_data['district'],
                guardian_consent=form.cleaned_data['guardian_consent'],
                created_by='trainee',
            )
            Contact.objects.create(
                person=person,
                own_phone=form.cleaned_data['own_phone'],
                second_phone=form.cleaned_data['second_phone'] or None,
                second_contact_name=form.cleaned_data['second_contact_name'] or None,
                second_contact_relationship=form.cleaned_data[
                    'second_contact_relationship'
                ] or None,
                device_type=form.cleaned_data['device_type'],
                language=form.cleaned_data['language'],
                shared_phone_flag=form.cleaned_data['device_type'] == 'shared',
            )
            for purpose in form.granted_purposes():
                Consent.objects.create(
                    person=person,
                    purpose=purpose,
                    language=form.cleaned_data['language'],
                    given_by='guardian' if form.cleaned_data['guardian_consent'] else 'trainee',
                )
            if form.cleaned_data['sdms_id']:
                IdCrosswalk.objects.create(
                    person=person, scheme='SDMS',
                    programme_id=form.cleaned_data['sdms_id'], linked_by='trainee',
                )
            # The schema enforces enrolment_date <= course_start_date. A trainee
            # enrolling after the course already began is dated to the start, so
            # the record stays consistent with the constraint the gatekeeper
            # also checks on CSV intake.
            today = timezone.localdate()
            course_start = form.cleaned_data['course_start_date']
            enrolment = Enrolment.objects.create(
                person=person,
                provider=provider,
                qualification=qualification,
                enrolment_date=min(today, course_start),
                course_start_date=form.cleaned_data['course_start_date'],
                course_end_date=form.cleaned_data['course_end_date'],
                certified_on=form.cleaned_data['course_end_date'],
                cohort=provider.provider_id,
            )
            jobs = followup.schedule_rounds_for_enrolment(enrolment)
            followup.schedule_missing_rounds()

            log_event(
                component='enrolment', event_type='enrolment_create',
                description=(
                    f'Enrolment created at {provider.provider_id}; '
                    f'{len(form.granted_purposes())} consent purposes granted; '
                    f'{len(jobs)} follow-up rounds scheduled'
                ),
                utid=utid, user_role='trainee',
            )

            # A demo-friendly login so the walkthrough can continue straight on.
            username = f'trainee_{utid.replace("-", "_").lower()}'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'role': Role.TRAINEE, 'utid': utid, 'email': ''},
            )
            if created:
                user.set_password('demo-pass-1234')
                user.save(update_fields=['password'])
            auth_login(request, user)

            if jobs:
                greeting = (
                    f'Welcome, {person.name}. Your unique ID is {utid}. '
                    f'Your first follow-up is scheduled for '
                    f'{jobs[0].due_at:%d %B %Y}.'
                )
            else:
                greeting = (
                    f'Welcome, {person.name}. Your unique ID is {utid}. '
                    f'No follow-up is scheduled yet.'
                )
            messages.success(request, greeting)
            return redirect('trainees:dashboard')

    return render(request, 'trainees/enrolment.html', {
        'form': form,
        'providers': providers,
        'qualifications': qualifications,
    })


# ===========================================================================
# Trainee dashboard — own history only (F-10, P-03)
# ===========================================================================
@login_required
@role_required(
    'trainee', 'employer',
    message='This dashboard shows one trainee record. Providers see their cohort '
            'dashboard instead.',
)
def dashboard(request):
    """P-03."""
    user = request.user
    if not user.utid:
        return render(request, 'trainees/dashboard.html', {
            'person': None,
            'no_identity': True,
        })
    person = get_object_or_404(Person, pk=user.utid)
    assert_can_view_person(user, person)

    outcomes = list(
        OutcomeEvent.objects.filter(person=person)
        .select_related('followup_job')
        .prefetch_related('evidence_set')
        .order_by('-reference_date', '-created_at')
    )
    jobs = list(
        person.followup_set.all().order_by('round')
    )
    next_job = next((job for job in jobs if job.status in ('PENDING', 'SENT')), None)
    consent_rows = _consent_rows(person)
    latest = outcomes[0] if outcomes else None
    if latest is not None:
        latest = OutcomeEvent.objects.select_related('employer', 'work_proof').get(
            pk=latest.pk
        )

    return render(request, 'trainees/dashboard.html', {
        'person': person,
        'outcomes': outcomes,
        'jobs': jobs,
        'next_job': next_job,
        'consent_rows': consent_rows,
        'latest': latest,
        'latest_grade': effective_grade(latest) if latest else None,
        'consent_has_record_match': person.consent_active('record_match'),
        'retention': retention_for(person) if outcomes else None,
        'wage_progression': wage_progression(person) if outcomes else None,
        'consent_withdrawal_stop': settings.CONSENT_WITHDRAWAL_STOP,
    })


# ===========================================================================
# Outcome capture (F-05) — trainee and follow-up link
# ===========================================================================
def outcome_reply(request, job_id):
    """Reply to a follow-up round from a link, or edit your own latest answer."""
    from core.models import FollowupJob

    job = get_object_or_404(FollowupJob, pk=job_id)
    person = job.person
    if request.user.is_authenticated:
        assert_can_view_person(request.user, person)

    form = OutcomeCaptureForm(request.POST or None, initial={
        'reference_date': timezone.localdate(),
    })

    if request.method == 'POST' and form.is_valid():
        outcome = _save_outcome(person, form, job=job, actor='trainee')
        if job.pk:
            record_reply(job, outcome)
        log_event(
            component='outcome', event_type='outcome_create',
            description=f'Trainee submitted status {form.cleaned_data["status"]} at E0',
            utid=person.pk, user_role='trainee',
        )
        messages.success(
            request,
            'Your outcome recorded. Thank you — this is what makes the placement '
            'rate honest for your centre.',
        )
        next_url = request.GET.get('next')
        return redirect(next_url or 'trainees:dashboard')

    return render(request, 'trainees/outcome_form.html', {
        'form': form,
        'person': person,
        'job': job,
        'round': job.round,
    })


@login_required
@role_required(
    'trainee', 'district_officer', 'policy_officer',
    message='Only the trainee, an officer, or a policy officer can record an outcome here.',
)
def outcome_capture(request):
    """Capture an outcome outside a follow-up round (officer or trainee entry)."""
    person = _person_for_user(request)
    if person is None:
        return HttpResponseForbidden('No trainee record is linked to this login.')

    job = None
    if request.GET.get('round'):
        from core.models import FollowupJob

        job = FollowupJob.objects.filter(
            person=person, round=request.GET['round']
        ).first()

    form = OutcomeCaptureForm(request.POST or None, initial={
        'reference_date': timezone.localdate(),
    })
    if request.method == 'POST' and form.is_valid():
        outcome = _save_outcome(
            person, form, job=job,
            actor=request.user.role,
            evidence_level='E1' if request.user.is_staff_side else 'E0',
        )
        if job is not None:
            record_reply(job, outcome)
        messages.success(request, f'Outcome saved at grade {outcome.evidence_level}.')
        return redirect(request.user.dashboard_url)

    return render(request, 'trainees/outcome_form.html', {
        'form': form, 'person': person, 'job': job,
        'round': job.round if job else None,
    })


def _save_outcome(person, form, *, job, actor: str, evidence_level: str = 'E0'):
    """Append one outcome event. Never updates an existing one (append-only)."""
    from core.models import Reason

    data = form.cleaned_data
    status = data['status']
    wage_band = data.get('wage_band')
    self_employment_activity = None
    income_band = None
    months_active = None
    establishment_name = None
    stipend_band = None
    apprenticeship_months = None

    if status == 'self_employment':
        self_employment_activity = data.get('self_employment_activity')
        income_band = data.get('income_band')
        months_active = data.get('months_active')
    elif status == 'apprenticeship':
        establishment_name = data.get('establishment_name')
        stipend_band = data.get('stipend_band')
        apprenticeship_months = data.get('apprenticeship_months')

    outcome = OutcomeEvent.objects.create(
        person=person,
        followup_job=job,
        reference_date=data['reference_date'],
        status=status,
        role_code=data.get('role_code') or None,
        start_date=data.get('start_date'),
        wage_band=wage_band,
        employer_name=data.get('employer_name') or establishment_name,
        employer_phone=data.get('employer_phone') or None,
        evidence_level=evidence_level,
        reason_codes=data['reason_codes'],
        self_employment_activity=self_employment_activity,
        income_band=income_band,
        months_active=months_active,
        stipend_band=stipend_band,
        apprenticeship_months=apprenticeship_months,
        created_by=actor,
    )

    for group, codes in data['reason_codes'].items():
        for code in codes:
            Reason.objects.create(outcome=outcome, group_name=group, code=code)
    if data.get('free_text'):
        Reason.objects.create(
            outcome=outcome, group_name='personal', code='free_text',
            text=data['free_text'], is_free_text=True,
        )

    if status == 'wage_employment' and data.get('employer_phone'):
        from core.services.employer_link import prepare_employer_link

        prepare_employer_link(outcome)
    return outcome


def _consent_rows(person) -> list[dict]:
    """One row per purpose, including purposes never granted."""
    from core.models import CONSENT_PURPOSES, CONSENT_PURPOSE_LABELS

    existing = {c.purpose: c for c in person.consent_set.all()}
    rows = []
    for purpose, label in CONSENT_PURPOSES:
        consent = existing.get(purpose)
        rows.append({
            'purpose': purpose,
            'label': label,
            'consent': consent,
            'active': bool(consent and consent.is_active),
        })
    return rows


def _person_for_user(request):
    if request.user.role == Role.TRAINEE and request.user.utid:
        return Person.objects.filter(pk=request.user.utid).first()
    utid = request.GET.get('utid')
    if utid:
        person = Person.objects.filter(pk=utid).first()
        if person:
            assert_can_view_person(request.user, person)
        return person
    return None


# ===========================================================================
# Outcome drill-down (P-10)
# ===========================================================================
@login_required
def outcome_detail(request, event_id):
    # The database policies make an out-of-scope outcome invisible, so this
    # misses rather than raising, and 404 is the right answer: it does not
    # confirm the record exists. The attempt is still recorded.
    try:
        outcome = OutcomeEvent.objects.select_related(
            'person', 'followup_job'
        ).get(pk=event_id)
    except OutcomeEvent.DoesNotExist:
        record_hidden_access(request.user, 'outcome', event_id)
        raise Http404('No such outcome record.')

    # Reachable when the ORM scope and the database policy disagree, which would
    # itself be a bug; kept so the denial is never silent.
    assert_can_view_person(request.user, outcome.person)
    return render(request, 'trainees/outcome_detail.html', {
        'outcome': outcome,
        'person': outcome.person,
        'grade': effective_grade(outcome),
        'reasons': outcome.reason_set.all(),
        'evidence': outcome.evidence_set.all(),
        'employer': getattr(outcome, 'employer', None),
        'attempts': outcome.followup_job.attempts.all() if outcome.followup_job else [],
    })


# ===========================================================================
# "My Data" page (S-04)
# ===========================================================================
@login_required
@role_required(
    'trainee',
    message='"My Data" is your own record. Each role has its own dashboard.',
)
def my_data(request):
    """P-07: view, correct, withdraw consent, request erasure."""
    person = _person_for_user(request)
    if person is None:
        return render(request, 'trainees/my_data.html', {'person': None, 'no_identity': True})

    assert_can_view_person(request.user, person)
    outcomes = OutcomeEvent.objects.filter(person=person).order_by('-reference_date')
    contact = Contact.objects.filter(person=person).first()
    correction_form = ContactCorrectionForm(
        initial={
            'own_phone': contact.own_phone if contact else '',
            'second_phone': contact.second_phone if contact else '',
            'second_contact_name': contact.second_contact_name if contact else '',
            'second_contact_relationship': contact.second_contact_relationship if contact else '',
            'device_type': contact.device_type if contact else 'smartphone',
            'language': contact.language if contact else 'en',
        }
    )
    return render(request, 'trainees/my_data.html', {
        'person': person,
        'contact': contact,
        'consent_rows': _consent_rows(person),
        'outcome_rows': [
            {'outcome': o, 'grade': effective_grade(o)} for o in outcomes
        ],
        'correction_form': correction_form,
        'otp_sent': request.session.get('otp_action'),
        'otp_pending': request.session.get('otp_pending') or {},
        'otp_form_action': _otp_form_action(request),
        'is_erased': not person.is_active,
        'audit_rows': AuditLog.objects.filter(utid=person.pk)[:20],
    })


def _otp_form_action(request) -> str:
    """URL the code-entry form posts back to for the pending action."""
    from django.urls import reverse

    pending = request.session.get('otp_pending') or {}
    action = pending.get('action')
    if action == 'CORRECTION':
        return reverse('trainees:correct_contact')
    if action == 'CONSENT_CHANGE':
        return reverse('trainees:withdraw_consent', args=[pending.get('purpose', '')])
    return reverse('trainees:confirm_erasure')


def _otp_pending(request, action: str):
    """The stashed change awaiting a code, or ``None`` if it is not this action."""
    pending = request.session.get('otp_pending')
    if pending and pending.get('action') == action:
        return pending
    return None


def _otp_challenged(request, person, action: str, payload: dict):
    """Issue a code and stash the pending change. Step 1 of every S-04 change."""
    issue_otp(person, action=action)
    request.session['otp_action'] = action
    request.session['otp_utid'] = person.pk
    request.session['otp_pending'] = {'action': action, **payload}
    messages.info(
        request,
        'We sent a 6-digit code to your registered phone. Enter it below to '
        'confirm this change.',
    )
    return redirect('trainees:my_data')





@login_required
@require_POST
def withdraw_consent(request, purpose):
    """S-04: withdraw one purpose, OTP-verified. Follow-up messages stop when
    configured to. Withdrawal is a change to a compliance record, so it is
    confirmed against the phone on file rather than trusted from the session
    alone (docs/01-PRD.md §7 S-04).
    """
    from django.conf import settings

    from core.models import CONSENT_PURPOSE_LABELS

    person = _person_for_user(request)
    if person is None:
        return HttpResponseForbidden('No trainee record is linked to this login.')

    consent = Consent.objects.filter(person=person, purpose=purpose).first()
    if consent is None:
        messages.error(request, 'That consent purpose is not recorded.')
        return redirect('trainees:my_data')

    if consent.withdrawn_at is not None:
        messages.info(
            request,
            f"'{CONSENT_PURPOSE_LABELS[purpose]}' consent is already withdrawn.",
        )
        return redirect('trainees:my_data')

    submitted = (request.POST.get('code') or '').strip()
    if not submitted:
        return _otp_challenged(
            request, person, 'CONSENT_CHANGE', {'purpose': purpose}
        )

    pending = _otp_pending(request, 'CONSENT_CHANGE')
    if pending is None or pending.get('purpose') != purpose:
        messages.error(request, 'Request a fresh code for this change.')
        return redirect('trainees:my_data')
    if not verify_otp(person, action='CONSENT_CHANGE', code=submitted):
        messages.error(request, 'That code is wrong or has expired. Request a new one.')
        return redirect('trainees:my_data')

    request.session.pop('otp_pending', None)
    request.session.pop('otp_action', None)
    consent.withdraw()
    cancelled = 0
    if purpose == 'follow-up' and settings.CONSENT_WITHDRAWAL_STOP:
        cancelled = followup.mark_cancelled_jobs_for(person)

    log_event(
        component='consent', event_type='consent_withdraw',
        description=(
            f'Consent for "{CONSENT_PURPOSE_LABELS[purpose]}" withdrawn; '
            f'{cancelled} pending follow-ups cancelled'
        ),
        utid=person.pk, user_role=request.user.role,
    )
    messages.success(
        request,
        f"Withdrawing '{CONSENT_PURPOSE_LABELS[purpose]}' consent stops those "
        f"messages. Existing outcomes keep their grade, but no new ones will be "
        f"collected for this purpose.",
    )
    return redirect('trainees:my_data')


@login_required
@require_POST
def correct_contact(request):
    """S-04: correct own contact details, OTP-verified.

    A new phone number is a new way to reach the trainee, so it is the change
    most worth confirming out of band: if the session were hijacked, changing
    the phone would let the attacker keep receiving the follow-ups.
    """
    person = _person_for_user(request)
    if person is None:
        return HttpResponseForbidden('No trainee record is linked to this login.')

    form = ContactCorrectionForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Please correct the highlighted fields.')
        return redirect('trainees:my_data')

    submitted = (request.POST.get('code') or '').strip()
    if not submitted:
        return _otp_challenged(request, person, 'CORRECTION', {})

    pending = _otp_pending(request, 'CORRECTION')
    if pending is None:
        messages.error(request, 'Request a fresh code to save these details.')
        return redirect('trainees:my_data')
    if not verify_otp(person, action='CORRECTION', code=submitted):
        messages.error(request, 'That code is wrong or has expired. Request a new one.')
        return redirect('trainees:my_data')

    request.session.pop('otp_pending', None)
    request.session.pop('otp_action', None)
    Contact.objects.update_or_create(
        person=person,
        defaults=form.cleaned_data | {'shared_phone_flag': form.cleaned_data['device_type'] == 'shared'},
    )
    log_event(
        component='contact', event_type='contact_correction',
        description='Trainee corrected their own contact details',
        utid=person.pk, user_role=request.user.role,
    )
    messages.success(request, 'Your contact details are updated.')
    return redirect('trainees:my_data')


@login_required
@require_POST
def request_erasure(request):
    """S-04: OTP-gated erasure. Identity soft-deleted, statistics kept."""
    person = _person_for_user(request)
    if person is None:
        return HttpResponseForbidden('No trainee record is linked to this login.')
    code = issue_otp(person, action='ERASURE')
    request.session['otp_action'] = 'ERASURE'
    request.session['otp_utid'] = person.pk
    messages.info(
        request,
        'We sent a 6-digit code to your registered phone. Enter it below to '
        'confirm the erasure request.',
    )
    return redirect('trainees:my_data')


@login_required
@require_POST
def confirm_erasure(request):
    person = _person_for_user(request)
    code = (request.POST.get('code') or '').strip()
    if not person or not verify_otp(person, action='ERASURE', code=code):
        messages.error(request, 'That code is wrong or has expired. Request a new one.')
        return redirect('trainees:my_data')

    result = apply_erasure(person, actor=request.user.role)
    request.session.pop('otp_action', None)
    messages.success(
        request,
        'Your data has been erased per your request. Aggregated statistics about '
        'your scheme are still available for policy making, and an audit trail is kept.',
    )
    return render(request, 'trainees/erasure_done.html', {'result': result, 'utid': person.pk})


# ===========================================================================
# Certificate check (S-08) — DigiLocker mock, consent required
# ===========================================================================
@login_required
@role_required('trainee', message='Certificate check is on your own trainee record.')
def certificate_check(request):
    person = _person_for_user(request)
    if person is None:
        return HttpResponseForbidden('No trainee record is linked to this login.')

    from core.adapters.government import ADAPTERS
    from core.models import CertificateCheck

    result = None
    if request.method == 'POST':
        certificate_id = (request.POST.get('certificate_id') or '').strip()
        has_consent = person.consent_active('record_match')
        result = ADAPTERS['DigiLocker'].check_certificate(
            person.pk, certificate_id, has_consent
        )
        CertificateCheck.objects.create(
            person=person,
            certificate_id=certificate_id or '(none)',
            verification_status=result.data.get('status', 'NOT_FOUND'),
        )
        log_event(
            component='digilocker', event_type='certificate_check',
            description=f'Certificate check returned {result.data.get("status")}',
            utid=person.pk, user_role=request.user.role,
        )

    history = CertificateCheck.objects.filter(person=person)[:10]
    return render(request, 'trainees/certificate_check.html', {
        'person': person, 'result': result, 'history': history,
        'has_consent': person.consent_active('record_match'),
    })


# ===========================================================================
# CSV upload with gatekeeper (F-02, P-08)
# ===========================================================================
@login_required
def csv_upload(request):
    """P-08. Provider coordinator uploads a batch; gatekeeper flags rows."""
    allowed = {Role.PROVIDER_COORDINATOR, Role.POLICY_OFFICER}
    if request.user.role not in allowed:
        log_event(
            component='rbac', event_type='permission_denied',
            description=f'{request.user.role} tried to upload a CSV',
            user_role=request.user.role,
        )
        raise PermissionDenied(
            'Only a provider coordinator can upload trainee batches. '
            'Ask your centre coordinator to do it, or ask a policy officer.'
        )

    report = None
    if request.method == 'POST':
        upload = request.FILES.get('csv_file')
        if upload is None:
            messages.error(request, 'Choose a CSV file first.')
        elif upload.size > 10 * 1024 * 1024:
            messages.error(request, 'CSV too large: maximum 10MB. Split into multiple files.')
        else:
            report = check_csv(upload.read())
    return render(request, 'trainees/csv_upload.html', {'report': report})


# ===========================================================================
# Password reset (P-15)
# ===========================================================================
class DemoPasswordResetView(PasswordResetView):
    template_name = 'registration/password_reset.html'
    form_class = PasswordResetForm
    email_template_name = 'registration/password_reset_email.txt'
    success_url = '/password-reset/done/'


password_reset = DemoPasswordResetView.as_view()