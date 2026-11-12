"""Public error contract (API.md; ZI-CHAT-ENG-003 section 04). Messages are safe: never an echo of input, never a trace."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

_MESSAGES = {
    "INVALID_REQUEST": "The request is not valid.",
    "INVALID_GUEST_SESSION": "Your session has ended. Start a new one to continue.",
    "ORIGIN_DENIED": "This request could not be verified.",
    "REGION_OR_POLICY_BLOCK": "This option is not available here.",
    "NOT_FOUND": "Not found.",
    "IDEMPOTENCY_CONFLICT": "That request was already used for something different. Please try again.",
    "PAYLOAD_TOO_LARGE": "The request is too large.",
    "UNSUPPORTED_MEDIA_TYPE": "Requests must be JSON.",
    "SENSITIVE_CONTENT_BLOCKED": "That looks like it contains a secret or personal identity data, so it was not processed or stored. Please remove it and try again.",
    "RESOURCE_LIMIT": "You are sending requests too quickly. Please wait a moment and try again.",
    "UNAVAILABLE": "The assistant is unavailable right now. The official links are still available.",
}
_STATUS = {
    "INVALID_REQUEST": 400,
    "INVALID_GUEST_SESSION": 401,
    "ORIGIN_DENIED": 403,
    "REGION_OR_POLICY_BLOCK": 403,
    "NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "SENSITIVE_CONTENT_BLOCKED": 422,
    "RESOURCE_LIMIT": 429,
    "UNAVAILABLE": 503,
}


class ApiError(Exception):
    def __init__(
        self, code: str, *, retry_after_s: int | None = None, field: str | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = _STATUS.get(code, 400)
        self.retry_after_s = retry_after_s
        self.field = field


def error_response(request: Request, exc: ApiError) -> JSONResponse:
    cid = (
        getattr(request.state, "correlation_id", None)
        or request.headers.get("x-correlation-id")
        or "cor_unknown"
    )
    body = {
        "error": {
            "code": exc.code,
            "message": _MESSAGES.get(exc.code, "The request could not be processed."),
            "correlation_id": cid,
            "retry_after_s": exc.retry_after_s,
        }
    }
    if exc.field:
        body["error"]["field"] = exc.field  # a field NAME only, never the submitted value
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after_s:
        headers["Retry-After"] = str(exc.retry_after_s)
    return JSONResponse(body, status_code=exc.status, headers=headers)
