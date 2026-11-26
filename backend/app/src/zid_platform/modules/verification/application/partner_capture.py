"""A partner's customer through the identity engine's automatic face route (CR-164; docs/specs/verify-api-live-check-product-definition.md).

The Verify API module never imports this module's internals: it uses these functions through `verification.ports`. Everything the engine decides stays the engine's: this layer opens a case in the PARTNER's tenant for a
pseudonymous applicant (one per session, derived from the session id, never a person's identity), records the notice and consent, issues the one-time capture value, and hands the two photographs to the same checks the
signed-in identity page uses. It never states a result.

The route is the automatic face route only (`WEB_FACE`). A partner's customer is never put in front of the two-reviewer route: that route keeps the customer's photographs for people to look at, and who may look at
another company's customers is not decided.

Everything is refused unless the owners have accepted it: a market (or the global policy, accepted in writing) must publish the route, and the face route's acknowledgement must be set. A refusal comes BEFORE the customer
is shown anything.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.application.capture_checks import (
    FACE_ROUTE_ACK,
    CaptureError,
    clean_and_read,
    face_against_document,
)
from zid_platform.modules.verification.domain.idv_decision import AlternateRoute
from zid_platform.modules.verification.domain.idv_evidence import Envelope
from zid_platform.modules.verification.domain.idv_policy import MarketRules, PolicyError
from zid_platform.modules.verification.infrastructure import idv_policy_file

FACE_ROUTE_ID = "WEB_FACE"
CASE_ERROR = svc.CaseError
Case = svc.Case

# the case-service codes that mean "no tries left", which the caller turns into a final, inconclusive result
EXHAUSTED_CODES = frozenset({"RETRY_CEILING_REACHED", "FACE_ATTEMPTS_USED_UP"})


@dataclass(frozen=True, slots=True)
class Services:
    """What the engine needs from the running cell: the malware scanner, the imaging sidecar, and the owners' acknowledgements and policy tables. Read from the application state by the caller."""

    scanner: Any = None
    imaging: Any = None
    face_ack: str | None = None
    policies: Callable[[], dict[str, MarketRules]] | None = None
    global_ack: str | None = None
    global_source: Callable[[], MarketRules | None] | None = None


def services_from_state(state: Any) -> Services:
    return Services(
        scanner=getattr(state, "evidence_scanner", None),
        imaging=getattr(state, "imaging_transport", None),
        face_ack=getattr(state, "idv_face_route_ack", None),
        policies=getattr(state, "idv_policies", None),
        global_ack=getattr(state, "idv_global_policy_ack", None),
        global_source=getattr(state, "idv_global_policy", None),
    )


def rules_for(sv: Services, country: str) -> MarketRules | None:
    """The rules a country is served under: its own entry, else the global policy when the owners have accepted it in writing, else none."""
    try:
        table = sv.policies() if callable(sv.policies) else idv_policy_file.load()
        rules = table.get(country.upper())
        if rules is None and sv.global_ack == idv_policy_file.GLOBAL_POLICY_ACK:
            rules = (
                sv.global_source() if callable(sv.global_source) else idv_policy_file.load_global()
            )
    except PolicyError as e:
        raise CaptureError("IDV_POLICY_FILE_INVALID", str(e), status=503, retryable=True) from e
    return rules


def face_route(rules: MarketRules) -> AlternateRoute | None:
    return next(
        (
            a
            for a in rules.policy.alternates
            if a.route_id == FACE_ROUTE_ID and a.automatic and a.face_match
        ),
        None,
    )


def enabled_rules(sv: Services, country: str) -> MarketRules | None:
    """The rules, only if this country can be served the live check right now: a policy publishes the automatic face route and the owners' acknowledgement is set."""
    if sv.face_ack != FACE_ROUTE_ACK:
        return None
    rules = rules_for(sv, country)
    return rules if rules is not None and face_route(rules) is not None else None


def notice_version(rules: MarketRules) -> str:
    """The version of the notice the customer must be shown. The market's own notice version when its policy names one; otherwise one tied to the policy version, so a changed policy needs a fresh agreement."""
    return rules.basis.notice_version or f"verify-api-live:{rules.policy.version}"


def max_attempts(rules: MarketRules) -> int:
    return int(rules.retry.max_capture_attempts)


def attempts_used(case: Case) -> int:
    return int(case.attempts.get("CAPTURE", 0))


