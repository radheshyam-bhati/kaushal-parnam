"""S-01 "Help me" routing and S-06 Work-Proof issue.

Both are action routes a trainee triggers from their own dashboard: a reason to
reply, and a reason for the employer to confirm.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.adapters.government import ADAPTERS
from core.models import FollowupTask, HelpRequest, OutcomeEvent, WorkProof
from core.services.audit import log_event
from core.services.evidence import effective_grade
from core.services.rls import assert_can_view_person


def _latest_outcome(person) -> OutcomeEvent | None:
    return OutcomeEvent.objects.filter(person=person).order_by(
        '-reference_date', '-created_at'
    ).first()


@login_required
def help_me(request):
    """P-11. Only offered when the latest outcome is 'not working and looking'."""
    from trainees.views import _person_for_user

    person = _person_for_user(request)
    if person is None:
        messages.error(request, 'No trainee record is linked to this login.')
        return redirect('trainees:dashboard')

    latest = _latest_outcome(person)
    eligible = bool(latest and latest.status == 'not_working_looking')

    occupation = (
        person.enrolment_set.select_related('qualification').first()
        .qualification.occupation_code
        if person.enrolment_set.exists() else '7125'
    )

    matches = []
    record = None
    if eligible and request.method == 'POST':
        option = request.POST.get('option')
        task = None
        if option == 'COUNSELLOR':
            task = FollowupTask.objects.create(
                person=person,
                district=person.district,
                task_type='CALLBACK',
                priority=2,
                note='Trainee asked for a counsellor callback (S-01)',
                due_at=timezone.now(),
            )
            messages.success(
                request,
                f'Thank you. A counsellor task (#{task.pk}) is open; an officer '
                f'will call you.',
            )
        else:
            option = 'MAHASWAYAM'
            matches = ADAPTERS['Mahaswayam'].job_matches(
                person.district, occupation, limit=5
            )
            if not matches:
                messages.info(
                    request,
                    'No matching openings right now, so a counsellor task was '
                    'created instead.',
                )
                task = FollowupTask.objects.create(
                    person=person, district=person.district, task_type='CALLBACK',
                    priority=2, note='No matching vacancy found (S-01 fallback)',
                )
            else:
                messages.success(request, f'{len(matches)} matching opening(s) found.')

        record = HelpRequest.objects.create(
            outcome=latest, option=option,
            job_matches_shown=len(matches), task=task,
        )
        log_event(
            component='help_me', event_type='help_routing',
            description=f'Trainee chose {option}; {len(matches)} matches shown; '
                        f'task={task.pk if task else "-"}',
            utid=person.pk, user_role='trainee',
        )

    return render(request, 'trainees/help_me.html', {
        'person': person,
        'eligible': eligible,
        'latest': latest,
        'matches': matches,
        'request_record': record,
        'occupation': occupation,
    })


@login_required
@require_POST
def issue_work_proof(request):
    """S-06. Only for an E2+ outcome, and only with trainee consent."""
    from trainees.views import _person_for_user

    person = _person_for_user(request)
    outcome = get_object_or_404(OutcomeEvent, pk=request.POST.get('outcome_id'))
    assert_can_view_person(request.user, outcome.person)

    if effective_grade(outcome) not in ('E2', 'E3', 'E4'):
        messages.error(
            request,
            'A Work-Proof is only issued for an outcome an employer has confirmed '
            '(grade E2 or above). Yours is still a self-report.',
        )
        return redirect('trainees:dashboard')

    if not person.consent_active('record_match'):
        messages.error(
            request,
            'You have not given record-match consent, so we cannot issue a '
            'Work-Proof. You can change that on the "My Data" page.',
        )
        return redirect('trainees:dashboard')

    employer = getattr(outcome, 'employer', None)
    # Deliberately no wage field: the record proves employment, not earnings.
    result = ADAPTERS['DigiLocker'].issue_work_proof({
        'trainee_utid': outcome.person_id,
        'employer': employer.employer_name if employer else (outcome.employer_name or ''),
        'role_code': outcome.role_code or '',
        'start_date': str(outcome.start_date or ''),
        'still_employed': str(employer.still_employed) if employer else 'unknown',
    })
    certificate_id = f'WP-{outcome.pk:06d}'
    WorkProof.objects.update_or_create(
        outcome=outcome,
        defaults={
            'certificate_id': certificate_id,
            'employer_name': result.data['payload']['employer'],
            'role_code': outcome.role_code,
            'start_date': outcome.start_date,
            'still_employed': employer.still_employed if employer else None,
            'digilocker_ref': result.data['digilocker_ref'],
        },
    )
    log_event(
        component='digilocker', event_type='work_proof_issued',
        description=f'Work-Proof {certificate_id} issued to DigiLocker (MOCK) '
                    f'for outcome {outcome.pk}; no wage field included',
        utid=person.pk, user_role='trainee',
    )
    messages.success(
        request,
        f'Work-Proof {certificate_id} issued to your DigiLocker '
        f'(reference {result.data["digilocker_ref"]}). It contains your employer, '
        f'role, start date and whether you are still working — never your wage.',
    )
    return redirect('trainees:dashboard')