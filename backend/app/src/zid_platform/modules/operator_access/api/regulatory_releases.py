"""Market releases and official-register attestations, operator plane (ZID-RCG-003 RCG-G10 WF-08, RCG-G02; CR-072, CR-073).

    GET  /v1/regulatory/market-releases                                           releases, newest first   (any release capability)
    GET  /v1/regulatory/market-releases/{release_id}/versions/{version}           one version, its gates and approvals
    GET  /v1/regulatory/market-status                                             is the market open now, and why not
    POST /v1/regulatory/market-releases                                           a new DRAFT                    (regulatory.release.author)
    POST /v1/regulatory/market-releases/{release_id}:revise                       a new DRAFT version            (regulatory.release.author)
    POST /v1/regulatory/market-releases/{release_id}/versions/{version}:attest    one evidence gate result       (regulatory.release.attest_<role>)
    POST /v1/regulatory/market-releases/{release_id}/versions/{version}:freeze    pin the packs and suppliers    (regulatory.release.author)
    POST /v1/regulatory/market-releases/{release_id}/versions/{version}:approve   sign the exact hash            (regulatory.release.approve_<role>)
    POST /v1/regulatory/market-releases/{release_id}/versions/{version}:launch    RELEASEABLE to LAUNCHED        (regulatory.release.launch)
    POST /v1/regulatory/market-releases/{release_id}/versions/{version}:withdraw  close the market at once       (regulatory.release.launch)
    GET  /v1/regulatory/attestations                                              a provider's scopes and today's decision for each
    POST /v1/regulatory/attestations                                              record what an official register said (regulatory.attestation.record)

Same gate and the same discipline as the policy pack API: the operator gate first, one capability per action, the change and its protected
audit record in one transaction, a refusal audited afterwards as regulatory.release.refused. The capabilities form a separation-of-duties
set, and the domain and the tables repeat the rules. Records carry ids, codes, roles and hashes, never evidence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.operator_actions import Actions


class _Verification:
    """The verification module's public interface, imported on first use (see regulatory.py: avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
# As in verification's domain; a unit test keeps the lists equal.
ATTESTOR_ROLES = ("legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt")
APPROVER_ROLES = ("legal", "security_grc", "qa", "product_owner")

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]

AUTHOR = "regulatory.release.author"
LAUNCH = "regulatory.release.launch"
RECORD_ATTESTATION = "regulatory.attestation.record"
RECORD_ENTITLEMENT = "regulatory.entitlement.record"
ATTEST = {r: f"regulatory.release.attest_{r}" for r in ATTESTOR_ROLES}
APPROVE = {r: f"regulatory.release.approve_{r}" for r in APPROVER_ROLES}
ALL_RELEASE_CAPABILITIES = (AUTHOR, LAUNCH, *ATTEST.values(), *APPROVE.values())

RELEASE_ID = r"^[a-z0-9][a-z0-9-]{0,127}$"
ReleaseIdPath = Annotated[str, Path(pattern=RELEASE_ID)]
VersionPath = Annotated[int, Path(ge=1, le=100_000)]
CODE = r"^[A-Z][A-Z0-9_]{0,63}$"

_STATUS = {
    "RELEASE_NOT_FOUND": 404,
    "RELEASE_EXISTS": 409,
    "RELEASE_NOT_DRAFT": 409,
    "RELEASE_NOT_FREEZABLE": 409,
    "RELEASE_NOT_FROZEN": 409,
    "RELEASE_NOT_RELEASEABLE": 409,
    "RELEASE_NOT_WITHDRAWABLE": 409,
    "RELEASE_DRAFT_OPEN": 409,
    "RELEASE_APPROVAL_HASH_MISMATCH": 409,
    "RELEASE_RECORDER_IS_AUTHOR": 403,
    "RELEASE_APPROVER_IS_AUTHOR": 403,
    "RELEASE_APPROVER_NOT_INDEPENDENT": 403,
    "RELEASE_APPROVER_NOT_DISTINCT": 403,
    "RELEASE_ROLE_MISMATCH": 403,
    "RELEASE_STORED_HASH_MISMATCH": 500,
    "ATTESTATION_OUT_OF_ORDER": 409,
    "ENTITLEMENT_OUT_OF_ORDER": 409,
}


def _errors() -> tuple[type[BaseException], ...]:
    return (
        v.release_model.ReleaseError,
        v.attestation_model.AttestationError,
        v.entitlement_model.EntitlementError,
        v.PolicyPackError,
    )


