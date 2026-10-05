"""Admin registration for the operators who need raw row access.

Row-level security still applies to the admin: the ORM-level scope helpers in
core.services.rls are the only safe way to query, so admin lists here are
plain filters and the audit middleware records every page view.
"""

from django.contrib import admin

from core.models import (
    AuditLog,
    AuditSample,
    CertificateCheck,
    Consent,
    Contact,
    ContactAttempt,
    DataQualityScore,
    Definition,
    DemandSnapshot,
    Employer,
    Enrolment,
    EvidenceRecord,
    FollowupJob,
    FollowupTask,
    HelpRequest,
    IdCrosswalk,
    IdMatchSuggestion,
    OutcomeEvent,
    Person,
    Provider,
    Qualification,
    Reason,
    StatsPlacement,
    StatsSkillGap,
    StatsWageProgression,
    WorkProof,
)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('utid', 'name', 'gender', 'category', 'district', 'is_active', 'is_anonymous')
    list_filter = ('district', 'category', 'gender', 'is_active', 'is_anonymous')
    search_fields = ('utid', 'name')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('person', 'own_phone', 'second_phone', 'device_type', 'language')
    list_filter = ('device_type', 'language', 'shared_phone_flag')
    search_fields = ('person__utid', 'own_phone', 'second_phone')


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = ('person', 'purpose', 'language', 'given_at', 'withdrawn_at', 'notice_version')
    list_filter = ('purpose', 'withdrawn_at')
    search_fields = ('person__utid',)


@admin.register(IdCrosswalk)
class IdCrosswalkAdmin(admin.ModelAdmin):
    list_display = ('person', 'scheme', 'programme_id', 'linked_at')
    list_filter = ('scheme',)
    search_fields = ('person__utid', 'programme_id')


@admin.register(IdMatchSuggestion)
class IdMatchSuggestionAdmin(admin.ModelAdmin):
    list_display = ('person', 'candidate_utid', 'basis', 'resolution', 'reviewed_by')
    list_filter = ('basis', 'resolution')


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('provider_id', 'name', 'district', 'scheme', 'is_registered')
    list_filter = ('district', 'scheme', 'is_registered')
    search_fields = ('provider_id', 'name')


@admin.register(Qualification)
class QualificationAdmin(admin.ModelAdmin):
    list_display = ('qualification_id', 'course_name', 'occupation_code', 'course_duration_days')
    search_fields = ('course_name', 'occupation_code')


@admin.register(Enrolment)
class EnrolmentAdmin(admin.ModelAdmin):
    list_display = ('person', 'provider', 'qualification', 'course_start_date', 'course_end_date', 'certified_on')
    list_filter = ('provider', 'qualification')
    search_fields = ('person__utid',)


@admin.register(FollowupJob)
class FollowupJobAdmin(admin.ModelAdmin):
    list_display = ('person', 'round', 'due_at', 'channel_plan', 'status', 'retries')
    list_filter = ('status', 'channel_plan', 'round')
    search_fields = ('person__utid',)


@admin.register(ContactAttempt)
class ContactAttemptAdmin(admin.ModelAdmin):
    list_display = ('followup_job', 'attempt_number', 'channel', 'result', 'demo_label', 'created_at')
    list_filter = ('channel', 'result', 'demo_label')


@admin.register(FollowupTask)
class FollowupTaskAdmin(admin.ModelAdmin):
    list_display = ('person', 'district', 'task_type', 'status', 'priority', 'due_at')
    list_filter = ('district', 'status', 'task_type')


class OutcomeEventAdmin(admin.ModelAdmin):
    """Read-mostly on purpose: outcome events are append-only."""

    list_display = ('person', 'reference_date', 'status', 'evidence_level', 'created_by', 'created_at')
    list_filter = ('status', 'evidence_level', 'created_by')
    search_fields = ('person__utid',)
    readonly_fields = [field.name for field in OutcomeEvent._meta.fields]


@admin.register(OutcomeEvent)
class RegisteredOutcomeEventAdmin(OutcomeEventAdmin):
    pass


@admin.register(Reason)
class ReasonAdmin(admin.ModelAdmin):
    list_display = ('outcome', 'group_name', 'code', 'is_free_text')
    list_filter = ('group_name', 'code', 'is_free_text')


@admin.register(Employer)
class EmployerAdmin(admin.ModelAdmin):
    list_display = ('employer_name', 'outcome', 'verified', 'wage_claimed', 'wage_verified', 'validated_at')
    list_filter = ('verified', 'employer_type')
    search_fields = ('employer_name', 'employer_phone')


@admin.register(EvidenceRecord)
class EvidenceRecordAdmin(admin.ModelAdmin):
    list_display = ('outcome', 'kind', 'level', 'result_verified', 'reviewer', 'created_at')
    list_filter = ('kind', 'level', 'result_verified')


@admin.register(AuditSample)
class AuditSampleAdmin(admin.ModelAdmin):
    list_display = ('outcome', 'sample_type', 'probability', 'result_verified', 'called_by', 'called_at')
    list_filter = ('sample_type', 'result_verified')


@admin.register(Definition)
class DefinitionAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'updated_at', 'updated_by')
    list_filter = ('key',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'component', 'user_role', 'event_type', 'utid', 'description')
    list_filter = ('component', 'user_role', 'event_type')
    search_fields = ('utid', 'description')
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DemandSnapshot)
class DemandSnapshotAdmin(admin.ModelAdmin):
    list_display = ('district', 'occupation_code', 'vacancy_count', 'source', 'snapshot_date')
    list_filter = ('district', 'source')


@admin.register(DataQualityScore)
class DataQualityScoreAdmin(admin.ModelAdmin):
    list_display = ('provider', 'score', 'missing_provenance', 'unreached_share', 'computed_at')
    list_filter = ('provider',)


@admin.register(StatsPlacement)
class StatsPlacementAdmin(admin.ModelAdmin):
    list_display = ('course', 'provider', 'district', 'min_grade', 'placement_rate', 'response_rate', 'weighted_rate')
    list_filter = ('district', 'min_grade', 'provider')


@admin.register(StatsWageProgression)
class StatsWageProgressionAdmin(admin.ModelAdmin):
    list_display = ('course', 'provider', 'district', 'wage_t3', 'wage_t6', 'wage_t12', 'retention_months')


@admin.register(StatsSkillGap)
class StatsSkillGapAdmin(admin.ModelAdmin):
    list_display = ('course', 'district', 'signals_agree_count', 'gap_flag', 'vacancy_count')
    list_filter = ('district', 'gap_flag')


@admin.register(WorkProof)
class WorkProofAdmin(admin.ModelAdmin):
    list_display = ('certificate_id', 'employer_name', 'role_code', 'issued_at')


@admin.register(CertificateCheck)
class CertificateCheckAdmin(admin.ModelAdmin):
    list_display = ('person', 'certificate_id', 'verification_status', 'checked_at')


@admin.register(HelpRequest)
class HelpRequestAdmin(admin.ModelAdmin):
    list_display = ('outcome', 'option', 'job_matches_shown', 'requested_at')
    list_filter = ('option',)