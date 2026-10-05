"""Request middleware: session identity, PostgreSQL row-level security, audit log.

Docs: docs/05-BACKEND-SCHEMA.md §1.1 (audit_log trigger reads app.user_role),
docs/08-Architecture.md §5.3 (RLS policies), docs/02-TRD.md §11 (logging).
"""

from __future__ import annotations

import uuid

from django.db import connection, transaction
from django.utils.deprecation import MiddlewareMixin

from core.logging import audit_log
from core.services.audit import current_request_id

AUDIT_LOGGER = "core.audit"

set_current_request_id = current_request_id.set

def bind_scope(**values) -> None:
    """Re-bind the RLS scope variables inside the request already in flight.

    Two flows need this, and both are cases where the scope genuinely changes
    partway through a request rather than being narrowed to make a write pass:

    ``utid``
        ``/enrol/`` mints the trainee's UTID mid-request. Without re-binding,
        the person and contact rows they are creating fall outside their own
        scope and the policies refuse the insert.

    ``employer_outcome_id``, with ``user_role='employer'``
        ``/employer/confirm/<token>/`` has no logged-in user. The token is the
        authorisation, so once it has been validated the view binds the single
        outcome it covers and nothing else.

    ``set_config(..., true)`` is transaction-local, which is exactly right here:
    the new value lives for this request's transaction and is gone afterwards.
    """
    if not postgres_enabled():
        return
    with connection.cursor() as cursor:
        for key, value in values.items():
            name = key if key.startswith("app.") else f"app.{key}"
            cursor.execute("SELECT set_config(%s, %s, true)", [name, str(value or "")])


def postgres_enabled() -> bool:
    return connection.vendor == "postgresql"


class UtidSessionMiddleware(MiddlewareMixin):
    """Expose the signed-in user's UTID / provider / district to templates."""

    def process_request(self, request):
        request.utid = request.session.get("utid")
        request.user_role = "anonymous"
        request.provider_id = None
        request.district = None

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            request.user_role = user.role
            request.utid = user.utid
            request.provider_id = user.provider_id
            request.district = user.district
            request.session["utid"] = user.utid
        return None


class RLSMiddleware(MiddlewareMixin):
    """Set the PostgreSQL session variables the RLS policies read.

    app.user_role / app.utid / app.provider_id / app.district are consumed by
    the policies installed in the ``core_rls`` migration. On SQLite (and any
    non-PostgreSQL backend) this is a no-op and isolation is enforced in the
    query layer by ``core.rls.visible_people``.

    Every value is set with ``set_config(..., true)`` so it is scoped to the
    current transaction and cannot leak between requests. That only holds
    because ``ATOMIC_REQUESTS`` wraps each request in one transaction; see
    core/env.py.

    app.request_id is generated when the client did not send ``X-Request-Id``,
    because ``audit_log`` needs a per-request identifier on every request, not
    only the ones a proxy happens to decorate. It is written back into META so
    ``core.services.audit`` stamps the same value on every row it writes, which
    is what lets the ``audit_log`` read policy let a session read back the row
    it just inserted (PostgreSQL checks RETURNING against the SELECT policy).
    """

    def process_request(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        request.META["HTTP_X_REQUEST_ID"] = request_id
        # core.services.audit.log_event reads this so audit rows written during
        # the request carry the id without every call site passing it.
        token = set_current_request_id(request_id)
        request._kp_request_id_token = token

        if not postgres_enabled():
            return None

        # This middleware owns the request's transaction, and that is load
        # bearing rather than tidiness. `set_config(..., true)` is scoped to the
        # transaction that issued it, and Django opens ATOMIC_REQUESTS
        # *after* every middleware has run -- so setting the session variables
        # here under autocommit committed them immediately and every later
        # statement in the view saw an empty role. Measured before this fix:
        # app.user_role read back as '' while in_atomic_block was True.
        #
        # Owning the boundary here means the variables are bound for the whole
        # request and cannot survive into the next one served by this worker.
        # Consequence worth knowing: a request is now atomic, so an exception
        # rolls back everything it wrote. That is the behaviour you want for
        # enrolment, consent withdrawal and erasure. A 500 still reaches the
        # structured log (logs/errors.log), which is outside the database.
        atomic = transaction.atomic()
        atomic.__enter__()
        request._kp_atomic = atomic

        bind_scope(
            user_role=getattr(request, "user_role", "anonymous"),
            utid=getattr(request, "utid", "") or "",
            provider_id=getattr(request, "provider_id", None) or "",
            district=getattr(request, "district", None) or "",
            request_id=request_id,
        )
        return None

    def _close_transaction(self, request, exc_info) -> None:
        """Leave the transaction opened in process_request.

        Passing the exception through is what makes it roll back; committing
        here unconditionally would persist a half-applied request.
        """
        atomic = getattr(request, "_kp_atomic", None)
        if atomic is None:
            return
        request._kp_atomic = None
        atomic.__exit__(*exc_info)

    def process_response(self, request, response):
        # Commit before the response leaves. process_response runs in reverse
        # order, so AuditLogMiddleware has already written its row and that row
        # is inside this transaction -- a privileged read is committed with the
        # request it belongs to, or rolled back with it.
        self._close_transaction(request, (None, None, None))
        token = getattr(request, "_kp_request_id_token", None)
        if token is not None:
            try:
                current_request_id.reset(token)
            except ValueError:  # pragma: no cover - different context
                current_request_id.set(None)
            request._kp_request_id_token = None
        return response

    def process_exception(self, request, exception):
        """Leave the transaction, rolling back only for genuine failures.

        ``Http404`` and ``PermissionDenied`` are answers, not failures: a
        request that was refused still wrote an audit row saying so, and rolling
        back on those would erase the record of the attempt. That matters
        because with the policies enforced a cross-tenant read *is* a 404 -- the
        refusal only exists in that audit row, and DPDP s.8(6) wants it kept.

        Anything else -- a database error, a bug -- rolls the request back so a
        half-applied enrolment cannot be committed.
        """
        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        expected = isinstance(exception, (Http404, PermissionDenied))
        exc_info = (None, None, None) if expected else (
            type(exception), exception, exception.__traceback__
        )
        self._close_transaction(request, exc_info)
        token = getattr(request, "_kp_request_id_token", None)
        if token is not None:
            try:
                current_request_id.reset(token)
            except ValueError:  # pragma: no cover
                current_request_id.set(None)
            request._kp_request_id_token = None
        return None


class AuditLogMiddleware(MiddlewareMixin):
    """Write privileged reads to the audit_log table (docs/02-TRD.md §11)."""

    AUDITED_PREFIXES = (
        "/dashboard/",
        "/policy/",
        "/officer/",
        "/my-data/",
        "/api/",
    )

    def process_response(self, request, response):
        path = request.path
        if any(path.startswith(prefix) for prefix in self.AUDITED_PREFIXES):
            from core.services.audit import log_access

            try:
                log_access(
                    request=request,
                    event_type="privileged_read",
                    description=f"GET {path} -> {response.status_code}",
                )
            except Exception:  # pragma: no cover - auditing must never break a page
                pass
        return response