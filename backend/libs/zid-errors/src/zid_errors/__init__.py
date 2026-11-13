"""Canonical error model and response envelopes (ZID-API-001 §3, §20).

Error codes are stable machine contracts; the full registry is contracts/error-codes/error-codes.yaml.
Messages are safe: no stack traces, SQL, hostnames, secrets or personal data (ZID-API-001 §3.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "DEPENDENCY_UNAVAILABLE",
    "INTERNAL_ERROR",
    "NOT_FOUND",
    "PROBLEM_CODES",
    "VALIDATION_FAILED",
    "ErrorBody",
    "ErrorDetail",
    "ErrorEnvelope",
    "Meta",
    "SuccessEnvelope",
    "ZidError",
    "problem_document",
]

API_VERSION = "v1"


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    retryable: bool = False
    retry_after_ms: int | None = None
    docs_ref: str | None = None


class Meta(BaseModel):
    api_version: str = API_VERSION
    service: str
    region: str
    tenant_id: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


class SuccessEnvelope(BaseModel):
    success: bool = True
    request_id: str
    correlation_id: str
    data: Any
    meta: Meta


class ErrorEnvelope(BaseModel):
    success: bool = False
    request_id: str
    correlation_id: str
    error: ErrorBody


class ZidError(Exception):
    """Raise anywhere in a request; the app's handler renders the canonical error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        retryable: bool = False,
        retry_after_ms: int | None = None,
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.headers = headers or {}  # e.g. WWW-Authenticate on 401 (RFC 6750 §3, RFC 9449 §7.1)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.details = details or []

    def body(self) -> ErrorBody:
        return ErrorBody(
            code=self.code,
            message=self.message,
            details=self.details,
            retryable=self.retryable,
            retry_after_ms=self.retry_after_ms,
            docs_ref=f"errors/{self.code}",
        )


# Platform-generic codes used by the service shell. Domain codes come from the registry.
NOT_FOUND = "NOT_FOUND"
VALIDATION_FAILED = "VALIDATION_FAILED"
INTERNAL_ERROR = "INTERNAL_ERROR"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


# ---------------------------------------------------------------------------------------------------------------------
# RFC 9457 Problem Details for the /v2 surface (ZID-INT-002 section 14). The canonical envelope above stays the /v1 contract.

# this platform's stable code -> (public problem `code`, title)
PROBLEM_CODES: dict[str, tuple[str, str]] = {
    "VALIDATION_FAILED": ("invalid_request", "Invalid request"),
    "IDEMPOTENCY_KEY_REUSED": (
        "idempotency_conflict",
        "Idempotency key reused with a different request",
    ),
    "IDEMPOTENCY_IN_PROGRESS": (
        "incompatible_state",
        "The original request is still being processed",
    ),
    "PACK_UNAVAILABLE": ("unsupported_market", "Verification route unavailable"),
    # the country registry does not open this capability in this country (the country gate, EDR-001; CR-088)
    "COUNTRY_CAPABILITY_UNAVAILABLE": ("unsupported_market", "Verification route unavailable"),
    "SOURCE_NOT_COVERED": ("unsupported_market", "Verification route unavailable"),
    "SOURCE_CONTRACT_NOT_IN_FORCE": ("unsupported_market", "Verification route unavailable"),
    "DEPENDENCY_UNAVAILABLE": ("provider_unavailable", "Provider unavailable"),
    "RATE_LIMITED": ("quota_exceeded", "Quota exceeded"),
    "UNAUTHENTICATED": ("unauthenticated", "Unauthenticated"),
    "FORBIDDEN": ("scope_forbidden", "Scope forbidden"),
    "NOT_FOUND": ("not_found", "Not found"),
}


def problem_document(
    code: str, status: int, detail: str, *, trace_id: str
) -> tuple[int, dict[str, Any]]:
    """An RFC 9457 problem. An unsupported market is always 422 and never a positive result (section 14)."""
    public, title = PROBLEM_CODES.get(code, (code.lower(), code.replace("_", " ").capitalize()))
    if public == "unsupported_market":
        status = 422
    return status, {
        "type": f"https://api.zoikoid.com/problems/{public.replace('_', '-')}",
        "title": title,
        "status": status,
        "code": public,
        "trace_id": trace_id,
        "detail": detail,
    }
