"""Would this tool call be allowed? A rehearsal of the real gateway (ZID-CP-WF07 v2.0 .05; A07-05).

The rehearsal runs the SAME code that decides a real call, against the same data, and then throws its transaction away. So what it answers is what the gateway would answer, and what it writes is nothing:
no grant, no action, no receipt, no audit record, no governance event and no billable decision survive. The decision service is asked in its own simulation mode, which does the same. Capacity is read, never reserved: a
rehearsal that said "it fits" holds nothing, and another spend can still take the room.

A question that cannot be answered (the policy decision failed) is UNAVAILABLE. It is never turned into an ALLOW.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.application import tool_gateway as gateway
from zid_platform.modules.machine.domain import tool_action as ta
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.mandate.ports import capacity_check


class Rollback(Exception):
    def __init__(self, view: dict[str, Any]) -> None:
        super().__init__("rehearsal complete")
        self.view = view


async def rehearse(
    conn: AsyncConnection, tenant_id: str, call: gateway.ToolCall, *, decide: gateway.Decide, sign: gateway.Sign, issuer: str, action_catalogued: Callable[[str], bool], now: datetime | None = None,
) -> None:  # fmt: skip
    """Always ends by raising `Rollback` with the answer. The caller owns the transaction and lets the exception undo it."""
    now = now or datetime.now(UTC)
    codes: list[str] = []
    allowed, unavailable, chain, approval_context = False, False, (), None
    try:
        result = await gateway.authorize_tool_call(
            conn,
            tenant_id,
            call,
            decide=decide,
            sign=sign,
            issuer=issuer,
            action_catalogued=action_catalogued,
            now=now,
        )
        allowed, codes, chain, approval_context = (
            result.allowed,
            list(result.codes),
            result.chain,  # type: ignore[assignment]
            result.approval_context,
        )
    except MachineError as e:
        codes = [e.code]  # a malformed or unknown request is a refusal with its reason
    except Exception:  # noqa: BLE001 - the decision service itself failed: say so, never guess
        unavailable, codes = True, ["POLICY_UNAVAILABLE"]
    capacity: Mapping[str, Any] | None = None
    if call.mandate_id and allowed:
        capacity = await capacity_check(conn, call.mandate_id, value=call.value, now=now)
    decision, reasons = ta.preflight_decision(
        allowed=allowed, codes=codes, capacity=capacity, unavailable=unavailable
    )
    view = {
        "decision": decision,
        "reason_codes": reasons,
        "canonical_request_hash": ta.canonical_request_hash(
            tool_id=call.tool_id, resource=f"{call.resource_type}:{call.resource_id}", arguments_digest=call.arguments_digest, represented_principal_id=call.represented_principal_id,
            mandate_id=call.mandate_id, reservation_id=call.reservation_id, purpose=call.purpose, value=call.value,
        ),  # fmt: skip  # noqa: RUF028
        "agent_chain": list(chain),
        "approval_context": approval_context if decision == "APPROVAL_REQUIRED" else None,
        "capacity": dict(capacity) if capacity is not None else None,
        "simulated": True,
        "side_effects": {"grant_issued": False, "action_recorded": False, "receipt_written": False, "audit_record_written": False, "billable_decision": False, "capacity_reserved": False},
        "limits": "This is a rehearsal. It does not hold capacity, and a real call is decided again at the moment it is made.",
        "as_of": now.isoformat(),
    }  # fmt: skip  # noqa: RUF028
    raise Rollback(view)
