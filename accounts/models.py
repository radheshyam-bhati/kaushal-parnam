"""RBAC user model.

docs/02-TRD.md §5 ("Django built-in auth + custom RBAC") and
docs/05-BACKEND-SCHEMA.md §3 require a `role` column on the login table plus the
provider / district scope that the Row-Level Security policies key off.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    TRAINEE = 'trainee', 'Trainee'
    PROVIDER = 'provider', 'Training Provider'
    PROVIDER_COORDINATOR = 'provider_coordinator', 'Provider Coordinator'
    DISTRICT_OFFICER = 'district_officer', 'District Officer'
    POLICY_OFFICER = 'policy_officer', 'Policy Officer (MSInS)'
    EMPLOYER = 'employer', 'Employer'


class User(AbstractUser):
    """Login identity.

    `utid` links a trainee login to the person record (F-03: one internal
    outcome ID). `provider_id` and `district` are the Row-Level Security scope
    for provider and district-officer roles (docs/08-Architecture.md §5.3).
    """

    role = models.CharField(
        max_length=32, choices=Role.choices, default=Role.TRAINEE, db_index=True
    )
    utid = models.CharField(
        max_length=20, null=True, blank=True, db_index=True,
        help_text='UTID of the person record; only for role=trainee',
    )
    provider_id = models.CharField(
        max_length=50, null=True, blank=True, db_index=True,
        help_text='Owning training centre; only for provider roles',
    )
    district = models.CharField(
        max_length=50, null=True, blank=True, db_index=True,
        help_text='Owning district; only for district_officer',
    )
    phone = models.CharField(max_length=15, null=True, blank=True)

    class Meta:
        db_table = 'auth_user'
        indexes = [models.Index(fields=['role', 'provider_id'])]

    def __str__(self) -> str:
        return f'{self.username} ({self.role})'

    # -- scope helpers used by core.rls -------------------------------
    @property
    def is_provider_side(self) -> bool:
        return self.role in {Role.PROVIDER, Role.PROVIDER_COORDINATOR}

    @property
    def is_staff_side(self) -> bool:
        """Officer / policy roles may read beyond their own rows."""
        return self.role in {Role.DISTRICT_OFFICER, Role.POLICY_OFFICER}

    DASHBOARD_ROUTES = {
        Role.TRAINEE: 'trainees:dashboard',
        Role.EMPLOYER: 'trainees:dashboard',
        Role.PROVIDER: 'providers:dashboard',
        Role.PROVIDER_COORDINATOR: 'providers:dashboard',
        Role.DISTRICT_OFFICER: 'officers:task_list',
        Role.POLICY_OFFICER: 'policy:dashboard',
    }

    @property
    def dashboard_url(self) -> str:
        from django.urls import reverse

        return reverse(self.DASHBOARD_ROUTES.get(self.role, 'trainees:dashboard'))