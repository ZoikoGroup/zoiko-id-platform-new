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
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.operator_access.ports import has_capability, require_operator


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
}


def _fail(e: Any) -> ZidError:
    text = str(e)
    detail = text.split(": ", 1)[1] if ": " in text else ""
    return ZidError(
        e.code,
        detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _denied(capability: str) -> ZidError:
    return ZidError(
        "OPERATOR_CAPABILITY_REQUIRED",
        f"This needs an approved, unexpired {capability} grant.",
        http_status=403,
    )


async def _need(conn: Any, op: Any, capability: str, resource: str) -> None:
    if not await has_capability(
        conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
    ):
        raise _denied(capability)


async def _need_any(conn: Any, op: Any, capabilities: tuple[str, ...], resource: str) -> None:
    for capability in capabilities:
        if await has_capability(
            conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
        ):
            return
    raise ZidError(
        "OPERATOR_CAPABILITY_REQUIRED", "This needs a market release capability.", http_status=403
    )


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


# ------------------------------------------------------------------------------------ audit


_AUDIT_NAMESPACE = uuid.UUID("7b2e4c18-5d90-4f3a-b1c6-0a8e9d27f451")


async def _audit(
    conn: Any,
    op: Any,
    *,
    event: str,
    action: str,
    target_type: str,
    target: str,
    outcome: str,
    now: datetime,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    once: bool = True,
) -> None:
    """One protected audit record in the platform partition. A success record is idempotent per event, target and role, so a retried
    request never doubles it; a refusal is its own record every time."""
    facts = details or {}
    key = f"{event}|{target}|{facts.get('role', '')}|{facts.get('gate', '')}"
    await record_audit(
        conn,
        None,
        AuditFacts(
            event_type=event,
            actor_type="OPERATOR",
            action=action,
            target_type=target_type,
            target_ref=target,
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=str(op.principal_id),
            reason_code=reason,
            source_service="operator_access",
            source_event_id=str(uuid.uuid5(_AUDIT_NAMESPACE, key)) if once else str(uuid.uuid4()),
            details=details or {},
        ),
    )


async def _act(
    request: Request,
    op: Any,
    *,
    event: str,
    action: str,
    capability: str,
    target_type: str,
    target: str,
    work: Any,
    role: str | None = None,
) -> dict[str, Any]:
    """Check the capability, make the change and audit it in one transaction. A refusal rolls the attempt back and is audited in a
    transaction of its own, then raised. `work(conn, now)` returns (response, audit target, audit details)."""
    now = datetime.now(UTC)
    refusal: ZidError
    code: str
    try:
        async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
            await _need(conn, op, capability, f"regulatory:{target}")
            response, audited, details = await work(conn, now)
            if role:
                details["role"] = role
            await _audit(
                conn,
                op,
                event=event,
                action=action,
                target_type=target_type,
                target=audited,
                outcome="SUCCESS",
                now=now,
                details=details,
            )
            return response  # type: ignore[no-any-return]
    except (v.release_model.ReleaseError, v.attestation_model.AttestationError) as e:
        refusal, code = _fail(e), str(e.code)
    except v.PolicyPackError as e:
        refusal, code = ZidError(e.code, str(e), http_status=500), str(e.code)
    except DBAPIError:
        refusal, code = (
            ZidError(
                "RELEASE_REFUSED",
                "The database refused the change; nothing was stored.",
                http_status=409,
            ),
            "RELEASE_REFUSED",
        )
    except ZidError as e:
        refusal, code = e, e.code
    attempted: dict[str, Any] = {"attempted": event}
    if role:
        attempted["role"] = role
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _audit(
            conn,
            op,
            event="regulatory.release.refused",
            action=action,
            target_type=target_type,
            target=target,
            outcome="DENIED",
            now=now,
            reason=code,
            details=attempted,
            once=False,
        )
    raise refusal


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
        action="record",
        capability=RECORD_ATTESTATION,
        target_type="certification_attestation",
        target=target,
        work=work,
    )