def applicant_for(session_id: str) -> str:
    """A stable pseudonymous id for one session. It is not a person and not a Zoiko iD principal: it only lets the engine keep one case's attempts together."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"zoiko-verify-api-session:{session_id}"))


async def start(
    conn: AsyncConnection, *, tenant_id: str, session_id: str, rules: MarketRules, now: datetime
) -> Case:
    return await svc.open_case(
        conn,
        tenant_id=tenant_id,
        applicant=applicant_for(session_id),
        route=FACE_ROUTE_ID,
        rules=rules,
        now=now,
    )


async def get(conn: AsyncConnection, case_id: str) -> Case | None:
    return await svc.get(conn, case_id)


async def find(conn: AsyncConnection, session_id: str) -> Case | None:
    """The case a session started, if it has started one. Inside the company's scope."""
    found = (
        await conn.execute(
            text(
                "SELECT case_id FROM verify.idv_case WHERE applicant = :a ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": uuid.UUID(applicant_for(session_id))},
        )
    ).first()
    return await svc.get(conn, str(found.case_id)) if found else None


async def consent(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    *,
    notice_version: str,
    consented: bool,
    now: datetime,
) -> Case:
    return await svc.record_notice_and_consent(
        conn, case_id, rules, notice_version=notice_version, consented=consented, now=now
    )


async def issue_nonce(conn: AsyncConnection, case_id: str, now: datetime) -> str:
    nonce, _case = await svc.issue_nonce(conn, case_id, now)
    return nonce


async def abandon(conn: AsyncConnection, case_id: str, rules: MarketRules, now: datetime) -> Case:
    return await svc.abandon(conn, case_id, rules, now=now)


async def submit(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    sv: Services,
    *,
    nonce: str,
    captured_at: datetime,
    image: bytes,
    selfie: bytes,
    now: datetime,
) -> tuple[Case, str | None]:
    """The two photographs. Returns the case as the engine now sees it and, when no face could be compared, the sidecar's reason. Raises `CaptureError` for a file that is refused or a service that is down
    (nothing is spent), and the case service's `CaseError` for a closed case or no tries left."""
    route = face_route(rules)
    case = await svc.get(conn, case_id)
    if route is None or case is None or case.route != FACE_ROUTE_ID:
        raise CaptureError(
            "ROUTE_NOT_ALLOWED_BY_POLICY",
            "That route is not one this market's signed policy names.",
            status=409,
        )
    if case.consent_at is None or case.withdrawn_at is not None:
        raise CaptureError(
            "FACE_CONSENT_REQUIRED", "Agree to the comparison of your photographs first."
        )
    clean, reading = await clean_and_read(image, scanner=sv.scanner, transport=sv.imaging)
    face, _cosine, problem = await face_against_document(
        face_ack=sv.face_ack,
        consent=True,
        selfie=selfie,
        document=clean,
        threshold=route.face_threshold,
        scanner=sv.scanner,
        transport=sv.imaging,
    )
    checks = {
        k: v for k, v in reading.checks.items() if not k.startswith("MRZ_")
    }  # this route reads no machine-readable lines: their absence is not a doubt about the picture
    envelope = Envelope(nonce, hashlib.sha256(image).hexdigest(), captured_at, None, None)
    done = await svc.submit_document(
        conn,
        case_id,
        rules,
        envelope,
        checks=checks,
        printed=reading.printed,
        now=now,
        web=True,
        declared=None,
        face=face,
    )
    return done, problem


async def revoke_claim(conn: AsyncConnection, case_id: str, now: datetime) -> None:
    """An approved case records an identity claim about its applicant. A partner's customer is not the company's own person and the applicant here is only a pseudonym, so nothing may rely on that claim: it is ended at once."""
    from zid_platform.modules.verification.application import idv_claims

    await idv_claims.revoke_for_case(conn, case_id, "PARTNER_CUSTOMER_CASE", now)


async def clean_image(sv: Services, raw: bytes, what: str = "logo") -> bytes:
    """Scan an uploaded image for malware and re-encode it as a plain image with its metadata removed: the same step every photograph goes through. Raises `CaptureError`."""
    from zid_platform.modules.verification.application.capture_checks import scan_and_clean

    return await scan_and_clean(raw, scanner=sv.scanner, transport=sv.imaging, what=what)
