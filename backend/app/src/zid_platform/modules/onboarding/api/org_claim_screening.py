"""Screening a company claim over HTTP (ZID-ED-04; WF-13B steps 03 and 04).

    POST /v1/org-claims/{id}/screening            declare the representative, directors, owners and holdings; screened at once
    PUT  /v1/org-claims/{id}/screening            declare again while the case is waiting for details (the earlier one stays)
    POST /v1/org-claims/{id}/appeal               appeal a rejection or ask for a correction; goes back to a person
    GET  /v1/reviews/screening-cases/{id}/replay  a reviewer decides a past version again from what it used (read_restricted)

What the applicant declares is checked against what the official register lists (directors and persons with significant control)
where the country has a source; a person the register lists and the applicant left out is an explicit task, and everyone the
register lists is screened as well. Restricted person records are stored only in the organization's approved region: the residency
check runs before a single name is stored, and a refusal is audited (committed before the answer) and stores nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import environment_of  # noqa: F401  (kept for parity with the claim routes)
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.onboarding.api.org_claims import (
    _adapters,
    _gate,
    _kyb_error,
    _me,
    _require_admin,
    _screening_error,
    _sign,
    _status,
)
from zid_platform.modules.onboarding.application.monitoring import register_evidence
from zid_platform.modules.organization.ports import (
    KybError,
    kyb_get_case,
    usable_root_for,
)
from zid_platform.modules.organization.ports import kyb_register_people as read_register
from zid_platform.modules.screening.ports import (
    AuthorityEvidence,
    AuthorityKind,
    Control,
    Edge,
    Graph,
    Holder,
    HolderKind,
    Party,
    PartyKind,
    Role,
    ScreeningError,
    amend_case,
    open_case,
    replay,
    request_appeal,
    screening_config,
)
from zid_platform.modules.tenant.ports import (
    Mode,
    check_category_transfer,
    load_tenant,
    matrix_gate,
)
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1", tags=["org-claims"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

PURPOSE = "SCREENING_RECORDS"
REFUSED = "verification.screening.refused"  # an audit record, not an event on the bus


class PartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")  # the applicant's own id for this person
    kind: Literal["PERSON", "ENTITY"] = "PERSON"
    name: str = Field(min_length=1, max_length=300)
    birth: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}-\d{2})?$")
    countries: list[str] = Field(default_factory=list, max_length=10)
    identifiers: list[str] = Field(default_factory=list, max_length=10)


class HoldingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    owned: str = Field(default="company", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    shares_pct: str | None = None
    votes_pct: str | None = None
    controls: list[
        Literal[
            "APPOINT_BOARD_MAJORITY", "SIGNIFICANT_INFLUENCE", "OTHER_CONTROL", "NOMINEE", "TRUST"
        ]
    ] = Field(default_factory=list)


class ScreeningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    representative: PartyIn
    directors: list[PartyIn] = Field(default_factory=list, max_length=50)
    owners: list[PartyIn] = Field(default_factory=list, max_length=100)
    holdings: list[HoldingIn] = Field(default_factory=list, max_length=300)
    elevated_risk: bool = False


class AppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["APPEAL", "CORRECTION"]
    statement: str = Field(min_length=1, max_length=2000)


def _pct(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(value)
    except InvalidOperation as e:
        raise ZidError(
            "VALIDATION_FAILED", "A holding percentage is not a number.", http_status=422
        ) from e
    if not (Decimal(0) <= d <= Decimal(100)):
        raise ZidError(
            "VALIDATION_FAILED", "A holding percentage is between 0 and 100.", http_status=422
        )
    return d


def _party(p: PartyIn) -> Party:
    return Party(
        p.ref, PartyKind(p.kind), p.name.strip(), p.birth, tuple(p.countries), tuple(p.identifiers)
    )


def _storage_gate(request: Request, conn: Any, tenant: str) -> Any:
    """Restricted person records are held only where the organization's residency allows (ZID-ED-04 section 06)."""

    async def gate() -> None:
        decision = await check_category_transfer(
            conn,
            tenant,
            data_category="D4",
            destination_cell_id=str(request.app.state.home_cell),
            purpose_code=PURPOSE,
        )
        if not decision.allowed:
            raise ScreeningError(
                "SCREENING_RESIDENCY_DENIED",
                "This organization's data region does not allow these records to be held here. Nothing was stored.",
            )

    return gate


async def _audit_refusal(
    conn: Any, tenant: str, claim: str, actor: str, code: str, now: datetime
) -> None:
    await record_audit(
        conn,
        tenant,
        AuditFacts(
            event_type=REFUSED,
            actor_type="HUMAN",
            action="refused",
            target_type="kyb_case",
            target_ref=claim,
            outcome="DENIED",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=code,
            source_service="screening",
            source_event_id=str(uuid.uuid4()),
            details={"reason": code},  # a code only: never a name
        ),
    )


