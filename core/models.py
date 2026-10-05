"""Core data model for Kaushal Parinam (SIH26135).

Every table here is taken from docs/05-BACKEND-SCHEMA.md §1.1. Design rules that
the code must never break:

* **Append-only outcomes** — ``OutcomeEvent`` rows are inserted, never updated.
  Current status = latest row per (person, reference_date).
* **Two stores** — identity tables (Person, Contact, Consent, Enrolment, ...) vs
  statistics tables (Stats*). Statistics tables carry no names or phone numbers.
* **Row-level security** — provider sees own rows, trainee sees own UTID only,
  district officer sees own district, policy officer sees everything aggregated.
* **One external ID per scheme per trainee** — ``IdCrosswalk`` enforces this.
* **No Aadhaar number is stored anywhere.** Only reference keys.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ===========================================================================
# Choices and constants
# ===========================================================================

#: Declared device type at enrolment. Drives the channel router (F-04).
DEVICE_TYPES = [
    ('smartphone', 'Smartphone'),
    ('feature_phone', 'Feature phone'),
    ('shared', 'Shared phone'),
]

#: Contact channel used for a follow-up round (F-04).
CHANNEL_CHOICES = [
    ('WA', 'WhatsApp form'),
    ('SMS', 'SMS nudge'),
    ('IVR', 'IVR call'),
    ('OFFICER', 'Officer assisted call'),
]

CHANNEL_LABELS = dict(CHANNEL_CHOICES)

#: The 7 outcome statuses (F-05).
OUTCOME_STATUS_CHOICES = [
    ('wage_employment', 'Wage employment'),
    ('self_employment', 'Self-employment'),
    ('apprenticeship', 'Apprenticeship / stipend'),
    ('further_study', 'Further study'),
    ('not_working_looking', 'Not working - looking'),
    ('not_working_not_looking', 'Not working - not looking'),
    ('could_not_be_reached', 'Could not be reached'),
]

#: Statuses that count towards a placement rate (F-10).
PLACEMENT_STATUSES = ('wage_employment', 'self_employment', 'apprenticeship')

#: Trust grades E0-E4 (F-07).
EVIDENCE_LEVELS = [
    ('E0', 'E0 - trainee self-report'),
    ('E1', 'E1 - officer confirmed'),
    ('E2', 'E2 - employer confirmed'),
    ('E3', 'E3 - document seen'),
    ('E4', 'E4 - record matched'),
]

EVIDENCE_ORDER = ['E0', 'E1', 'E2', 'E3', 'E4']

#: Reason code groups, NCVER-style (F-05 / F-11).
REASON_GROUPS = [
    ('employment-related', 'Employment-related'),
    ('training-related', 'Training-related'),
    ('personal', 'Personal'),
]

#: Audit sample types (F-08).
SAMPLE_TYPES = [
    ('NON_REPLIER', 'Non-replier sample'),
    ('CLAIMED_JOB', 'Claimed job sample'),
    ('SELF_EMPLOYMENT', 'Self-employment field-verification sample'),
]

#: External schemes linked through the crosswalk (F-03). e-Shram UAN is NOT
#: EPFO UAN — the two registries hold different populations and are never merged.
CROSSWALK_SCHEMES = [
    ('SID', 'Skill India Digital (SID)'),
    ('SDMS', 'SDMS'),
    ('CM-YKPY', 'CM-YKPY'),
    ('DDU-GKY', 'DDU-GKY'),
    ('e-Shram', 'e-Shram UAN'),
    ('EPFO', 'EPFO UAN'),
]

#: Consent purposes (F-01).
CONSENT_PURPOSES = [
    ('follow-up', 'Follow-up contact'),
    ('employer_check', 'Employer validation'),
    ('record_match', 'Record match'),
    ('statistics', 'Statistics'),
]

CONSENT_PURPOSE_LABELS = dict(CONSENT_PURPOSES)

LANGUAGE_CHOICES = [
    ('en', 'English'),
    ('hi', 'Hindi'),
    ('mr', 'Marathi'),
]

LANGUAGE_LABELS = dict(LANGUAGE_CHOICES)

GENDER_CHOICES = [
    ('F', 'Female'),
    ('M', 'Male'),
    ('O', 'Other'),
    ('X', 'Unspecified'),
]

CATEGORY_CHOICES = [
    ('SC', 'Scheduled Caste'),
    ('ST', 'Scheduled Tribe'),
    ('OBC', 'Other Backward Class'),
    ('General', 'General'),
]

#: Reason code vocabulary (F-11). Grouped exactly as the PRD lists them.
REASON_CODES = {
    'employment-related': [
        ('pay_too_low', 'Pay too low'),
        ('business_closed', 'Business closed'),
        ('lost_job', 'Lost job'),
        ('seasonal_work_ended', 'Seasonal work ended'),
        ('no_jobs_in_area', 'No jobs in the area'),
        ('health_or_injury', 'Health or injury'),
    ],
    'training-related': [
        ('skills_lacking', 'Skills lacking'),
        ('course_did_not_match', 'Course did not match the job'),
        ('no_practical_training', 'No practical training'),
        ('certificate_not_recognised', 'Certificate not recognised'),
    ],
    'personal': [
        ('family_responsibilities', 'Family responsibilities'),
        ('travel_or_migration', 'Travel or migration'),
        ('health', 'Health'),
        ('caregiving', 'Caregiving'),
        ('moved_away', 'Moved away'),
    ],
}

REASON_LABELS = {
    code: label
    for group in REASON_CODES.values()
    for code, label in group
}

#: Wage bands in rupees per month (F-09).
WAGE_BANDS = [
    ('0-9999', 'Below ₹10,000'),
    ('10000-14999', '₹10,000 - 14,999'),
    ('15000-19999', '₹15,000 - 19,999'),
    ('20000-24999', '₹20,000 - 24,999'),
    ('25000-29999', '₹25,000 - 29,999'),
    ('30000-39999', '₹30,000 - 39,999'),
    ('40000+', '₹40,000 and above'),
]

WAGE_BAND_LABELS = dict(WAGE_BANDS)

#: Income bands for self-employment (F-12).
INCOME_BANDS = [
    ('0-4999', 'Below ₹5,000'),
    ('5000-9999', '₹5,000 - 9,999'),
    ('10000-19999', '₹10,000 - 19,999'),
    ('20000-29999', '₹20,000 - 29,999'),
    ('30000+', '₹30,000 and above'),
]

INCOME_BAND_LABELS = dict(INCOME_BANDS)

SELF_EMPLOYMENT_ACTIVITIES = [
    ('vendor', 'Street vendor'),
    ('freelancer', 'Freelancer'),
    ('micro_enterprise', 'Micro-enterprise'),
    ('agriculture', 'Agriculture / farm work'),
    ('home_based', 'Home-based work'),
    ('transport', 'Transport / driver'),
    ('other', 'Other'),
]

SELF_EMPLOYMENT_ACTIVITY_LABELS = dict(SELF_EMPLOYMENT_ACTIVITIES)


# ===========================================================================
# Identity store
# ===========================================================================
class Person(models.Model):
    """Trainee record. Soft-deleted via ``is_active``; rows are never deleted."""

    utid = models.CharField(
        max_length=20, primary_key=True, serialize=False,
        help_text='Unique Trainee ID, format KO-YYYYMMDD-NNNN (F-03)',
    )
    name = models.CharField(max_length=100)
    dob = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='X')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='General')
    district = models.CharField(max_length=50, db_index=True)
    is_active = models.BooleanField(
        default=True, help_text='Soft-delete flag: FALSE after erasure (S-04)'
    )
    is_anonymous = models.BooleanField(
        default=False, help_text='True once identity is erased; outcome rows are kept'
    )
    guardian_consent = models.BooleanField(
        default=False, help_text='Verifiable guardian consent recorded for under-18 (DPDP s.9)'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=50, null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'person'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['district']),
            models.Index(fields=['category']),
            models.Index(fields=['dob']),
        ]

    def __str__(self) -> str:
        return f'{self.utid}: {self.display_name}'

    @property
    def display_name(self) -> str:
        return 'Withheld' if self.is_anonymous else self.name

    def age_on(self, on_date=None) -> int:
        on_date = on_date or timezone.localdate()
        years = on_date.year - self.dob.year
        if (on_date.month, on_date.day) < (self.dob.month, self.dob.day):
            years -= 1
        return years

    @property
    def age_band(self) -> str:
        age = self.age_on()
        if age < 18:
            return 'Under 18'
        if age <= 25:
            return '18-25'
        if age <= 35:
            return '26-35'
        if age <= 45:
            return '36-45'
        return '46+'

    def consent_active(self, purpose: str) -> bool:
        return self.consent_set.filter(purpose=purpose, withdrawn_at__isnull=True).exists()


class Contact(models.Model):
    """Reach details and declared device type. One row per person (F-01)."""

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, primary_key=True, related_name='contact'
    )
    own_phone = models.CharField(max_length=15, help_text='+91 followed by 10 digits')
    second_phone = models.CharField(max_length=15, null=True, blank=True)
    second_contact_name = models.CharField(max_length=100, null=True, blank=True)
    second_contact_relationship = models.CharField(max_length=50, null=True, blank=True)
    device_type = models.CharField(
        max_length=20, choices=DEVICE_TYPES, default='smartphone',
        help_text='Declared at enrolment; drives the channel router (F-04)',
    )
    language = models.CharField(max_length=20, choices=LANGUAGE_CHOICES, default='en')
    shared_phone_flag = models.BooleanField(
        default=False, help_text='True when the phone is shared with family members'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contact'
        indexes = [
            models.Index(fields=['own_phone']),
            models.Index(fields=['device_type']),
            models.Index(fields=['language']),
        ]

    def __str__(self) -> str:
        return f'{self.person_id} - {self.own_phone}'

    @property
    def preferred_channel(self) -> str:
        """Channel router: device type decides the first channel (F-04)."""
        return {
            'smartphone': 'WA',
            'feature_phone': 'IVR',
            'shared': 'SMS',
        }.get(self.device_type, 'SMS')


class Consent(models.Model):
    """Purpose-specific consent. Append-only: withdrawal sets ``withdrawn_at``."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='consent_set'
    )
    purpose = models.CharField(max_length=30, choices=CONSENT_PURPOSES)
    language = models.CharField(max_length=20, choices=LANGUAGE_CHOICES, default='en')
    given_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    notice_version = models.CharField(
        max_length=20, default='1.0', help_text='Version of the notice the trainee saw'
    )
    given_by = models.CharField(
        max_length=50, default='trainee', help_text='trainee or guardian'
    )

    class Meta:
        db_table = 'consent'
        constraints = [
            models.UniqueConstraint(
                fields=['person', 'purpose'], name='uniq_consent_person_purpose'
            )
        ]
        indexes = [
            models.Index(fields=['purpose']),
            models.Index(fields=['purpose', 'withdrawn_at']),
        ]

    def __str__(self) -> str:
        state = 'withdrawn' if self.withdrawn_at else 'active'
        return f'{self.person_id} - {self.purpose} ({state})'

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None

    def withdraw(self, notice_version: str | None = None) -> None:
        """Withdraw one purpose. The row is kept for the audit trail."""
        if self.withdrawn_at is None:
            self.withdrawn_at = timezone.now()
            if notice_version:
                self.notice_version = notice_version
            self.save(update_fields=['withdrawn_at', 'notice_version'])

    def restore(self, notice_version: str | None = None) -> None:
        """Re-grant a purpose. Withdrawal history is kept in the audit log."""
        self.withdrawn_at = None
        self.given_at = timezone.now()
        if notice_version:
            self.notice_version = notice_version
        self.save(update_fields=['withdrawn_at', 'given_at', 'notice_version'])


