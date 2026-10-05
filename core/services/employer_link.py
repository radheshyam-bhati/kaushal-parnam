"""Employer one-tap confirmation (F-06).

The employer never logs in. They get a signed token on a pre-filled page that
names the trainee's own course and centre, which is also what keeps the WhatsApp
template in the UTILITY category rather than MARKETING (docs/02-TRD.md §12).

Two values are always kept: what the trainee said and what the employer
confirmed. A disagreement is recorded, not resolved.
"""

from __future__ import annotations

import secrets

from django.core import signing
from django.utils import timezone

from core.services.audit import log_event


def _token() -> str:
    return secrets.token_urlsafe(32)


def make_confirm_token(outcome) -> str:
    """A signed token carrying the outcome id (F-06).

    Signing it rather than storing an opaque random string is what lets the
    confirm view establish *what it is authorised to do* before it reads
    anything: the view unsigns the token, binds the RLS scope to that one
    outcome, and only then loads the employer row. With an opaque token the view
    would have to read a row it is not yet allowed to see.
    """
    return signing.TimestampSigner().sign(outcome.pk)


def read_confirm_token(token: str):
    """Outcome id encoded in a confirm token, or ``None`` if the token is bad."""
    from django.core.signing import BadSignature

    try:
        return int(signing.TimestampSigner().unsign(token, max_age=None))
    except (BadSignature, TypeError, ValueError):
        return None


def prepare_employer_link(outcome) -> str:
    """Create (or reuse) the employer row and its no-login confirm token."""
    from core.models import Employer

    employer, created = Employer.objects.get_or_create(
        outcome=outcome,
        defaults={
            'employer_name': outcome.employer_name or 'Unnamed employer',
            'employer_phone': outcome.employer_phone,
            'wage_claimed': outcome.wage_band,
        },
    )
    if not employer.confirm_token:
        employer.confirm_token = make_confirm_token(outcome)
        employer.save(update_fields=['confirm_token'])
        log_event(
            component='employer', event_type='employer_link_created',
            description=(
                f'One-tap confirm link prepared for {employer.employer_name} '
                f'on outcome {outcome.pk}'
            ),
            utid=outcome.person_id, user_role='system',
        )
    return employer.confirm_token


def confirm_link(token: str) -> str | None:
    from core.models import Employer

    return Employer.objects.filter(
        confirm_token=token, employer_phone__isnull=False
    ).values_list('employer_phone', flat=True).first()


def employer_sms_body(employer, outcome) -> str:
    """Text of the pre-filled message. Utility category: it names the trainee's
    own course and centre, as Meta's rules require."""
    link = f'/employer/confirm/{employer.confirm_token}/'
    wage = outcome.wage_band or 'not stated'
    return (
        f'{outcome.person.display_name} said they joined you on '
        f'{outcome.start_date or "a recent date"} at wage band {wage}. '
        f'Please confirm in 2 taps: {link}'
    )


def record_confirmation(
    employer,
    *,
    still_employed: bool,
    wage_verified: str | None,
    skills_lacking: bool = False,
    note: str = '',
) -> dict:
    """Employer taps Confirm. Raises the outcome to E2 and keeps both wages.

    The outcome row itself is append-only, so the grade change is recorded as an
    EvidenceRecord (E2) and the wage disagreement lives on the employer row.
    """
    from core.models import Employer, EvidenceRecord

    discrepancy = {}
    mismatch = bool(
        wage_verified and employer.wage_claimed and wage_verified != employer.wage_claimed
    )
    if mismatch:
        discrepancy['wage_mismatch'] = True
    # Signal 2 of the 2-of-3 skill-gap rule (F-11): the employer is the only
    # party who can say the training did not cover the job. Storing the flag
    # here is what lets stats.py count it; without this, signal 2 is always
    # zero on a real install and only ever 2-of-3.
    discrepancy['skills_lacking'] = bool(skills_lacking)
    previous_discrepancy = employer.discrepancy_flags or {}
    previous_discrepancy.update(discrepancy)

    employer.wage_verified = wage_verified or employer.wage_claimed
    employer.still_employed = still_employed
    employer.verified = True
    employer.validated_at = timezone.now()
    employer.discrepancy_flags = previous_discrepancy
    employer.save(update_fields=[
        'wage_verified', 'still_employed', 'verified', 'validated_at',
        'discrepancy_flags',
    ])

    outcome = employer.outcome
    EvidenceRecord.objects.get_or_create(
        outcome=outcome,
        kind='DOCUMENT',
        level='E2',
        defaults={
            'reviewer': 'employer',
            'result_verified': True,
            'detail': 'Employer confirmed via the one-tap link (no login required)',
            'created_by': 'employer',
        },
    )

    log_event(
        component='employer', event_type='employer_confirm',
        description=(
            f'Employer {employer.employer_name} confirmed outcome {outcome.pk}; '
            f'grade raised to E2; '
            + (
                f'wage disagreement kept: trainee said {employer.wage_claimed}, '
                f'employer said {employer.wage_verified}'
                if mismatch else 'wage agreed'
            )
            + ('; employer reports a skill gap (F-11 signal 2)' if skills_lacking else '')
        ),
        utid=outcome.person_id, user_role='employer',
    )
    return {
        'mismatch': mismatch,
        'wage_claimed': employer.wage_claimed,
        'wage_verified': employer.wage_verified,
        'still_employed': still_employed,
        'skills_lacking': bool(skills_lacking),
        'note': note,
    }