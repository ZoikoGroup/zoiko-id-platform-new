"""Biometric method lifecycle and kill switch, operator plane (ZID-DTV-WF-04 section 06, BIO-03; CR-107).

    POST /v1/regulatory/biometric-methods/proposals                    propose one step up           (regulatory.biometric_method.propose)
    POST /v1/regulatory/biometric-methods/proposals/{id}:approve       the second person approves    (regulatory.biometric_method.approve)
    POST /v1/regulatory/biometric-methods/proposals/{id}:reject        refuse or withdraw            (regulatory.biometric_method.approve)
    POST /v1/regulatory/biometric-methods/{scope_id}:step-down         PAUSED / lower state / OFF    (regulatory.biometric_method.pause)
    GET  /v1/regulatory/biometric-methods                              every scope, its versions, and the open proposals

A step up is two people and one step at a time (OFF or PAUSED -> LAB_ONLY -> PILOT_GATED -> PRODUCTION_ELIGIBLE); the domain and the table both
refuse an approval by the proposer. A step down (the kill switch) is one person and takes effect at once; a resume from PAUSED is a new approval. Every
action writes an audit record in the same transaction (regulatory.biometric_method.proposed, .approved, .rejected, .stepped_down); a refusal is recorded
as regulatory.biometric_method.refused. The audit carries the scope, the states and the references, never the reason text.

Approval is not activation: a live session still needs the deployment's accepted engine, the tenant's own approved DPIA and the country overlay
(`GET /v1/biometric-methods/eligibility`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions


class _Verification:
    """The verification module's public interface, imported on first use (avoids a dependency cycle at import time, as regulatory.py does)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
PROPOSE = "regulatory.biometric_method.propose"
APPROVE = "regulatory.biometric_method.approve"
PAUSE = "regulatory.biometric_method.pause"
STATE_CHANGED = "verification.biometric_method.state_changed"  # CR-108: approvals and step downs are announced (priority lane)
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("regulatory.")))
SCOPE_ID = r"^[a-z0-9][a-z0-9-]{0,127}$"
REF = r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$"

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
ScopePath = Annotated[str, Path(pattern=SCOPE_ID)]

actions = Actions(
    namespace=uuid.UUID("b7c41e0a-2f6d-4a19-9e83-5d0c7a2b1f64"),
    refused_event="regulatory.biometric_method.refused",
    status={
        "METHOD_NOT_FOUND": 404,
        "METHOD_PROPOSAL_NOT_FOUND": 404,
        "METHOD_PROPOSAL_NOT_OPEN": 409,
        "METHOD_PROPOSAL_OPEN": 409,
        "METHOD_PROPOSAL_STALE": 409,
        "METHOD_PROPOSAL_EXPIRED": 409,
        "METHOD_SCOPE_ID_TAKEN": 409,
        "METHOD_APPROVED_BY_PROPOSER": 403,
    },
    errors=lambda: (v.biometric_method_model.MethodError,),
    refused_code="BIOMETRIC_METHOD_REFUSED",
    capability_hint="a regulatory capability",
)


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["FACE_MATCH", "LIVENESS"]
    engine_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    engine_version: str = Field(min_length=1, max_length=64)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    purpose: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    pilot_tenant: uuid.UUID | None = (
        None  # the one tenant a PILOT_GATED scope runs for; never the caller's own tenant context
    )

    def domain(self) -> Any:
        m = v.biometric_method_model
        return m.Scope(
            m.Method(self.method),
            self.engine_id,
            self.engine_version,
            self.country,
            self.purpose,
            str(self.pilot_tenant) if self.pilot_tenant else None,
        )


class ProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_id: str | None = Field(
        default=None, pattern=SCOPE_ID
    )  # required for a scope that has no record yet
    scope: ScopeIn
    target: Literal["LAB_ONLY", "PILOT_GATED", "PRODUCTION_ELIGIBLE"]
    dpia_ref: str | None = Field(default=None, pattern=REF)
    evaluation_ref: str | None = Field(default=None, pattern=REF)
    reason: str = Field(min_length=10, max_length=1000)
    evidence_ref: str = Field(pattern=REF)
    exposure_percent: int = Field(
        default=100, ge=1, le=100
    )  # CR-110: below 100 only for PRODUCTION_ELIGIBLE


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


class StepDownIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["PAUSED", "OFF", "LAB_ONLY", "PILOT_GATED", "PRODUCTION_ELIGIBLE"]
    reason: str = Field(min_length=10, max_length=1000)
    evidence_ref: str = Field(pattern=REF)
    exposure_percent: int | None = Field(
        default=None, ge=1, le=99
    )  # CR-110: narrowing a production rollout


def _scope_view(s: Any) -> dict[str, Any]:
    return {
        "method": s.method.value,
        "engine_id": s.engine_id,
        "engine_version": s.engine_version,
        "country": s.country,
        "purpose": s.purpose,
        "pilot_tenant": s.tenant_id,
    }


def _record_view(r: Any) -> dict[str, Any]:
    return {
        "scope_id": r.scope_id,
        "version": r.version,
        "state": r.state.value,
        "scope": _scope_view(r.scope),
        "dpia_ref": r.dpia_ref,
        "evaluation_ref": r.evaluation_ref,
        "reason": r.reason,
        "evidence_ref": r.evidence_ref,
        "recorded_by": r.recorded_by,
        "proposed_by": r.proposed_by,
        "proposal_id": r.proposal_id,
        "effective_at": r.effective_at.isoformat(),
        "exposure_percent": r.exposure_percent,
        "decision_hash": r.decision_hash,
    }


