"""Verification & assurance API (ZID-VERIFY-001 §21-22; FR-VER-001..008).

POST /verifications                                   start           (verification:execute)
GET  /verifications/{id}                              read            (verification:read)
POST /verifications/{id}:cancel | :reverify           (verification:execute)
GET  /principals/{id}/verification-assertions         (verification:read, or self)
GET  /principals/{id}/assurance                       facts only      (verification:read, or self)
POST /verified-claims                                  OpenID IDA verified_claims projection (VERIFY-25)
POST /verification-reliance-decisions                 explicit reuse  (verification:execute)
POST /verification-review-cases/{id}/decisions        protected reviewer (verification.review.decide)
POST /verification-provider-callbacks/{tenant}.{adapter}   signed by the provider
GET  /verification-profiles                           (verification:read)
POST /verification-profiles | /{id}:activate          protected admin (verification.profile.manage)
POST /verification-adapters | /{id}:quarantine        protected admin (verification.adapter.manage)
POST /verification-assertions/{id}:revoke             protected (verification.assertion.revoke)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError
from zid_outbox import emit

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce, enforce_packs
from zid_platform.modules.commercial.ports import market_gate, require_feature, tenant_vendor_cost
from zid_platform.modules.verification.application import privacy_gate, service
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain import proofing
from zid_platform.modules.verification.domain.budget import (
    Budget,
    month_start,
    other_currencies,
    parse_budget,
    spent_in,
)
from zid_platform.modules.verification.domain.countries import (
    Availability,
    Capability,
    availability,
)
from zid_platform.modules.verification.domain.providers import (
    ProviderKind,
    validate_provider_config,
)
from zid_platform.modules.verification.domain.verification import (
    SessionStatus,
    VerificationError,
    transition,
    validate_requirements,
)
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays
from zid_platform.protected import protected_action

_LOG = logging.getLogger("zid.regulatory")

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {
    "VERIFICATION_NOT_FOUND": 404,
    "PROFILE_NOT_FOUND": 404,
    "REVIEW_CASE_NOT_FOUND": 404,
    "PROFILE_NOT_AVAILABLE": 422,
    "ASSURANCE_NOT_MET": 422,
    "PROFILE_INVALID": 422,
    "REVIEWER_IS_SUBJECT": 403,
    "VERIFICATION_BUDGET_EXCEEDED": 429,  # WF01-014: the company's monthly supplier spend budget; new verifications start next month or after a person raises it
    "BUDGET_INVALID": 422,
    "VERIFICATION_ATTEMPTS_EXCEEDED": 429,  # WF01-014: the profile's daily cap for one person; not a verdict, try again later
    "PROVIDER_UNTRUSTED": 401,
    "CALLBACK_SIGNATURE_INVALID": 401,
    "CALLBACK_SCHEMA_INVALID": 400,
    "CALLBACK_STALE": 400,
    "APPLICANT_DATA_UNAVAILABLE": 503,  # nothing was recorded: the provider retries
    "COUNTRY_CAPABILITY_UNAVAILABLE": 503,  # the provider retries once the country is enabled again
    "REGULATORY_POLICY_UNAVAILABLE": 503,  # the same for a market whose policy pack is gone or not yet approved
    "REGULATORY_REVIEW_REQUIRED": 503,
}


def _err(e: VerificationError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 409)
    )


def _country_gate(request: Request, jurisdiction: str) -> None:
    """EDR-001: no identity verification in a country that has not been enabled for it (fail closed)."""
    enforce(request, jurisdiction, Capability.INDIVIDUAL_VERIFICATION)


async def _pack_gate(request: Request, caller: Caller, jurisdiction: str) -> dict[str, Any] | None:
    """ZID-RCG-003: an enabled country also needs an approved policy pack for the market, checked before any provider is asked (T01, T02).
    Returns the regulatory basis the start goes on (09.1), or None when no regulatory check applies."""
    return await enforce_packs(
        request, caller.ctx, jurisdiction, Capability.INDIVIDUAL_VERIFICATION
    )


async def _privacy_gate(
    request: Request,
    caller: Caller,
    jurisdiction: str,
    profile_id: str,
    subject: str,
    *,
    purpose: str = "IDENTITY_VERIFICATION",
    self_run: bool = False,
) -> dict[str, Any] | None:
    """ZID-RCG-003 RCG-G09 (CR-079): the country's privacy overlay, before any provider is asked. The provider's processing region must be a home
    region or a lawful transfer (T32); a biometric route needs the country's permission and, where the country requires it, the person's consent;
    a refused biometric route names the tenant's compliant alternate profiles (T31). Applies to an ENABLED country only, like the other checks.
    `ZID_PRIVACY_OVERLAY`: shadow (default) reports, enforce refuses, off skips (refused outside local, test and dev)."""
    mode = str(getattr(request.app.state, "privacy_overlay", "shadow"))
    if mode == "off":
        return None
    reg = registry()
    now = datetime.now(UTC)
    if (
        availability(
            reg,
            jurisdiction,
            Capability.INDIVIDUAL_VERIFICATION,
            now=now,
            environment=str(getattr(request.app.state, "environment", "production")),
        )
        is not Availability.ENABLED
    ):
        return None
    c = reg.countries.get(jurisdiction.upper())
    entry = c.capabilities.get(Capability.INDIVIDUAL_VERIFICATION) if c else None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        decision, alternates = await privacy_gate.check_start(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            profile_id=profile_id,
            subject_principal_id=subject,
            country=jurisdiction,
            provider=entry.provider if entry else None,
            overlays=privacy_overlays(),
            purpose=purpose,
            self_run=self_run,
        )
    seen = {
        "mode": mode,
        "allowed": decision.allowed,
        "reasons": list(decision.reasons),
        "region": decision.region,
        "transfer_mechanism": decision.mechanism,
    }
    if decision.allowed:
        return seen
    reasons = set(decision.reasons)
    code = (
        "CROSS_BORDER_ROUTE_DENIED"
        if "CROSS_BORDER_ROUTE_DENIED" in reasons
        else "BIOMETRIC_ROUTE_NOT_PERMITTED"
        if reasons <= po.BIOMETRIC_REASONS
        else "PRIVACY_OVERLAY_UNAVAILABLE"
    )
    _LOG.warning(
        "privacy overlay check %s: %s in %s (%s)",
        "refused" if mode == "enforce" else "would refuse (shadow)",
        code,
        jurisdiction,
        ",".join(decision.reasons),
    )
    if mode == "enforce":
        raise ZidError(
            code,
            {
                "CROSS_BORDER_ROUTE_DENIED": f"Identity data for {jurisdiction} may not be sent to where this provider processes it.",
                "BIOMETRIC_ROUTE_NOT_PERMITTED": f"A biometric check is not permitted or not approved for this person in {jurisdiction}; use an alternate route.",
                "PRIVACY_OVERLAY_UNAVAILABLE": f"No approved privacy overlay covers identity verification in {jurisdiction}.",
            }[code],
            http_status=422,
            details=[
                ErrorDetail(field="alternate_profile_id", issue=str(p["profile_id"]))
                for p in alternates
            ],
        )
    return seen  # shadow: reported, and recorded with the session


async def _record_basis(
    request: Request,
    caller: Caller,
    session_id: str,
    basis: dict[str, Any] | None,
    privacy: dict[str, Any] | None,
) -> None:
    """Write the regulatory basis on the session, once (ZID-RCG-003 09.1). Nothing is written when no regulatory check applied."""
    if basis is None and privacy is None:
        return
    record = dict(basis or {})
    if privacy is not None:
        record["privacy"] = privacy
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await store.set_regulatory_basis(conn, session_id, record)


def _callback_country_check(request: Request, country: str) -> None:
    """A provider result for a country switched off since the session began is held, never applied."""
    try:
        _country_gate(request, country)
    except ZidError as e:
        raise VerificationError("COUNTRY_CAPABILITY_UNAVAILABLE", country) from e


async def _callback_check(request: Request, tenant: str, country: str) -> None:
    """A provider result is held, never applied, when the country was switched off since the session began (EDR-001) or its policy pack was
    retired or never approved (ZID-RCG-003). Nothing is recorded and the provider retries (503), as for a closed country. In shadow mode the pack
    check only logs."""
    _callback_country_check(request, country)
    try:
        await enforce_packs(
            request, _ctx().with_identity(tenant, None), country, Capability.INDIVIDUAL_VERIFICATION
        )
    except ZidError as e:
        raise VerificationError(e.code, country) from e


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    return getattr(request.app.state, "verification_transport", None)


def _secrets(request: Request) -> Callable[[str], str | None]:
    """Provider secrets by reference, read from the secrets broker at the moment of use and never kept."""
    broker = getattr(request.app.state, "secret_broker", None)

    def get(name: str) -> str | None:
        if broker is None:
            return None
        try:
            return str(broker.get(name))
        except Exception:  # noqa: BLE001 - a missing secret is "not available", never a crash or a pass
            return None

    return get


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] | None = None


def _session_view(s: store.VSession) -> dict[str, Any]:
    return {
        "verification_id": s.session_id,
        "status": s.status.value,
        "outcome": s.outcome_code,
        "profile_id": s.profile_id,
        "subject_principal_id": s.subject_principal_id,
        "purpose": s.purpose,
        "jurisdiction": s.jurisdiction,
        "adapter_id": s.adapter_id,
        "expires_at": s.expires_at.isoformat(),
    }


@router.post("/verifications", status_code=201)
async def start(body: StartIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    # CR-068: count this verification attempt (best effort, never blocks it; a refused attempt still counts, so probing is seen). The contact key is the person's stable id here.
    from zid_platform.attempt_context import attempt_inputs
    from zid_platform.modules.screening.ports import AttemptAction, note_attempt

    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await note_attempt(
            conn,
            caller.ctx.tenant_id or "",
            subject_ref=f"verification:{body.subject_principal_id}",
            action=AttemptAction.VERIFICATION,
            now=datetime.now(UTC),
            contact=str(body.subject_principal_id),
            **attempt_inputs(request),
        )
    from zid_platform.modules.verification.application.identity_signal_feed import observe_request

    await observe_request(
        request_engine(request),
        caller.ctx,
        str(body.subject_principal_id),
        request,
        datetime.now(UTC),
    )
    _country_gate(request, body.jurisdiction)
    basis = await _pack_gate(request, caller, body.jurisdiction)
    privacy = await _privacy_gate(
        request,
        caller,
        body.jurisdiction,
        str(body.profile_id),
        str(body.subject_principal_id),
        purpose=body.purpose,
    )
    # Section 10.1, observe only: record whether the market matrix would have released identity verification for this document country and payer. It never blocks.
    async with tenant_transaction(request_engine(request), caller.ctx) as gate_conn:
        await market_gate.observe(
            gate_conn,
            tenant_id=caller.ctx.tenant_id or "",
            family="IDV",
            subject_country=body.jurisdiction,
            operating_region=getattr(request.app.state, "commercial_region", None),
        )
    try:
        out = await service.start(
            request_engine(request),
            ctx=caller.ctx,
            profile_id=str(body.profile_id),
            subject_principal_id=str(body.subject_principal_id),
            purpose=body.purpose,
            jurisdiction=body.jurisdiction,
            floor=body.required_level,
            transport=_transport(request),
            now=datetime.now(UTC),
            country_registry_version=registry().version,
            secrets=_secrets(request),
        )
    except VerificationError as e:
        raise _err(e) from e
    await _record_basis(request, caller, out.session_id, basis, privacy)
    started: dict[str, Any] = {
        "verification_id": out.session_id,
        "status": out.status,
        "adapter_id": out.adapter_id,
        "outcome": out.outcome,
    }
    if out.handoff:
        started["handoff"] = (
            out.handoff
        )  # the applicant's short-lived link or token: shown once, never stored
    return started


class BulkStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    subject_principal_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    purpose: str = Field(min_length=1, max_length=128)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] | None = None


@router.post("/verifications:bulk", status_code=207)
async def start_bulk(body: BulkStartIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Start the same verification for up to 50 people in one request (plan feature advanced.bulk-identity-company-verification).
    Each person is started on its own, exactly as POST /verifications would: one person's failure never stops or hides another's, and each
    result says started or why not. A person's short-lived handoff link is returned once, here, and never stored."""
    _scope(caller, "verification:execute")
    _country_gate(request, body.jurisdiction)
    basis = await _pack_gate(request, caller, body.jurisdiction)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(
            conn,
            "advanced.bulk-identity-company-verification",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
    results: list[dict[str, Any]] = []
    for subject in dict.fromkeys(body.subject_principal_ids):
        try:  # per person: one person's missing biometric consent never stops another's start
            privacy = await _privacy_gate(
                request,
                caller,
                body.jurisdiction,
                str(body.profile_id),
                str(subject),
                purpose=body.purpose,
            )
        except ZidError as e:
            results.append({"subject_principal_id": str(subject), "started": False, "code": e.code})
            continue
        try:
            out = await service.start(
                request_engine(request),
                ctx=caller.ctx,
                profile_id=str(body.profile_id),
                subject_principal_id=str(subject),
                purpose=body.purpose,
                jurisdiction=body.jurisdiction,
                floor=body.required_level,
                transport=_transport(request),
                now=datetime.now(UTC),
                country_registry_version=registry().version,
                secrets=_secrets(request),
            )
        except VerificationError as e:
            results.append({"subject_principal_id": str(subject), "started": False, "code": e.code})
            continue
        await _record_basis(request, caller, out.session_id, basis, privacy)
        item: dict[str, Any] = {
            "subject_principal_id": str(subject),
            "started": True,
            "verification_id": out.session_id,
            "status": out.status,
        }
        if out.handoff:
            item["handoff"] = out.handoff
        results.append(item)
    started = sum(1 for r in results if r["started"])
    return {
        "requested": len(results),
        "started": started,
        "failed": len(results) - started,
        "items": results,
    }


@router.get("/verifications/{verification_id}")
async def read(verification_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return _session_view(await store.load_session(conn, str(verification_id)))
        except VerificationError as e:
            raise _err(e) from e


MAX_USAGE_DAYS = 366


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_limit: (
        StrictStr | StrictInt
    )  # a decimal string or a whole number: money is never a float
    currency: str


def _money(value: Decimal) -> str:
    """A decimal as a plain string without trailing zeros (100.500000 is 100.5, 0 is 0). Money is never a float."""
    return format(value.normalize(), "f")


def _budget_view(
    row: dict[str, Any] | None, spent: dict[str, Decimal], now: datetime
) -> dict[str, Any]:
    if row is None:
        return {"budget": None, "month_start": month_start(now).isoformat()}
    budget = Budget(Decimal(row["monthly_limit"]), str(row["currency"]))
    used = spent_in(budget, spent)
    return {
        "budget": {
            "monthly_limit": _money(budget.monthly_limit),
            "currency": budget.currency,
            "version": row["version"],
        },
        "month_start": month_start(now).isoformat(),
        "spent": _money(used),
        "remaining": _money(max(budget.monthly_limit - used, Decimal(0))),
        "exceeded": used >= budget.monthly_limit,
        "other_currencies": other_currencies(
            budget, spent
        ),  # costs in another currency are not converted and not counted
    }


@router.get("/verification-budget")
async def read_budget(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The company's monthly supplier spend budget and what has been spent this month (WF01-014). Totals only."""
    _scope(caller, "verification:read")
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        row = await store.get_budget(conn)
        spent = await tenant_vendor_cost(conn, caller.ctx.tenant_id or "", month_start(now))
    return _budget_view(row, spent, now)


@router.put("/verification-budget")
async def set_budget(body: BudgetIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        parsed = parse_budget(body.monthly_limit, body.currency)
    except VerificationError as e:
        raise _err(e) from e
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="verification.budget.manage", resource="verification_budget:tenant"
        )
        await store.set_budget(
            conn, tenant_id=caller.ctx.tenant_id or "", limit=parsed.monthly_limit, currency=parsed.currency,
            actor=str(caller.ctx.principal_id), now=now,
        )  # fmt: skip
        row = await store.get_budget(conn)
        spent = await tenant_vendor_cost(conn, caller.ctx.tenant_id or "", month_start(now))
    return _budget_view(row, spent, now)


@router.delete("/verification-budget")
async def clear_budget(request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="verification.budget.manage", resource="verification_budget:tenant"
        )
        await store.clear_budget(conn, actor=str(caller.ctx.principal_id), now=now)
    return _budget_view(None, {}, now)


@router.get("/verification-engine-readiness")
async def engine_readiness(caller: CallerDep) -> dict[str, Any]:
    """Whether a face engine could be approved today, and what is missing (WF01-011/012): the engine record, each component's licence reading and the evaluation
    references, as the versioned configuration states them. Read only: approving an engine is a change to that configuration by the people who own it."""
    _scope(caller, "verification:read")
    from zid_platform.modules.verification.domain.biometric_engine import readiness
    from zid_platform.modules.verification.infrastructure import engine_register, self_run_config

    return readiness(
        self_run_config.raw(), engine_register.register(), today=datetime.now(UTC).date()
    )


@router.get("/verification-method-certification")
async def method_certification_board(request: Request, caller: CallerDep) -> dict[str, Any]:
    """How far each identity-proofing method is from certified (WF03 P10; CR-119): not built, built but never run on a real document or lab, awaiting records, or approved. Read only: it states
    what the versioned configuration and the trust store hold and what is still missing, and approves nothing."""
    _scope(caller, "verification:read")
    from zid_platform.modules.evidence.ports import effective_csca, trust_config
    from zid_platform.modules.verification.domain import method_certification as mc
    from zid_platform.modules.verification.domain.biometric_engine import readiness
    from zid_platform.modules.verification.domain.idv_image import PRODUCED
    from zid_platform.modules.verification.infrastructure import (
        device_attestation,
        engine_register,
        image_inspect,
        self_run_config,
    )

    config = getattr(request.app.state, "trust_config", None) or trust_config()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        effective = await effective_csca(conn, config.csca)
    rows = mc.board(
        trust_list_active=bool(effective.csca),
        engine=readiness(
            self_run_config.raw(), engine_register.register(), today=datetime.now(UTC).date()
        ),
        optical_checks_produced=PRODUCED,
        apple_attestation_configured=device_attestation.configured() is not None,
        android_attestation_configured=device_attestation.configured_play() is not None,
        imaging_configured=image_inspect.configured_url() is not None,
    )
    return {
        "methods": [
            {
                "method": r.method,
                "level": r.level.value,
                "summary": r.summary,
                "needs": list(r.needs),
            }
            for r in rows
        ],
        "counts": mc.summary(rows),
        "note": "Approved means every record the approval needs is present. Built but unproven means it has never met a real document, a real device or a lab.",
    }


@router.get("/verification-usage")
async def usage(
    request: Request,
    caller: CallerDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Usage counts for Finance and operations (WF01-014): sessions by provider, status and outcome, review cases, repeat attempts. Aggregates only:
    no person, no claim, no evidence. What each provider attempt cost is the vendor cost report's job (commercial); this says how many there were."""
    _scope(caller, "verification:read")
    end = until or datetime.now(UTC)
    start = since or end - timedelta(days=30)
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or not start < end
        or end - start > timedelta(days=MAX_USAGE_DAYS)
    ):
        raise ZidError(
            "VALIDATION_FAILED",
            f"since and until must be timezone-aware, in order, and at most {MAX_USAGE_DAYS} days apart.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        counts = await store.usage_between(conn, start, end)
    return {"since": start.isoformat(), "until": end.isoformat(), **counts}


@router.get("/verifications/{verification_id}/proofing")
async def proofing_facts(
    verification_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """WF01's result facts for one session (CR-107): the state in WF01's words, the distinct assertions that hold, each check as pass, fail,
    indeterminate or not requested, and the pinned policy. A view over the recorded decision: no evidence, no value of any claim, no score."""
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            s = await store.load_session(conn, str(verification_id))
            profile = await store.load_profile(conn, s.profile_id)
            basis = await store.decision_basis(conn, s.session_id)
            reviewed = await store.review_approved(conn, s.session_id)
        except VerificationError as e:
            raise _err(e) from e
    facts = proofing.derive(
        profile,
        basis,
        status=s.status,
        outcome=s.outcome_code,
        reviewed=reviewed and s.status is SessionStatus.VERIFIED,
    )
    return {
        "verification_id": s.session_id,
        "scope": s.purpose,
        "region": s.jurisdiction,
        "policy": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "level": profile.level,
        },
        "expires_at": s.expires_at.isoformat(),
        **facts.public(),
    }


@router.post("/verifications/{verification_id}:cancel")
async def cancel(verification_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            s = await store.load_session(conn, str(verification_id), lock=True)
            s = await store.update_session(
                conn,
                s,
                status=transition(s.status, SessionStatus.CANCELED),
                now=now,
                outcome="CANCELED",
            )
        except VerificationError as e:
            raise _err(e) from e
    return _session_view(s)


@router.post("/verifications/{verification_id}:reverify", status_code=201)
async def reverify(
    verification_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A new session (and new assertions); prior history stays immutable (§18)."""
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            old = await store.load_session(conn, str(verification_id))
        except VerificationError as e:
            raise _err(e) from e
    _country_gate(request, old.jurisdiction)
    basis = await _pack_gate(request, caller, old.jurisdiction)
    privacy = await _privacy_gate(
        request,
        caller,
        old.jurisdiction,
        old.profile_id,
        old.subject_principal_id,
        purpose=old.purpose,
    )
    try:
        out = await service.start(
            request_engine(request),
            ctx=caller.ctx,
            profile_id=old.profile_id,
            subject_principal_id=old.subject_principal_id,
            purpose=old.purpose,
            jurisdiction=old.jurisdiction,
            floor=None,
            transport=_transport(request),
            now=datetime.now(UTC),
            supersedes=old.session_id,
            country_registry_version=registry().version,
            secrets=_secrets(request),
        )
    except VerificationError as e:
        raise _err(e) from e
    await _record_basis(request, caller, out.session_id, basis, privacy)
    again: dict[str, Any] = {
        "verification_id": out.session_id,
        "status": out.status,
        "supersedes": old.session_id,
    }
    if out.handoff:
        again["handoff"] = out.handoff
    return again


def _self_or(caller: Caller, principal_id: uuid.UUID, scope: str) -> None:
    if str(principal_id) != caller.ctx.principal_id:
        _scope(caller, scope)


@router.get("/principals/{principal_id}/verification-assertions")
async def assertions(
    principal_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _self_or(caller, principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = await store.assertions_for(conn, str(principal_id))
    return {
        "items": [
            {
                "assertion_id": str(r["assertion_id"]),
                "claim_type": r["claim_type"],
                "verification_method": r["verification_method"],
                "evidence_types": list(r["evidence_types"]),
                "assurance_level": r["assurance_level"],
                "status": r["status"],
                "verified_at": r["performed_at"].isoformat(),
                "fresh_until": r["valid_until"].isoformat() if r["valid_until"] else None,
                "reason_codes": list(r["reason_codes"]),
            }
            for r in rows
        ]
    }


@router.get("/principals/{principal_id}/assurance")
async def assurance(principal_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _self_or(caller, principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.assurance(conn, str(principal_id), datetime.now(UTC))


class VerifiedClaimsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    claims: list[str] = Field(min_length=1, max_length=20)
    minimum_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] = "IAL-Z1"


@router.post("/verified-claims")
async def verified_claims(
    body: VerifiedClaimsIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """OpenID IDA `verified_claims` projection (VERIFY-25): attested claims only, for the stated purpose."""
    _self_or(caller, body.subject_principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.verified_claims(
            conn,
            str(body.subject_principal_id),
            purpose=body.purpose,
            requested=body.claims,
            now=datetime.now(UTC),
            min_level=body.minimum_level,
        )


class RelianceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"]
    claims: list[str] = Field(min_length=1, max_length=20)
    jurisdiction: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


@router.post("/verification-reliance-decisions", status_code=201)
async def rely(body: RelianceIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.rely(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            principal_id=str(body.subject_principal_id),
            client_id=caller.client_id,
            purpose=body.purpose,
            required_level=body.required_level,
            claims=body.claims,
            jurisdiction=body.jurisdiction,
            now=datetime.now(UTC),
        )


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "ESCALATE"]
    reason_codes: list[str] = Field(min_length=1, max_length=10)


@router.post("/verification-review-cases/{case_id}/decisions")
async def review(
    case_id: uuid.UUID, body: ReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.review.decide",
            resource=f"verification_review_case:{case_id}",
        )
        try:
            outcome = await service.review(
                conn,
                case_id=str(case_id),
                reviewer=caller.ctx.principal_id or "",
                decision=body.decision,
                reasons=body.reason_codes,
                now=datetime.now(UTC),
            )
        except VerificationError as e:
            raise _err(e) from e
    return {"case_id": str(case_id), "outcome": outcome}


async def _callback_ok_status(request: Request, tenant: str, adapter_id: str) -> int:
    async with tenant_transaction(
        request_engine(request), _ctx().with_identity(tenant, None)
    ) as conn:
        adapter = await store.load_adapter(conn, adapter_id)
    return 202 if adapter is None or adapter.get("provider_kind", "GENERIC") == "GENERIC" else 200


@router.post("/verification-provider-callbacks/{adapter_ref}", status_code=202)
async def callback(adapter_ref: str, request: Request) -> Response:
    tenant, _, adapter_id = adapter_ref.partition(".")
    try:
        uuid.UUID(tenant)
        body = await request.body()
        if len(body) > 64 * 1024:
            raise VerificationError("CALLBACK_SCHEMA_INVALID", "too large")
        outcome = await service.provider_callback(
            request_engine(request),
            tenant_id=tenant,
            adapter_id=adapter_id,
            body=body,
            base_ctx=_ctx(),
            now=datetime.now(UTC),
            country_check=lambda country: _callback_check(request, tenant, country),
            headers=dict(request.headers),
            secrets=_secrets(request),
            transport=_transport(request),
        )
    except ValueError as e:
        code = e.code if isinstance(e, VerificationError) else "PROVIDER_UNTRUSTED"
        if code in service.REJECTED_CALLBACKS:  # T35: refused, recorded and alerted
            await service.record_rejected_callback(
                request_engine(request),
                tenant_id=tenant,
                adapter_id=adapter_id,
                code=code,
                base_ctx=_ctx(),
                now=datetime.now(UTC),
            )
        return JSONResponse({"error": code}, status_code=_STATUS.get(code, 409))
    # Veriff and Sumsub retry anything that is not a plain 200, so their callbacks are answered 200; Zoiko's own protocol keeps 202
    return JSONResponse(
        {"outcome": outcome}, status_code=await _callback_ok_status(request, tenant, adapter_id)
    )


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"]
    requirements: dict[str, Any]


@router.get("/verification-profiles")
async def profiles(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await store.list_profiles(conn)}


@router.post("/verification-profiles", status_code=201)
async def create_profile(body: ProfileIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        validate_requirements(body.level, body.requirements)
    except VerificationError as e:
        raise _err(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.profile.manage",
            resource=f"verification_profile:{body.name}",
        )
        pid, version = await store.create_profile(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            name=body.name,
            level=body.level,
            requirements=body.requirements,
        )
    return {"profile_id": pid, "name": body.name, "version": version, "status": "DRAFT"}


@router.post("/verification-profiles/{profile_id}:activate")
async def activate_profile(
    profile_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            p = await store.load_profile(conn, str(profile_id))
            await protected_action(
                conn,
                caller,
                action="verification.profile.manage",
                resource=f"verification_profile:{p.name}",
                change=f"activate:{p.version}",
            )
            await store.activate_profile(conn, p, now)
        except VerificationError as e:
            raise _err(e) from e
        await emit(
            conn,
            name="verification.profile.activated",
            tenant_id=p.tenant_id,
            aggregate_type="verification_profile",
            aggregate_id=p.profile_id,
            aggregate_version=1,
            data={"profile_id": p.profile_id, "name": p.name, "version": p.version},
        )
    return {"profile_id": str(profile_id), "status": "ACTIVE"}


def _default_algorithms() -> list[Literal["ES256", "Ed25519", "RS256", "PS256"]]:
    return ["ES256"]


class AdapterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    provider: str = Field(min_length=1, max_length=64)
    mapping_version: str = Field(min_length=1, max_length=32)
    submit_endpoint: str = Field(
        pattern=r"^https://"
    )  # for Veriff and Sumsub, the provider's base address
    callback_jwks: dict[str, Any] = Field(default_factory=lambda: {"keys": []})  # GENERIC only
    callback_algorithms: list[Literal["ES256", "Ed25519", "RS256", "PS256"]] = Field(
        default_factory=_default_algorithms, min_length=1
    )
    provider_kind: Literal["GENERIC", "VERIFF", "SUMSUB"] = "GENERIC"
    provider_config: dict[str, Any] = Field(
        default_factory=dict
    )  # secret names and settings, never the secrets
    equivalence_group: str = Field(min_length=1, max_length=64)
    supported_evidence: list[str] = Field(min_length=1)
    jurisdictions: list[str] = Field(min_length=1)


@router.get("/verification-adapters")
async def adapters(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await store.list_adapters(conn)
    return {"items": [store.adapter_view(a) for a in sorted(items, key=lambda a: a["adapter_id"])]}


@router.post("/verification-adapters", status_code=201)
async def create_adapter(body: AdapterIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    kind = ProviderKind(body.provider_kind)
    try:
        config = validate_provider_config(kind, body.provider_config, body.jurisdictions)
    except VerificationError as e:
        raise _err(e) from e
    if kind is ProviderKind.GENERIC and not body.callback_jwks.get("keys"):
        raise ZidError(
            "VALIDATION_FAILED",
            "A generic adapter needs its pinned callback keys.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.adapter.manage",
            resource=f"verification_adapter:{body.adapter_id}",
        )
        await store.create_adapter(
            conn,
            {
                **body.model_dump(),
                "provider_config": config,
                "tenant_id": caller.ctx.tenant_id or "",
            },
        )
    return {
        "adapter_id": body.adapter_id,
        "status": "ACTIVE",
        "callback_path": f"/verification-provider-callbacks/{caller.ctx.tenant_id}.{body.adapter_id}",
    }


@router.post("/verification-adapters/{adapter_id}:quarantine")
async def quarantine(adapter_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.adapter.manage",
            resource=f"verification_adapter:{adapter_id}",
            change="quarantine",
        )
        if not await store.set_adapter_status(conn, adapter_id, "QUARANTINED"):
            raise ZidError("ADAPTER_NOT_FOUND", "Not found.", http_status=404)
        await emit(
            conn,
            name="verification.provider.quarantined",
            tenant_id=caller.ctx.tenant_id or "",
            aggregate_type="verification_adapter",
            aggregate_id=adapter_id,
            aggregate_version=1,
            data={"adapter_id": adapter_id},
        )
    return {"adapter_id": adapter_id, "status": "QUARANTINED"}


@router.post("/verification-assertions/{assertion_id}:revoke")
async def revoke_assertion(
    assertion_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.assertion.revoke",
            resource=f"verification_assertion:{assertion_id}",
        )
        ids = await service.revoke(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            principal_id=None,
            assertion_id=str(assertion_id),
            now=datetime.now(UTC),
        )
    if not ids:
        raise ZidError("ASSERTION_NOT_FOUND", "Not found.", http_status=404)
    return {"assertion_id": str(assertion_id), "status": "REVOKED"}
