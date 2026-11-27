"""What becomes of an authorized tool call, and the signed record of it (ZID-CP-WF07 v2.0 .07, .10, .11; CR-169; A07-06, A07-08, A07-09).

The gateway signs a grant; the tool's own server redeems it, acts, and reports what happened. Here the report is taken, the capacity held for the action is committed, given back or kept held, and a receipt is signed for the new revision.
Three things are never allowed: a report from anyone but the server the grant was for, a report on a grant nobody redeemed, and a success receipt while the outcome is unknown.

Also here: a dry run of stopping an agent. It lists what the stop would reach and, just as plainly, what it would not undo.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import peek_header

from zid_platform.modules.evidence.ports import EvidenceClass
from zid_platform.modules.machine.application.agents import _record
from zid_platform.modules.machine.domain import tool_action as ta
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.infrastructure import action_store, agent_store, machine_store
from zid_platform.modules.mandate.ports import (
    canonical_hash_of,
    mandates_executed_by,
    open_reservation_count,
    reservation_state,
)

Settle = Callable[
    [str, str], Awaitable[tuple[str, str | None]]
]  # (effect, reservation id) -> (reservation state, ledger refusal)
Sign = Callable[[Mapping[str, Any], str], str]
Verify = Callable[[str], Mapping[str, Any]]
DOWNSTREAM_MAX = 128


async def reservation_blockers(
    conn: AsyncConnection, *, mandate_id: str | None, reservation_id: str | None
) -> list[str]:
    """A call may name the capacity it will spend. It must be this mandate's, and still held."""
    if reservation_id is None:
        return []
    if mandate_id is None:
        return ["RESERVATION_NEEDS_MANDATE"]
    held = await reservation_state(conn, reservation_id)
    if held is None or str(held["mandate_id"]) != mandate_id:
        return ["RESERVATION_INVALID"]
    return [] if held["status"] == "RESERVED" else ["RESERVATION_NOT_HELD"]


async def record_authorized(
    conn: AsyncConnection, tenant_id: str, *, action_id: str, agent_id: str, tool_id: str, audience: str, resource: str, args_sha256: str, decision_id: str, chain_commitment: str,
    agent_chain: list[str], mandate_id: str | None, reservation_id: str | None, now: datetime,
) -> None:  # fmt: skip
    mandate_hash = (
        await canonical_hash_of(conn, mandate_id) if mandate_id else None
    )  # what the grantor confirmed, named on every receipt
    await action_store.insert_action(
        conn, tenant_id, action_id=action_id, agent_id=agent_id, tool_id=tool_id, audience=audience, resource=resource, args_sha256=args_sha256, decision_id=decision_id,
        chain_commitment=chain_commitment, agent_chain=agent_chain, mandate_id=mandate_id, mandate_hash=mandate_hash, reservation_id=reservation_id, now=now,
    )  # fmt: skip


async def record_dispatched(conn: AsyncConnection, action_id: str, now: datetime) -> bool:
    """The server redeemed the grant. An older grant, made before actions were recorded, simply has no action to move."""
    return await action_store.mark_dispatched(conn, action_id, now)


def action_view(a: Mapping[str, Any]) -> dict[str, Any]:
    state = ta.ActionState(a["state"])
    return {
        "action_id": str(a["action_id"]), "agent_id": str(a["agent_principal_id"]), "agent_chain": list(a["agent_chain"]), "tool_id": a["tool_id"], "resource": a["resource"],
        "arguments_sha256": a["args_sha256"], "decision_id": a["decision_id"], "mandate_id": str(a["mandate_id"]) if a["mandate_id"] else None,
        "reservation_id": str(a["reservation_id"]) if a["reservation_id"] else None, "state": state.value, "conclusion": ta.conclusion(state), "revision": a["revision"],
        "downstream_ref": a["downstream_ref"], "reported_at": a["reported_at"].isoformat() if a["reported_at"] else None, "created_at": a["created_at"].isoformat(),
        "updated_at": a["updated_at"].isoformat(),
    }  # fmt: skip


