"""Trainee URLs. Names follow the page list in docs/03-APP-FLOW.md §1."""

from django.urls import path

from trainees import actions, views

app_name = 'trainees'

urlpatterns = [
    path('enrol/', views.enrol, name='enrol'),
    path('dashboard/trainee/', views.dashboard, name='dashboard'),
    path('outcome/reply/<int:job_id>/', views.outcome_reply, name='outcome_reply'),
    path('outcome/capture/', views.outcome_capture, name='outcome_capture'),
    path('outcome/<int:event_id>/', views.outcome_detail, name='outcome_detail'),
    path('my-data/', views.my_data, name='my_data'),
    path('my-data/withdraw/<str:purpose>/', views.withdraw_consent, name='withdraw_consent'),
    path('my-data/correct/', views.correct_contact, name='correct_contact'),
    path('my-data/erasure/request/', views.request_erasure, name='request_erasure'),
    path('my-data/erasure/confirm/', views.confirm_erasure, name='confirm_erasure'),
    path('certificate-check/', views.certificate_check, name='certificate_check'),
    path('help-me/', actions.help_me, name='help_me'),
    path('work-proof/', actions.issue_work_proof, name='issue_work_proof'),
    path('csv-upload/', views.csv_upload, name='csv_upload'),
]