class IdCrosswalk(models.Model):
    """Links one UTID to external scheme IDs (F-03).

    Long form from docs/05-BACKEND-SCHEMA.md §1.1: one row per (person, scheme),
    so a trainee can hold a SID, an SDMS id, an e-Shram UAN and an EPFO UAN at
    once but never two SIDs. Near matches are queued for a human in
    ``IdMatchSuggestion``; records are never auto-merged.
    """

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='crosswalk_set'
    )
    scheme = models.CharField(max_length=20, choices=CROSSWALK_SCHEMES)
    programme_id = models.CharField(max_length=50)
    linked_at = models.DateTimeField(auto_now_add=True)
    linked_by = models.CharField(max_length=50, default='system')

    class Meta:
        db_table = 'id_crosswalk'
        constraints = [
            models.UniqueConstraint(
                fields=['person', 'scheme'], name='uniq_crosswalk_person_scheme'
            ),
            models.UniqueConstraint(
                fields=['scheme', 'programme_id'], name='uniq_crosswalk_scheme_programme'
            ),
        ]
        indexes = [
            models.Index(fields=['scheme']),
            models.Index(fields=['scheme', 'programme_id']),
        ]

    def __str__(self) -> str:
        return f'{self.person_id} {self.scheme}={self.programme_id}'


