"""Top-level routing.

Page list from docs/03-APP-FLOW.md §1 (P-01 to P-15).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import health, role_home
from trainees import views as trainee_views

urlpatterns = [
    # P-01 landing
    path('', trainee_views.home, name='home'),
    path('dashboard/', role_home, name='role_home'),

    # P-13 / P-14 / P-15 auth
    path('login/', trainee_views.login_view, name='login'),
    path('logout/', trainee_views.logout_view, name='logout'),
    path('password-reset/', trainee_views.password_reset, name='password_reset'),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html'
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url='/password-reset/complete/',
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html'
        ),
        name='password_reset_complete',
    ),

    # Role areas
    path('', include('trainees.urls')),
    path('', include('providers.urls')),
    path('', include('officers.urls')),
    path('', include('policy.urls')),

    # Health check (public, TRD §6)
    path('api/health/', health, name='health'),

    path('admin/', admin.site.urls),
]

# MEDIA serving is only wired up when the directory exists; no model in this
# MVP has a FileField, so there is nothing to serve.
if settings.DEBUG and settings.MEDIA_ROOT.exists():
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'core.views.page_not_found'
handler403 = 'core.views.permission_denied'
handler500 = 'core.views.server_error'