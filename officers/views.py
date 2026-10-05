"""District officer views: assisted follow-up tasks, E1 recording, audit sample.

docs/03-APP-FLOW.md P-05. The officer can see every trainee in their district and
nothing outside it (F-10 / docs/08-Architecture.md §5.3).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from core.decorators import role_required
from core.models import (
    AuditSample,
    FollowupTask,
    OutcomeEvent,
    OUTCOME_STATUS_CHOICES,
)
from core.services.audit import log_event
from core.services.rls import record_hidden_access, visible_people, visible_outcomes
from core.services.weights import (
    draw_claimed_job_sample,
    draw_non_replier_sample,
    draw_self_employment_sample,
    record_call_result,
    record_field_verification,
)


@login_required
@role_required('district_officer', 'policy_officer', message='This page is for district officers.')
def task_list(request):
    """P-05. Field actions operational queue for district officers."""
    district = request.user.district or 'Pune'
    base_tasks = FollowupTask.objects.filter(district=district).select_related(
        'person', 'followup_job', 'person__contact'
    ).prefetch_related('person__enrolment_set__qualification')

    open_tasks = base_tasks.filter(status='OPEN')
    today = timezone.localdate()

    # Calculate operational metrics
    pending_count = open_tasks.count()
    non_repliers_count = open_tasks.filter(task_type__in=['CALL', 'CALLBACK']).count()
    claimed_jobs_count = open_tasks.filter(task_type='EMPLOYER_CHECK').count()
    high_priority_count = open_tasks.filter(priority=1).count()
    due_today_count = open_tasks.filter(due_at__date=today).count()
    overdue_count = open_tasks.filter(due_at__date__lt=today).count()

    filter_type = request.GET.get('filter', 'all')
    tasks = open_tasks
    if filter_type == 'non_repliers':
        tasks = open_tasks.filter(task_type__in=['CALL', 'CALLBACK'])
    elif filter_type == 'claimed_jobs':
        tasks = open_tasks.filter(task_type='EMPLOYER_CHECK')
    elif filter_type == 'due_today':
        tasks = open_tasks.filter(due_at__date=today)
    elif filter_type == 'overdue':
        tasks = open_tasks.filter(due_at__date__lt=today)
    elif filter_type == 'completed':
        tasks = base_tasks.filter(status='DONE')

    # Order tasks: priority first, then due_at
    tasks = tasks.order_by('priority', 'due_at')

    people = visible_people(request.user)

    return render(request, 'officers/task_list.html', {
        'district': district,
        'today_date': today,
        'tasks': tasks,
        'filter_type': filter_type,
        'pending_count': pending_count,
        'non_repliers_count': non_repliers_count,
        'claimed_jobs_count': claimed_jobs_count,
        'high_priority_count': high_priority_count,
        'due_today_count': due_today_count,
        'overdue_count': overdue_count,
        'people_count': people.count(),
        'status_choices': FollowupTask.STATUS_CHOICES,
    })


@login_required
@require_POST
def close_task(request, task_id):
    task = get_object_or_404(FollowupTask, pk=task_id)
    if request.user.district and task.district != request.user.district:
        raise PermissionDenied("No permission to view another district's tasks.")

    status = request.POST.get('status', 'DONE')
    task.status = status if status in dict(FollowupTask.STATUS_CHOICES) else 'DONE'
    task.outcome_note = request.POST.get('note', '')[:300]
    task.assigned_to = request.user.username
    task.closed_at = timezone.now() if task.status != 'OPEN' else None
    task.save(update_fields=['status', 'outcome_note', 'assigned_to', 'closed_at'])
    log_event(
        component='officer', event_type='task_close',
        description=f'Task {task.pk} marked {task.status}',
        utid=task.person_id, user_role=request.user.role,
    )
    messages.success(request, f'Task closed as {task.get_status_display()}.')
    return redirect('officers:task_list')


@login_required
@require_POST
def record_officer_outcome(request, task_id):
    """Record what the officer heard on the call, at grade E1 (F-05 / F-07)."""
    task = get_object_or_404(FollowupTask, pk=task_id)
    if request.user.district and task.district != request.user.district:
        raise PermissionDenied("No permission to view another district's tasks.")

    status = request.POST.get('status')
    if status not in dict(OUTCOME_STATUS_CHOICES):
        messages.error(request, 'Choose one of the seven statuses.')
        return redirect('officers:task_list')

    outcome = OutcomeEvent.objects.create(
        person=task.person,
        followup_job=task.followup_job,
        reference_date=timezone.localdate(),
        status=status,
        evidence_level='E1',
        reason_codes={},
        created_by='officer',
    )
    task.status = 'DONE'
    task.outcome_note = request.POST.get('note', '')[:300]
    task.assigned_to = request.user.username
    task.closed_at = timezone.now()
    task.save(update_fields=['status', 'outcome_note', 'assigned_to', 'closed_at'])

    log_event(
        component='officer', event_type='officer_outcome',
        description=f'Officer recorded "{status}" for {task.person_id} at E1',
        utid=task.person_id, user_role='officer',
    )
    messages.success(request, f'Recorded "{status}" at grade E1.')
    return redirect('officers:task_list')


@login_required
def audit_sample(request):
    """F-08: draw the department sample and record call-back results."""
    if request.user.role != Role.DISTRICT_OFFICER and request.user.role != Role.POLICY_OFFICER:
        raise PermissionDenied('Only officers draw the audit sample.')

    people = visible_people(request.user)
    base = AuditSample.objects.filter(outcome__person__in=people)
    # Count over the whole set, then slice only for display: a sliced queryset
    # cannot be filtered again.
    called = base.filter(result_verified__isnull=False).count()
    verified = base.filter(result_verified=True).count()
    samples = base.select_related('outcome__person')[:100]

    return render(request, 'officers/audit_sample.html', {
        'samples': samples,
        'called': called,
        'verified': verified,
        'verification_rate': round(100.0 * verified / called, 1) if called else None,
        'non_replier_rate': request.GET.get('non_replier_rate', ''),
        'claimed_job_rate': request.GET.get('claimed_job_rate', ''),
        'self_employment_rate': request.GET.get('self_employment_rate', ''),
        'self_employment_checked': base.filter(
            sample_type='SELF_EMPLOYMENT', result_verified__isnull=False
        ).count(),
        'self_employment_confirmed': base.filter(
            sample_type='SELF_EMPLOYMENT', result_verified=True
        ).count(),
    })


@login_required
@require_POST
def draw_samples(request):
    non_replier_rate = request.POST.get('non_replier_rate') or None
    claimed_rate = request.POST.get('claimed_job_rate') or None
    self_emp_rate = request.POST.get('self_employment_rate') or None
    non_repliers = draw_non_replier_sample(
        float(non_replier_rate) if non_replier_rate else None
    )
    claimed = draw_claimed_job_sample(
        float(claimed_rate) if claimed_rate else None
    )
    self_employment = draw_self_employment_sample(
        float(self_emp_rate) if self_emp_rate else None
    )
    log_event(
        component='audit', event_type='audit_sample_draw',
        description=(
            f'Drew {len(non_repliers)} non-repliers, {len(claimed)} claimed jobs and '
            f'{len(self_employment)} self-employment outcomes for '
            f'{request.user.district or "all districts"}'
        ),
        user_role=request.user.role,
    )
    messages.success(
        request,
        f'Drew {len(non_repliers)} non-repliers, {len(claimed)} claimed jobs and '
        f'{len(self_employment)} self-employment outcomes. Each carries weight 1/p '
        f'so the rate can represent everyone.',
    )
    return redirect('officers:audit_sample')


@login_required
@require_POST
def verify_self_employment(request, sample_id):
    """F-12: field-verify a sampled self-employment outcome, raising it to E1."""
    # A sample outside the officer's district is invisible under the policies,
    # so the lookup misses; 404 rather than 403 because a 403 would confirm the
    # record exists. The attempt is logged either way.
    try:
        sample = AuditSample.objects.select_related('outcome__person').get(pk=sample_id)
    except AuditSample.DoesNotExist:
        record_hidden_access(request.user, 'audit sample', sample_id)
        raise Http404('No such audit sample.')

    if sample.sample_type != 'SELF_EMPLOYMENT':
        messages.error(request, 'That row is not a self-employment field sample.')
        return redirect('officers:audit_sample')

    person = sample.outcome.person
    if request.user.district and person.district != request.user.district:
        record_hidden_access(request.user, 'audit sample', sample_id)
        raise Http404("No such audit sample.")

    record_field_verification(
        sample,
        verified=request.POST.get('result') == 'verified',
        checked_by=request.user.username,
        note=(request.POST.get('note') or '')[:200],
    )
    messages.success(
        request,
        f'Field check recorded for {person.pk}: '
        + (
            'confirmed, raised to E1.'
            if request.POST.get('result') == 'verified'
            else 'not confirmed. The claim stays at E0.'
        ),
    )
    return redirect('officers:audit_sample')


@login_required
@require_POST
def record_call(request, sample_id):
    sample = get_object_or_404(AuditSample, pk=sample_id)
    verified = request.POST.get('result') == 'verified'
    record_call_result(sample, verified=verified, called_by=request.user.username)
    messages.success(
        request,
        f'Call-back recorded: {"confirmed" if verified else "could not confirm"} '
        f'(weight {sample.weight:.0f}).',
    )
    return redirect('officers:audit_sample')


@login_required
@role_required('district_officer', 'policy_officer', message='This page is for district officers.')
def officer_dashboard(request):
    """Small summary used as the officer landing page."""
    district = request.user.district
    people = visible_people(request.user)
    outcomes = visible_outcomes(request.user)
    return render(request, 'officers/dashboard.html', {
        'district': district,
        'people_count': people.count(),
        'outcome_count': outcomes.count(),
        'open_tasks': FollowupTask.objects.filter(district=district, status='OPEN').count(),
        'recent_outcomes': outcomes.order_by('-created_at')[:15],
    })