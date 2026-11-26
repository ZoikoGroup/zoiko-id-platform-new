"""Opening production for a partner (ZID-EXT-IDV-001 section 11, epic E10, gates G1 to G4; CR-153). Pure rules, no I/O.

A company asks to go live with the markets, checks and purpose it wants and accepts the current terms and data-processing terms. The platform checks what it can see on its own: the sandbox
integration is certified (it has really driven an approved and a declined result and has a working webhook endpoint). What only a person can confirm (the company is verified, the contract is signed, privacy
has reviewed, billing is ready, the check provider is approved for the market) an operator confirms one by one, and a DIFFERENT operator from the one who started the review decides. Approval records exactly
what was allowed, with a digest, as the company's production grant. Nothing here says a company is "compliant" or "certified" beyond the checks it names.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.verify_api.domain.live import LIVE_METHOD
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.domain.sessions import PURPOSES

TERMS_VERSION = "2026-11"
DPA_VERSION = "2026-11"
VOLUME_BANDS = {
    "lt_1k": "Fewer than 1,000 a month",
    "1k_10k": "1,000 to 10,000 a month",
    "10k_100k": "10,000 to 100,000 a month",
    "gt_100k": "More than 100,000 a month",
}
METHODS = (
    LIVE_METHOD,
)  # the sandbox offers more; production can be approved only for the check the live product actually has
PURPOSE_CHOICES = tuple(PURPOSES)
MAX_MARKETS = 20
COUNTRY = re.compile(r"^[A-Z]{2}$")
NOTE_MAX = 1000


class GoLiveStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


OPEN = frozenset({GoLiveStatus.SUBMITTED, GoLiveStatus.IN_REVIEW})
TRANSITIONS: dict[GoLiveStatus, frozenset[GoLiveStatus]] = {
    GoLiveStatus.SUBMITTED: frozenset(
        {GoLiveStatus.IN_REVIEW, GoLiveStatus.DECLINED, GoLiveStatus.WITHDRAWN}
    ),
    GoLiveStatus.IN_REVIEW: frozenset(
        {GoLiveStatus.APPROVED, GoLiveStatus.DECLINED, GoLiveStatus.WITHDRAWN}
    ),
    GoLiveStatus.APPROVED: frozenset(),
    GoLiveStatus.DECLINED: frozenset(),
    GoLiveStatus.WITHDRAWN: frozenset(),
}

# What a person at Zoiko confirms before production opens; every one must be confirmed, none is assumed.
ATTESTATIONS: dict[str, str] = {
    "company_verified": "The company's identity and authority have been verified.",
    "terms_signed": "The commercial terms and data-processing terms are signed.",
    "privacy_reviewed": "Privacy has reviewed the markets, purpose and retention.",
    "billing_ready": "Billing is set up for the expected volume.",
    "provider_approved": "The check provider is approved for each requested market and method.",
}


def check_transition(current: GoLiveStatus, target: GoLiveStatus) -> GoLiveStatus:
    if target not in TRANSITIONS[current]:
        raise PartnerError(
            "GO_LIVE_STATE_INVALID",
            f"A {current.value.lower().replace('_', ' ')} request cannot become {target.value.lower().replace('_', ' ')}.",
        )
    return target


def check_request(body: dict[str, Any]) -> dict[str, Any]:
    """The cleaned request, or the first thing wrong with it. The company's own words are kept as they are, trimmed."""
    markets = body.get("markets")
    if (
        not isinstance(markets, list)
        or not markets
        or len(markets) > MAX_MARKETS
        or not all(isinstance(c, str) and COUNTRY.fullmatch(c.upper()) for c in markets)
    ):
        raise PartnerError(
            "VALIDATION_FAILED", "Choose between 1 and 20 markets, as two-letter country codes."
        )
    methods = body.get("methods")
    if (
        not isinstance(methods, list)
        or not methods
        or not all(isinstance(x, str) and x in METHODS for x in methods)
    ):
        raise PartnerError("VALIDATION_FAILED", "Choose at least one of the available checks.")
    purpose = body.get("purpose_code")
    if purpose not in PURPOSES:
        raise PartnerError("VALIDATION_FAILED", "Choose one of the listed purposes.")
    band = body.get("volume_band")
    if band not in VOLUME_BANDS:
        raise PartnerError("VALIDATION_FAILED", "Choose how many verifications you expect.")
    use_case = body.get("use_case")
    if not isinstance(use_case, str) or not 10 <= len(use_case.strip()) <= 500:
        raise PartnerError(
            "VALIDATION_FAILED", "Describe in 10 to 500 characters what you will use it for."
        )
    if body.get("terms_version") != TERMS_VERSION or body.get("accept_terms") is not True:
        raise PartnerError(
            "TERMS_NOT_ACCEPTED", f"Accept the current terms (version {TERMS_VERSION})."
        )
    if body.get("dpa_version") != DPA_VERSION or body.get("accept_dpa") is not True:
        raise PartnerError(
            "TERMS_NOT_ACCEPTED",
            f"Accept the current data-processing terms (version {DPA_VERSION}).",
        )
    return {
        "markets": sorted({c.upper() for c in markets}),
        "methods": sorted(set(methods)),
        "purpose_code": str(purpose),
        "volume_band": str(band),
        "use_case": use_case.strip(),
        "terms_version": TERMS_VERSION,
        "dpa_version": DPA_VERSION,
    }


