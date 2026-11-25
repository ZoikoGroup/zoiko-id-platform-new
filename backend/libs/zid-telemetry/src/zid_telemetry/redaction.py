"""Telemetry redaction, applied at the source (DEC-G5 §2; SRE-001 §27; OPS-07, SRE-05).

Two rules, both fail-closed:
- **keys**: a span, log or baggage attribute is kept only if its key is on the allowlist
  (`zoiko.*` extensions are reviewed additions). Unknown keys are dropped, never passed through;
- **values**: a kept value that looks like a credential or direct identifier (JWT, bearer or basic
  credential, PEM block, e-mail, card-like or long secret-like strings) is replaced by `[REDACTED]`.

The collector pipeline applies the same rules a second time; this module is the first pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

# OpenTelemetry stable semantic-convention keys plus the reviewed zoiko.* extensions. Adding a key
# here is a reviewed change (DEC-G5 consequences).
ATTRIBUTE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # http / server (semconv, stable)
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "url.scheme",
        "server.address",
        "network.protocol.version",
        "error.type",
        # service resource
        "service.name",
        "service.version",
        "deployment.environment.name",
        "cloud.region",
        # database / messaging (operation level only, never statements or payloads)
        "db.system.name",
        "db.operation.name",
        "messaging.system",
        "messaging.destination.name",
        "messaging.operation.type",
        # zoiko extensions: opaque ids and bounded enums only
        "zoiko.cell_id",
        "zoiko.tenant_id",
        "zoiko.request_id",
        "zoiko.correlation_id",
        "zoiko.decision_id",
        "zoiko.receipt_id",
        "zoiko.journey",
        "zoiko.risk_class",
        "zoiko.outcome",
        "zoiko.reason_code",
        "zoiko.evidence_class",
        "zoiko.lane",
        "zoiko.component",
        # structured log fields of zid-telemetry
        "method",
        "path",
        "status",
        "duration_ms",
        "component",
        "outcome",
    }
)

# Values that are never telemetry, whatever key carries them
_SECRET_VALUE = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),  # JWT / JWS
    re.compile(r"(?i)\b(bearer|basic|dpop)\s+[A-Za-z0-9._~+/=-]{8,}"),  # Authorization values
    re.compile(r"-----BEGIN [A-Z ]+-----"),  # PEM keys/certificates
    re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[A-Za-z]{2,}"),  # e-mail
    re.compile(
        r"(?<![\w-])\d{4}(?:[ -]?\d{4}){2,3}(?:[ -]?\d{1,3})?(?![\w-])"
    ),  # card-like (not UUID parts)
    re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|client_secret|private_key)\s*[=:]\s*\S+"
    ),
    re.compile(r"\b(?:zid|sk|pk|ghp|gho|xox[abp])_[A-Za-z0-9]{16,}\b"),  # vendor-style tokens
    re.compile(r"(?<![A-Za-z0-9])(?:zvl|zat|zps)_[A-Za-z0-9_-]{16,}"),  # Verify API launch links, access tokens and client secrets (WF10 row 4-02)
    # ZID-UX-PRICE-004 P47: no card number or security code reaches a log line or span attribute, in any of the shapes a payload or a message puts them in
    re.compile(r"(?<![\w-])3[47]\d{2}[ -]?\d{6}[ -]?\d{5}(?![\w-])"),  # American Express 4-6-5
    re.compile(
        r"(?i)[\"']?\b(cvc2?|cvv2?|cid|csc|security[ _-]?code|card[ _-]?verification(?:[ _-]?(?:code|value))?)\b[\"']?(?:\s*[=:]\s*|\s+)[\"']?\d{3,4}\b[\"']?"
    ),  # a labelled security code
    re.compile(
        r"(?i)[\"']?\b(card[ _-]?number|card[ _-]?no|primary[ _-]?account[ _-]?number|pan)\b[\"']?\s*[=:]\s*[\"']?[\d \-]{12,}[\"']?"
    ),  # a labelled card number
)


def scrub(value: str) -> str:
    """Replace every secret- or identifier-shaped run inside a free-text value."""
    for pattern in _SECRET_VALUE:
        value = pattern.sub(REDACTED, value)
    return value


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return REDACTED  # nested objects are not telemetry attributes


def redact(
    attributes: Mapping[str, Any], allow: frozenset[str] = ATTRIBUTE_ALLOWLIST
) -> dict[str, Any]:
    """Allowlisted keys with scrubbed values; everything else is dropped."""
    return {k: redact_value(v) for k, v in attributes.items() if k in allow}


def dropped_keys(
    attributes: Mapping[str, Any], allow: frozenset[str] = ATTRIBUTE_ALLOWLIST
) -> list[str]:
    """For the cardinality/redaction audit: the keys `redact` would drop."""
    return sorted(k for k in attributes if k not in allow)
