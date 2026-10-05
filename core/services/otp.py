"""One-time codes and erasure (S-04, DPDP).

Erasure soft-deletes identity. Outcome rows are kept, marked anonymous, because
de-identified statistics are still needed for policy (docs/03-APP-FLOW.md J-05).
No Aadhaar number is ever stored (docs/01-PRD.md §12).
"""

from __future__ import annotations

import secrets

from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

from core.services.audit import log_event
from core.services.followup import mark_cancelled_jobs_for

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def issue_otp(person, action: str, channel: str = 'SMS') -> str:
    """Create and deliver a six-digit code. The demo prints it to the console."""
    from core.models import OtpCode

    code = f'{secrets.randbelow(1_000_000):06d}'
    OtpCode.objects.create(
        person=person,
        action=action,
        code=code,
        channel=channel,
        expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
    )
    if channel == 'EMAIL' and person.contact:
        pass
    if channel == 'SMS':
        phone = person.contact.own_phone if hasattr(person, 'contact') else ''
        print(f'[OTP STUB] {action} code for {person.pk}: {code} (sent by SMS to {phone})')
    else:
        send_mail(
            f'Kaushal Parinam verification code ({action})',
            f'Your verification code is {code}. It expires in {OTP_TTL_MINUTES} minutes.',
            None,
            [],
            fail_silently=True,
        )
    return code


def verify_otp(person, action: str, code: str) -> bool:
    """Consume a matching, unexpired, unused code."""
    from core.models import OtpCode

    otp = (
        OtpCode.objects.filter(person=person, action=action, consumed_at__isnull=True)
        .order_by('-created_at')
        .first()
    )
    if otp is None:
        return False
    otp.attempts += 1
    if otp.expires_at <= timezone.now() or otp.attempts > OTP_MAX_ATTEMPTS:
        otp.save(update_fields=['attempts'])
        return False
    if not secrets.compare_digest(otp.code, (code or '').strip()):
        otp.save(update_fields=['attempts'])
        return False
    otp.consume()
    return True


def apply_erasure(person, actor: str = 'trainee') -> dict:
    """Soft-delete identity, keep de-identified statistics (docs/03-APP-FLOW.md J-05)."""
    person.is_active = False
    person.is_anonymous = True
    person.name = 'Withheld'
    person.updated_at = timezone.now()
    person.updated_by = actor
    person.save(update_fields=['is_active', 'is_anonymous', 'name', 'updated_at', 'updated_by'])

    person.consent_set.filter(withdrawn_at__isnull=True).update(
        withdrawn_at=timezone.now()
    )
    cancelled = mark_cancelled_jobs_for(person)
    # The one allowed write on an append-only table: the privacy flag only.
    person.outcome_set.anonymise()

    log_event(
        component='dpdp',
        event_type='erasure_completed',
        description=(
            f'Erasure completed for {person.pk}; identity soft-deleted, '
            f'{cancelled} pending follow-ups cancelled, outcome rows kept as anonymous'
        ),
        utid=person.pk,
        user_role=actor,
    )
    return {
        'utid': person.pk,
        'jobs_cancelled': cancelled,
        'outcomes_kept': person.outcome_set.count(),
    }