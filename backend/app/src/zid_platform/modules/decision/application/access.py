"""One access decision, end to end (ZID-API-001 §9; DEC-C1; FR-AUTHZ-005): the policy decision point as a function.

The AuthZEN endpoint (`POST /access/v1/evaluation`) and the agent tool gateway (ZID-GAP-001 WP-11) both ask the
same question and must get the same answer, so the question is answered here once: resolve who is being decided
for, read a named mandate (read only), evaluate the request, apply the onboarding overlay, and meter the decision.

The caller's tenant comes from the authenticated context, never from the query (INV-13).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import uuid7
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.authority.ports import ValueLimit
from zid_platform.modules.commercial.ports import key_decision, record_billable
from zid_platform.modules.decision.application.pdp_service import action_spec, evaluate_request
from zid_platform.modules.decision.application.subjects import NO_PERSON, resolve_subject
from zid_platform.modules.decision.domain.pdp import DecisionRequest, MandateFact
from zid_platform.modules.decision.domain.risk import RiskClass
from zid_platform.modules.mandate.ports import check_for_decision
from zid_platform.modules.onboarding.ports import apply_overlay

if TYPE_CHECKING:
    from zid_platform.api_auth import Caller

log = logging.getLogger("zid.authzen")


class _Simulated(Exception):  # noqa: N818 - a signal to roll back, not an error
    """Raised on purpose to leave a transaction without committing it."""


@dataclass(frozen=True, slots=True)
class AccessQuery:
    """The question, without any transport shape: who, may do what, to which resource, in which context."""

    subject_type: str
    subject_id: str
    action: str
    resource_type: str
    resource_id: str
    purpose: str | None = None
    risk_class: str | None = None  # "R0".."R3"
    represented_principal_id: str | None = None
    mandate_id: str | None = None
    transaction_context_id: str | None = None
    tool_id: str | None = None
    value: tuple[str, str] | None = None  # (amount as a decimal string, ISO currency)
    incumbent_decision: str | None = None  # "ALLOW" | "DENY" (shadow / pilot)


def _value(v: tuple[str, str] | None) -> ValueLimit | None:
    if v is None:
        return None
    try:
        return ValueLimit(Decimal(v[0]), v[1])
    except (InvalidOperation, ValueError) as exc:
        raise ZidError(
            "VALIDATION_FAILED",
            "context.value.amount must be a non-negative decimal string.",
            http_status=422,
        ) from exc


async def evaluate_access(
    engine: AsyncEngine,
    caller: Caller,
    q: AccessQuery,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    simulate: bool = False,
) -> dict[str, Any]:
    """The AuthZEN-shaped answer: `decision` is true only for ALLOW; Zoiko facts sit in `context`.

    `simulate` runs the same evaluation and then rolls its whole transaction back: no receipt, no overlay record and no billable decision survive, and the answer says so (`simulated`, and a decision id that names nothing).
    Anything the evaluation wrote in the database is undone; only in-process counters are not."""
    ctx, authn = caller.ctx, caller.authn
    assert ctx.tenant_id is not None
    started = time.perf_counter()
    decision_id, now = str(uuid7()), datetime.now(UTC)
    try:
        spec = action_spec(q.action, catalog)
    except KeyError:
        return {
            "decision": False,
            "context": {"decision_id": decision_id, "reason_codes": ["ACTION_UNKNOWN"]},
        }
    resource_ref = f"{q.resource_type}:{q.resource_id}"
    req = DecisionRequest(
        decision_id=decision_id,
        tenant_id=ctx.tenant_id,
        action=spec,
        resource_id=resource_ref,
        represented_principal_id=q.represented_principal_id,
        purpose=q.purpose,
        tool_id=q.tool_id,
        at=now,
        workload_ref=caller.certificate_thumbprint,
        requested_risk=RiskClass[q.risk_class] if q.risk_class else None,
    )

    async def _decide(conn: AsyncConnection) -> tuple[Any, Any, str | None]:
        # The person decided for: a principal id as given, or a person the company's directory provisioned under the id its own system uses.
        resolved = await resolve_subject(conn, q.subject_type, q.subject_id)
        executor = resolved or NO_PERSON
        # A named mandate is checked here (read only: nothing is reserved or spent by asking). An unknown
        # mandate id is treated as no mandate at all, so a caller learns nothing about other mandates.
        mandate_fact: MandateFact | None = None
        if q.mandate_id:
            try:
                mandate_ref = str(uuid.UUID(q.mandate_id))
            except ValueError:
                mandate_ref = ""
            check = (
                await check_for_decision(
                    conn,
                    mandate_ref,
                    executor_id=executor,
                    represented_id=q.represented_principal_id,
                    action=q.action,
                    resource=resource_ref,
                    tool=q.tool_id,
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
            executor_id=executor,
            mandate=mandate_fact,
            authority_request=AuthorityRequest(
                q.action,
                resource_ref,
                now,
                purpose=q.purpose,
                tool=q.tool_id,
                value=_value(q.value),
            ),
            authn=authn,
            security_state=caller.security_state,
            catalog=catalog,
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
            if getattr(q, k) is not None
        )
        verdict = await apply_overlay(
            conn,
            tenant_id=ctx.tenant_id,
            tenant_status=caller.tenant_status,
            client_id=caller.client_id,
            subject=executor,
            action=q.action,
            risk=int(d.risk_class),
            decision_id=d.decision_id,
            outcome=d.outcome.value,
            decision_allow=d.outcome.value == "ALLOW",
            obligations=[str(o["type"]) for o in d.obligations],
            present_context=present,
            incumbent=q.incumbent_decision,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        # One final PDP result is one billable decision: allow or deny, never a retry (CR-025). Metering is not on the
        # trust path (SVC-14): a failure to record it is logged and left for the meter reconciliation to find, and
        # never changes or blocks the answer. The savepoint keeps the decision and its receipt in the transaction.
        try:
            if simulate:
                raise _Simulated  # asking "would this be allowed" is never a billable decision
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
        except _Simulated:
            pass
        except Exception:
            log.exception("billable decision not recorded for %s", d.decision_id)
        return d, verdict, resolved

    try:
        async with tenant_transaction(engine, ctx) as conn:
            d, verdict, resolved = await _decide(conn)
            if simulate:
                raise _Simulated  # leaving the block this way rolls the whole transaction back
    except _Simulated:
        pass
    return {
        "decision": verdict.allow,
        "context": {
            "decision_id": "simulated" if simulate else d.decision_id,
            **({"simulated": True} if simulate else {}),
            # the overlay can only ever turn an ALLOW into a DENY, never the reverse
            "outcome": "DENY"
            if d.outcome.value == "ALLOW" and not verdict.allow
            else d.outcome.value,
            "enforcement": verdict.enforcement.value,
            "reason_codes": [
                *d.reason_codes,
                *verdict.reason_codes,
                *(["SUBJECT_UNKNOWN"] if resolved is None else []),
            ],
            "obligations": [{**o, "type": str(o["type"]).lower()} for o in d.obligations],
            "risk_class": d.risk_class.name,
            "policy_release_id": d.policy_release_id,
            "relation_revision": d.relation_revision,
            "security_revision": d.security_revision,
            "valid_until": d.valid_until.isoformat().replace("+00:00", "Z"),
        },
    }
