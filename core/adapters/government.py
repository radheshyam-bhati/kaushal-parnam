"""Government-system adapters. All MOCK or SIMULATED for the demo.

docs/08-Architecture.md §4.9. Nothing here calls a live endpoint: each adapter
returns a deterministic synthetic answer derived from the UTID, so the demo is
reproducible and no real person's data ever leaves the laptop.

Live onboarding for all of these is out of scope (docs/01-PRD.md §10).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _digest(*parts: str) -> int:
    joined = '|'.join(parts)
    return int(hashlib.sha256(joined.encode()).hexdigest()[:12], 16)


@dataclass
class AdapterResult:
    ok: bool
    demo_label: str
    source: str
    data: dict = field(default_factory=dict)
    detail: str = ''


class BaseAdapter:
    name = ''
    demo_label = 'MOCK API'

    def health(self) -> AdapterResult:
        """Adapters report their demo label so a screen can show the badge."""
        return AdapterResult(
            ok=True, demo_label=self.demo_label, source=self.name,
            detail='Demo adapter; no live call is made.',
        )


class SidhAdapter(BaseAdapter):
    """Skill India Digital Hub: UTID to SID mapping and tracking status."""

    name = 'SIDH'
    demo_label = 'MOCK API'

    def tracking_status(self, utid: str) -> AdapterResult:
        value = _digest('sidh', utid)
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source=self.name,
            data={
                'sid': f'SID-{utid[-8:]}',
                'certified': True,
                'batch': f'B{value % 40 + 1:02d}',
            },
            detail='Feed shaped like PMKVY 4.0 fields; no live call',
        )


class SdmsAdapter(BaseAdapter):
    """SDMS centre records. SIMULATED CSV of synthetic trainees."""

    name = 'SDMS'
    demo_label = 'SIMULATED DATA'

    def centre_record(self, utid: str) -> AdapterResult:
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source=self.name,
            data={'centre': f'Centre-{_digest("sdms", utid) % 30 + 1:02d}', 'seat': 'A'},
            detail='Synthetic SDMS export',
        )


class MahaswayamAdapter(BaseAdapter):
    """Mahaswayam / CM-YKPY stipend months. First real integration in the pilot."""

    name = 'Mahaswayam'
    demo_label = 'SIMULATED DATA'

    def stipend_months(self, utid: str, month: str) -> AdapterResult:
        months = _digest('mahaswayam', utid, month) % 7
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source='Mahaswayam',
            data={'month': month, 'stipend_months': months, 'dbt_ref': f'DBT{_digest("dbt", utid) % 10**8:08d}'},
            detail='Synthetic stipend record; no live Mahaswayam call',
        )

    def job_matches(self, district: str, occupation_code: str, limit: int = 5) -> list[dict]:
        """S-01 "Help me": synthetic openings by district and occupation."""
        count = _digest('jobs', district, occupation_code) % (limit + 1)
        titles = {
            '7111': 'Mason', '7125': 'Plumber', '7113': 'Carpenter',
            '7141': 'Electrician', '7131': 'Painter',
        }
        return [
            {
                'ref': f'MSW-{district[:3].upper()}-{i + 1:03d}',
                'title': titles.get(occupation_code, 'Skilled worker'),
                'district': district,
                'employer': f'Synthetic Employer {i + 1}',
                'wage_band': '15000-19999' if i % 2 == 0 else '10000-14999',
            }
            for i in range(count)
        ]


class DigiLockerAdapter(BaseAdapter):
    """Certificate check (S-08) and Work-Proof issue (S-06). MOCK only."""

    name = 'DigiLocker'
    demo_label = 'MOCK API'

    def check_certificate(self, utid: str, certificate_id: str, has_consent: bool) -> AdapterResult:
        if not has_consent:
            return AdapterResult(
                ok=False,
                demo_label=self.demo_label,
                source=self.name,
                data={'status': 'CONSENT_MISSING'},
                detail='Record-match consent required before checking a certificate',
            )
        valid = bool(certificate_id)
        return AdapterResult(
            ok=valid,
            demo_label=self.demo_label,
            source=self.name,
            data={
                'certificate_id': certificate_id,
                'status': 'VERIFIED' if valid else 'NOT_FOUND',
                'issued_by': 'Skill India Digital (simulated)',
            },
            detail='Mock verifier; real issuance needs issuer approval',
        )

    def issue_work_proof(self, payload: dict) -> AdapterResult:
        """Issue a Work-Proof. Deliberately carries no wage field."""
        # sorted() on items() yields (key, value) tuples; flatten them so the
        # digest gets strings.
        flat = [part for key, value in sorted(payload.items()) for part in (key, str(value))]
        ref = f'DL-WP-{_digest("workproof", *flat) % 10**10:010d}'
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source=self.name,
            data={'digilocker_ref': ref, 'payload': payload},
            detail='Mock issuer; real issuance needs DigiLocker issuer onboarding',
        )


class EpfoAdapter(BaseAdapter):
    """EPFO / ESIC months worked, which would produce an E4 grade."""

    name = 'EPFO/ESIC'
    demo_label = 'MOCK API + SIMULATED DATA'

    def months_worked(self, utid: str) -> AdapterResult:
        months = _digest('epfo', utid) % 24
        return AdapterResult(
            ok=months > 0,
            demo_label=self.demo_label,
            source=self.name,
            data={'uan': f'10{_digest("uan", utid) % 10**9:09d}', 'months_worked': months},
            detail='Synthetic months worked; no live EPFO call',
        )


class NcsAdapter(BaseAdapter):
    """NCS / ASEEM vacancies by district — signal 1 of 3 for the skill-gap flag."""

    name = 'NCS/ASEEM'
    demo_label = 'SIMULATED DATA'

    def vacancies(self, district: str, occupation_code: str) -> AdapterResult:
        count = _digest('ncs', district, occupation_code) % 60
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source='NCS',
            data={'vacancy_count': count, 'occupation_code': occupation_code},
            detail='Synthetic vacancy snapshot',
        )


class EShramAdapter(BaseAdapter):
    """e-Shram UAN linking. e-Shram UAN is NOT EPFO UAN — never merged."""

    name = 'e-Shram'
    demo_label = 'SIMULATED DATA'

    def lookup_uan(self, utid: str) -> AdapterResult:
        return AdapterResult(
            ok=True,
            demo_label=self.demo_label,
            source=self.name,
            data={'uan': f'10{_digest("eshram", utid) % 10**9:09d}'},
            detail='Synthetic UAN; a different population from EPFO',
        )


# Instantiate here: mapping name -> class would make every adapter call miss
# ``self``.
ADAPTERS = {
    cls.name: cls()
    for cls in (
        SidhAdapter, SdmsAdapter, MahaswayamAdapter,
        DigiLockerAdapter, EpfoAdapter, NcsAdapter, EShramAdapter,
    )
}


def all_badges() -> list[dict]:
    """Badge rows for templates/partials/integration_badges.html."""
    return [{'name': a.name, 'label': a.demo_label} for a in ADAPTERS.values()]