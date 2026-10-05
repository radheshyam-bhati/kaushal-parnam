"""Request middleware: session identity, PostgreSQL row-level security, audit log.

Docs: docs/05-BACKEND-SCHEMA.md §1.1 (audit_log trigger reads app.user_role),
docs/08-Architecture.md §5.3 (RLS policies), docs/02-TRD.md §11 (logging).
"""

from __future__ import annotations

from django.db import connection
from django.utils.deprecation import MiddlewareMixin

from core.logging import audit_log

AUDIT_LOGGER = "core.audit"


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
    current transaction and cannot leak between requests.
    """

    def process_request(self, request):
        if not postgres_enabled():
            return None
        values = {
            "app.user_role": getattr(request, "user_role", "anonymous"),
            "app.utid": getattr(request, "utid", "") or "",
            "app.provider_id": getattr(request, "provider_id", None) or "",
            "app.district": getattr(request, "district", None) or "",
            "app.request_id": request.META.get("HTTP_X_REQUEST_ID", ""),
        }
        try:
            with connection.cursor() as cursor:
                for key, value in values.items():
                    cursor.execute("SELECT set_config(%s, %s, true)", [key, str(value)])
        except Exception:  # pragma: no cover - never block a request on RLS setup
            audit_log(
                AUDIT_LOGGER,
                "rls_session_setup_failed",
                component="rls",
                user_role=str(getattr(request, "user_role", "anonymous")),
            )
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