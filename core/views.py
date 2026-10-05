"""Core views: role routing, health check and the custom error pages (P-12).

Error pages carry the wording docs/02-TRD.md §11 specifies rather than Django's
default "Server Error (500)" text.
"""

from __future__ import annotations

import os

from django.db import connection
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render

from core.services.audit import log_event


def role_home(request):
    """Send each role to its own dashboard (docs/03-APP-FLOW.md J-09)."""
    if not request.user.is_authenticated:
        return redirect('login')
    return redirect(request.user.dashboard_url)


def health(request):
    """GET /api/health/ -- public, no auth (docs/02-TRD.md §6)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        database = 'ok'
    except Exception as exc:  # pragma: no cover - health must never raise
        database = f'error: {exc.__class__.__name__}'

    return JsonResponse({
        'status': 'ok' if database == 'ok' else 'degraded',
        'version': '1.0-mvp',
        'django_env': os.environ.get('DJANGO_ENV', 'development'),
        'database': database,
        'database_vendor': connection.vendor,
    })


def page_not_found(request, exception=None):
    """P-12."""
    log_event(
        component='http', event_type='page_404',
        description=f'404 for {request.path}',
        user_role=getattr(request, 'user_role', 'anonymous'),
    )
    return render(request, 'errors/404.html', {'path': request.path}, status=404)


def permission_denied(request, exception=None):
    """An isolation failure, not a mistake by the user."""
    message = str(exception) if exception else ''
    log_event(
        component='rls', event_type='rls_violation_attempt',
        description=message or f'403 for {request.path}',
        user_role=getattr(request, 'user_role', 'anonymous'),
        path=request.path,
    )
    if request.path.startswith('/api/'):
        return HttpResponseForbidden(message or 'Access denied.')
    return render(request, 'errors/403.html', {'message': message}, status=403)


def server_error(request):
    return render(request, 'errors/500.html', status=500)


def design_system(request):
    """Interactive Design System Showcase & Living Component Catalog."""
    return render(request, 'design_system.html')