async def _declare(
    request: Request, caller: Caller, claim_id: uuid.UUID, body: ScreeningIn, *, amend: bool
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    refusal: ScreeningError | None = None
    result: dict[str, Any] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            kyb = await kyb_get_case(conn, tenant, str(claim_id), now=now)
        except KybError as e:
            raise _kyb_error(e) from e
        # Authority is what the server finds, not what the applicant says: an authority root this person holds, which exists only when
        # the person was matched to a current officer of the register. No root, no verified authority.
        t = await load_tenant(conn, tenant)
        root_id = (
            await usable_root_for(conn, tenant, t.organization_id or "", me, now)
            if t is not None and t.organization_id
            else None
        )
        authority = (
            AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, root_id)
            if root_id
            else AuthorityEvidence(AuthorityKind.NONE)
        )
        subjects = [
            (Role.ENTITY, Party("company", PartyKind.ENTITY, kyb.legal_name or "")),
            (Role.REPRESENTATIVE, _party(body.representative)),
            *[(Role.DIRECTOR, _party(p)) for p in body.directors],
            *[(Role.OWNER, _party(p)) for p in body.owners],
        ]
        holders = {"company": Holder("company", HolderKind.ENTITY)}
        for p in [body.representative, *body.directors, *body.owners]:
            holders[p.ref] = Holder(
                p.ref, HolderKind.PERSON if p.kind == "PERSON" else HolderKind.ENTITY
            )
        for h in body.holdings:
            holders.setdefault(
                h.owner, Holder(h.owner, HolderKind.UNKNOWN)
            )  # a holder nobody described is unknown
        graph = Graph(
            "company",
            holders,
            tuple(
                Edge(
                    h.owner,
                    "company" if h.owned == "company" else h.owned,
                    _pct(h.shares_pct),
                    _pct(h.votes_pct),
                    frozenset(Control(c) for c in h.controls),
                    False,  # declared by the applicant and not confirmed by a source: the rules treat it as unverified
                )
                for h in body.holdings
            ),
        )
        # what the official register lists, read before anything is stored (the read sends only the company number)
        register = register_evidence(
            await read_register(_adapters(request), kyb.registration_number, kyb.jurisdiction, now)
        )
        args: dict[str, Any] = {
            "country": kyb.jurisdiction, "subjects": subjects, "graph": graph, "authority": authority, "requested_by": me,
            "cfg": screening_config(), "sign": _sign(request), "now": now, "elevated_risk": body.elevated_risk,
            "country_gate": _gate(request), "register": register,
            "register_source": "companies-house" if register is not None else "",
            "storage_gate": _storage_gate(request, conn, tenant),
        }  # fmt: skip
        try:
            if amend:
                await amend_case(conn, tenant, str(claim_id), **args)
            else:
                await open_case(
                    conn,
                    tenant,
                    idempotency_key=f"claim-{claim_id}",
                    kyb_case_id=str(claim_id),
                    **args,
                )
        except ScreeningError as e:
            if e.code != "SCREENING_RESIDENCY_DENIED":
                raise _screening_error(e) from e
            await _audit_refusal(conn, tenant, str(claim_id), me, e.code, now)
            refusal = e  # leave the block normally so the audit record is committed
        if refusal is None:
            result = await _status(conn, tenant, str(claim_id), now)
    if refusal is not None:
        raise _screening_error(refusal)
    assert result is not None
    return result


@router.post(
    "/org-claims/{claim_id}/screening",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def start_screening(
    claim_id: uuid.UUID, body: ScreeningIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _declare(request, caller, claim_id, body, amend=False)


@router.put(
    "/org-claims/{claim_id}/screening",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def amend_screening(
    claim_id: uuid.UUID, body: ScreeningIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _declare(request, caller, claim_id, body, amend=True)


@router.post(
    "/org-claims/{claim_id}/appeal",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def appeal(
    claim_id: uuid.UUID, body: AppealIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    refusal: ScreeningError | None = None
    result: dict[str, Any] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            case = await _case_of(conn, tenant, str(claim_id), now)
            await request_appeal(
                conn, tenant, case, kind=body.kind, statement=body.statement, requested_by=me, cfg=screening_config(),
                sign=_sign(request), now=now, storage_gate=_storage_gate(request, conn, tenant),
            )  # fmt: skip
        except KybError as e:
            raise _kyb_error(e) from e
        except ScreeningError as e:
            if e.code != "SCREENING_RESIDENCY_DENIED":
                raise _screening_error(e) from e
            await _audit_refusal(conn, tenant, str(claim_id), me, e.code, now)
            refusal = e
        if refusal is None:
            result = await _status(conn, tenant, str(claim_id), now)
    if refusal is not None:
        raise _screening_error(refusal)
    assert result is not None
    return result


async def _case_of(conn: Any, tenant: str, claim_id: str, now: datetime) -> str:
    """The screening case of this claim (CASE_NOT_FOUND, the same 404 as any unknown claim, when there is none)."""
    from zid_platform.modules.screening.ports import case_for_kyb

    await kyb_get_case(conn, tenant, claim_id, now=now)
    found = await case_for_kyb(conn, tenant, claim_id, now=now)
    if found is None:
        raise ScreeningError("CASE_NOT_FOUND")
    return found[0].case_id


@router.get("/reviews/screening-cases/{case_id}/replay")
async def replay_case(
    case_id: uuid.UUID, version: int, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.read_restricted",
            resource=f"screening_case:{case_id}",
        )
        _me(caller)
        try:
            return await replay(
                conn, caller.ctx.tenant_id or "", str(case_id), version, cfg=screening_config()
            )
        except ScreeningError as e:
            raise _screening_error(e) from e