class IdMatchSuggestion(models.Model):
    """A possible duplicate person, routed to a human for review (F-03).

    The system never merges two UTIDs by itself.
    """

    class MatchBasis(models.TextChoices):
        SAME_PHONE = 'same_phone', 'Same phone number'
        SAME_NAME_DOB = 'same_name_dob', 'Same name and date of birth'
        # A third basis, "same external scheme ID", is deliberately absent:
        # uniq_crosswalk_scheme_programme makes two UTIDs claiming one scheme ID
        # unrepresentable, so a check on it could never fire. If that constraint
        # is ever relaxed, add the basis and the check together.

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='match_suggestions'
    )
    candidate_utid = models.CharField(max_length=20)
    basis = models.CharField(max_length=30, choices=MatchBasis.choices)
    detail = models.CharField(max_length=200, blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=50, null=True, blank=True)
    resolution = models.CharField(
        max_length=30, blank=True, default='',
        help_text='merged | kept_separate | pending',
    )

    class Meta:
        db_table = 'id_match_suggestion'
        constraints = [
            models.UniqueConstraint(
                fields=['person', 'candidate_utid'], name='uniq_match_suggestion_pair'
            )
        ]

    def __str__(self) -> str:
        return f'{self.person_id} ~ {self.candidate_utid} ({self.basis})'


