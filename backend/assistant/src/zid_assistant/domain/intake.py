"""Sales and support intake: field schemas and validation (ZI-CHAT-INT-010 section 03; ADR-09, INV-05).

The forms are minimal by design: no file upload, no transcript, no account identifiers, no free-form diagnostics. A free-text field
is capped, stripped of markup and scanned for secrets before it can be stored. Notices and consent are server-verified; a client flag
is never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .sensitive import normalize, scan

SALES_FORM_REV = "intake-sales-v1"
SUPPORT_FORM_REV = "intake-support-v1"

SALES_INQUIRY_TYPES = ("DEMO", "SALES", "PARTNERSHIP", "DEVELOPER", "PROCUREMENT", "OTHER")
SALES_TIMEFRAMES = ("EXPLORING", "0_3_MONTHS", "3_6_MONTHS", "LATER")
SUPPORT_TOPICS = (
    "PUBLIC_DOCS",
    "API_GUIDANCE",
    "GENERAL_PRODUCT",
    "ACCESSIBILITY",
    "REPORT_CONTENT",
    "OTHER",
)
REQUESTER_TYPES = ("PROSPECT", "CUSTOMER", "DEVELOPER", "OTHER")

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")
_TAGS = re.compile(r"<[^>]*>")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class IntakeError(Exception):
    """A rejected submission. `code` is a stable API code; `field` is a safe field name, never the submitted value."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: str
    required: bool
    max_length: int | None = None
    options: tuple[str, ...] = ()


SALES_FIELDS = (
    FieldSpec("email", "email", True, 254),
    FieldSpec("contact_name", "text", False, 100),
    FieldSpec("organization_name", "text", False, 140),
    FieldSpec("inquiry_type", "enum", True, None, SALES_INQUIRY_TYPES),
    FieldSpec("timeframe", "enum", False, None, SALES_TIMEFRAMES),
    FieldSpec("message", "text", False, 500),
)
SUPPORT_FIELDS = (
    FieldSpec("email", "email", True, 254),
    FieldSpec("contact_name", "text", False, 100),
    FieldSpec("topic", "enum", True, None, SUPPORT_TOPICS),
    FieldSpec("requester_type", "enum", False, None, REQUESTER_TYPES),
    FieldSpec("summary", "text", True, 300),
)


def specs(purpose: str) -> tuple[FieldSpec, ...]:
    return SALES_FIELDS if purpose == "SALES" else SUPPORT_FIELDS


def form_revision(purpose: str) -> str:
    return SALES_FORM_REV if purpose == "SALES" else SUPPORT_FORM_REV


def clean_text(value: str) -> str:
    """Strip markup and control characters, collapse whitespace. Never trusts the client."""
    v = normalize(value)
    v = _TAGS.sub("", v)
    v = "".join(ch for ch in v if ch == "\n" or ch >= " ")
    return re.sub(r"[ \t]+", " ", v).strip()


def validate_country(code: Any) -> str:
    if not isinstance(code, str) or not _COUNTRY.match(code):
        raise IntakeError("INVALID_REQUEST", "country_code")
    return code


def validate_fields(purpose: str, fields: Any) -> dict[str, str]:
    """Return the cleaned fields or raise IntakeError. Unknown keys are rejected (no overposting); sensitive content is blocked."""
    if not isinstance(fields, dict):
        raise IntakeError("INVALID_REQUEST", "fields")
    allowed = {s.name: s for s in specs(purpose)}
    for k in fields:
        if k not in allowed:
            raise IntakeError("INVALID_REQUEST", "fields")
    out: dict[str, str] = {}
    for name, spec in allowed.items():
        raw = fields.get(name)
        if raw is None or raw == "":
            if spec.required:
                raise IntakeError("INVALID_REQUEST", name)
            continue
        if not isinstance(raw, str):
            raise IntakeError("INVALID_REQUEST", name)
        if spec.type == "enum":
            if raw not in spec.options:
                raise IntakeError("INVALID_REQUEST", name)
            out[name] = raw
            continue
        v = clean_text(raw)
        if spec.max_length is not None and len(v) > spec.max_length:
            raise IntakeError(
                "PAYLOAD_TOO_LARGE" if len(raw) > 4 * spec.max_length else "INVALID_REQUEST", name
            )
        if spec.type == "email":
            v = v.lower()
            if not _EMAIL.match(v):
                raise IntakeError("INVALID_REQUEST", name)
        elif scan(v).blocked:
            raise IntakeError("SENSITIVE_CONTENT_BLOCKED", name)
        if name == "summary" and len(v) < 10:
            raise IntakeError("INVALID_REQUEST", name)
        out[name] = v
    return out


def validate_phone(
    value: str,
) -> str:  # reserved: phone is not collected in v1 (OD-013); kept for the signed field matrix
    if not _E164.match(value):
        raise IntakeError("INVALID_REQUEST", "phone")
    return value
