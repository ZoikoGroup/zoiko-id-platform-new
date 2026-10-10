"""Canonical error model and response envelopes (ZID-API-001 §3, §20).

Error codes are stable machine contracts; the full registry is contracts/error-codes/error-codes.yaml.
Messages are safe: no stack traces, SQL, hostnames, secrets or personal data (ZID-API-001 §3.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ZidError", "ErrorDetail", "ErrorBody", "ErrorEnvelope", "SuccessEnvelope", "Meta",
    "NOT_FOUND", "VALIDATION_FAILED", "INTERNAL_ERROR", "DEPENDENCY_UNAVAILABLE",
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
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))


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

    def __init__(self, code: str, message: str, *, http_status: int, retryable: bool = False,
                 retry_after_ms: int | None = None, details: list[ErrorDetail] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.details = details or []

    def body(self) -> ErrorBody:
        return ErrorBody(code=self.code, message=self.message, details=self.details,
                         retryable=self.retryable, retry_after_ms=self.retry_after_ms,
                         docs_ref=f"errors/{self.code}")


# Platform-generic codes used by the service shell. Domain codes come from the registry.
NOT_FOUND = "NOT_FOUND"
VALIDATION_FAILED = "VALIDATION_FAILED"
INTERNAL_ERROR = "INTERNAL_ERROR"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
