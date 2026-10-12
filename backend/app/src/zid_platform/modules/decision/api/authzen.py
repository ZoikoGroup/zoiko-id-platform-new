"""OpenID AuthZEN Authorization API 1.0 evaluation endpoint (ZID-API-001 §9; DEC-C1; FR-AUTHZ-005).

    POST /access/v1/evaluation

The caller is a PEP. Its tenant comes from the authenticated request context, never from the body
(INV-13); the subject is whoever the PEP is asking about. The response keeps AuthZEN's shape:
`decision` is true only for ALLOW, and Zoiko facts sit in `context` (reason codes, obligations,
policy release, revisions, valid_until). A PEP that can't fulfil a mandatory obligation must not act.

Authentication is Phase E: until its middleware sets `request.state.authn`, every call is refused
with 401 rather than evaluated without a trusted caller.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import uuid7
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.authority.ports import ValueLimit
from zid_platform.modules.decision.application.pdp_service import action_spec, evaluate_request
from zid_platform.modules.decision.domain.pdp import DecisionRequest
from zid_platform.modules.decision.domain.risk import RiskClass

router = APIRouter(tags=["authzen"])

CATALOG_FILE = Path(
    os.environ.get("ZID_ACTION_CATALOG")
    or Path(__file__).resolve().parents[7] / "contracts" / "action-catalog" / "actions.yaml"
)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_FILE.read_text(encoding="utf-8"))["actions"]  # type: ignore[no-any-return]


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=256)
    properties: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str  # string decimal, never binary floating point (API §1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class EvaluationContext(BaseModel):
    """Zoiko extensions: optional and namespaced by meaning; unknown keys are ignored (API §4)."""

    model_config = ConfigDict(extra="ignore")
    purpose: str | None = None
    risk_class: str | None = Field(default=None, pattern=r"^R[0-3]$")
    represented_principal_id: str | None = None
    mandate_id: str | None = None
    transaction_context_id: str | None = None
    tool_id: str | None = None
    value: Value | None = None


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a tenant field in the body is rejected, not ignored
    subject: Entity
    action: Action
    resource: Entity
    context: EvaluationContext = Field(default_factory=EvaluationContext)


def _value(v: Value | None) -> ValueLimit | None:
    if v is None:
        return None
    try:
        return ValueLimit(Decimal(v.amount), v.currency)
    except (InvalidOperation, ValueError) as exc:
        raise ZidError(
            "VALIDATION_FAILED",
            "context.value.amount must be a non-negative decimal string.",
            http_status=422,
        ) from exc


@router.post("/access/v1/evaluation")
async def evaluation(
    body: EvaluationRequest,
    request: Request,
    caller: Annotated[Caller, Depends(authenticated_caller)],
) -> dict[str, Any]:
    ctx, authn = caller.ctx, caller.authn
    assert ctx.tenant_id is not None
    engine: AsyncEngine | None = request.app.state.engine
    if engine is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE",
            "Decision state is unavailable.",
            http_status=503,
            retryable=True,
            retry_after_ms=1000,
        )
    decision_id, now = str(uuid7()), datetime.now(UTC)
    try:
        spec = action_spec(body.action.name, catalog())
    except KeyError:
        return {
            "decision": False,
            "context": {"decision_id": decision_id, "reason_codes": ["ACTION_UNKNOWN"]},
        }
    c = body.context
    resource_ref = f"{body.resource.type}:{body.resource.id}"
    req = DecisionRequest(
        decision_id=decision_id,
        tenant_id=ctx.tenant_id,
        action=spec,
        resource_id=resource_ref,
        represented_principal_id=c.represented_principal_id,
        purpose=c.purpose,
        tool_id=c.tool_id,
        at=now,
        requested_risk=RiskClass[c.risk_class] if c.risk_class else None,
    )
    async with tenant_transaction(engine, ctx) as conn:
        d = await evaluate_request(
            conn,
            req=req,
            executor_id=body.subject.id,
            authority_request=AuthorityRequest(
                body.action.name,
                resource_ref,
                now,
                purpose=c.purpose,
                tool=c.tool_id,
                value=_value(c.value),
            ),
            authn=authn,
            security_state=caller.security_state,
            catalog=catalog(),
        )
    return {
        "decision": d.outcome.value == "ALLOW",
        "context": {
            "decision_id": d.decision_id,
            "outcome": d.outcome.value,
            "reason_codes": list(d.reason_codes),
            "obligations": [{**o, "type": str(o["type"]).lower()} for o in d.obligations],
            "risk_class": d.risk_class.name,
            "policy_release_id": d.policy_release_id,
            "relation_revision": d.relation_revision,
            "security_revision": d.security_revision,
            "valid_until": d.valid_until.isoformat().replace("+00:00", "Z"),
        },
    }
