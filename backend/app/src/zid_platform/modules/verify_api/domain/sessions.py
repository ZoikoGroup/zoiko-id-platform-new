"""Verify API sessions and decisions (ZID-EXT-IDV-001 sections 9, 10.1, 10.2, WF-09; CR-150). Pure rules, no I/O.

A SESSION is one verification of one of a partner's customers. The partner's SERVER creates it (never a browser), gets a launch link for the person, and later reads a minimized DECISION. The person gets only
the launch link: a token tied to this one session that ends on its own. Nothing here lets anyone state a result: a decision comes from the engine, or, in the SANDBOX, from a simulator that is always marked as a
simulation and never touches a real person, document or face.

Rules that follow the document:
- a session is bound to the application that created it; another application of the same company cannot read it;
- the same idempotency key with the same request returns the same session, and with a different request is refused;
- the states and the allowed moves between them are fixed here, and a final state never reopens;
- a decision is versioned: a later one supersedes an earlier one and the earlier one is kept, so nothing is quietly overwritten;
- a decision carries a minimal outcome, check results and reason codes. No photograph, document image, score or reviewer note is ever part of it.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

SESSION_TTL = timedelta(
    hours=24
)  # how long a session can be finished in; a proposal until the owners ratify the launch policy (decision D09)
LAUNCH_TTL = timedelta(minutes=30)  # how long a launch link works
IDEMPOTENCY_TTL = timedelta(hours=24)
LAUNCH_PREFIX = "zvl_"
MAX_LIST = 100


class Status(StrEnum):
    CREATED = "created"
    READY = "ready"
    OPENED = "opened"
    CAPTURE_STARTED = "capture_started"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    MANUAL_REVIEW = "manual_review"
    ACTION_REQUIRED = "action_required"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    EXPIRED = "expired"
    CANCELED = "canceled"


FINAL = frozenset({Status.COMPLETED, Status.INCONCLUSIVE, Status.EXPIRED, Status.CANCELED})

# created -> ready -> opened -> capture_started -> submitted -> processing -> (manual_review | action_required | completed | inconclusive | expired | canceled)
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.CREATED: frozenset({Status.READY, Status.CANCELED, Status.EXPIRED}),
    Status.READY: frozenset({Status.OPENED, Status.CANCELED, Status.EXPIRED}),
    Status.OPENED: frozenset({Status.CAPTURE_STARTED, Status.CANCELED, Status.EXPIRED}),
    Status.CAPTURE_STARTED: frozenset({Status.SUBMITTED, Status.CANCELED, Status.EXPIRED}),
    Status.SUBMITTED: frozenset({Status.PROCESSING, Status.CANCELED, Status.EXPIRED}),
    Status.PROCESSING: frozenset(
        {
            Status.MANUAL_REVIEW,
            Status.ACTION_REQUIRED,
            Status.COMPLETED,
            Status.INCONCLUSIVE,
            Status.EXPIRED,
            Status.CANCELED,
        }
    ),
    Status.MANUAL_REVIEW: frozenset(
        {Status.COMPLETED, Status.INCONCLUSIVE, Status.EXPIRED, Status.CANCELED}
    ),
    Status.ACTION_REQUIRED: frozenset({Status.CAPTURE_STARTED, Status.EXPIRED, Status.CANCELED}),
    Status.COMPLETED: frozenset(),
    Status.INCONCLUSIVE: frozenset(),
    Status.EXPIRED: frozenset(),
    Status.CANCELED: frozenset(),
}


def check_transition(current: Status, target: Status) -> Status:
    if target not in TRANSITIONS[current]:
        raise PartnerError(
            "SESSION_STATE_INVALID",
            f"A session that is {current.value.replace('_', ' ')} cannot become {target.value.replace('_', ' ')}.",
        )
    return target


# The sandbox has no capture: a simulated outcome goes straight from a session that is ready, opened or waiting for review to its result. This is the only place the normal path is shortened, and only for SANDBOX.
SIMULATED_FROM = frozenset({Status.READY, Status.OPENED, Status.MANUAL_REVIEW})
SIMULATED_TO = frozenset(
    {Status.COMPLETED, Status.INCONCLUSIVE, Status.MANUAL_REVIEW, Status.EXPIRED}
)


def check_simulated_transition(current: Status, target: Status) -> Status:
    if (
        current not in SIMULATED_FROM
        or target not in SIMULATED_TO
        or (current is Status.MANUAL_REVIEW and target is Status.MANUAL_REVIEW)
    ):
        raise PartnerError(
            "SESSION_STATE_INVALID",
            f"A session that is {current.value.replace('_', ' ')} cannot take a simulated outcome that leaves it {target.value.replace('_', ' ')}.",
        )
    return target


class Outcome(StrEnum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INCONCLUSIVE = "inconclusive"
    REVIEW_REQUIRED = "review_required"


# ------------------------------------------------------------------------------- the catalogue the sandbox offers

# Approved, immutable workflows (the document's "workflow ids and versions"). A session pins one version; a partner can never name a version that is not listed here.
CURRENT_VERSION = "2026-11-sandbox"
WORKFLOWS: dict[str, dict[str, Any]] = {
    "wf_document_selfie": {
        "name": "Document and selfie",
        "description": "The person photographs an identity document and takes a selfie. In the sandbox the outcome is simulated.",
        "methods": ["document_authenticity", "face_match"],
        "versions": [CURRENT_VERSION],
    },
    "wf_document_only": {
        "name": "Document only",
        "description": "The person photographs an identity document. In the sandbox the outcome is simulated.",
        "methods": ["document_authenticity"],
        "versions": [CURRENT_VERSION],
    },
}
PRODUCTS = [
    {
        "method": "document_authenticity",
        "name": "Identity document check",
        "environments": ["sandbox"],
        "note": "Sandbox only: outcomes are simulated. Live checks open per market after the production gate.",
    },
    {
        "method": "face_match",
        "name": "Face match to the document",
        "environments": ["sandbox"],
        "note": "Sandbox only: outcomes are simulated. A face check is enabled per market only after its privacy and testing gates.",
    },
]
PURPOSES = (
    "customer_onboarding",
    "account_recovery",
    "fraud_prevention",
    "regulatory_compliance",
    "employment_screening",
    "tenancy_screening",
)

_REF = re.compile(r"^[\x21-\x7e][\x20-\x7e]{0,126}[\x21-\x7e]$|^[\x21-\x7e]$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_REGION = re.compile(r"^[a-z0-9-]{2,16}$")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_IDEM = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")


def check_idempotency_key(key: str | None) -> str:
    if not key or not _IDEM.fullmatch(key):
        raise PartnerError(
            "VALIDATION_FAILED",
            "Send an Idempotency-Key header of 8 to 128 letters, numbers or - _ : . that is unique for this one business operation.",
        )
    return key


def check_session_request(body: dict[str, Any]) -> dict[str, Any]:
    """The request, cleaned and checked. The partner's own reference is opaque to us: it is how the partner finds its customer again, and nothing in it is read as identity."""
    ref = body.get("customer_reference")
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise PartnerError(
            "VALIDATION_FAILED",
            "customer_reference is your own reference for the person: 1 to 128 visible characters.",
        )
    workflow_id = body.get("workflow_id", "wf_document_selfie")
    if workflow_id not in WORKFLOWS:
        raise PartnerError(
            "WORKFLOW_VERSION_UNAVAILABLE",
            f"Unknown workflow {workflow_id!r}. List the available ones with GET /v1/verify-api/workflows.",
        )
    version = body.get("workflow_version") or CURRENT_VERSION
    if version not in WORKFLOWS[workflow_id]["versions"]:
        raise PartnerError(
            "WORKFLOW_VERSION_UNAVAILABLE",
            f"Version {version!r} of {workflow_id} is not available.",
        )
    country = body.get("country")
    if not isinstance(country, str) or not _COUNTRY.fullmatch(country):
        raise PartnerError(
            "VALIDATION_FAILED", "country is a two-letter upper-case code such as US."
        )
    region = body.get("region") or country.lower()
    if not isinstance(region, str) or not _REGION.fullmatch(region):
        raise PartnerError(
            "VALIDATION_FAILED", "region is 2 to 16 lower-case letters, numbers or dashes."
        )
    purpose = body.get("purpose_code")
    if purpose not in PURPOSES:
        raise PartnerError("VALIDATION_FAILED", f"purpose_code is one of: {', '.join(PURPOSES)}.")
    locale = body.get("locale", "en-US")
    if not isinstance(locale, str) or not _LOCALE.fullmatch(locale):
        raise PartnerError("VALIDATION_FAILED", "locale looks like en or en-US.")
    meta = body.get("metadata")
    meta = {} if meta is None else meta
    if (
        not isinstance(meta, dict)
        or len(meta) > 10
        or any(
            not isinstance(k, str) or not isinstance(v, str) or len(k) > 40 or len(v) > 200
            for k, v in meta.items()
        )
    ):
        raise PartnerError(
            "VALIDATION_FAILED",
            "metadata is up to 10 short text pairs. Do not put personal data in it.",
        )
    origin = body.get("return_origin")
    if origin is not None and not isinstance(origin, str):
        raise PartnerError(
            "VALIDATION_FAILED", "return_origin is one of your application's registered origins."
        )
    return {
        "customer_reference": ref,
        "workflow_id": workflow_id,
        "workflow_version": version,
        "country": country,
        "region": region,
        "purpose_code": purpose,
        "locale": locale,
        "metadata": meta,
        "return_origin": origin.lower().rstrip("/") if origin else None,
    }


