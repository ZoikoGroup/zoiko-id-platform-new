"""The partner-facing error taxonomy (ZID-CP-WF10 v2.0 section 07.2; CR-173). Pure domain, no I/O.

A partner's server has to decide what to do with each error: retry, wait, ask an administrator, or stop. So every error the partner API can raise belongs to one named
class with one stated behaviour, and the class is the same wherever the error came from. The platform's internal codes stay as they are (existing integrations rely on
them); this catalog says which partner class each one belongs to, and a test keeps the two in step so a new internal code cannot ship unclassified.

Classes the platform cannot raise yet (a provider outcome unknown, a regional block) are listed with `raised_today` false: the document names them and a partner should
handle them, but nothing here pretends they happen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorClass:
    code: str
    http_status: int
    meaning: str
    caller_behavior: str
    retryable: bool
    internal_codes: tuple[str, ...]
    raised_today: bool = True


CATALOG: tuple[ErrorClass, ...] = (
    ErrorClass(
        "AUTH_INVALID",
        401,
        "The credential or token is missing, wrong, expired or revoked.",
        "Get a new token with a valid credential. Do not retry the same token.",
        False,
        ("AUTH_INVALID",),
    ),
    ErrorClass(
        "AUTH_SCOPE_DENIED",
        403,
        "The credential lacks the tenant, application, scope, method or network permission for this call.",
        "Never retry with a broader scope automatically. Ask a company administrator to grant it.",
        False,
        (
            "TENANT_SCOPE_DENIED",
            "INVALID_SCOPE",
            "IP_NOT_ALLOWED",
            "PRODUCT_NOT_ENTITLED",
            "APPROVAL_REQUIRED",
        ),
    ),  # fmt: skip  # noqa: RUF028
    ErrorClass(
        "ENVIRONMENT_MISMATCH",
        403,
        "A sandbox credential or session was used for production, or the other way round.",
        "Use the credential and session of the right environment. Crossing is never allowed.",
        False,
        ("ENVIRONMENT_MISMATCH", "PRODUCTION_NOT_APPROVED"),
    ),
    ErrorClass(
        "WORKFLOW_NOT_ELIGIBLE",
        403,
        "The requested workflow, version, market or country is not available to this application.",
        "Show the configured alternatives. Do not try to get round the policy.",
        False,
        (
            "WORKFLOW_NOT_ENTITLED",
            "WORKFLOW_VERSION_UNAVAILABLE",
            "MARKET_NOT_ENABLED",
            "RETURN_ORIGIN_NOT_REGISTERED",
        ),
    ),  # fmt: skip  # noqa: RUF028
    ErrorClass(
        "REGIONAL_POLICY_BLOCK",
        403,
        "Data residency or a legal route rule cannot be met for this request.",
        "Stop. Ask for the market to be approved.",
        False,
        (),
        raised_today=False,
    ),
    ErrorClass(
        "IDEMPOTENCY_CONFLICT",
        409,
        "The same Idempotency-Key was reused with a different request.",
        "Use a new key for a different request. No new session was created.",
        False,
        ("INVALID_IDEMPOTENCY_REUSE", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_KEY_REUSED"),
    ),
    ErrorClass(
        "PROVIDER_OUTCOME_UNKNOWN",
        409,
        "A provider attempt started and its outcome is not known.",
        "Wait and read the session again. Never start another verification for the same person.",
        False,
        (),
        raised_today=False,
    ),
    ErrorClass(
        "GO_LIVE_PRECONDITION_NOT_MET",
        409,
        "A step required before production access is not complete: sandbox checks, a verified company, or a recent pass of the sandbox test scenarios.",
        "Finish the step the message names, then ask again. Nothing was requested.",
        False,
        ("GO_LIVE_NOT_READY", "GO_LIVE_SUITE_NOT_PASSED", "GO_LIVE_COMPANY_NOT_VERIFIED"),
    ),  # fmt: skip  # noqa: RUF028
    ErrorClass(
        "RATE_LIMITED",
        429,
        "A quota or burst limit for the application or company was exceeded.",
        "Wait for the time in the Retry-After header, then retry once. Do not retry in a tight loop.",
        True,
        ("RATE_LIMITED",),
    ),
    ErrorClass(
        "FINAL_DECISION_NOT_COMMITTED",
        404,
        "Capture may be complete but no final decision has been committed yet.",
        "Keep showing the session as pending. Never treat it as verified.",
        False,
        ("DECISION_NOT_AVAILABLE",),
    ),
    ErrorClass(
        "SESSION_STATE_INVALID",
        409,
        "The session is in a state that does not allow this action.",
        "Read the session and act on its real state.",
        False,
        (
            "SESSION_STATE_INVALID",
            "CREDENTIAL_STATE_INVALID",
            "APPLICATION_STATE_INVALID",
            "GO_LIVE_ALREADY_APPROVED",
        ),
    ),
    ErrorClass(
        "NOT_FOUND",
        404,
        "There is no such object for this company.",
        "Check the identifier. A session or credential of another company is reported the same way.",
        False,
        ("NOT_FOUND", "LAUNCH_INVALID"),
    ),
    ErrorClass(
        "VALIDATION_FAILED",
        422,
        "The request is malformed or breaks a rule.",
        "Fix the request using the message. Retrying unchanged will fail again.",
        False,
        (
            "VALIDATION_FAILED",
            "SANDBOX_REQUIRES_SYNTHETIC_DATA",
            "APPLICATION_NAME_TAKEN",
            "LIMIT_REACHED",
            "NOTE_REQUIRED",
            "MTLS_NOT_CONFIGURED",
            "APPROVAL_SELF_DECISION",
            "APPROVAL_STATE_INVALID",
        ),
    ),
)

_BY_INTERNAL = {i: c for c in CATALOG for i in c.internal_codes}
_BY_CODE = {c.code: c for c in CATALOG}


def classify(internal_code: str) -> ErrorClass | None:
    """The partner class of an internal code, or None when it has not been classified (a test fails on that)."""
    return _BY_INTERNAL.get(internal_code) or _BY_CODE.get(internal_code)


def catalog_view() -> dict[str, object]:
    return {
        "version": "1",
        "errors": [
            {
                "code": c.code,
                "http_status": c.http_status,
                "meaning": c.meaning,
                "caller_behavior": c.caller_behavior,
                "retryable": c.retryable,
                "raised_today": c.raised_today,
                "internal_codes": list(c.internal_codes),
            }
            for c in CATALOG
        ],
        "note": "Classes marked raised_today false are defined so a partner can handle them; the platform does not raise them yet.",
    }


def parity_table() -> dict[str, object]:
    """The table every SDK ships: each internal code the API can answer with, mapped to its documented class and whether a retry can help. Generated from CATALOG, copied byte for byte
    into each SDK; a test fails if any copy differs, so the SDKs cannot disagree about what an error means."""
    codes: dict[str, dict[str, object]] = {}
    for c in CATALOG:
        for i in (c.code, *c.internal_codes):
            codes[i] = {"class": c.code, "retryable": c.retryable}
    return {
        "version": "1",
        "classes": sorted(c.code for c in CATALOG),
        "codes": dict(sorted(codes.items())),
    }


def parity_json() -> str:
    import json

    return json.dumps(parity_table(), indent=2, sort_keys=True) + "\n"
