"""Audit trail helpers (docs/02-TRD.md §11, docs/05-BACKEND-SCHEMA.md §1.1).

Two sinks:
  * ``audit_log`` table  — durable, queryable, survives a restart.
  * structured JSON log  — console plus logs/errors.log.
"""

from __future__ import annotations

import logging

from core.logging import audit_log as emit

LOGGER = "core.audit"


def _utid_for(request):
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return getattr(user, 'utid', None)
    return None


def log_event(
    component: str,
    event_type: str,
    description: str,
    *,
    utid: str | None = None,
    user_role: str = 'system',
    request_id: str | None = None,
    **extra,
) -> None:
    """Write one audit row and one structured log line."""
    from core.models import AuditLog

    try:
        AuditLog.objects.create(
            component=component,
            user_role=user_role,
            event_type=event_type,
            description=description[:2000],
            utid=utid,
            request_id=request_id,
        )
    except Exception:  # pragma: no cover - auditing must never break the caller
        logging.getLogger(LOGGER).exception(
            'audit_row_failed', extra={'event': event_type, 'component': component}
        )

    emit(LOGGER, event_type, component=component, user_role=user_role,
         utid=utid, request_id=request_id, description=description, **extra)


def log_access(request, event_type: str, description: str) -> None:
    """Record a privileged read from the audit middleware."""
    user = getattr(request, 'user', None)
    role = getattr(user, 'role', 'anonymous') if getattr(user, 'is_authenticated', False) else 'anonymous'
    log_event(
        component='access',
        event_type=event_type,
        description=description,
        utid=_utid_for(request),
        user_role=role,
        request_id=request.META.get('HTTP_X_REQUEST_ID'),
        path=request.path,
        method=request.method,
    )