@dataclass(frozen=True, slots=True)
class SandboxFacts:
    """What the company's sandbox work shows: counts only, never a person or a reference."""

    verified_decisions: int
    not_verified_decisions: int
    active_webhook_endpoints: int


def certification(f: SandboxFacts) -> list[dict[str, Any]]:
    """The automatic checks, each with whether it is met and what to do if not."""
    return [
        {
            "id": "sandbox_approved",
            "label": "A test verification that passes",
            "met": f.verified_decisions >= 1,
            "help": "In the Test Lab, make a test verification and choose Pass.",
        },
        {
            "id": "sandbox_declined",
            "label": "A test verification that fails",
            "met": f.not_verified_decisions >= 1,
            "help": "Make another and choose Fail, so you know your system handles a refusal.",
        },
        {
            "id": "webhook_ready",
            "label": "A working webhook endpoint",
            "met": f.active_webhook_endpoints >= 1,
            "help": "Register and verify a webhook endpoint under Webhooks so results reach your system.",
        },
    ]


def certified(checks: list[dict[str, Any]]) -> bool:
    return all(c["met"] for c in checks)


def check_note(note: str | None, *, required: bool) -> str:
    clean = (note or "").strip()
    if len(clean) > NOTE_MAX:
        raise PartnerError("VALIDATION_FAILED", f"The note is longer than {NOTE_MAX} characters.")
    if required and len(clean) < 3:
        raise PartnerError("NOTE_REQUIRED", "Say why it is declined; the company reads it.")
    return clean


def check_attestations(given: dict[str, Any]) -> dict[str, bool]:
    """Every confirmation must be present and true. A missing one is a refusal, not a default."""
    missing = [k for k in ATTESTATIONS if given.get(k) is not True]
    if missing:
        raise PartnerError(
            "ATTESTATION_MISSING",
            "Confirm every item before approving: " + ", ".join(missing).replace("_", " ") + ".",
        )
    return {k: True for k in ATTESTATIONS}


def manifest(
    *,
    tenant_id: str,
    request_id: str,
    markets: list[str],
    methods: list[str],
    purpose_code: str,
    terms_version: str,
    dpa_version: str,
    certification_checks: list[dict[str, Any]],
    attestations: dict[str, bool],
    reviewer_id: str,
    approver_id: str,
    approved_at: datetime,
    company_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exactly what was approved, in one record. The environment is production; the engine that serves it is a separate, later gate. `company_check` is the company claim the platform itself read at approval (not a person's say-so)."""
    out = {
        "kind": "verify_api.production_grant",
        "version": 1,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "environment": "PRODUCTION",
        "markets": sorted(markets),
        "methods": sorted(methods),
        "purpose_code": purpose_code,
        "terms_version": terms_version,
        "dpa_version": dpa_version,
        "sandbox_certification": {c["id"]: bool(c["met"]) for c in certification_checks},
        "confirmed": dict(sorted(attestations.items())),
        "reviewed_by": reviewer_id,
        "approved_by": approver_id,
        "approved_at": approved_at.isoformat(),
    }
    if (
        company_check is not None
    ):  # absent for a record made before the automatic check existed, so its digest is unchanged
        out["company_check"] = dict(sorted(company_check.items()))
    return out


def company_fact(claim: tuple[str, datetime] | None) -> dict[str, Any]:
    """What the company's own verification shows, from the claim the organization module holds: a registry match, or nothing. Documents alone are not a match."""
    if claim is None:
        return {"verified": False, "claim": None, "assessed_at": None}
    return {"verified": True, "claim": claim[0], "assessed_at": claim[1].isoformat()}


def digest(record: dict[str, Any]) -> bytes:
    """SHA-256 of the record in one fixed spelling, so the same record always has the same digest."""
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).digest()