class Provider(models.Model):
    """Registered training centre / provider (SDMS / SIDH master list).

    Used by the intake gatekeeper to flag rows naming an unregistered provider
    (F-02), and by provider-scoped row-level security (F-10).
    """

    provider_id = models.CharField(max_length=50, primary_key=True, serialize=False)
    name = models.CharField(max_length=120)
    district = models.CharField(max_length=50, db_index=True)
    scheme = models.CharField(
        max_length=30, default='SDMS', help_text='SDMS, SIDH, CM-YKPY or DDU-GKY'
    )
    is_registered = models.BooleanField(
        default=True, help_text='False when the centre is not on the master list'
    )
    contact_email = models.EmailField(null=True, blank=True)
    contact_phone = models.CharField(max_length=15, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'provider'
        ordering = ['provider_id']

    def __str__(self) -> str:
        return f'{self.provider_id} - {self.name}'


class Qualification(models.Model):
    """Course with its NCO 2015 occupation code and skill list (F-03, F-11)."""

    qualification_id = models.CharField(
        max_length=50, primary_key=True, serialize=False,
        help_text='e.g. PMKVY-Q-2023-PLUMBING',
    )
    course_name = models.CharField(max_length=100)
    occupation_code = models.CharField(
        max_length=10, db_index=True, help_text='National Classification of Occupations 2015'
    )
    skill_list = models.JSONField(
        default=list, help_text='JSON list of skills, e.g. ["bending", "welding"]'
    )
    course_duration_days = models.PositiveIntegerField(default=90)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'qualification'
        ordering = ['course_name']

    def __str__(self) -> str:
        return f'{self.qualification_id}: {self.course_name}'

    @property
    def skill_count(self) -> int:
        return len(self.skill_list or [])


class Enrolment(models.Model):
    """Baseline record: course, provider, dates, district (F-03)."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='enrolment_set'
    )
    provider = models.ForeignKey(
        Provider, on_delete=models.PROTECT, related_name='enrolments'
    )
    qualification = models.ForeignKey(
        Qualification, on_delete=models.PROTECT, related_name='enrolments'
    )
    enrolment_date = models.DateField()
    course_start_date = models.DateField()
    course_end_date = models.DateField(null=True, blank=True)
    cohort = models.CharField(max_length=50, null=True, blank=True)
    certified_on = models.DateField(
        null=True, blank=True,
        help_text='Certification date; follow-up rounds are counted from here (F-04)',
    )

    class Meta:
        db_table = 'enrolment'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(course_end_date__isnull=True)
                | models.Q(course_start_date__lte=models.F('course_end_date')),
                name='chk_course_date_order',
            ),
            models.CheckConstraint(
                condition=models.Q(enrolment_date__lte=models.F('course_start_date')),
                name='chk_enrolment_before_course',
            ),
        ]
        indexes = [
            models.Index(fields=['provider']),
            models.Index(fields=['qualification']),
        ]

    def __str__(self) -> str:
        return f'{self.person_id} - {self.qualification_id} at {self.provider_id}'

    @property
    def reference_date(self):
        """Round clock starts at certification, falling back to course end."""
        return self.certified_on or self.course_end_date or self.course_start_date


# ===========================================================================
# Workflow store — follow-up jobs and contact attempts (F-04)
# ===========================================================================
class FollowupJob(models.Model):
    """One job per trainee per round. Drained with FOR UPDATE SKIP LOCKED."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('REPLIED', 'Replied'),
        ('EXPIRED', 'Expired - escalated to an officer'),
        ('CANCELLED', 'Cancelled - consent withdrawn'),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='followup_set'
    )
    round = models.PositiveSmallIntegerField(
        help_text='1=T+90, 2=T+180, 3=T+270, 4=T+365 days'
    )
    due_at = models.DateTimeField()
    channel_plan = models.CharField(
        max_length=10, choices=CHANNEL_CHOICES, help_text='Channel chosen at enrolment'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    retries = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=50, default='scheduler')
    updated_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'followup_job'
        constraints = [
            models.UniqueConstraint(
                fields=['person', 'round'], name='uniq_followup_person_round'
            ),
            models.CheckConstraint(
                condition=models.Q(round__gte=1) & models.Q(round__lte=4),
                name='chk_followup_round',
            ),
        ]
        indexes = [
            # Drained by: WHERE status='PENDING' AND due_at <= NOW()
            models.Index(fields=['status', 'due_at'], name='idx_followup_due'),
            models.Index(fields=['person', 'round']),
        ]
        ordering = ['due_at', 'person_id']

    def __str__(self) -> str:
        return f'{self.person_id} round {self.round} ({self.channel_plan})'


class ContactAttempt(models.Model):
    """Every time a channel fires, one row is appended (F-04)."""

    RESULT_CHOICES = [
        ('SENT', 'Sent'),
        ('NO_REPLY', 'No reply'),
        ('KEYWORD_REPLY', 'Keyword reply received'),
        ('FORM_REPLY', 'Form reply received'),
        ('ERROR', 'Error'),
    ]

    followup_job = models.ForeignKey(
        FollowupJob, on_delete=models.CASCADE, related_name='attempts'
    )
    attempt_number = models.PositiveSmallIntegerField(default=1)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default='SENT')
    reply_keyword = models.CharField(max_length=50, null=True, blank=True)
    reply_time = models.DateTimeField(null=True, blank=True)
    reply_json = models.JSONField(
        null=True, blank=True, help_text='Structured reply payload'
    )
    demo_label = models.CharField(
        max_length=20, default='STUB',
        help_text='SANDBOX / MOCK / STUB / SIMULATED badge for this attempt',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contact_attempt'
        constraints = [
            models.UniqueConstraint(
                fields=['followup_job', 'attempt_number'],
                name='uniq_attempt_per_job',
            )
        ]
        indexes = [
            models.Index(fields=['followup_job']),
            models.Index(fields=['channel', 'result']),
        ]
        ordering = ['followup_job_id', 'attempt_number']

    def __str__(self) -> str:
        return f'{self.followup_job_id} attempt {self.attempt_number} {self.channel}/{self.result}'


class FollowupTask(models.Model):
    """Assisted follow-up task for a district officer (F-04 escalation).

    Created when a job goes EXPIRED: three channels tried, still no reply.
    """

    TASK_TYPE_CHOICES = [
        ('CALL', 'Call the trainee'),
        ('CALLBACK', 'Counsellor callback requested (S-01)'),
        ('EMPLOYER_CHECK', 'Ask the employer'),
    ]

    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('DONE', 'Done'),
        ('CLOSED', 'Closed - trainee not reachable'),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='followup_tasks'
    )
    followup_job = models.ForeignKey(
        FollowupJob, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tasks',
    )
    district = models.CharField(max_length=50, db_index=True)
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES, default='CALL')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    priority = models.PositiveSmallIntegerField(
        default=5, help_text='1 is highest; round 4 tasks rank above round 1'
    )
    note = models.CharField(max_length=300, blank=True, default='')
    outcome_note = models.CharField(max_length=300, blank=True, default='')
    assigned_to = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'followup_task'
        indexes = [
            models.Index(fields=['district', 'status']),
            models.Index(fields=['status', 'priority']),
        ]
        ordering = ['priority', 'due_at']

    def __str__(self) -> str:
        return f'{self.get_task_type_display()} for {self.person_id} ({self.status})'


