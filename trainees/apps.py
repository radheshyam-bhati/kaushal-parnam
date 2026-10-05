from django.apps import AppConfig


class TraineesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trainees'
    verbose_name = 'Trainee-facing views (enrolment, outcome reply, My Data)'
