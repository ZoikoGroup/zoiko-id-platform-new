"""A cross-cell transfer that the residency rules AND the plan must both allow (ZID-ENG-DEC-008 acceptance 08; CR-045).

The residency gate (tenant `authorize_transfer`) decides first and its answer is final when it says no: a plan can neither soften nor
replace a residency refusal, and a premium plan buys no transfer the rules do not allow. When residency allows, the plan is asked
for the feature that sends the data (`decide_in_region`). A plan that does not include it, a suspended subscription or an
entitlement that cannot be read turns the answer into a refusal with its own code, `ENTITLEMENT_REQUIRED` or `ENTITLEMENT_HOLD`, and
the field allowlist is withdrawn with it. In shadow mode a commercial refusal is reported and does not block, exactly as elsewhere.

The result has the residency gate's own shape, so it can be handed to any caller that already takes that gate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application.entitlement_resolver import decide_in_region

Gate = Callable[..., Awaitable[Any]]


def plan_aware_transfer_gate(residency_gate: Gate, *, feature_id: str, enforcement: str) -> Gate:
    async def gate(
        conn: AsyncConnection,
        tenant_id: str,
        *,
        data_class: str,
        destination_cell_id: str,
        purpose_code: str,
        **rest: Any,
    ) -> Any:
        answer = await residency_gate(
            conn,
            tenant_id,
            data_class=data_class,
            destination_cell_id=destination_cell_id,
            purpose_code=purpose_code,
            **rest,
        )
        if not answer.allowed:
            return answer  # residency said no: that stands, whatever the plan

        async def known(*_a: Any, **_k: Any) -> Any:
            return answer  # the residency answer, already given

        plan = await decide_in_region(
            conn,
            tenant_id,
            feature_id,
            gate=known,
            destination_cell_id=destination_cell_id,
            data_class=data_class,
            purpose_code=purpose_code,
            enforcement=enforcement,
        )
        if plan.allowed:
            return answer
        held = plan.decision.outcome == "HOLD"
        return replace(
            answer,
            allowed=False,
            code="ENTITLEMENT_HOLD" if held else "ENTITLEMENT_REQUIRED",
            rule_id=None,
            field_allowlist=(),
        )

    return gate
