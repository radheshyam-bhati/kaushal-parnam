"""Structured JSON logging (docs/02-TRD.md §11, docs/08-Architecture.md §4.11)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class StructuredJsonFormatter(logging.Formatter):
    """Render each record as one JSON object.

    Extra keys passed via ``logger.info("x", extra={...})`` are merged in, which
    is how component / user_role / event / utid / request_id get attached.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload or key == "event":
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def audit_log(logger_name: str, event: str, **fields) -> None:
    """Emit one structured audit line."""
    logging.getLogger(logger_name).info(event, extra={"event": event, **fields})