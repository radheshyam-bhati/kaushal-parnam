"""Intake gatekeeper (F-02).

Four checks run at entry, per docs/01-PRD.md §7 and docs/06-IMPLEMENTATION-PLAN.md
Phase 3. Every check flags the row and lets the upload continue: a flagged row
is excluded from outcome tracking but the rest of the batch is still imported.

    1. duplicate phone   — the number already belongs to another trainee
    2. age below minimum — PMKVY minimum age 15
    3. dates out of order— enrolment after course start, or a future enrolment date
    4. unregistered provider — centre not on the SDMS/SIDH/CM-YKPY/DDU-GKY master list
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from django.utils import timezone

from core.services.audit import log_event

PMKVY_MINIMUM_AGE = 15
REQUIRED_HEADERS = [
    'name', 'dob', 'gender', 'category', 'phone',
    'course', 'provider', 'enrolment_date', 'course_start_date',
]
OPTIONAL_HEADERS = [
    'second_phone', 'second_contact_name', 'second_contact_relationship',
    'device_type', 'language', 'guardian_consent', 'course_end_date', 'cohort',
]

FLAG_DUPLICATE_PHONE = 'duplicate_phone'
FLAG_UNDER_AGE = 'under_age'
FLAG_DATE_ORDER = 'date_out_of_order'
FLAG_UNREGISTERED_PROVIDER = 'unregistered_provider'
FLAG_BAD_FORMAT = 'bad_format'

FLAG_LABELS = {
    FLAG_DUPLICATE_PHONE: 'Duplicate phone number',
    FLAG_UNDER_AGE: 'Age below the course minimum',
    FLAG_DATE_ORDER: 'Dates out of order',
    FLAG_UNREGISTERED_PROVIDER: 'Unregistered provider',
    FLAG_BAD_FORMAT: 'Missing or unreadable field',
}


@dataclass
class RowResult:
    row_number: int
    data: dict
    flags: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def is_flagged(self) -> bool:
        return bool(self.flags)

    def flag(self, code: str, message: str) -> None:
        if code not in self.flags:
            self.flags.append(code)
            self.messages.append(message)


@dataclass
class GatekeeperReport:
    total_rows: int = 0
    accepted_rows: int = 0
    results: list[RowResult] = field(default_factory=list)
    header_error: str = ''

    @property
    def flagged_rows(self) -> int:
        return sum(1 for result in self.results if result.is_flagged)

    @property
    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            for flag in result.flags:
                counts[flag] = counts.get(flag, 0) + 1
        return counts

    @property
    def flag_rate(self) -> float:
        if not self.total_rows:
            return 0.0
        return round(100.0 * self.flagged_rows / self.total_rows, 1)

    def summary(self) -> str:
        if self.header_error:
            return self.header_error
        parts = [f'{self.total_rows} rows read', f'{self.accepted_rows} accepted']
        for code, count in sorted(self.reason_counts.items()):
            parts.append(f'{count} flagged for {FLAG_LABELS[code].lower()}')
        return '; '.join(parts)


def _parse_date(raw: str):
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _normalise_phone(raw: str) -> str:
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if len(digits) == 10:
        return f'+91{digits}'
    if len(digits) == 12 and digits.startswith('91'):
        return f'+{digits}'
    return f'+{digits}' if digits else ''


def age_on(dob: date, on_date: date | None = None) -> int:
    on_date = on_date or timezone.localdate()
    years = on_date.year - dob.year
    if (on_date.month, on_date.day) < (dob.month, dob.day):
        years -= 1
    return years


def validate_row(
    row: dict,
    row_number: int,
    *,
    seen_phones: dict[str, int],
    existing_phones: set[str],
    registered_providers: set[str],
    today: date | None = None,
) -> RowResult:
    """Run all four checks against one CSV row."""
    today = today or timezone.localdate()
    result = RowResult(row_number=row_number, data=dict(row))

    phone = _normalise_phone(row.get('phone', ''))
    result.data['phone'] = phone
    dob = _parse_date(row.get('dob', ''))
    enrolment_date = _parse_date(row.get('enrolment_date', ''))
    course_start = _parse_date(row.get('course_start_date', ''))
    course_end = _parse_date(row.get('course_end_date', ''))
    result.data['_dob'] = dob
    result.data['_enrolment_date'] = enrolment_date
    result.data['_course_start'] = course_start
    result.data['_course_end'] = course_end

    if not phone.startswith('+91') or len(phone) != 13:
        result.flag(FLAG_BAD_FORMAT, f'Row {row_number}: phone must be +91 followed by 10 digits.')
        return result

    if not row.get('name') or dob is None:
        result.flag(FLAG_BAD_FORMAT, f'Row {row_number}: name and dob are required.')
        return result

    # 1. duplicate phone, inside the file or already in the database
    if phone in existing_phones:
        result.flag(
            FLAG_DUPLICATE_PHONE,
            f'Row {row_number}: phone {phone} already exists for another trainee. '
            'Keep both only with consent withdrawal, or merge via admin.',
        )
    elif phone in seen_phones:
        result.flag(
            FLAG_DUPLICATE_PHONE,
            f'Row {row_number}: phone {phone} already appears in row {seen_phones[phone]}.',
        )

    # 2. age below the course minimum
    person_age = age_on(dob, today)
    if person_age < PMKVY_MINIMUM_AGE:
        result.flag(
            FLAG_UNDER_AGE,
            f'Row {row_number}: trainee DOB {dob} is below the PMKVY minimum age '
            f'{PMKVY_MINIMUM_AGE}. This row is excluded from outcome tracking.',
        )

    # 3. dates out of order
    if enrolment_date and enrolment_date > today:
        result.flag(
            FLAG_DATE_ORDER,
            f'Row {row_number}: enrolment date {enrolment_date} is in the future.',
        )
    if enrolment_date and course_start and enrolment_date > course_start:
        result.flag(
            FLAG_DATE_ORDER,
            f'Row {row_number}: enrolment date {enrolment_date} is after the course '
            f'start date {course_start}.',
        )
    if course_start and course_end and course_start > course_end:
        result.flag(
            FLAG_DATE_ORDER,
            f'Row {row_number}: course start {course_start} is after the course end '
            f'{course_end}.',
        )

    # 4. unregistered provider
    provider = (row.get('provider') or '').strip()
    if provider and registered_providers and provider not in registered_providers:
        result.flag(
            FLAG_UNREGISTERED_PROVIDER,
            f'Row {row_number}: provider {provider} is not on the registered '
            'centre master list.',
        )

    seen_phones.setdefault(phone, row_number)
    return result


def check_csv(
    raw: bytes | str,
    *,
    registered_providers: set[str] | None = None,
    existing_phones: set[str] | None = None,
    today: date | None = None,
) -> GatekeeperReport:
    """Validate an uploaded CSV and return the summary report."""
    report = GatekeeperReport()

    if isinstance(raw, bytes):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            report.header_error = (
                'CSV format error: the file is not UTF-8 encoded. '
                'Download the template and re-upload.'
            )
            return report
    else:
        text = raw

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [header for header in REQUIRED_HEADERS if header not in headers]
    if missing:
        report.header_error = (
            "CSV format error: missing header(s) "
            f"{', '.join(missing)}. Download the template and re-upload."
        )
        return report

    from core.models import Contact

    if existing_phones is None:
        existing_phones = set(
            Contact.objects.values_list('own_phone', flat=True).distinct()
        )
    if registered_providers is None:
        from core.models import Provider

        registered_providers = set(
            Provider.objects.filter(is_registered=True).values_list('provider_id', flat=True)
        )

    seen_phones: dict[str, int] = {}
    for index, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k or '').strip().lower(): (v or '').strip() for k, v in raw_row.items()}
        report.results.append(
            validate_row(
                row,
                index,
                seen_phones=seen_phones,
                existing_phones=existing_phones,
                registered_providers=registered_providers,
                today=today,
            )
        )

    report.total_rows = len(report.results)
    report.accepted_rows = sum(1 for r in report.results if not r.is_flagged)
    log_event(
        component='gatekeeper',
        event_type='csv_upload',
        description=report.summary(),
        user_role='provider_coordinator',
        row_number=report.total_rows,
    )
    return report


CSV_TEMPLATE_HEADERS = REQUIRED_HEADERS + OPTIONAL_HEADERS