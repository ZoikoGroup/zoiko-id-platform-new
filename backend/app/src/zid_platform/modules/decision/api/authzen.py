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

import logging
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

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
from zid_platform.modules.commercial.ports import key_decision, record_billable
from zid_platform.modules.decision.application.pdp_service import action_spec, evaluate_request
from zid_platform.modules.decision.domain.pdp import DecisionRequest, MandateFact
from zid_platform.modules.decision.domain.risk import RiskClass
from zid_platform.modules.mandate.ports import check_for_decision
from zid_platform.modules.onboarding.ports import apply_overlay

log = logging.getLogger("zid.authzen")
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
    started = time.perf_counter()
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
        workload_ref=caller.certificate_thumbprint,
        requested_risk=RiskClass[c.risk_class] if c.risk_class else None,
    )
    async with tenant_transaction(engine, ctx) as conn:
        # A named mandate is checked here (read only: nothing is reserved or spent by asking). An unknown
        # mandate id is treated as no mandate at all, so a caller learns nothing about other mandates.
        mandate_fact: MandateFact | None = None
        if c.mandate_id:
            try:
                mandate_ref = str(uuid.UUID(c.mandate_id))
            except ValueError:
                mandate_ref = ""
            check = (
                await check_for_decision(
                    conn,
                    mandate_ref,
                    executor_id=body.subject.id,
                    represented_id=c.represented_principal_id,
                    action=body.action.name,
                    resource=resource_ref,
                    tool=c.tool_id,
                    now=now,
                )
                if mandate_ref
                else None
            )
            if check is not None:
                mandate_fact = MandateFact(
                    check.mandate_id,
                    check.active,
                    check.in_scope,
                    check.tool_allowed,
                    check.valid_until,
                )
        d = await evaluate_request(
            conn,
            req=req,
            executor_id=body.subject.id,
            mandate=mandate_fact,
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
        present = frozenset(
            k
            for k in (
                "purpose",
                "represented_principal_id",
                "mandate_id",
                "transaction_context_id",
                "tool_id",
                "value",
            )
            if getattr(c, k) is not None
        )
        verdict = await apply_overlay(
            conn,
            tenant_id=ctx.tenant_id,
            tenant_status=caller.tenant_status,
            client_id=caller.client_id,
            subject=body.subject.id,
            action=body.action.name,
            risk=int(d.risk_class),
            decision_id=d.decision_id,
            outcome=d.outcome.value,
            decision_allow=d.outcome.value == "ALLOW",
            obligations=[str(o["type"]) for o in d.obligations],
            present_context=present,
            incumbent=c.incumbent_decision,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        # One final PDP result is one billable decision: allow or deny, never a retry (CR-025). Metering is not on the
        # trust path (SVC-14): a failure to record it is logged and left for the meter reconciliation to find, and
        # never changes or blocks the answer. The savepoint keeps the decision and its receipt in the transaction.
        try:
            async with conn.begin_nested():
                await record_billable(
                    conn,
                    tenant_id=ctx.tenant_id,
                    meter="POLICY_DECISION",
                    key=key_decision(d.decision_id),
                    at=now,
                    source_request_id=ctx.request_id,
                    principal_class=None,
                )
        except Exception:
            log.exception("billable decision not recorded for %s", d.decision_id)
    return {
        "decision": verdict.allow,
        "context": {
            "decision_id": d.decision_id,
            # the overlay can only ever turn an ALLOW into a DENY, never the reverse
            "outcome": "DENY"
            if d.outcome.value == "ALLOW" and not verdict.allow
            else d.outcome.value,
            "enforcement": verdict.enforcement.value,
            "reason_codes": [*d.reason_codes, *verdict.reason_codes],
            "obligations": [{**o, "type": str(o["type"]).lower()} for o in d.obligations],
            "risk_class": d.risk_class.name,
            "policy_release_id": d.policy_release_id,
            "relation_revision": d.relation_revision,
            "security_revision": d.security_revision,
            "valid_until": d.valid_until.isoformat().replace("+00:00", "Z"),
        },
    }
