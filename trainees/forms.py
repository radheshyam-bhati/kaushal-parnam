"""Trainee-facing forms: consent passport (F-01), outcome capture (F-05, F-12).

The forms carry the same wording the docs use in
docs/04-UI-UX-BRIEF.md §11 "Sample microcopy", because that copy is part of the
compliance story: the trainee has to be told what each purpose means before
ticking it.
"""

from __future__ import annotations

import re

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from core.models import (
    CATEGORY_CHOICES,
    CONSENT_PURPOSES,
    DEVICE_TYPES,
    GENDER_CHOICES,
    INCOME_BANDS,
    LANGUAGE_CHOICES,
    OUTCOME_STATUS_CHOICES,
    REASON_CODES,
    SELF_EMPLOYMENT_ACTIVITIES,
    WAGE_BANDS,
)

PHONE_RE = re.compile(r'^\+91[6-9]\d{9}$')

CONSENT_PURPOSE_HELP = {
    'follow-up': "We'll message you quarterly to check your employment status. You can withdraw at any time.",
    'employer_check': "We'll ask your employer to confirm you are working. No wage is shared without your consent.",
    'record_match': "We'll check your employment record against EPFO/ESIC/DBT to confirm months worked.",
    'statistics': "Your de-identified outcome counts towards district and provider statistics.",
}

DEVICE_HELP = {
    'smartphone': "Smartphone: you'll receive a WhatsApp form.",
    'feature_phone': "Feature phone: you'll receive an IVR call with keypad options.",
    'shared': "Shared phone: you'll receive an SMS nudge you can answer later.",
}

PLACED_STATUSES = ('wage_employment', 'self_employment', 'apprenticeship')


