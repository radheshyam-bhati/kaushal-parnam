from django.urls import path

from providers import views

app_name = 'providers'

urlpatterns = [
    path('dashboard/provider/', views.dashboard, name='dashboard'),
    # The employer needs no login: the token is the authorisation (F-06).
    path('employer/confirm/<str:token>/', views.employer_confirm, name='employer_confirm'),
    path('employer/send/<int:event_id>/', views.send_employer_link, name='send_employer_link'),
]