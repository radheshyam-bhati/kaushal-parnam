from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Login identities with the RBAC scope the RLS policies read."""

    list_display = ('username', 'role', 'utid', 'provider_id', 'district', 'is_active')
    list_filter = ('role', 'district', 'provider_id', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'utid', 'phone')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Kaushal Parinam scope', {'fields': ('role', 'utid', 'provider_id', 'district', 'phone')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Kaushal Parinam scope', {'fields': ('role', 'utid', 'provider_id', 'district')}),
    )