# ===========================================================================
# Outcome store — append-only events with a trust grade (F-05, F-07)
# ===========================================================================
class AppendOnlyQuerySet(models.QuerySet):
    """Blocks ``update()`` / ``delete()`` so an outcome is never overwritten.

    The one sanctioned exception is :meth:`anonymise`, used when a trainee
    requests erasure. It touches only ``is_anonymous`` — a privacy flag, not the
    outcome content — because DPDP requires the identity to go while
    de-identified statistics are retained (docs/03-APP-FLOW.md J-05).
    """

    def update(self, **kwargs):
        raise ValidationError(
            'outcome_event is append-only: insert a new event instead of updating '
            '(docs/05-BACKEND-SCHEMA.md §1.2).'
        )

    def delete(self):
        raise ValidationError(
            'outcome_event is append-only and is never deleted '
            '(docs/05-BACKEND-SCHEMA.md §2 delete rules).'
        )

    def anonymise(self):
        """Mark rows anonymous after an erasure request. Status and grade stay."""
        return models.QuerySet.update(self, is_anonymous=True)


class OutcomeEvent(models.Model):
    """One trainee statement about their status on a fixed reference date."""

    objects = AppendOnlyQuerySet.as_manager()

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='outcome_set'
    )
    followup_job = models.ForeignKey(
        FollowupJob, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='outcomes', help_text='Round this reply answers, if any',
    )
    reference_date = models.DateField(help_text='Fixed reference date, e.g. as of today')
    status = models.CharField(max_length=30, choices=OUTCOME_STATUS_CHOICES)
    role_code = models.CharField(
        max_length=10, null=True, blank=True, help_text='NCO 2015 occupation code'
    )
    start_date = models.DateField(null=True, blank=True)
    wage_band = models.CharField(max_length=20, null=True, blank=True, choices=WAGE_BANDS)
    employer_name = models.CharField(max_length=100, null=True, blank=True)
    employer_phone = models.CharField(max_length=15, null=True, blank=True)
    evidence_level = models.CharField(
        max_length=2, choices=EVIDENCE_LEVELS, default='E0'
    )
    reason_codes = models.JSONField(
        default=dict, blank=True,
        help_text='Grouped codes: {"training-related": ["skills_lacking"]}',
    )
    # Self-employment module (F-12)
    self_employment_activity = models.CharField(
        max_length=30, null=True, blank=True, choices=SELF_EMPLOYMENT_ACTIVITIES
    )
    income_band = models.CharField(
        max_length=20, null=True, blank=True, choices=INCOME_BANDS
    )
    months_active = models.PositiveSmallIntegerField(null=True, blank=True)
    # Apprenticeship / stipend (F-05)
    establishment_name = models.CharField(max_length=100, null=True, blank=True)
    stipend_band = models.CharField(max_length=20, null=True, blank=True, choices=WAGE_BANDS)
    apprenticeship_months = models.PositiveSmallIntegerField(null=True, blank=True)
    # Provenance and reach weights (F-08, S-07)
    weight = models.FloatField(
        default=1.0, help_text='Inverse-probability weight 1/p from the audit sample'
    )
    is_anonymous = models.BooleanField(
        default=False, help_text='Set on erasure; the row is kept for statistics'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(
        max_length=50, default='trainee',
        help_text='trainee | officer | policy | employer',
    )

    class Meta:
        db_table = 'outcome_event'
        indexes = [
            models.Index(fields=['person', 'reference_date']),
            models.Index(fields=['evidence_level', 'status']),
            models.Index(fields=['reference_date', 'status']),
        ]
        ordering = ['-reference_date', '-created_at']

    def __str__(self) -> str:
        return f'{self.person_id} {self.reference_date} {self.status} {self.evidence_level}'

    @property
    def is_placed(self) -> bool:
        return self.status in PLACEMENT_STATUSES

    @property
    def is_verified(self) -> bool:
        return self.evidence_level in ('E2', 'E3', 'E4')

    @property
    def grade_rank(self) -> int:
        return EVIDENCE_ORDER.index(self.evidence_level)

    def reason_labels(self) -> list[str]:
        out = []
        for group, codes in (self.reason_codes or {}).items():
            for code in codes:
                out.append(f'{group}: {REASON_LABELS.get(code, code)}')
        return out


class Reason(models.Model):
    """Structured reason code attached to an outcome (F-05, F-11)."""

    outcome = models.ForeignKey(
        OutcomeEvent, on_delete=models.CASCADE, related_name='reason_set'
    )
    group_name = models.CharField(max_length=30, choices=REASON_GROUPS)
    code = models.CharField(max_length=30)
    text = models.CharField(max_length=200, null=True, blank=True)
    is_free_text = models.BooleanField(
        default=False, help_text='True when this row is uncoded free text'
    )

    class Meta:
        db_table = 'reason'
        indexes = [models.Index(fields=['group_name', 'code'])]

    def __str__(self) -> str:
        return f'{self.group_name}/{self.code}'

    @property
    def label(self) -> str:
        return REASON_LABELS.get(self.code, self.code)


class Employer(models.Model):
    """Employer named by the trainee, then confirmed by the employer (F-06).

    ``wage_claimed`` and ``wage_verified`` are both kept: a disagreement is data,
    not an error.
    """

    outcome = models.OneToOneField(
        OutcomeEvent, on_delete=models.CASCADE, related_name='employer'
    )
    employer_name = models.CharField(max_length=100)
    employer_phone = models.CharField(max_length=15, null=True, blank=True)
    employer_type = models.CharField(
        max_length=30, null=True, blank=True,
        help_text='formal | informal | self-employed | unknown',
    )
    wage_claimed = models.CharField(
        max_length=20, null=True, blank=True, choices=WAGE_BANDS,
        help_text='Wage band the trainee reported',
    )
    wage_verified = models.CharField(
        max_length=20, null=True, blank=True, choices=WAGE_BANDS,
        help_text='Wage band the employer confirmed; may differ from the claim',
    )
    still_employed = models.BooleanField(null=True, blank=True)
    verified = models.BooleanField(
        default=False, help_text='True after the employer tapped Confirm (E2)'
    )
    discrepancy_flags = models.JSONField(
        default=dict, blank=True,
        help_text='e.g. {"wage_mismatch": true, "date_mismatch": false}',
    )
    confirm_token = models.CharField(
        max_length=64, null=True, blank=True, unique=True,
        help_text='Signed token so the employer needs no login (F-06)',
    )
    validated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'employer'
        indexes = [models.Index(fields=['verified']), models.Index(fields=['employer_phone'])]

    def __str__(self) -> str:
        state = 'verified' if self.verified else 'unconfirmed'
        return f'{self.employer_name} ({state})'

    @property
    def has_wage_mismatch(self) -> bool:
        return bool(self.wage_claimed and self.wage_verified and self.wage_claimed != self.wage_verified)


class EvidenceRecord(models.Model):
    """Document seen (E3) or record matched against EPFO/ESIC/DBT (E4)."""

    KIND_CHOICES = [
        ('DOCUMENT', 'Document seen - salary slip, offer letter, stipend proof'),
        ('RECORD_MATCH', 'Record matched - EPFO / ESIC / DBT stipend'),
        ('EMPLOYER_CONFIRM', 'Employer confirmed - one-tap link, no login (F-06)'),
        ('FIELD_VERIFICATION', 'Field or peer verification by an officer (F-12)'),
    ]

    outcome = models.ForeignKey(
        OutcomeEvent, on_delete=models.CASCADE, related_name='evidence_set'
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    reviewer = models.CharField(max_length=50, null=True, blank=True)
    result_verified = models.BooleanField(default=False)
    level = models.CharField(
        max_length=2, null=True, blank=True,
        choices=[('E2', 'E2'), ('E3', 'E3'), ('E4', 'E4')],
        help_text='Grade this evidence supports; E1 is written by the officer '
                  'call itself, so it lives on the outcome row',
    )
    detail = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'evidence_record'
        indexes = [models.Index(fields=['kind']), models.Index(fields=['level'])]

    def __str__(self) -> str:
        return f'{self.kind} {"verified" if self.result_verified else "pending"} {self.level or ""}'


class AuditSample(models.Model):
    """Department-drawn random sample used for non-response weighting (F-08)."""

    outcome = models.ForeignKey(
        OutcomeEvent, on_delete=models.CASCADE, related_name='audit_samples'
    )
    sample_type = models.CharField(max_length=20, choices=SAMPLE_TYPES)
    probability = models.FloatField(
        help_text='Probability p of being sampled; weight = 1 / p'
    )
    result_verified = models.BooleanField(null=True, blank=True)
    called_by = models.CharField(max_length=50, null=True, blank=True)
    called_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        db_table = 'audit_sample'
        indexes = [
            models.Index(fields=['sample_type']),
            models.Index(fields=['probability']),
        ]

    def __str__(self) -> str:
        return f'{self.get_sample_type_display()} p={self.probability:.2f}'

    @property
    def weight(self) -> float:
        return 1.0 / self.probability if self.probability else 1.0


# ===========================================================================
# Configuration and audit (F-03, DPDP)
# ===========================================================================
class Definition(models.Model):
    """Editable policy settings. One row per key (F-03).

    Keys: RETENTION_SCHEDULE, BREAK_RULE, SKILL_GAP_THRESHOLD, FLAG_SETTINGS.
    """

    KEY_CHOICES = [
        ('RETENTION_SCHEDULE', 'Retention schedule'),
        ('BREAK_RULE', 'Break rule'),
        ('SKILL_GAP_THRESHOLD', 'Skill-gap threshold'),
        ('FLAG_SETTINGS', 'Flag settings'),
    ]

    key = models.CharField(max_length=50, unique=True, choices=KEY_CHOICES)
    value = models.JSONField(default=dict)
    description = models.CharField(max_length=300, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'definition'
        ordering = ['key']

    def __str__(self) -> str:
        return f'{self.key} = {self.value}'


class AuditLog(models.Model):
    """Immutable record of privileged access and every compliance action."""

    component = models.CharField(max_length=50)
    user_role = models.CharField(max_length=50, default='anonymous')
    event_type = models.CharField(max_length=50)
    description = models.TextField()
    utid = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    request_id = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'audit_log'
        ordering = ['-timestamp']

    def __str__(self) -> str:
        return f'[{self.timestamp:%Y-%m-%d %H:%M}] {self.event_type} {self.utid or ""}'


class OtpCode(models.Model):
    """One-time code for sensitive trainee actions (S-04 erasure, corrections).

    The demo prints the code to the console; a production build would send it by
    SMS. Never store a full Aadhaar number here or anywhere else.
    """

    ACTION_CHOICES = [
        ('ERASURE', 'Erasure request'),
        ('CORRECTION', 'Data correction'),
        ('CONSENT_CHANGE', 'Consent change'),
        ('OFFICER_LOGIN', 'Officer secondary verification'),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='otp_codes'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    code = models.CharField(max_length=6)
    channel = models.CharField(max_length=10, default='SMS', help_text='SMS or EMAIL')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'otp_code'
        indexes = [models.Index(fields=['person', 'action'])]

    def __str__(self) -> str:
        return f'{self.person_id} {self.action} {"used" if self.consumed_at else "open"}'

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=['consumed_at'])


class DemandSnapshot(models.Model):
    """Vacancies by district and occupation (signal 1 of 3 for F-11).

    Sourced from the NCS / ASEEM adapter, which is SIMULATED for the demo.
    """

    SOURCE_CHOICES = [
        ('NCS', 'National Career Service'),
        ('ASEEM', 'ASEEM'),
        ('MAHASWAYAM', 'Mahaswayam / CM-YKPY'),
    ]

    district = models.CharField(max_length=50, db_index=True)
    occupation_code = models.CharField(max_length=10, db_index=True)
    vacancy_count = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='NCS')
    snapshot_date = models.DateField(db_index=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'demand_snapshot'
        constraints = [
            models.UniqueConstraint(
                fields=['district', 'occupation_code', 'snapshot_date'],
                name='uniq_demand_snapshot',
            )
        ]

    def __str__(self) -> str:
        return f'{self.district} {self.occupation_code}: {self.vacancy_count} vacancies'

    @property
    def is_stale(self) -> bool:
        from django.conf import settings

        age = (timezone.localdate() - self.snapshot_date).days
        return age > settings.AGEING_SNAPSHOT_DAYS


class HelpRequest(models.Model):
    """S-01 "Help me" routing from a trainee who is not working and looking."""

    OPTION_CHOICES = [
        ('MAHASWAYAM', 'Show Mahaswayam job matches'),
        ('COUNSELLOR', 'Ask a counsellor to call me'),
    ]

    outcome = models.ForeignKey(
        OutcomeEvent, on_delete=models.CASCADE, related_name='help_requests'
    )
    option = models.CharField(max_length=20, choices=OPTION_CHOICES)
    job_matches_shown = models.PositiveSmallIntegerField(default=0)
    task = models.ForeignKey(
        FollowupTask, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='help_requests',
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'help_request'
        ordering = ['-requested_at']

    def __str__(self) -> str:
        return f'{self.outcome_id} {self.option}'


class WorkProof(models.Model):
    """S-06 mock Work-Proof issued to DigiLocker for an E2+ outcome.

    Contains no wage. Real issuance needs DigiLocker issuer onboarding, which is
    out of scope for the MVP (docs/01-PRD.md §10).
    """

    outcome = models.OneToOneField(
        OutcomeEvent, on_delete=models.CASCADE, related_name='work_proof'
    )
    certificate_id = models.CharField(max_length=60, unique=True)
    employer_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=10, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    still_employed = models.BooleanField(null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    digilocker_ref = models.CharField(max_length=60, blank=True, default='')

    class Meta:
        db_table = 'work_proof'

    def __str__(self) -> str:
        return f'{self.certificate_id} for {self.outcome_id}'


class CertificateCheck(models.Model):
    """S-08 mock DigiLocker certificate check, only with trainee consent."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name='certificate_checks'
    )
    certificate_id = models.CharField(max_length=60)
    verification_status = models.CharField(
        max_length=20, default='VERIFIED',
        help_text='VERIFIED | NOT_FOUND | CONSENT_MISSING',
    )
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'certificate_check'
        ordering = ['-checked_at']

    def __str__(self) -> str:
        return f'{self.certificate_id} {self.verification_status}'


class DataQualityScore(models.Model):
    """S-05 per-provider data-quality score, 0-100."""

    def band(self) -> str:
        if self.score >= 75:
            return 'green'
        if self.score >= 50:
            return 'amber'
        return 'red'

    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name='quality_scores'
    )
    score = models.PositiveSmallIntegerField(default=0)
    missing_provenance = models.PositiveSmallIntegerField(default=0)
    inconsistent_ids = models.PositiveSmallIntegerField(default=0)
    stale_demand_rows = models.PositiveSmallIntegerField(default=0)
    unreached_share = models.FloatField(
        default=0.0, help_text='Share of due follow-ups with no reply, 0-1'
    )
    response_rate = models.FloatField(default=0.0)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'data_quality_score'
        ordering = ['-score']

    def __str__(self) -> str:
        return f'{self.provider_id}: {self.score}/100 ({self.band()})'


# ===========================================================================
# Statistics store — aggregates only, no names or phone numbers (F-10, F-11)
# ===========================================================================
class StatsPlacement(models.Model):
    """Nightly placement aggregate per course x provider x district.

    Every rate carries its response rate and grade mix: a bare percentage is
    never published (docs/01-PRD.md F-10).
    """

    course = models.CharField(max_length=100)
    provider = models.CharField(max_length=50)
    district = models.CharField(max_length=50)
    cohort_total = models.PositiveIntegerField(default=0)
    reached_total = models.PositiveIntegerField(default=0)
    placed_total = models.PositiveIntegerField(default=0)
    placement_rate = models.FloatField(
        default=0.0, help_text='Placed / reached, 0-100'
    )
    response_rate = models.FloatField(
        default=0.0, help_text='Reached / due, 0-100'
    )
    grade_mix = models.JSONField(default=dict, help_text='{"E0": 60, "E1": 20, ...}')
    weighted_rate = models.FloatField(
        default=0.0, help_text='Inverse-probability weighted placement rate (S-07)'
    )
    weighted_response_rate = models.FloatField(default=0.0)
    sample_probability = models.FloatField(null=True, blank=True)
    min_grade = models.CharField(
        max_length=2, default='E0', help_text='Grade filter this row was computed at'
    )
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stats_placement'
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'provider', 'district', 'min_grade'],
                name='uniq_stats_placement',
            )
        ]
        indexes = [models.Index(fields=['district', 'course'])]

    def __str__(self) -> str:
        return f'{self.course} @ {self.provider} ({self.district}) {self.placement_rate:.0f}%'


class StatsWageProgression(models.Model):
    """Wage band at T+3, T+6, T+12 plus months retained (F-09)."""

    course = models.CharField(max_length=100)
    provider = models.CharField(max_length=50)
    district = models.CharField(max_length=50)
    wage_t3 = models.CharField(max_length=20, null=True, blank=True)
    wage_t6 = models.CharField(max_length=20, null=True, blank=True)
    wage_t12 = models.CharField(max_length=20, null=True, blank=True)
    retention_months = models.PositiveSmallIntegerField(default=0)
    respondents = models.PositiveIntegerField(default=0)
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stats_wage_progression'
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'provider', 'district'],
                name='uniq_stats_wage_progression',
            )
        ]

    def __str__(self) -> str:
        return f'{self.course} @ {self.provider} retention {self.retention_months}m'


class StatsSkillGap(models.Model):
    """Skill-gap flag per course x district. Set where 2 of 3 signals agree."""

    course = models.CharField(max_length=100)
    district = models.CharField(max_length=50)
    occupation_code = models.CharField(max_length=10, blank=True, default='')
    vacancy_count = models.PositiveIntegerField(default=0)
    employer_skills_lacking_count = models.PositiveIntegerField(default=0)
    trainee_skills_lacking_count = models.PositiveIntegerField(default=0)
    signals_agree_count = models.PositiveSmallIntegerField(
        default=0, help_text='How many of the three signals are present'
    )
    gap_flag = models.BooleanField(
        default=False, help_text='True when signals_agree_count >= SKILL_GAP_THRESHOLD'
    )
    demand_snapshot_date = models.DateField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stats_skill_gap'
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'district'], name='uniq_stats_skill_gap'
            )
        ]
        indexes = [models.Index(fields=['gap_flag'])]

    def __str__(self) -> str:
        return f'{self.course} @ {self.district} gap={self.gap_flag} ({self.signals_agree_count}/3)'

    def compute_signals(self) -> int:
        """Count how many of the three skill-gap signals are present."""
        return sum(
            [
                self.vacancy_count > 0,
                self.employer_skills_lacking_count > 0,
                self.trainee_skills_lacking_count > 0,
            ]
        )

    def apply_threshold(self, threshold: int) -> None:
        from django.conf import settings

        self.signals_agree_count = self.compute_signals()
        self.gap_flag = self.signals_agree_count >= (
            threshold or settings.SKILL_GAP_THRESHOLD
        )


# ===========================================================================
# Helpers
# ===========================================================================
class Definitions:
    """Read-through accessor for the editable policy registry (F-03)."""

    DEFAULTS = {
        'RETENTION_SCHEDULE': {'round_interval': [90, 180, 270, 365]},
        'BREAK_RULE': {'break_days': 60, 'min_employment_days': 365},
        'SKILL_GAP_THRESHOLD': {'min_signals': 2},
        'FLAG_SETTINGS': {'min_group_size': 5, 'stale_demand_days': 90},
    }

    @classmethod
    def get(cls, key: str) -> dict:
        try:
            row = Definition.objects.filter(key=key).first()
        except Exception:
            return dict(cls.DEFAULTS[key])
        return row.value if row else dict(cls.DEFAULTS.get(key, {}))

    @classmethod
    def break_days(cls) -> int:
        return int(cls.get('BREAK_RULE').get('break_days', 60))

    @classmethod
    def min_employment_days(cls) -> int:
        return int(cls.get('BREAK_RULE').get('min_employment_days', 365))

    @classmethod
    def round_intervals(cls) -> list[int]:
        return list(cls.get('RETENTION_SCHEDULE').get('round_interval', [90, 180, 270, 365]))

    @classmethod
    def min_signals(cls) -> int:
        return int(cls.get('SKILL_GAP_THRESHOLD').get('min_signals', 2))

    @classmethod
    def min_group_size(cls) -> int:
        return int(cls.get('FLAG_SETTINGS').get('min_group_size', 5))


def load_default_definitions() -> dict[str, dict]:
    """Seed the four registry rows. Safe to run repeatedly."""
    written = {}
    descriptions = {
        'RETENTION_SCHEDULE': 'Follow-up rounds counted from certification (F-04).',
        'BREAK_RULE': 'Employed >= 365 days with a break of at most 60 days counts as retained (F-09).',
        'SKILL_GAP_THRESHOLD': 'Number of the three signals that must agree before a course is flagged (F-11).',
        'FLAG_SETTINGS': 'Minimum group size for dashboard cells, and staleness window for demand data (F-10).',
    }
    for key, value in Definitions.DEFAULTS.items():
        Definition.objects.update_or_create(
            key=key,
            defaults={
                'value': value,
                'description': descriptions[key],
                'updated_by': 'system',
            },
        )
        written[key] = value
    return written