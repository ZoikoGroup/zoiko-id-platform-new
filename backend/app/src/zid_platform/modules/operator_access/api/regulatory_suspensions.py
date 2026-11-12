"""Exact-scope provider suspensions, operator plane (ZID-RCG-003 RCG-G08, WF-06, section 08.2; CR-078).

    POST /v1/regulatory/suspensions                          the kill switch: suspend an exact scope now     (regulatory.suspension.apply)
    POST /v1/regulatory/suspensions/{suspension_id}:lift     the rollback: lift it, by another person         (regulatory.suspension.lift)
    GET  /v1/regulatory/suspensions                          every suspension and its versions; ?active=true for those in force
    GET  /v1/regulatory/suspensions/impact                   what a scope would touch, before anyone applies it

A suspension takes effect at once and is always enforced (no shadow mode): new calls in its scope are refused at the country gate with
PROVIDER_SCOPE_SUSPENDED, a market whose pinned supplier it covers closes, and a claim resting on that supplier comes off. One service suspended never
disables the provider's other services; a whole provider is frozen only for a breach. Applying returns its impact; both actions write an audit record
in the same transaction (regulatory.suspension.applied, .lifted), a refusal is recorded as regulatory.suspension.refused. The audit carries the id,
the trigger, the scope fields and the evidence reference, never the reason text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions


class _Verification:
    """The verification module's public interface, imported on first use (see regulatory.py: avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
APPLY = "regulatory.suspension.apply"
LIFT = "regulatory.suspension.lift"
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("regulatory.")))

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
SuspensionPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")]
CODE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

actions = Actions(
    namespace=uuid.UUID("5e8a1c2d-9b47-4f60-a3d1-7c0e2b94f6a8"),
    refused_event="regulatory.suspension.refused",
    status={
        "SUSPENSION_EXISTS": 409,
        "SUSPENSION_NOT_FOUND": 404,
        "SUSPENSION_NOT_ACTIVE": 409,
        "SUSPENSION_OUT_OF_ORDER": 409,
        "SUSPENSION_LIFTED_BY_APPLIER": 403,
    },
    errors=lambda: (v.suspension_model.SuspensionError,),
    refused_code="SUSPENSION_REFUSED",
    capability_hint="a regulatory capability",
)


class SuspensionScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=128)
    service_id: str | None = Field(default=None, pattern=CODE)
    scheme: str | None = Field(default=None, pattern=CODE)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    subdivision: str | None = Field(default=None, pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
    purpose: str | None = Field(default=None, pattern=CODE)
    profession: str | None = Field(default=None, pattern=CODE)
    credential_class: str | None = Field(default=None, pattern=CODE)

    def domain(self) -> Any:
        return v.suspension_model.Scope(**self.model_dump())


class SuspensionApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suspension_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    scope: SuspensionScopeIn
    trigger: Literal[
        "CERTIFICATION_WITHDRAWN",
        "PROVIDER_BREACH",
        "ENTITLEMENT_WITHDRAWN",
        "TRUST_LIST_STALE",
        "LICENSE_SUSPENDED",
        "MANUAL",
    ]
    reason: str = Field(min_length=10, max_length=1000)
    evidence_ref: str = Field(min_length=3, max_length=256)


class SuspensionLiftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)
    evidence_ref: str = Field(min_length=3, max_length=256)


def _scope_view(s: Any) -> dict[str, Any]:
    return {
        k: getattr(s, k)
        for k in (
            "provider",
            "service_id",
            "scheme",
            "country",
            "subdivision",
            "purpose",
            "profession",
            "credential_class",
        )
    }


def _view(s: Any) -> dict[str, Any]:
    return {
        "suspension_id": s.suspension_id,
        "version": s.version,
        "status": s.status.value,
        "scope": _scope_view(s.scope),
        "trigger": s.trigger.value,
        "reason": s.reason,
        "evidence_ref": s.evidence_ref,
        "effective_at": s.effective_at.isoformat(),
        "recorded_by": s.recorded_by,
    }


