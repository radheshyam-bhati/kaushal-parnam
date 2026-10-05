"""District officer views: assisted follow-up tasks, E1 recording, audit sample.

docs/03-APP-FLOW.md P-05. The officer can see every trainee in their district and
nothing outside it (F-10 / docs/08-Architecture.md §5.3).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
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
from core.services.rls import visible_people, visible_outcomes
from core.services.weights import (
    draw_claimed_job_sample,
    draw_non_replier_sample,
    record_call_result,
)


@login_required
@role_required('district_officer', 'policy_officer', message='This page is for district officers.')
def task_list(request):
    """P-05. Open assisted follow-up tasks for the officer's district."""
    district = request.user.district
    tasks = FollowupTask.objects.filter(district=district)
    if request.GET.get('status') != 'all':
        tasks = tasks.filter(status=request.GET.get('status', 'OPEN'))

    people = visible_people(request.user)

    return render(request, 'officers/task_list.html', {
        'district': district,
        'tasks': tasks.select_related('person', 'followup_job'),
        'open_count': FollowupTask.objects.filter(district=district, status='OPEN').count(),
        'people_count': people.count(),
        'unreached_count': OutcomeEvent.objects.filter(
            status='could_not_be_reached', person__in=people
        ).count(),
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
    })


@login_required
@require_POST
def draw_samples(request):
    non_replier_rate = request.POST.get('non_replier_rate') or None
    claimed_rate = request.POST.get('claimed_job_rate') or None
    non_repliers = draw_non_replier_sample(
        float(non_replier_rate) if non_replier_rate else None
    )
    claimed = draw_claimed_job_sample(
        float(claimed_rate) if claimed_rate else None
    )
    log_event(
        component='audit', event_type='audit_sample_draw',
        description=(
            f'Drew {len(non_repliers)} non-repliers and {len(claimed)} claimed jobs '
            f'for {request.user.district or "all districts"}'
        ),
        user_role=request.user.role,
    )
    messages.success(
        request,
        f'Drew {len(non_repliers)} non-repliers and {len(claimed)} claimed jobs. '
        f'Each carries weight 1/p so the rate can represent everyone.',
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