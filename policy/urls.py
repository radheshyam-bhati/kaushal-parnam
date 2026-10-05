from django.urls import path

from policy import views

app_name = 'policy'

urlpatterns = [
    path('policy/dashboard/', views.dashboard, name='dashboard'),
    path('policy/definitions/', views.definitions, name='definitions'),
    path('policy/providers/', views.provider_view, name='provider_comparison'),
    path('policy/skill-gaps/', views.skill_gap_view, name='skill_gap'),
    path('policy/funding-report/', views.funding_report, name='funding_report'),
    path('policy/funding-report/export/', views.funding_export, name='funding_export'),
    path('policy/data-quality/', views.data_quality, name='data_quality'),
    path('policy/wage-retention/', views.wage_retention, name='wage_retention'),
    path('policy/audit-trail/', views.audit_trail, name='audit_trail'),
]