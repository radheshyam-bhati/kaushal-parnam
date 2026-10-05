"""Audit trail helpers (docs/02-TRD.md §11, docs/05-BACKEND-SCHEMA.md §1.1).

Two sinks:
  * ``audit_log`` table  — durable, queryable, survives a restart.
  * structured JSON log  — console plus logs/errors.log.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from core.logging import audit_log as emit

LOGGER = "core.audit"

#: Request id of the request currently being served, set by RLSMiddleware.
#: log_event falls back to it so every row written during a request carries the
#: id, without threading a ``request`` argument through every call site. It is a
#: ContextVar rather than a thread-local because that is correct under both
#: threaded and async workers.
current_request_id: ContextVar[str | None] = ContextVar(
    'kp_request_id', default=None
)


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
    """Write one audit row and one structured log line.

    ``request_id`` defaults to the id of the request in flight. It has to be
    present on rows written during a request for two reasons: it is what
    correlates a log line with the requests that produced it, and PostgreSQL
    checks the SELECT policy against the ``RETURNING`` clause of an INSERT, so a
    session must be able to read back the row it just wrote. Rows written outside
    a request (the scheduler, management commands) legitimately have no id; those
    run as ``kp_worker``, which bypasses RLS.
    """
    from core.models import AuditLog

    if request_id is None:
        request_id = current_request_id.get()

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