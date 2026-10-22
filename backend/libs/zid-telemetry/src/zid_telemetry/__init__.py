"""Structured, PII-safe telemetry (DEC-G5): JSON logs, W3C trace context, governed metrics and
the SLO/error-budget engine. OTLP export runs through the cell's OpenTelemetry collector.

Logs carry opaque identifiers only (request_id, correlation_id, tenant_id, trace/span ids) — never
bodies, tokens, credentials or personal data. The message text itself is scrubbed (`redaction`),
so a secret that reaches a log call by mistake is masked at the source (OPS-07).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from zid_context import current_context

from zid_telemetry.redaction import redact_value, scrub
from zid_telemetry.tracing import current_trace

__all__ = ["JsonFormatter", "configure_logging", "get_logger"]

_ALLOWED_EXTRA = ("method", "path", "status", "duration_ms", "component", "outcome")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, cell_id: str) -> None:
        super().__init__()
        self.service = service
        self.cell_id = cell_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "cell": self.cell_id,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        ctx = current_context()
        if ctx is not None:
            entry["request_id"] = ctx.request_id
            entry["correlation_id"] = ctx.correlation_id
            if ctx.tenant_id:
                entry["tenant_id"] = ctx.tenant_id
        trace = current_trace()
        if trace is not None:
            entry["trace_id"] = trace.trace_id
            entry["span_id"] = trace.span_id
        for key in _ALLOWED_EXTRA:  # allowlist: unknown attributes are never logged
            if hasattr(record, key):
                entry[key] = redact_value(getattr(record, key))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, separators=(",", ":"))


def configure_logging(service: str, cell_id: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service, cell_id))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access",):  # replaced by our request log line
        logging.getLogger(noisy).disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
