from django.urls import path

from officers import views

app_name = 'officers'

urlpatterns = [
    path('officer/tasks/', views.task_list, name='task_list'),
    path('officer/', views.officer_dashboard, name='dashboard'),
    path('officer/tasks/<int:task_id>/close/', views.close_task, name='close_task'),
    path('officer/tasks/<int:task_id>/outcome/', views.record_officer_outcome, name='record_outcome'),
    path('officer/audit-sample/', views.audit_sample, name='audit_sample'),
    path('officer/audit-sample/draw/', views.draw_samples, name='draw_samples'),
    path('officer/audit-sample/<int:sample_id>/call/', views.record_call, name='record_call'),
    path(
        'officer/audit-sample/<int:sample_id>/field-verify/',
        views.verify_self_employment, name='verify_self_employment',
    ),
]