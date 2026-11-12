"""OpenID AuthZEN Authorization API 1.0 evaluation endpoint (ZID-API-001 §9; DEC-C1; FR-AUTHZ-005).

    POST /access/v1/evaluation

The caller is a PEP. Its tenant comes from the authenticated request context, never from the body
(INV-13); the subject is whoever the PEP is asking about. The response keeps AuthZEN's shape:
`decision` is true only for ALLOW, and Zoiko facts sit in `context` (reason codes, obligations,
policy release, revisions, valid_until). A PEP that can't fulfil a mandatory obligation must not act.

Overlay (G-W4): `context.enforcement` is ENFORCE or EVALUATE_ONLY. While the tenant is in SHADOW,
or in PILOT outside an active scope, the decision is evaluated and recorded next to
`context.incumbent_decision`, but the incumbent still decides. An obligation the PEP's registered
profile cannot enforce, or an R2/R3 request without the context that profile requires, is DENY.

Authentication is Phase E: until its middleware sets `request.state.authn`, every call is refused
with 401 rather than evaluated without a trusted caller.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncEngine
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.decision.application.access import AccessQuery, evaluate_access

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
    # Shadow/pilot (OVERLAY §18): what the incumbent system decided for this very request
    incumbent_decision: Literal["ALLOW", "DENY"] | None = None


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a tenant field in the body is rejected, not ignored
    subject: Entity
    action: Action
    resource: Entity
    context: EvaluationContext = Field(default_factory=EvaluationContext)


@router.post("/access/v1/evaluation")
async def evaluation(
    body: EvaluationRequest,
    request: Request,
    caller: Annotated[Caller, Depends(authenticated_caller)],
) -> dict[str, Any]:
    assert caller.ctx.tenant_id is not None
    engine: AsyncEngine | None = request.app.state.engine
    if engine is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE",
            "Decision state is unavailable.",
            http_status=503,
            retryable=True,
            retry_after_ms=1000,
        )
    c = body.context
    return await evaluate_access(
        engine,
        caller,
        AccessQuery(
            subject_type=body.subject.type,
            subject_id=body.subject.id,
            action=body.action.name,
            resource_type=body.resource.type,
            resource_id=body.resource.id,
            purpose=c.purpose,
            risk_class=c.risk_class,
            represented_principal_id=c.represented_principal_id,
            mandate_id=c.mandate_id,
            transaction_context_id=c.transaction_context_id,
            tool_id=c.tool_id,
            value=(c.value.amount, c.value.currency) if c.value else None,
            incumbent_decision=c.incumbent_decision,
        ),
        catalog=catalog(),
    )
