"""Dashboard filter dimensions (F-10).

docs/01-PRD.md §7 F-10 asks for district, course, provider and demographic
(gender, age band, category) filters on the dashboards. The minimum-grade
filter that ships alongside them lives in ``core.services.evidence``; this
module holds the cohort dimensions.

One rule holds everywhere these are applied: a filter narrows what is *counted*,
never what is *visible*. RLS runs first (``core.services.rls``) and every filter
is applied to an already-scoped queryset, so narrowing to one district can never
widen a provider's or an officer's scope.
"""

from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

#: Age bands in display order. Must stay in step with ``Person.age_band``.
AGE_BANDS = ['Under 18', '18-25', '26-35', '36-45', '46+']

#: Inclusive age bounds per band. ``46+`` uses a sentinel upper bound.
AGE_BAND_BOUNDS = {
    'Under 18': (0, 17),
    '18-25': (18, 25),
    '26-35': (26, 35),
    '36-45': (36, 45),
    '46+': (46, 120),
}

#: Query-string keys, in the order they appear in the filter bar.
FILTER_KEYS = ['district', 'course', 'provider', 'gender', 'age_band', 'category']


def _shift_years(value: date, years: int) -> date:
    """The same calendar day ``years`` away, clamped for 29 February."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:  # 29 Feb in a non-leap target year
        return value.replace(year=value.year - years, day=28)


def age_band_dob_range(band: str, on_date: date | None = None) -> tuple[date, date]:
    """Birth-date bounds for an age band, as a half-open ``(low, high]`` range.

    Age on ``on_date`` is completed years. Someone is at least ``low`` years old
    exactly when they were born on or before ``on_date`` shifted back ``low``
    years, and at most ``high`` exactly when they were born after ``on_date``
    shifted back ``high + 1`` years. Deriving dates rather than comparing an
    expression keeps this a plain indexed range scan on ``dob`` instead of a
    per-row property call, and it agrees with ``Person.age_band`` by
    construction.
    """
    reference = on_date or date.today()
    low, high = AGE_BAND_BOUNDS[band]
    return _shift_years(reference, high + 1), _shift_years(reference, low)


def selected_filters(params) -> dict:
    """Read the filter bar out of the query string, dropping blanks."""
    chosen = {}
    for key in FILTER_KEYS:
        value = (params.get(key) or '').strip()
        if value:
            chosen[key] = value
    return chosen


def apply_filters(people: QuerySet, filters: dict, on_date: date | None = None) -> QuerySet:
    """Narrow an already-RLS-scoped people queryset by the selected dimensions.

    ``district``, ``gender`` and ``category`` are person columns; ``course`` and
    ``provider`` reach through the enrolment. An unrecognised age band narrows
    to nothing rather than silently returning everyone, so a stale bookmark can
    never show a province-wide total while claiming to be one district.
    """
    if not filters:
        return people

    if 'district' in filters:
        people = people.filter(district=filters['district'])
    if 'gender' in filters:
        people = people.filter(gender=filters['gender'])
    if 'category' in filters:
        people = people.filter(category=filters['category'])
    if 'provider' in filters:
        people = people.filter(
            enrolment_set__provider__provider_id=filters['provider']
        )
    if 'course' in filters:
        people = people.filter(
            enrolment_set__qualification__course_name=filters['course']
        )
    if 'age_band' in filters:
        if filters['age_band'] not in AGE_BAND_BOUNDS:
            return people.none()
        earliest, latest = age_band_dob_range(filters['age_band'], on_date)
        people = people.filter(dob__gt=earliest, dob__lte=latest)

    if 'course' in filters or 'provider' in filters:
        people = people.distinct()
    return people


def filter_options(people: QuerySet) -> dict:
    """Choices for the filter bar, scoped to what the viewer may already see.

    Every list is drawn from the RLS-scoped queryset rather than from the full
    tables. Listing a district or a centre the viewer cannot open would leak
    that it exists, which for a provider means leaking a competitor's name.
    """
    from core.models import CATEGORY_CHOICES, GENDER_CHOICES

    return {
        'districts': sorted({row for row in people.values_list('district', flat=True) if row}),
        'courses': sorted({
            row for row in people.values_list(
                'enrolment_set__qualification__course_name', flat=True
            ) if row
        }),
        'providers': sorted({
            row for row in people.values_list(
                'enrolment_set__provider__provider_id', flat=True
            ) if row
        }),
        'genders': list(GENDER_CHOICES),
        'categories': list(CATEGORY_CHOICES),
        'age_bands': list(AGE_BANDS),
    }