def receipt_view(r: Mapping[str, Any], verify: Verify | None = None) -> dict[str, Any]:
    out = {
        "receipt_id": str(r["receipt_id"]), "action_id": str(r["action_id"]), "revision": r["revision"], "state": r["state"], "digest": r["digest"], "payload": r["payload"],
        "signature": r["signature"], "key_id": r["key_id"], "issued_at": r["issued_at"].isoformat(),
        "verification": "Verify the signature (typ " + ta.RECEIPT_TYP + ") with the issuer's published public keys, then recompute the digest of the payload. Neither needs a Zoiko account.",
    }  # fmt: skip
    if verify is not None:
        try:
            claims = verify(r["signature"])
            out["signature_valid"] = not ta.receipt_problems(claims, r)
        except Exception:  # noqa: BLE001 - a receipt that does not verify is reported as such, never raised
            out["signature_valid"] = False
    return out


async def _issue_receipt(
    conn: AsyncConnection,
    tenant_id: str,
    action: Mapping[str, Any],
    *,
    reservation_state: str | None,
    sign: Sign,
    issuer: str,
    now: datetime,
) -> dict[str, Any]:
    payload = ta.receipt_payload(
        action,
        chain=action["agent_chain"],
        reservation_state=reservation_state,
        revision=action["revision"],
        issued_at=now,
        issuer=issuer,
    )
    token = sign(payload, ta.RECEIPT_TYP)
    digest = ta.payload_digest(payload)
    await action_store.add_receipt(
        conn, tenant_id, action_id=str(action["action_id"]), revision=action["revision"], state=action["state"], payload=payload, digest=digest, signature=token,
        key_id=str(peek_header(token).get("kid")) if token.count(".") == 2 else None, issued_at=now,
    )  # fmt: skip
    return payload


async def issue_first_receipt(
    conn: AsyncConnection, tenant_id: str, action_id: str, *, sign: Sign, issuer: str, now: datetime
) -> None:
    """The grant itself is the first statement: the call was authorized, and nothing has happened yet."""
    action = await action_store.get_action(conn, action_id)
    if action is not None:
        await _issue_receipt(
            conn, tenant_id, action, reservation_state=None, sign=sign, issuer=issuer, now=now
        )