actions = Actions(
    namespace=uuid.UUID("7b2e4c18-5d90-4f3a-b1c6-0a8e9d27f451"),
    refused_event="regulatory.release.refused",
    status=_STATUS,
    errors=_errors,
    refused_code="RELEASE_REFUSED",
    capability_hint="a market release capability",
)
_fail = actions.fail
_need_any = actions.need_any
_act = actions.act


def _iso(d: datetime | date | None) -> str | None:
    return d.isoformat() if d else None


def _scope_view(s: Any) -> dict[str, str]:
    return {
        "entity": s.entity,
        "scheme": s.scheme,
        "country": s.country,
        "service_code": s.service_code,
    }


def _view(r: Any) -> dict[str, Any]:
    gates = v.release_model.current_results(r)
    return {
        "release_id": r.release_id,
        "version": r.version,
        "status": r.status.value,
        "market": {
            "country": r.market.country,
            "product": r.market.product,
            "purpose": r.market.purpose,
        },
        "author": r.author,
        "release_hash": r.release_hash,
        "pack_refs": [
            {"pack_id": p.pack_id, "version": p.version, "pack_hash": p.pack_hash}
            for p in r.pack_refs
        ],
        "supplier_scopes": [_scope_view(s) for s in r.supplier_scopes],
        "gates": [
            {
                "gate": g.gate_id,
                "title": g.title,
                "owner_role": g.owner_role,
                "status": gates[g.gate_id].status.value if g.gate_id in gates else None,
                "evidence_ref": gates[g.gate_id].evidence_ref if g.gate_id in gates else None,
                "recorded_by": gates[g.gate_id].recorded_by if g.gate_id in gates else None,
                "recorded_at": _iso(gates[g.gate_id].recorded_at) if g.gate_id in gates else None,
            }
            for g in v.release_model.GATES
        ],
        "blockers": v.release_model.gate_blockers(r),
        "approvals": {
            role: {"approver": a.approver, "release_hash": a.release_hash}
            for role, a in r.approvals.items()
        },
        "missing_approvals": [role for role in APPROVER_ROLES if role not in r.approvals],
    }


# ------------------------------------------------------------------------------------ bodies


class MarketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    product: str = Field(pattern=CODE)
    purpose: str = Field(pattern=CODE)


class ReleaseOpenIn(MarketIn):
    release_id: str = Field(pattern=RELEASE_ID)


class GateAttestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: str = Field(pattern=r"^RG-0[1-7]$")
    role: Literal["legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt"]
    status: Literal["PASSED", "FAILED"]
    evidence_ref: str = Field(
        min_length=3, max_length=256
    )  # a reference, never the evidence itself


class SupplierScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str = Field(min_length=1, max_length=128)
    scheme: str = Field(min_length=1, max_length=64)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    service_code: str = Field(min_length=1, max_length=64)

    def domain(self) -> Any:
        return v.attestation_model.Scope(self.entity, self.scheme, self.country, self.service_code)


class ReleaseFreezeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suppliers: list[SupplierScopeIn] = Field(default_factory=list, max_length=50)


class ReleaseApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["legal", "security_grc", "qa", "product_owner"]
    signed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttestationIn(SupplierScopeIn):
    scheme_version: str = Field(min_length=1, max_length=64)
    certificate_ref: str = Field(min_length=1, max_length=128)
    component_only: bool = False
    status: Literal["ACTIVE", "SUSPENDED", "WITHDRAWN", "EXPIRED"]
    valid_from: date
    valid_until: date | None = None
    register_url: str = Field(pattern=r"^https://\S{3,480}$")
    register_entry_id: str = Field(min_length=1, max_length=128)
    cab: str | None = Field(default=None, max_length=128)
    fetched_at: AwareDatetime
    proof_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_locator: str = Field(min_length=3, max_length=256)


class ContractEntitlementIn(BaseModel):
    """What Zoiko may use one provider for in one country, capability and purpose (CR-074)."""

    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=128)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    capability: str = Field(pattern=CODE)
    purpose: str = Field(pattern=CODE)
    status: Literal["GRANTED", "SUSPENDED", "WITHDRAWN"]
    effective_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    contract_ref: str = Field(min_length=3, max_length=256)
    terms_ref: str = Field(min_length=3, max_length=256)


def _ref(r: Any) -> str:
    return f"{r.release_id}:v{r.version}"


def _release_work(call: Any) -> Any:
    """Adapts a lifecycle call returning a release to what _act wants: the view, the audited target and the audited details."""

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        r = await call(conn, now)
        details: dict[str, Any] = {"status": r.status.value}
        if r.release_hash:
            details["release_hash"] = r.release_hash
        return _view(r), _ref(r), details

    return work


