"""Fair-peer provider comparison (S-02).

A raw placement rate punishes a provider for training people in a weak labour
market. The WIOA-style adjustment here regresses placement on the trainee mix
and the district labour market, then compares each provider to its expected
value and shows a 95% interval.

Rules + regression only. No ML (docs/02-TRD.md §1, docs/05-BACKEND-SCHEMA.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import DemandSnapshot, PLACEMENT_STATUSES
from core.services.rls import visible_outcomes
from core.services.weights import confidence_interval


@dataclass
class ProviderRow:
    provider_id: str
    provider_name: str
    district: str
    reached: int = 0
    placed: int = 0
    raw_rate: float = 0.0
    expected_rate: float = 0.0
    adjusted_rate: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    withheld: bool = False
    mix: dict = field(default_factory=dict)

    @property
    def gap(self) -> float:
        """Actual minus expected. Near zero means the trainee mix explains it."""
        return round(self.adjusted_rate - self.expected_rate, 1)

    @property
    def verdict(self) -> str:
        if self.withheld:
            return 'Data withheld'
        if self.gap >= 5:
            return 'Above expected'
        if self.gap <= -5:
            return 'Below expected'
        return 'In line with peers'


def district_labour_market() -> dict[str, dict]:
    """Labour-market indicators per district, from the NCS adapter snapshot."""
    indicators: dict[str, dict] = {}
    for snapshot in DemandSnapshot.objects.all():
        entry = indicators.setdefault(snapshot.district, {'vacancies': 0, 'courses': 0})
        entry['vacancies'] += snapshot.vacancy_count
        entry['courses'] += 1
    for district, entry in indicators.items():
        entry['vacancy_index'] = min(
            1.0, entry['vacancies'] / max(1, entry['courses'] * 40)
        )
    return indicators


def occupation_vacancy_index() -> dict[tuple[str, str], float]:
    """Vacancy pressure per (district, occupation), normalised to 0-1.

    A district-wide index is wrong here: Pune has real Mason vacancies and none
    for plumbing, and a Mason graduate in Pune is not in the same labour market
    as a plumber. Indexing per occupation is what lets the adjustment separate
    "weak market" from "weak teaching" (S-02).
    """
    counts: dict[tuple[str, str], int] = {}
    for snapshot in DemandSnapshot.objects.all():
        key = (snapshot.district, snapshot.occupation_code)
        counts[key] = counts.get(key, 0) + snapshot.vacancy_count
    if not counts:
        return {}
    ceiling = max(counts.values()) or 1
    return {key: min(1.0, value / ceiling) for key, value in counts.items()}


#: Feature order for the least-squares fit. Kept in one place so the design
#: matrix and the prediction function cannot drift apart.
FEATURES = ('intercept', 'sc_st', 'female', 'young', 'vacancy_index', 'feature_phone')


def _row_features(row: dict, vacancy_index: dict[tuple[str, str], float]) -> list[float]:
    return [
        1.0,                                        # intercept
        1.0 if row['category'] in ('SC', 'ST') else 0.0,
        1.0 if row['gender'] == 'F' else 0.0,
        1.0 if row['age_band'] in ('Under 18', '18-25') else 0.0,
        float(vacancy_index.get((row['district'], row['occupation_code']), 0.5)),
        1.0 if row.get('device_type') == 'feature_phone' else 0.0,
    ]


def _design_matrix(rows: list[dict], vacancy_index: dict[tuple[str, str], float]):
    """Build X (features) and y (placed 0/1) for ordinary least squares."""
    if len(rows) < 6:
        return None, None
    x = [_row_features(row, vacancy_index) for row in rows]
    y = [1.0 if row['placed'] else 0.0 for row in rows]
    return x, y


def _ols(x: list[list[float]], y: list[float]) -> list[float] | None:
    """Least squares via numpy when available, otherwise Gauss-Jordan by hand."""
    k = len(x[0])
    if len(x) <= k:
        return None
    try:
        import numpy as np

        xtx = np.array(x, dtype=float).T @ np.array(x, dtype=float)
        xty = np.array(x, dtype=float).T @ np.array(y, dtype=float)
        # Ridge term keeps the solve stable on a tiny demo dataset.
        ridge = np.eye(k) * 1e-6
        ridge[0][0] = 0.0
        try:
            return list(np.linalg.solve(xtx + ridge, xty))
        except np.linalg.LinAlgError:
            return None
    except ImportError:
        return _gauss_jordan(x, y)


def _gauss_jordan(x, y):
    k = len(x[0])
    n = len(x)
    xtx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * value for row, value in zip(x, y)) for i in range(k)]
    for i in range(k):
        pivot_row = max(range(i, k), key=lambda r: abs(xtx[r][i]))
        if abs(xtx[pivot_row][i]) < 1e-12:
            return None
        xtx[i], xtx[pivot_row] = xtx[pivot_row], xtx[i]
        xty[i], xty[pivot_row] = xty[pivot_row], xty[i]
        pivot = xtx[i][i]
        xtx[i] = [value / pivot for value in xtx[i]]
        xty[i] /= pivot
        for r in range(k):
            if r == i:
                continue
            factor = xtx[r][i]
            if factor:
                xtx[r] = [a - factor * b for a, b in zip(xtx[r], xtx[i])]
                xty[r] -= factor * xty[i]
    return xty


def _predict(coefficients, row, vacancy_index) -> float:
    return sum(
        c * f for c, f in zip(coefficients, _row_features(row, vacancy_index))
    )


def provider_comparison(user, *, min_group_size: int = 5) -> list[ProviderRow]:
    """Expected vs actual provider view (S-02).

    Providers below the minimum group size k are returned with ``withheld`` set,
    so the template can print "data withheld" instead of ranking them.
    """
    from core.models import Definitions, Provider

    min_group_size = Definitions.min_group_size() or min_group_size
    outcomes = list(visible_outcomes(user))
    vacancy_index = occupation_vacancy_index()

    # One row per trainee: the latest outcome decides placed / not placed.
    latest: dict[str, dict] = {}
    for outcome in outcomes:
        person = outcome.person
        enrolment = person.enrolment_set.select_related('qualification').first()
        latest[person.pk] = {
            'provider_id': enrolment.provider.provider_id if enrolment else None,
            'district': person.district,
            'occupation_code': enrolment.qualification.occupation_code if enrolment else '',
            'device_type': (
                person.contact.device_type
                if hasattr(person, 'contact') else 'smartphone'
            ),
            'category': person.category,
            'gender': person.gender,
            'age_band': person.age_band,
            'placed': outcome.status in PLACEMENT_STATUSES,
        }

    grouped: dict[str, list[dict]] = {}
    for row in latest.values():
        if not row['provider_id']:
            continue
        grouped.setdefault(row['provider_id'], []).append(row)

    names = {
        provider.provider_id: provider.name
        for provider in Provider.objects.all()
    }

    all_rows: list[dict] = []
    per_provider: dict[str, list[dict]] = {}
    for provider_id, rows in grouped.items():
        per_provider[provider_id] = rows
        all_rows.extend(rows)

    design, target = _design_matrix(all_rows, vacancy_index)
    coefficients = _ols(design, target) if design else None

    result: list[ProviderRow] = []
    for provider_id, rows in per_provider.items():
        reached = len(rows)
        placed = sum(1 for row in rows if row['placed'])
        districts = sorted({row['district'] for row in rows})
        row = ProviderRow(
            provider_id=provider_id,
            provider_name=names.get(provider_id, provider_id),
            district=', '.join(districts),
            reached=reached,
            placed=placed,
            raw_rate=round(100.0 * placed / reached, 1) if reached else 0.0,
        )
        if reached < min_group_size:
            row.withheld = True
            result.append(row)
            continue
        if coefficients is not None:
            expected = sum(
                _predict(coefficients, r, vacancy_index) for r in rows
            ) / reached
            row.expected_rate = round(100.0 * expected, 1)
        else:
            row.expected_rate = row.raw_rate  # too few rows to fit a model
        row.adjusted_rate = row.raw_rate
        low, high = confidence_interval(row.raw_rate, reached)
        row.ci_low, row.ci_high = low, high
        result.append(row)

    result.sort(key=lambda r: (-r.raw_rate, r.provider_id))
    return result


def weights_note(user) -> str:
    """One line explaining the adjustment, shown above the table."""
    return (
        'Expected rate is a least-squares fit on trainee mix (category, gender, '
        'age band), the phone type the follow-up was sent to, and the vacancy '
        'pressure for that occupation in that district from the NCS adapter. '
        'Actual minus expected is the only fair comparison between providers; a '
        'raw rate mostly measures the local labour market. No machine learning '
        'is involved — every number here can be reproduced by hand.'
    )

def provider_id_for(person) -> str | None:
    enrolment = person.enrolment_set.select_related('provider').first()
    return enrolment.provider.provider_id if enrolment else None
