"""W3C Trace Context propagation (SRE-001 §3.1, §10; OPS-06, SRE-06).

The cell continues a well-formed inbound `traceparent` (so one journey correlates across services
and approved integrations) and starts a new trace otherwise. A trace id is a correlation
reference only: it never carries or implies permission, and `tracestate`/baggage from outside are
not propagated. Spans are exported by the OpenTelemetry collector sidecar where one is deployed;
the ids are always present in logs so logs, metrics exemplars and evidence correlate.
"""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    sampled: bool

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


def parse_traceparent(value: str | None) -> tuple[str, str, bool] | None:
    """(trace_id, parent_span_id, sampled) of a valid version-00 header, else None."""
    if not value or len(value) != 55:
        return None
    m = _TRACEPARENT.match(value.strip().lower())
    if m is None:
        return None
    trace_id, parent, flags = m.groups()
    if trace_id == "0" * 32 or parent == "0" * 16:
        return None  # all-zero ids are invalid by the spec
    return trace_id, parent, bool(int(flags, 16) & 1)


def start_trace(inbound: str | None, *, sample: bool = True) -> TraceContext:
    """A server span for this request: child of a valid inbound parent, else a new root."""
    parsed = parse_traceparent(inbound)
    span = secrets.token_hex(8)
    if parsed:
        trace_id, parent, sampled = parsed
        return TraceContext(trace_id, span, parent, sampled or sample)
    return TraceContext(secrets.token_hex(16), span, None, sample)


_current: ContextVar[TraceContext | None] = ContextVar("zid_trace", default=None)


def current_trace() -> TraceContext | None:
    return _current.get()


def set_trace(t: TraceContext) -> object:
    return _current.set(t)


def reset_trace(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]
