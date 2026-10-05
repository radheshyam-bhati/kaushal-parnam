"""UTID generation — one internal outcome ID per trainee (F-03).

Format: ``KO-YYYYMMDD-NNNN`` (docs/01-PRD.md §11, docs/06-IMPLEMENTATION-PLAN.md
Phase 3). The sequence restarts every day and is derived from the database, so
it is deterministic for a given day and safe under concurrent enrolment.
"""

from __future__ import annotations

import re
from datetime import date

from django.conf import settings
from django.db import transaction

UTID_PATTERN = re.compile(r'^[A-Z]{2}-\d{8}-\d{4}$')
SEQUENCE_WIDTH = 4


def format_utid(day: date, sequence: int) -> str:
    prefix = settings.INTERNAL_UTID_PREFIX
    return f'{prefix}-{day:%Y%m%d}-{sequence:0{SEQUENCE_WIDTH}d}'


def is_valid_utid(value: str) -> bool:
    return bool(UTID_PATTERN.match(value or ''))


@transaction.atomic
def generate_utid(on_date: date | None = None, provider_id: str | None = None) -> str:
    """Return the next unused UTID for ``on_date``.

    Uses a row lock on the person table so two concurrent enrolments cannot
    receive the same sequence number.
    """
    from core.models import Person

    day = on_date or date.today()
    prefix = settings.INTERNAL_UTID_PREFIX
    day_prefix = f'{prefix}-{day:%Y%m%d}-'

    with transaction.atomic():
        # Lock the table for the duration so the count-then-insert is atomic.
        list(Person.objects.filter(utid__startswith=day_prefix).values_list(
            'utid', flat=True
        ).order_by('utid').select_for_update())

        existing = set(
            Person.objects.filter(utid__startswith=day_prefix)
            .values_list('utid', flat=True)
        )

    sequence = 1
    while True:
        candidate = format_utid(day, sequence)
        if candidate not in existing:
            return candidate
        sequence += 1
        if sequence > 9999:
            raise RuntimeError(
                'UTID sequence exhausted for today; widen the sequence width.'
            )