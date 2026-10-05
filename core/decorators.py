"""Role-based access control helpers (docs/02-TRD.md §5, §3).

``role_required`` is the single gate every non-public view goes through. It also
raises 403 rather than redirecting when a signed-in user lacks the role, because
docs/02-TRD.md §11 specifies "403 Forbidden: Provider A cannot SELECT trainee
from district X belonging to Provider B" for an isolation failure.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from accounts.models import Role
from core.services.audit import log_event

PROVIDER_SIDE = {Role.PROVIDER, Role.PROVIDER_COORDINATOR}
STAFF_SIDE = {Role.DISTRICT_OFFICER, Role.POLICY_OFFICER}


def role_required(*roles, message: str = ''):
    """Allow only the listed roles. Anonymous users go to the login page."""
    allowed = set(roles)

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())

            if allowed and user.role not in allowed:
                log_event(
                    component='rbac',
                    event_type='permission_denied',
                    description=f'{user.role} tried to open {request.path}',
                    utid=user.utid,
                    user_role=user.role,
                )
                if message:
                    messages.error(request, message)
                raise PermissionDenied(
                    message or f'Your role ({user.role}) cannot open this page.'
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def login_redirect(user):
    """Send each role to its own dashboard (docs/03-APP-FLOW.md J-09)."""
    if not user.is_authenticated:
        return redirect('/login/')
    return redirect(user.dashboard_url)