def _proposal_view(p: Any) -> dict[str, Any]:
    return {
        "proposal_id": p.proposal_id,
        "scope_id": p.scope_id,
        "scope": _scope_view(p.scope),
        "from_version": p.from_version,
        "target": p.target.value,
        "dpia_ref": p.dpia_ref,
        "evaluation_ref": p.evaluation_ref,
        "reason": p.reason,
        "evidence_ref": p.evidence_ref,
        "proposed_by": p.proposed_by,
        "proposed_at": p.proposed_at.isoformat(),
        "status": p.status.value,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decision_reason": p.decision_reason,
        "exposure_percent": p.exposure_percent,
    }


def _audit(scope: Any, **extra: Any) -> dict[str, Any]:
    return {"scope": {k: val for k, val in _scope_view(scope).items() if val is not None}, **extra}


@router.post("/v1/regulatory/biometric-methods/proposals", status_code=201)
async def propose(body: ProposeIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        p = await v.biometric_methods.propose(
            conn,
            scope=body.scope.domain(),
            target=v.biometric_method_model.State(body.target),
            dpia_ref=body.dpia_ref,
            evaluation_ref=body.evaluation_ref,
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            by=str(op.principal_id),
            now=now,
            scope_id=body.scope_id,
            exposure_percent=body.exposure_percent,
        )
        details = _audit(
            p.scope,
            proposal_id=p.proposal_id,
            from_version=p.from_version,
            target=p.target.value,
            dpia_ref=p.dpia_ref,
            evaluation_ref=p.evaluation_ref,
            evidence_ref=p.evidence_ref,
            exposure_percent=p.exposure_percent,
        )
        return _proposal_view(p), p.proposal_id, details

    return await actions.act(
        request,
        op,
        event="regulatory.biometric_method.proposed",
        publish=None,
        action="propose",
        capability=PROPOSE,
        target_type="biometric_method_proposal",
        target=body.scope_id or "new-scope",
        work=work,
    )


@router.post("/v1/regulatory/biometric-methods/proposals/{proposal_id}:approve")
async def approve(
    proposal_id: uuid.UUID, body: DecideIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        p, rec = await v.biometric_methods.approve(
            conn, str(proposal_id), by=str(op.principal_id), reason=body.reason, now=now
        )
        details = _audit(
            rec.scope,
            proposal_id=p.proposal_id,
            scope_id=rec.scope_id,
            version=rec.version,
            state=rec.state.value,
            proposed_by=p.proposed_by,
            exposure_percent=rec.exposure_percent,
            decision_hash=rec.decision_hash,
        )
        return (
            {"proposal": _proposal_view(p), "state": _record_view(rec)},
            f"{rec.scope_id}:v{rec.version}",
            details,
        )

    return await actions.act(
        request,
        op,
        event="regulatory.biometric_method.approved",
        publish=STATE_CHANGED,
        action="approve",
        capability=APPROVE,
        target_type="biometric_method_proposal",
        target=str(proposal_id),
        work=work,
    )


@router.post("/v1/regulatory/biometric-methods/proposals/{proposal_id}:reject")
async def reject(
    proposal_id: uuid.UUID, body: DecideIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        p = await v.biometric_methods.reject(
            conn, str(proposal_id), by=str(op.principal_id), reason=body.reason, now=now
        )
        return (
            _proposal_view(p),
            p.proposal_id,
            _audit(p.scope, proposal_id=p.proposal_id, target=p.target.value),
        )

    return await actions.act(
        request,
        op,
        event="regulatory.biometric_method.rejected",
        publish=None,
        action="reject",
        capability=APPROVE,
        target_type="biometric_method_proposal",
        target=str(proposal_id),
        work=work,
    )


@router.post("/v1/regulatory/biometric-methods/{scope_id}:step-down")
async def step_down(
    scope_id: ScopePath, body: StepDownIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        rec = await v.biometric_methods.step_down(
            conn,
            scope_id,
            target=v.biometric_method_model.State(body.target),
            by=str(op.principal_id),
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            now=now,
            exposure_percent=body.exposure_percent,
        )
        details = _audit(
            rec.scope,
            version=rec.version,
            state=rec.state.value,
            evidence_ref=rec.evidence_ref,
            exposure_percent=rec.exposure_percent,
        )
        return _record_view(rec), f"{scope_id}:v{rec.version}", details

    return await actions.act(
        request,
        op,
        event="regulatory.biometric_method.stepped_down",
        publish=STATE_CHANGED,
        action="step-down",
        capability=PAUSE,
        target_type="biometric_method_state",
        target=scope_id,
        work=work,
    )


@router.get("/v1/regulatory/biometric-methods")
async def list_methods(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:biometric-methods")
        listing = await v.biometric_methods.list_scopes(conn)
        return {
            "items": [
                {"current": _record_view(vs[-1]), "versions": [_record_view(x) for x in vs]}
                for vs in listing["scopes"]
            ],
            "open_proposals": [_proposal_view(p) for p in listing["open_proposals"]],
        }