# ------------------------------------------------------------------------------------ reads


@router.get("/v1/regulatory/market-releases")
async def list_market_releases(
    request: Request,
    op: OperatorDep,
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
    status: Annotated[
        Literal["DRAFT", "FROZEN", "RELEASEABLE", "LAUNCHED", "WITHDRAWN"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, ALL_RELEASE_CAPABILITIES, "regulatory:market-releases")
        try:
            rows = await v.market_releases.list_releases(
                conn, country=country, status=status, limit=limit
            )
        except v.release_model.ReleaseError as e:
            raise _fail(e) from e
        return {"items": [_view(r) for r in rows]}


@router.get("/v1/regulatory/market-releases/{release_id}/versions/{version}")
async def get_market_release(
    release_id: ReleaseIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, ALL_RELEASE_CAPABILITIES, f"regulatory:{release_id}")
        try:
            return _view(await v.market_releases.get_release(conn, release_id, version))
        except v.release_model.ReleaseError as e:
            raise _fail(e) from e


@router.get("/v1/regulatory/market-status")
async def market_status(
    request: Request,
    op: OperatorDep,
    country: Annotated[str, Query(pattern=r"^[A-Z]{2}$")],
    product: Annotated[str, Query(pattern=CODE)],
    purpose: Annotated[str, Query(pattern=CODE)],
) -> dict[str, Any]:
    """Is this market open now, or why not. A preview for operators; it is the same answer the country gate acts on."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, ALL_RELEASE_CAPABILITIES, "regulatory:market-status")
        try:
            d = await v.market_releases.market_open(
                conn, v.release_model.MarketKey(country, product, purpose), now=datetime.now(UTC)
            )
        except v.release_model.ReleaseError as e:
            raise _fail(e) from e
        return {
            "open": d.open,
            "reason": d.reason,
            "release": None
            if d.release is None
            else {"release_id": d.release.release_id, "version": d.release.version},
        }


@router.get("/v1/regulatory/attestations")
async def list_attestations(
    request: Request,
    op: OperatorDep,
    entity: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> dict[str, Any]:
    """The scopes recorded for a provider and today's decision for each: certified, or on hold and why."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(
            conn, op, (RECORD_ATTESTATION, *ALL_RELEASE_CAPABILITIES), "regulatory:attestations"
        )
        scopes = await v.attestations.scopes_of(conn, entity=entity)
        decisions = await v.attestations.decide_scopes(conn, scopes, at=datetime.now(UTC))
        return {
            "items": [
                {
                    **_scope_view(s),
                    "outcome": d.outcome.value,
                    "reason": d.reason,
                    "register_url": d.snapshot.register_url if d.snapshot else None,
                    "fetched_at": _iso(d.snapshot.fetched_at) if d.snapshot else None,
                }
                for s, d in decisions.items()
            ]
        }


# ------------------------------------------------------------------------------------ writes


@router.post("/v1/regulatory/market-releases", status_code=201)
async def open_release(body: ReleaseOpenIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    market = v.release_model.MarketKey(body.country, body.product, body.purpose)
    return await _act(
        request,
        op,
        event="regulatory.release.opened",
        action="open",
        capability=AUTHOR,
        target_type="market_release",
        target=body.release_id,
        work=_release_work(
            lambda conn, now: v.market_releases.open_release(
                conn, body.release_id, market, author=str(op.principal_id), now=now
            )
        ),
    )


@router.post("/v1/regulatory/market-releases/{release_id}:revise", status_code=201)
async def revise_release(
    release_id: ReleaseIdPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _act(
        request,
        op,
        event="regulatory.release.revised",
        action="revise",
        capability=AUTHOR,
        target_type="market_release",
        target=release_id,
        work=_release_work(
            lambda conn, now: v.market_releases.revise_release(
                conn, release_id, author=str(op.principal_id), now=now
            )
        ),
    )


@router.post("/v1/regulatory/market-releases/{release_id}/versions/{version}:attest")
async def attest_gate(
    release_id: ReleaseIdPath,
    version: VersionPath,
    body: GateAttestIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    async def call(conn: Any, now: datetime) -> Any:
        return await v.market_releases.attest_gate(
            conn,
            release_id,
            version,
            gate=body.gate,
            status=v.release_model.GateStatus(body.status),
            evidence_ref=body.evidence_ref,
            role=body.role,
            by=str(op.principal_id),
            now=now,
        )

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        r = await call(conn, now)
        return _view(r), _ref(r), {"gate": body.gate, "status": body.status}

    return await _act(
        request,
        op,
        event="regulatory.release.gate_attested",
        action="attest",
        capability=ATTEST[body.role],  # the capability IS the role
        target_type="market_release",
        target=f"{release_id}:v{version}",
        work=work,
        role=body.role,
    )


@router.post("/v1/regulatory/market-releases/{release_id}/versions/{version}:freeze")
async def freeze_release(
    release_id: ReleaseIdPath,
    version: VersionPath,
    body: ReleaseFreezeIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    return await _act(
        request,
        op,
        event="regulatory.release.frozen",
        action="freeze",
        capability=AUTHOR,
        target_type="market_release",
        target=f"{release_id}:v{version}",
        work=_release_work(
            lambda conn, now: v.market_releases.freeze_release(
                conn, release_id, version, suppliers=[s.domain() for s in body.suppliers], now=now
            )
        ),
    )


@router.post("/v1/regulatory/market-releases/{release_id}/versions/{version}:approve")
async def approve_release(
    release_id: ReleaseIdPath,
    version: VersionPath,
    body: ReleaseApproveIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    return await _act(
        request,
        op,
        event="regulatory.release.approved",
        action="approve",
        capability=APPROVE[body.role],
        target_type="market_release",
        target=f"{release_id}:v{version}",
        work=_release_work(
            lambda conn, now: v.market_releases.approve_release(
                conn,
                release_id,
                version,
                role=body.role,
                approver=str(op.principal_id),
                signed_hash=body.signed_hash,
                now=now,
            )
        ),
        role=body.role,
    )


@router.post("/v1/regulatory/market-releases/{release_id}/versions/{version}:launch")
async def launch_release(
    release_id: ReleaseIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _act(
        request,
        op,
        event="regulatory.release.launched",
        publish="verification.market.launched",
        action="launch",
        capability=LAUNCH,
        target_type="market_release",
        target=f"{release_id}:v{version}",
        work=_release_work(
            lambda conn, now: v.market_releases.launch_release(conn, release_id, version, now=now)
        ),
    )


@router.post("/v1/regulatory/market-releases/{release_id}/versions/{version}:withdraw")
async def withdraw_release(
    release_id: ReleaseIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _act(
        request,
        op,
        event="regulatory.release.withdrawn",
        publish="verification.market.withdrawn",
        action="withdraw",
        capability=LAUNCH,
        target_type="market_release",
        target=f"{release_id}:v{version}",
        work=_release_work(
            lambda conn, now: v.market_releases.withdraw_release(conn, release_id, version, now=now)
        ),
    )


@router.post("/v1/regulatory/attestations", status_code=201)
async def record_attestation(
    body: AttestationIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    scope = body.domain()
    target = f"{scope.entity}/{scope.scheme}/{scope.country}/{scope.service_code}"

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        snapshot = v.attestation_model.Snapshot(
            snapshot_id=f"att-{uuid.uuid4().hex}",
            scope=scope,
            scheme_version=body.scheme_version,
            certificate_ref=body.certificate_ref,
            component_only=body.component_only,
            status=v.attestation_model.CertStatus(body.status),
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            register_url=body.register_url,
            register_entry_id=body.register_entry_id,
            cab=body.cab,
            fetched_at=body.fetched_at,
            proof_hash=body.proof_hash,
            evidence_locator=body.evidence_locator,
            recorded_by=str(op.principal_id),
            recorded_at=now,
        )
        saved = await v.attestations.record(conn, snapshot, now=now)
        d = await v.attestations.decide_scope(conn, scope, at=now)
        return (
            {
                "snapshot_id": saved.snapshot_id,
                **_scope_view(scope),
                "status": saved.status.value,
                "outcome": d.outcome.value,
                "reason": d.reason,
            },
            f"{target}:{saved.snapshot_id}",
            {"proof_hash": saved.proof_hash, "status": saved.status.value},
        )

    return await _act(
        request,
        op,
        event="regulatory.attestation.recorded",
        publish="verification.certification.changed",
        action="record",
        capability=RECORD_ATTESTATION,
        target_type="certification_attestation",
        target=target,
        work=work,
    )


def _entitlement_scope_view(s: Any) -> dict[str, str]:
    return {
        "provider": s.provider,
        "country": s.country,
        "capability": s.capability,
        "purpose": s.purpose,
    }


@router.get("/v1/regulatory/entitlements")
async def list_entitlements(
    request: Request,
    op: OperatorDep,
    provider: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> dict[str, Any]:
    """The scopes recorded for a provider and today's decision for each: entitled, or denied and why."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(
            conn,
            op,
            (RECORD_ENTITLEMENT, RECORD_ATTESTATION, *ALL_RELEASE_CAPABILITIES),
            "regulatory:entitlements",
        )
        scopes = await v.entitlements.scopes_of(conn, provider=provider)
        decisions = await v.entitlements.decide_scopes(conn, scopes, at=datetime.now(UTC))
        return {
            "items": [
                {
                    **_entitlement_scope_view(s),
                    "outcome": d.outcome.value,
                    "reason": d.reason,
                    "contract_ref": d.grant.contract_ref if d.grant else None,
                    "valid_until": _iso(d.grant.valid_until) if d.grant else None,
                }
                for s, d in decisions.items()
            ]
        }


@router.post("/v1/regulatory/entitlements", status_code=201)
async def record_entitlement(
    body: ContractEntitlementIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    scope = v.entitlement_model.Scope(body.provider, body.country, body.capability, body.purpose)
    target = f"{scope.provider}/{scope.country}/{scope.capability}/{scope.purpose}"

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        grant = v.entitlement_model.Grant(
            grant_id=f"ent-{uuid.uuid4().hex}",
            scope=scope,
            status=v.entitlement_model.Status(body.status),
            effective_at=body.effective_at,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            contract_ref=body.contract_ref,
            terms_ref=body.terms_ref,
            recorded_by=str(op.principal_id),
            recorded_at=now,
        )
        saved = await v.entitlements.record(conn, grant, now=now)
        d = await v.entitlements.decide_scope(conn, scope, at=now)
        return (
            {
                "grant_id": saved.grant_id,
                **_entitlement_scope_view(scope),
                "status": saved.status.value,
                "outcome": d.outcome.value,
                "reason": d.reason,
            },
            f"{target}:{saved.grant_id}",
            {"status": saved.status.value, "contract_ref": saved.contract_ref},
        )

    return await _act(
        request,
        op,
        event="regulatory.entitlement.recorded",
        publish="verification.provider.entitlement_changed",
        action="record",
        capability=RECORD_ENTITLEMENT,
        target_type="provider_entitlement",
        target=target,
        work=work,
    )


# ------------------------------------------------------------------------------------ regulated claims (CR-077)

READ_CLAIMS = (RECORD_ATTESTATION, RECORD_ENTITLEMENT, *ALL_RELEASE_CAPABILITIES)
AUDIENCE = Literal["PUBLIC_WEBSITE", "PRODUCT_LABEL", "SALES", "SUPPORT", "API"]


def _templates(request: Request) -> Any:
    """The approved claim templates: the configured file, or a set an app was given (tests, a staging review)."""
    given = getattr(request.app.state, "claim_templates", None)
    return given if given is not None else v.claim_templates()


@router.get("/v1/regulatory/claims")
async def list_claims(
    request: Request,
    op: OperatorDep,
    audience: Annotated[AUDIENCE | None, Query()] = None,
    publishable_only: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Every claim template (latest version) and whether it may be shown now, with every reason it may not. `publishable_only` returns what a
    website, label or sales sheet may show for `audience`, and nothing else."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:claims")
        now = datetime.now(UTC)
        if publishable_only:
            if audience is None:
                raise ZidError(
                    "CLAIM_AUDIENCE_REQUIRED",
                    "Say which audience the claims are for.",
                    http_status=422,
                )
            return {
                "items": await v.regulated_claims.publishable(
                    conn, _templates(request), at=now, audience=audience
                )
            }
        judged = await v.regulated_claims.judge(
            conn, _templates(request), at=now, audience=audience
        )
        return {
            "items": [
                {
                    "template_id": t.template_id,
                    "version": t.version,
                    "text": t.text,
                    "audiences": sorted(t.audiences),
                    "countries": sorted(t.countries),
                    "template_hash": d.template_hash,
                    "publishable": d.publishable,
                    "reasons": list(d.reasons),
                }
                for t, d in judged
            ]
        }


@router.get("/v1/regulatory/claims/{template_id}/proof")
async def claim_proof(
    template_id: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")],
    request: Request,
    op: OperatorDep,
    at: Annotated[AwareDatetime | None, Query()] = None,
) -> dict[str, Any]:
    """The exportable proof bundle for one claim at a moment (default now): the template and its hash, each certification snapshot and proof
    hash, each market release and its hash, the outcome and its reasons, and a digest over all of it. Exporting it is audited."""
    when = at or datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, f"regulatory:{template_id}")
        try:
            bundle: dict[str, Any] = await v.regulated_claims.proof(
                conn, _templates(request), template_id, at=when
            )
        except v.claim_model.ClaimTemplateError as e:
            raise ZidError(e.code, str(e), http_status=404) from e
        await actions.audit(
            conn,
            op,
            event="regulatory.claim.proof_exported",
            action="export",
            target_type="claim_template",
            target=f"{template_id}:v{bundle['template']['version']}",
            outcome="SUCCESS",
            now=datetime.now(UTC),
            details={"digest": bundle["digest"], "publishable": bundle["publishable"]},
            once=False,
        )
        return bundle


# ------------------------------------------------------------------------------------ privacy overlays (CR-079)


def _overlays(request: Request) -> Any:
    given = getattr(request.app.state, "privacy_overlays", None)
    return given if given is not None else v.privacy_overlays()


@router.get("/v1/regulatory/privacy-overlays")
async def list_privacy_overlays(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Each country's privacy overlay with the hash its approvers sign and what, if anything, makes it unusable."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:privacy-overlays")
    m = v.privacy_overlay_model
    return {
        "items": [
            {
                "country": o.country,
                "version": o.version,
                "overlay_hash": m.overlay_hash(o),
                "home_regions": sorted(o.home_regions),
                "transfer_regions": dict(sorted(o.transfer_regions.items())),
                "provider_regions": dict(sorted(o.provider_regions.items())),
                "biometrics": o.biometrics.value,
                "dpia_required": o.dpia_required,
                "problems": m.problems(o),
            }
            for _, o in sorted(_overlays(request).items())
        ]
    }


@router.get("/v1/regulatory/privacy-route-check")
async def privacy_route_check(
    request: Request,
    op: OperatorDep,
    country: Annotated[str, Query(pattern=r"^[A-Z]{2}$")],
    provider: Annotated[str, Query(min_length=1, max_length=128)],
    biometric: Annotated[bool, Query()] = False,
    consent: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Would this country's data be routed to this provider, with or without biometrics? A preview for operators: the same rule the start applies."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:privacy-overlays")
    d = v.privacy_overlay_model.route(
        _overlays(request).get(country), provider=provider, biometric=biometric, consent=consent
    )
    return {
        "allowed": d.allowed,
        "reasons": list(d.reasons),
        "region": d.region,
        "mechanism": d.mechanism,
    }


# ------------------------------------------------------------------------------------ monitoring scope (CR-081)


class _Screening:
    """The screening module's public interface, imported on first use."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.screening.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


screening_ports = _Screening()


@router.get("/v1/regulatory/monitoring-check")
async def monitoring_check(
    request: Request,
    op: OperatorDep,
    country: Annotated[str, Query(pattern=r"^[A-Z]{2}$")],
    kind: Annotated[
        Literal[
            "SANCTIONS_ONGOING",
            "PEP",
            "ADVERSE_MEDIA",
            "TRANSACTION_MONITORING",
            "BANK_OWNERSHIP",
            "CRYPTO_TRAVEL_RULE",
            "CRYPTO_WALLET_SCREENING",
        ],
        Query(),
    ],
    sector: Annotated[str, Query(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] = "ANY",
) -> dict[str, Any]:
    """May ongoing monitoring of this kind run in this country for this sector now? The same rule the monitoring jobs apply (no global background
    monitoring by default)."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:monitoring")
    d = screening_ports.monitoring_allowed(
        country=country,
        kind=screening_ports.MonitoringKind(kind),
        sector=sector,
        at=datetime.now(UTC),
    )
    return {"allowed": d.allowed, "reasons": list(d.reasons), "approval_id": d.approval_id}


# ------------------------------------------------------------------------------------ England Right to Rent (CR-084)


@router.get("/v1/regulatory/right-to-rent/check")
async def right_to_rent_check(
    request: Request,
    op: OperatorDep,
    country: Annotated[str, Query(pattern=r"^[A-Z]{2}$")],
    route: Annotated[Literal["DIGITAL_IDSP", "HOME_OFFICE_SHARE_CODE", "LANDLORD_MANUAL"], Query()],
    subdivision: Annotated[str | None, Query(pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")] = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> dict[str, Any]:
    """Does England Right to Rent apply to this property, and may this route produce evidence now? The same decision Zoiko Rooms asks; the
    landlord always keeps the statutory responsibility."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:right-to-rent")
        d = await v.right_to_rent.decide_for(
            conn,
            property_country=country,
            property_subdivision=subdivision,
            route=v.rtr_model.Route(route),
            provider=provider,
            at=datetime.now(UTC),
        )
    return {
        "outcome": d.outcome.value,
        "route": d.route.value if d.route else None,
        "reasons": list(d.reasons),
        "zoiko_evidence": d.zoiko_evidence,
        "statutory_excuse_by_zoiko": d.statutory_excuse_by_zoiko,
        "retention": d.retention,
    }


# ------------------------------------------------------------------------------------ readiness (CR-086)

_REGULATED = (
    "INDIVIDUAL_VERIFICATION",
    "BUSINESS_VERIFICATION",
    "CREDENTIAL_ISSUANCE",
    "ENTERPRISE_USE",
)  # the same list as verification coverage.REGULATED


@router.get("/v1/regulatory/readiness")
async def readiness(request: Request, op: OperatorDep) -> dict[str, Any]:
    """What switching every regulatory check to enforce would refuse today, country by capability, and which owner-supplied configuration is still
    empty. A report for the people who decide to enforce (ZID-RCG-003 section 13, "global availability is earned market-by-market"); it changes
    nothing."""
    now = datetime.now(UTC)
    reg = v.registry()
    env = str(getattr(request.app.state, "environment", "production"))
    overlays = v.privacy_overlays()
    countries: list[dict[str, Any]] = []
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:readiness")
        for code in sorted(reg.countries):
            caps: list[dict[str, Any]] = []
            for name in _REGULATED:
                capability = v.Capability(name)
                entry = reg.countries[code].capabilities.get(capability)
                if entry is None:
                    continue
                provider = entry.provider
                refuse = await v.coverage.capability_refusals(
                    conn,
                    country=code,
                    capability=capability,
                    provider=provider,
                    overlays=overlays,
                    now=now,
                )
                caps.append(
                    {
                        "capability": name,
                        "availability": v.availability(
                            reg, code, capability, now=now, environment=env
                        ).value,
                        "provider": provider,
                        "would_refuse": refuse,
                        "missing": sorted({r.split(":", 1)[0] for r in refuse}),
                    }
                )
            if caps:
                sanctions = screening_ports.monitoring_allowed(
                    country=code, kind=screening_ports.MonitoringKind("SANCTIONS_ONGOING"), at=now
                )
                caps.append(
                    {
                        "capability": "SANCTIONS_ONGOING_MONITORING",
                        "availability": "-",
                        "provider": None,
                        "would_refuse": [f"monitoring:{r}" for r in sanctions.reasons],
                        "missing": [] if sanctions.allowed else ["monitoring"],
                    }
                )
                countries.append({"country": code, "capabilities": caps})
    state = request.app.state
    empty: list[str] = [
        name
        for name, items in (
            ("claim templates", v.claim_templates()),
            ("privacy overlays", overlays),
        )
        if not items
    ]
    return {
        "countries": countries,
        "modes": {
            "regulatory_packs": str(getattr(state, "regulatory_packs", "shadow")),
            "market_releases": str(getattr(state, "market_releases", "shadow")),
            "provider_entitlements": str(getattr(state, "provider_entitlements", "shadow")),
            "privacy_overlay": str(getattr(state, "privacy_overlay", "shadow")),
            "eudi_wallet": str(getattr(state, "eudi_wallet", "shadow")),
            "monitoring_scope": str(getattr(state, "monitoring_scope", "shadow")),
            "kill_switch": "always enforced",
        },
        "empty_configuration": empty,
    }


# ------------------------------------------------------------------------------------ service offerings (CR-088)


@router.get("/v1/regulatory/offerings")
async def offerings(
    request: Request,
    op: OperatorDep,
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
) -> dict[str, Any]:
    """Each service offering, country by country: AVAILABLE only when provider rights, country rules, privacy controls and an independently
    accepted conformance run all hold, otherwise every reason it is not. The public catalog (GET /v1/offerings) shows only what is AVAILABLE."""
    now = datetime.now(UTC)
    catalog = v.offerings_catalog()
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:offerings")
        cov = await v.coverage.offerings_coverage(
            conn,
            catalog,
            v.registry(),
            overlays=v.privacy_overlays(),
            now=now,
            countries=[country] if country else None,
        )
    by: dict[str, list[dict[str, Any]]] = {}
    for c in cov:
        by.setdefault(c.offering_id, []).append(
            {"country": c.country, "status": c.status.value, "reasons": list(c.reasons)}
        )
    return {
        "items": [
            {
                "offering_id": o.offering_id,
                "title": o.title,
                "wording": o.wording,
                "capabilities": list(o.capabilities),
                "biometric": o.biometric,
                "cases": list(o.cases),
                "code": list(o.code),
                "countries": by.get(o.offering_id, []),
            }
            for o in catalog
        ]
    }


# ------------------------------------------------------------------------------------ official-register connectors (CR-087)


class RegisterRefreshIn(SupplierScopeIn):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")


@router.get("/v1/regulatory/register-sources")
async def register_sources(request: Request, op: OperatorDep) -> dict[str, Any]:
    """The official registers, trusted lists and professional registers the platform may query, and whether each may be used here and why not."""
    env = str(getattr(request.app.state, "environment", "production"))
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:register-sources")
    m = v.register_sources_model
    items = []
    for sid, s in sorted(v.register_sources.sources().items()):
        u = m.usable(
            s, environment=env, connector_available=v.register_sources.connector(sid) is not None
        )
        items.append(
            {
                "source_id": sid,
                "kind": s.kind.value,
                "scheme": s.scheme,
                "base_url": s.base_url,
                "contract_ref": s.contract_ref,
                "usable": u.usable,
                "reasons": list(u.reasons),
            }
        )
    return {"items": items}


@router.post("/v1/regulatory/attestations:refresh", status_code=201)
async def refresh_attestation(
    body: RegisterRefreshIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Ask the official register itself about one exact scope, preserve its answer and record the snapshot it proves (or nothing, when the
    register does not list it). The same rules as a hand-recorded attestation; the proof hash is computed here, never taken from the connector."""
    scope = body.domain()
    target = f"{scope.entity}/{scope.scheme}/{scope.country}/{scope.service_code}"
    env = str(getattr(request.app.state, "environment", "production"))

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        try:
            r = await v.register_refresh.refresh(
                conn,
                body.source_id,
                scope,
                environment=env,
                now=now,
                recorded_by=str(op.principal_id),
            )
        except v.register_sources_model.SourceError as e:
            raise ZidError(e.code, str(e), http_status=409) from e
        view = {
            **_scope_view(scope),
            "source_id": body.source_id,
            "listed": r.listed,
            "snapshot_id": r.snapshot.snapshot_id if r.snapshot else None,
            "proof_hash": r.snapshot.proof_hash if r.snapshot else None,
            "outcome": r.decision.outcome.value,
            "reason": r.decision.reason,
        }
        return (
            view,
            f"{target}:{view['snapshot_id'] or 'not-listed'}",
            {"source_id": body.source_id, "listed": r.listed, "proof_hash": view["proof_hash"]},
        )

    return await _act(
        request,
        op,
        event="regulatory.attestation.refreshed",
        publish="verification.certification.changed",
        action="refresh",
        capability=RECORD_ATTESTATION,
        target_type="certification_attestation",
        target=target,
        work=work,
    )


# ------------------------------------------------------------------------------------ qualified trust lists (WF-05, RCG-G06)


class _Evidence:
    """The evidence module's trust configuration, imported on first use (no import-time dependency on the evidence module)."""

    def __getattr__(self, name: str) -> Any:
        from zid_platform.modules.evidence.ports import trust_model

        return getattr(trust_model, name)


evidence_trust = _Evidence()


@router.get("/v1/regulatory/trust-lists")
async def trust_lists(request: Request, op: OperatorDep) -> dict[str, Any]:
    """WF-05: what the signature and seal validator may rely on. Qualified status is read only from trusted lists whose signature was verified;
    an unauthenticated list counts for nothing, and with none every result is NOT_ASSESSED. Technical validation is reported separately from
    legal interpretation: a valid signature is never lawful authority for a purpose."""
    from zid_platform.modules.evidence.ports import trust_config

    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, READ_CLAIMS, "regulatory:trust-lists")
    cfg = trust_config()
    now = datetime.now(UTC)
    t = evidence_trust
    lists = []
    for tl in cfg.trusted_lists:
        qc = [s for s in tl.services if t.CA_QC in s.service_type]
        granted = [s for s in qc if t.GRANTED in (t._status_at(s, now) or "")]
        lists.append(
            {
                "name": tl.name,
                "authenticated": tl.authenticated,
                "problem": tl.problem,
                "services": len(tl.services),
                "qualified_ca_services": len(qc),
                "granted_now": len(granted),
            }
        )
    usable = [x for x in lists if x["authenticated"]]
    return {
        "lists": lists,
        "anchors": len(cfg.anchors),
        "tsa_roots": len(cfg.tsa_roots),
        "crls": len(cfg.crls),
        "qualified_status_assessable": bool(usable),
        "reasons": [] if usable else ["NO_AUTHENTICATED_TRUST_LIST"],
        "timestamp_authority_configured": bool(
            getattr(request.app.state, "evidence_tsa_configured", False)
        ),
        "interpretation": "Technical validation only. A valid or qualified signature is not lawful authority for any purpose; legal effect is decided separately.",
    }