def request_hash(clean: dict[str, Any]) -> bytes:
    """The identity of a create request, for idempotency: the same cleaned request always has the same hash."""
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


# ------------------------------------------------------------------------------- ids and tokens


def new_session_id() -> str:
    return "vfs_" + uuid.uuid4().hex


def new_decision_id() -> str:
    return "dec_" + uuid.uuid4().hex


def new_launch_token() -> str:
    return LAUNCH_PREFIX + secrets.token_urlsafe(32)


# ------------------------------------------------------------------------------- the sandbox simulator


class Fixture(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"
    REVIEW = "review"
    INCONCLUSIVE = "inconclusive"
    EXPIRED = "expired"


def check_fixture(name: str) -> Fixture:
    try:
        return Fixture(name.lower())
    except ValueError as e:
        raise PartnerError(
            "VALIDATION_FAILED",
            f"The test outcome is one of: {', '.join(f.value for f in Fixture)}.",
        ) from e


def simulate(
    fixture: Fixture, methods: list[str]
) -> tuple[Status, Outcome | None, list[dict[str, str]], list[str]]:
    """What a simulated verification ends as: the session's status, the decision's outcome (none for an expired session), the check results and the reason codes. Always synthetic."""
    if fixture is Fixture.APPROVED:
        return (
            Status.COMPLETED,
            Outcome.VERIFIED,
            [{"method": m, "result": "pass"} for m in methods],
            [],
        )
    if fixture is Fixture.DECLINED:
        return (
            Status.COMPLETED,
            Outcome.NOT_VERIFIED,
            [{"method": m, "result": "fail" if i == 0 else "pass"} for i, m in enumerate(methods)],
            ["SIMULATED_DOCUMENT_NOT_ACCEPTED"],
        )
    if fixture is Fixture.REVIEW:
        return (
            Status.MANUAL_REVIEW,
            Outcome.REVIEW_REQUIRED,
            [{"method": m, "result": "review"} for m in methods],
            ["SIMULATED_HUMAN_REVIEW_NEEDED"],
        )
    if fixture is Fixture.INCONCLUSIVE:
        return (
            Status.INCONCLUSIVE,
            Outcome.INCONCLUSIVE,
            [{"method": m, "result": "inconclusive"} for m in methods],
            ["SIMULATED_PROVIDER_UNAVAILABLE"],
        )
    return Status.EXPIRED, None, [], []