class PhoneNumberField(forms.CharField):
    """+91 followed by 10 digits, starting 6-9 (docs/04-UI-UX-BRIEF.md §11)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('max_length', 13)
        kwargs.setdefault(
            'help_text', 'Phone number must be +91 followed by 10 digits (e.g., +919876543210).'
        )
        super().__init__(**kwargs)

    def clean(self, value):
        value = (value or '').strip()
        if not value:
            # An optional phone may be omitted; CharField.clean normally skips
            # validation in that case, but overriding clean() bypasses it.
            if self.required:
                raise ValidationError('Enter a phone number so we can reach you.')
            return value
        digits = re.sub(r'\D', '', value)
        if len(digits) == 10:
            value = f'+91{digits}'
        if not PHONE_RE.match(value):
            raise ValidationError(
                'Phone number must be +91 followed by 10 digits (e.g., +919876543210).'
            )
        return value


class ConsentPassportForm(forms.Form):
    """F-01: consent by purpose, language, contact details, device type, age gate."""

    name = forms.CharField(max_length=100, label='Your name')
    dob = forms.DateField(
        label='Date of birth',
        help_text='Used only for the age gate and for district statistics. Never published.',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    gender = forms.ChoiceField(choices=GENDER_CHOICES, label='Gender')
    category = forms.ChoiceField(choices=CATEGORY_CHOICES, label='Category')
    district = forms.CharField(max_length=50, label='District')

    language = forms.ChoiceField(
        choices=LANGUAGE_CHOICES, initial='mr', label='Language for notices and messages'
    )
    own_phone = PhoneNumberField(label='Your phone number')
    second_contact_name = forms.CharField(
        max_length=100, required=False,
        label='Second contact name',
        help_text='A family member, in case your phone changes. Optional.',
    )
    second_phone = PhoneNumberField(
        label='Second contact phone', required=False,
    )
    second_contact_relationship = forms.CharField(
        max_length=50, required=False, label='Relationship to you'
    )
    device_type = forms.ChoiceField(
        choices=DEVICE_TYPES, initial='smartphone', label='What kind of phone do you use?'
    )

    # -- the four consent purposes ---------------------------------------
    consent_follow_up = forms.BooleanField(
        required=True,
        label='Follow-up contact: yes',
        help_text=CONSENT_PURPOSE_HELP['follow-up'],
    )
    consent_employer_check = forms.BooleanField(
        required=True,
        label='Employer validation: yes',
        help_text=CONSENT_PURPOSE_HELP['employer_check'],
    )
    consent_record_match = forms.BooleanField(
        required=False,
        label='Record match: yes',
        help_text=CONSENT_PURPOSE_HELP['record_match'],
    )
    consent_statistics = forms.BooleanField(
        required=True,
        label='Statistics: yes',
        help_text=CONSENT_PURPOSE_HELP['statistics'],
    )

    guardian_consent = forms.BooleanField(
        required=False,
        label='A parent or guardian has agreed to this tracking',
    )
    notice_accepted = forms.BooleanField(
        required=True,
        label='I have read the DPDP summary and I agree to these purposes',
    )

    # -- course and centre, needed for the baseline record -------------
    provider_id = forms.CharField(max_length=50, label='Training centre ID')
    qualification_id = forms.CharField(max_length=50, label='Course code')
    course_start_date = forms.DateField(
        label='Course start date', widget=forms.DateInput(attrs={'type': 'date'})
    )
    course_end_date = forms.DateField(
        required=False, label='Course end date',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    sdms_id = forms.CharField(
        max_length=50, required=False,
        label='SDMS / SID registration ID',
        help_text='Links your training record to your outcomes. Leave blank if you do not have one.',
    )

    PURPOSES = CONSENT_PURPOSES

    def clean_dob(self):
        dob = self.cleaned_data['dob']
        from django.utils import timezone

        today = timezone.localdate()
        if dob > today:
            raise ValidationError('Date of birth cannot be in the future.')
        if dob.year < 1900:
            raise ValidationError('Enter a valid date of birth.')
        return dob

    def clean(self):
        cleaned = super().clean()
        from django.utils import timezone

        dob = cleaned.get('dob')
        today = timezone.localdate()
        limit = settings.DPDP_CHILD_AGE_LIMIT

        if dob:
            years = today.year - dob.year
            if (today.month, today.day) < (dob.month, dob.day):
                years -= 1
            cleaned['age'] = years
            if years < limit and not cleaned.get('guardian_consent'):
                self.add_error(
                    'guardian_consent',
                    f'DPDP requires verifiable parental consent below {limit}. '
                    'This pilot targets trainees aged 18-35; if you are 15-17, '
                    'please consult your programme coordinator.',
                )

        if cleaned.get('second_phone') and cleaned.get('second_phone') == cleaned.get('own_phone'):
            self.add_error(
                'second_phone',
                'The second contact number must be different from your own number.',
            )

        course_start = cleaned.get('course_start_date')
        course_end = cleaned.get('course_end_date')
        if course_start and course_end and course_end < course_start:
            self.add_error('course_end_date', 'The course end date is before the start date.')
        if course_start and course_start > today:
            self.add_error('course_start_date', 'The course start date is in the future.')

        # Follow-up contact is the one purpose the pilot cannot run without.
        if not cleaned.get('consent_follow_up'):
            self.add_error(
                'consent_follow_up',
                'We cannot send you follow-up messages without this consent, '
                'so no outcome tracking would happen. You can withdraw it later.',
            )
        return cleaned

    def granted_purposes(self) -> list[str]:
        """Consent purposes the trainee actually ticked, in glossary order."""
        field_for = {
            'follow-up': 'consent_follow_up',
            'employer_check': 'consent_employer_check',
            'record_match': 'consent_record_match',
            'statistics': 'consent_statistics',
        }
        return [
            purpose for purpose, _label in CONSENT_PURPOSES
            if self.cleaned_data.get(field_for[purpose])
        ]


class OutcomeCaptureForm(forms.Form):
    """F-05: seven statuses with conditional fields, grouped reason codes."""

    reference_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}), label='As of this date'
    )
    status = forms.ChoiceField(choices=OUTCOME_STATUS_CHOICES, label='What is your status?')

    # wage employment
    employer_name = forms.CharField(
        max_length=100, required=False, label='Employer name'
    )
    employer_phone = PhoneNumberField(
        required=False, label='Employer phone (for confirmation)',
    )
    role_code = forms.CharField(
        max_length=10, required=False, label='Job role (NCO code)'
    )
    start_date = forms.DateField(
        required=False, label='Date you started this job',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    wage_band = forms.ChoiceField(
        choices=WAGE_BANDS, required=False, label='Monthly wage band'
    )

    # self-employment (F-12)
    self_employment_activity = forms.ChoiceField(
        choices=SELF_EMPLOYMENT_ACTIVITIES, required=False, label='What work do you do?'
    )
    income_band = forms.ChoiceField(
        choices=INCOME_BANDS, required=False, label='Monthly income band'
    )
    months_active = forms.IntegerField(
        required=False, min_value=0, max_value=600, label='Months active'
    )

    # apprenticeship / stipend
    establishment_name = forms.CharField(
        max_length=100, required=False, label='Establishment name'
    )
    stipend_band = forms.ChoiceField(
        choices=WAGE_BANDS, required=False, label='Monthly stipend band'
    )
    apprenticeship_months = forms.IntegerField(
        required=False, min_value=0, max_value=120, label='Apprenticeship duration in months'
    )

    # reason codes
    employment_reasons = forms.MultipleChoiceField(
        choices=REASON_CODES['employment-related'], required=False,
        label='Employment-related',
    )
    training_reasons = forms.MultipleChoiceField(
        choices=REASON_CODES['training-related'], required=False,
        label='Training-related',
    )
    personal_reasons = forms.MultipleChoiceField(
        choices=REASON_CODES['personal'], required=False, label='Personal',
    )
    free_text = forms.CharField(
        required=False, max_length=200, label='Anything else you want to add',
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean_reference_date(self):
        from django.utils import timezone

        value = self.cleaned_data['reference_date']
        if value > timezone.localdate():
            raise ValidationError('The reference date cannot be in the future.')
        return value

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')

        if status == 'wage_employment':
            if not cleaned.get('employer_name'):
                self.add_error('employer_name', 'Tell us the employer name.')
            start = cleaned.get('start_date')
            if start and start > cleaned.get('reference_date', start):
                self.add_error('start_date', 'The start date is after the reference date.')

        if status == 'self_employment':
            if not cleaned.get('self_employment_activity'):
                self.add_error('self_employment_activity', 'Choose the kind of work you do.')
            if not cleaned.get('months_active'):
                self.add_error('months_active', 'Tell us how many months you have been working.')

        if status == 'apprenticeship':
            if not cleaned.get('establishment_name'):
                self.add_error('establishment_name', 'Tell us the establishment name.')

        if status in ('not_working_looking', 'not_working_not_looking'):
            if not any(
                cleaned.get(field)
                for field in ('employment_reasons', 'training_reasons', 'personal_reasons')
            ):
                self.add_error(
                    'employment_reasons',
                    'Pick at least one reason, or write your own below. This is what '
                    'tells us which courses to change.',
                )

        cleaned['reason_codes'] = self._grouped_reasons(cleaned)
        return cleaned

    def _grouped_reasons(self, cleaned) -> dict[str, list[str]]:
        grouped = {}
        for group, field in (
            ('employment-related', 'employment_reasons'),
            ('training-related', 'training_reasons'),
            ('personal', 'personal_reasons'),
        ):
            codes = cleaned.get(field) or []
            if codes:
                grouped[group] = list(codes)
        return grouped


class ContactCorrectionForm(forms.Form):
    """S-04: correct your own contact details."""

    own_phone = PhoneNumberField(label='Your phone number')
    second_contact_name = forms.CharField(max_length=100, required=False)
    second_phone = PhoneNumberField(required=False, label='Second contact phone')
    second_contact_relationship = forms.CharField(max_length=50, required=False)
    device_type = forms.ChoiceField(choices=DEVICE_TYPES, label='Phone type')
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, label='Language')