def _impact_view(i: Any) -> dict[str, list[str]]:
    return {
        "markets": list(i.markets),
        "entitlements": list(i.entitlements),
        "certifications": list(i.certifications),
        "claims": list(i.claims),
    }


def _templates(request: Request) -> Any:
    given = getattr(request.app.state, "claim_templates", None)
    return given if given is not None else v.claim_templates()


def _audit_scope(s: Any) -> dict[str, Any]:
    return {k: val for k, val in _scope_view(s.scope).items() if val is not None}


@router.post("/v1/regulatory/suspensions", status_code=201)
async def suspend(body: SuspensionApplyIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        m = v.suspension_model
        s = m.Suspension(
            suspension_id=body.suspension_id,
            version=1,
            scope=body.scope.domain(),
            trigger=m.Trigger(body.trigger),
            status=m.Status.ACTIVE,
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            effective_at=now,
            recorded_by=str(op.principal_id),
            recorded_at=now,
        )
        saved, impact = await v.suspensions.apply_suspension(
            conn, s, now=now, templates=v.claim_model.latest(_templates(request))
        )
        details = {
            "trigger": saved.trigger.value,
            "scope": _audit_scope(saved),
            "evidence_ref": saved.evidence_ref,
            "impact_counts": {k: len(val) for k, val in _impact_view(impact).items()},
        }
        return _view(saved) | {"impact": _impact_view(impact)}, f"{saved.suspension_id}:v1", details

    return await actions.act(
        request,
        op,
        event="regulatory.suspension.applied",
        publish="verification.provider.scope_suspended",
        action="suspend",
        capability=APPLY,
        target_type="provider_suspension",
        target=body.suspension_id,
        work=work,
    )


@router.post("/v1/regulatory/suspensions/{suspension_id}:lift")
async def lift(
    suspension_id: SuspensionPath, body: SuspensionLiftIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        lifted = await v.suspensions.lift_suspension(
            conn,
            suspension_id,
            by=str(op.principal_id),
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            now=now,
        )
        details = {
            "trigger": lifted.trigger.value,
            "scope": _audit_scope(lifted),
            "evidence_ref": lifted.evidence_ref,
        }
        return _view(lifted), f"{suspension_id}:v{lifted.version}", details

    return await actions.act(
        request,
        op,
        event="regulatory.suspension.lifted",
        publish="verification.provider.scope_restored",
        action="lift",
        capability=LIFT,
        target_type="provider_suspension",
        target=suspension_id,
        work=work,
    )


@router.get("/v1/regulatory/suspensions")
async def list_suspensions(
    request: Request, op: OperatorDep, active: Annotated[bool, Query()] = False
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:suspensions")
        rows = await v.suspensions.list_suspensions(conn, at=datetime.now(UTC), active_only=active)
        return {
            "items": [{"current": _view(vs[-1]), "versions": [_view(x) for x in vs]} for vs in rows]
        }


@router.get("/v1/regulatory/suspensions/impact")
async def preview_impact(
    request: Request,
    op: OperatorDep,
    provider: Annotated[str, Query(min_length=1, max_length=128)],
    service_id: Annotated[str | None, Query(pattern=CODE)] = None,
    scheme: Annotated[str | None, Query(pattern=CODE)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
    purpose: Annotated[str | None, Query(pattern=CODE)] = None,
) -> dict[str, Any]:
    """What this scope would touch now, so people can decide before pulling the switch."""
    scope = v.suspension_model.Scope(
        provider, service_id=service_id, scheme=scheme, country=country, purpose=purpose
    )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:suspensions")
        impact = await v.suspensions.impact_of(
            conn, scope, templates=v.claim_model.latest(_templates(request))
        )
        return {"scope": _scope_view(scope), "impact": _impact_view(impact)}