async def finalize(
    conn: AsyncConnection, tenant_id: str, *, server_id: str, action_id: str, outcome: str, downstream_ref: str | None, settle: Settle, sign: Sign, issuer: str, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The tool's server reports what became of an action it redeemed. The same report twice is the same answer; a different one after a final outcome is refused."""
    now = now or datetime.now(UTC)
    action = await action_store.get_action(conn, action_id, lock=True)
    if action is None or str(action["audience"]) != server_id:
        raise MachineError(
            "TOOL_ACTION_NOT_FOUND", "no such action for this server"
        )  # the same answer for another server's action
    target = ta.check_outcome(outcome)
    ref = " ".join((downstream_ref or "").split())[:DOWNSTREAM_MAX] or None
    state = ta.ActionState(action["state"])
    if state is target:
        return await view(conn, action_id)  # a repeat of the same report changes nothing
    ta.check_transition(state, target)
    reservation_state: str | None = None
    refusal: str | None = None
    if action["reservation_id"]:
        effect = ta.ledger_effect(target)
        if effect == "NONE":
            held = await reservation_state(conn, str(action["reservation_id"]))  # type: ignore[misc, operator]
            reservation_state = str(held["status"]) if held else None
        else:
            reservation_state, refusal = await settle(effect, str(action["reservation_id"]))
    if not await action_store.set_state(
        conn,
        action_id,
        expected_revision=action["revision"],
        state=target.value,
        reported_by=server_id,
        downstream_ref=ref,
        now=now,
    ):
        raise MachineError("TOOL_ACTION_STATE_INVALID", "it changed while you were reporting")
    fresh = await action_store.get_action(conn, action_id)
    assert fresh is not None
    await _issue_receipt(
        conn,
        tenant_id,
        fresh,
        reservation_state=reservation_state,
        sign=sign,
        issuer=issuer,
        now=now,
    )
    await _record(
        conn, tenant_id, name=f"tool_action.{target.value.lower()}", event=False, aggregate_type="agent", aggregate_id=str(action["agent_principal_id"]), actor=server_id, actor_type="SERVICE",
        evidence=EvidenceClass.E3, outcome="SUCCESS",
        data={"action_id": action_id, "tool_id": action["tool_id"], "state": target.value, "reservation_state": reservation_state or "", "ledger_refusal": refusal or "", "decision_id": action["decision_id"]},
    )  # fmt: skip
    out = await view(conn, action_id)
    if refusal:
        out["ledger_note"] = (
            f"The capacity could not be moved ({refusal}); it was left as it was. The receipt says so."
        )
    return out


async def view(
    conn: AsyncConnection, action_id: str, verify: Verify | None = None
) -> dict[str, Any]:
    action = await action_store.get_action(conn, action_id)
    if action is None:
        raise MachineError("TOOL_ACTION_NOT_FOUND", "no such action")
    out = action_view(action)
    receipts = await action_store.receipts_for(conn, action_id)
    out["latest_receipt"] = receipt_view(receipts[-1], verify) if receipts else None
    out["receipt_revisions"] = [r["revision"] for r in receipts]
    return out


async def list_actions(
    conn: AsyncConnection, *, state: str | None, agent_id: str | None
) -> dict[str, Any]:
    if state is not None and state not in {s.value for s in ta.ActionState}:
        raise MachineError("TOOL_ACTION_INVALID", "unknown state")
    return {
        "items": [
            action_view(a)
            for a in await action_store.list_actions(
                conn, state=state, agent_id=agent_id, limit=100
            )
        ]
    }


async def receipts(
    conn: AsyncConnection, action_id: str, verify: Verify | None = None
) -> dict[str, Any]:
    if await action_store.get_action(conn, action_id) is None:
        raise MachineError("TOOL_ACTION_NOT_FOUND", "no such action")
    return {
        "action_id": action_id,
        "items": [
            receipt_view(r, verify) for r in await action_store.receipts_for(conn, action_id)
        ],
    }


# ------------------------------------------------------------------------------- the dry run of a stop


async def stop_impact(
    conn: AsyncConnection, agent_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """What stopping this agent would reach, without stopping anything. It names the agents, the live mandates and the grants not yet spent; it also names what a stop does NOT do, because a stop denies new calls and cannot
    undo one that already ran."""
    now = now or datetime.now(UTC)
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise MachineError("AGENT_NOT_FOUND", "no such agent")
    below = await agent_store.descendants(conn, agent_id)
    ids = [agent_id, *below]
    mandates = await mandates_executed_by(conn, ids, limit=100)
    held = (
        await open_reservation_count(conn, [str(m["mandate_id"]) for m in mandates])
        if mandates
        else 0
    )
    return {
        "dry_run": True,
        "agent_id": agent_id,
        "status": agent.status.value,
        "would_stop": {
            "agents": ids,
            "agent_count": len(ids),
            "live_mandates": [
                {
                    "mandate_id": str(m["mandate_id"]),
                    "executor": str(m["executor_principal_id"]),
                    "status": m["status"],
                }
                for m in mandates
            ],
            "live_mandate_count": len(mandates),
            "grants_not_yet_spent": await action_store.live_grants_of(conn, ids, now),
        },
        "would_not_change": {
            "actions_already_running_or_unknown": await action_store.unresolved_actions_of(
                conn, ids
            ),
            "capacity_still_held": held,
            "note": "A stop denies new calls. It cannot undo an action that already ran, and capacity held for an action whose outcome is unknown stays held until someone reconciles it.",
        },
        "enforcement": {
            "enforced_at": [
                "tool gateway: every authorize and every redeem re-reads the agent, its parents, its sponsor and the freeze"
            ],
            "not_enforced": [
                "a call that does not pass the gateway, and anything a downstream system finished before the stop"
            ],
        },
    }
