"""Template context shared by every page."""

from __future__ import annotations

from django.conf import settings


def app_settings(request):
    """Expose the policy settings and integration labels docs require on every page."""
    return {
        "app": {
            "utid_prefix": settings.INTERNAL_UTID_PREFIX,
            "min_group_size": settings.MIN_GROUP_SIZE,
            "skill_gap_threshold": settings.SKILL_GAP_THRESHOLD,
            "retention_break_days": settings.RETENTION_DEFAULT_BREAK_DAYS,
            "consent_withdrawal_stop": settings.CONSENT_WITHDRAWAL_STOP,
            "dpdp_child_age_limit": settings.DPDP_CHILD_AGE_LIMIT,
        },
        "integrations": integration_labels(),
        "current_path": request.path,
    }


def integration_labels() -> list[dict]:
    """Badge table from docs/03-APP-FLOW.md "Integration Classification (demo)".

    Rendered by templates/partials/integration_badges.html on every screen.
    """
    return [
        {"name": "Skill India Digital Hub (SIDH)", "label": "MOCK API", "kind": "MOCK API + SIMULATED DATA"},
        {"name": "SDMS records", "label": "SIMULATED", "kind": "SIMULATED DATA"},
        {"name": "Mahaswayam / CM-YKPY", "label": "SIMULATED", "kind": "SIMULATED DATA"},
        {"name": "DigiLocker (check, Work-Proof)", "label": "MOCK", "kind": "MOCK API"},
        {"name": "Aadhaar eKYC", "label": "MOCK", "kind": "MOCK API; no number stored"},
        {"name": "EPFO / ESIC", "label": "MOCK", "kind": "MOCK API + SIMULATED DATA"},
        {"name": "NCS / ASEEM", "label": "SIMULATED", "kind": "SIMULATED DATA"},
        {"name": "e-Shram", "label": "SIMULATED", "kind": "SIMULATED DATA"},
        {"name": "WhatsApp Business", "label": "SANDBOX", "kind": "SANDBOX"},
        {"name": "SMS", "label": "STUB", "kind": "PROTOTYPE STUB"},
        {"name": "IVR", "label": "STUB", "kind": "PROTOTYPE STUB"},
        {"name": "Hosting", "label": "SIMULATED", "kind": "SIMULATED (Docker on one laptop)"},
    ]