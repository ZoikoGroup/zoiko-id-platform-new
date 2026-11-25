"""What happens to a tool call after the gateway signs its grant (ZID-CP-WF07 v2.0 .07, .10, .11; CR-169). Pure domain, no I/O.

The gateway answers "may this agent call this tool now". It says nothing about what the tool's server then did. This module is the other half: the life of one authorized action, and the receipt that records it.

    AUTHORIZED   the gateway signed a grant for this exact call
    DISPATCHED   the tool's server redeemed the grant, so it is about to act
    EXECUTED     the server reports the action completed
    FAILED       the server reports it did not happen
    UNKNOWN      the server cannot say (a timeout, a lost reply). It is NOT a failure: any capacity held for the action stays held
    COMPENSATED  the server reports it undid an executed action (recorded, never assumed)

Only the tool's own server reports an outcome, and only for an action it redeemed. A receipt states what the gateway decided and what the server reported. It does not prove the real-world effect, and it never reads as a
success while the outcome is unknown. Nothing here reads free text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.machine.domain.agents import MachineError

RECEIPT_TYP = "zid-action-receipt+jwt"
RECEIPT_VERSION = "1"
LIMITS = "This receipt records what the gateway decided and what the tool's server reported. It does not prove the real-world effect, and a signature proves only that Zoiko iD issued it."


class ActionState(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    DISPATCHED = "DISPATCHED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    COMPENSATED = "COMPENSATED"


S = ActionState
TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    S.AUTHORIZED: frozenset({S.DISPATCHED}),
    S.DISPATCHED: frozenset({S.EXECUTED, S.FAILED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.EXECUTED, S.FAILED}),  # the execution owner reconciles and says which
    S.EXECUTED: frozenset({S.COMPENSATED}),
    S.FAILED: frozenset(),
    S.COMPENSATED: frozenset(),
}
REPORTED = frozenset({S.EXECUTED, S.FAILED, S.UNKNOWN, S.COMPENSATED})  # what a tool's server may report


def check_outcome(value: str) -> ActionState:
    try:
        s = ActionState(value)
    except ValueError as e:
        raise MachineError("TOOL_ACTION_INVALID", "an outcome is EXECUTED, FAILED, UNKNOWN or COMPENSATED") from e
    if s not in REPORTED:
        raise MachineError("TOOL_ACTION_INVALID", "an outcome is EXECUTED, FAILED, UNKNOWN or COMPENSATED")
    return s


def check_transition(src: ActionState, dst: ActionState) -> ActionState:
    if dst not in TRANSITIONS[src]:
        if src is S.AUTHORIZED:
            raise MachineError("TOOL_ACTION_NOT_DISPATCHED", "the grant was never redeemed, so there is nothing to report on")
        raise MachineError("TOOL_ACTION_STATE_INVALID", f"an action that is {src.value.lower()} cannot become {dst.value.lower()}")
    return dst


def ledger_effect(outcome: ActionState) -> str:
    """What the capacity held for the action does. EXECUTED commits it, FAILED gives it back, UNKNOWN keeps holding it until someone reconciles. COMPENSATED does not return capacity on its own: whether an undone action restores a cap is a policy decision for people."""
    return {S.EXECUTED: "COMMIT", S.FAILED: "RELEASE", S.UNKNOWN: "HOLD", S.COMPENSATED: "NONE"}[outcome]


def flags(state: ActionState, reservation_state: str | None) -> dict[str, bool]:
    """The distinct things a receipt can say. Authorized is not executed, executed is not committed, and unknown is none of the others."""
    return {
        "authorized": True,
        "executed": state in (S.EXECUTED, S.COMPENSATED),
        "committed": reservation_state == "COMMITTED",
        "failed": state is S.FAILED,
        "unknown": state is S.UNKNOWN,
        "compensated": state is S.COMPENSATED,
    }


def conclusion(state: ActionState) -> str:
    """One word, and never a success unless the server reported one."""
    return {
        S.AUTHORIZED: "AUTHORIZED_NOT_YET_RUN",
        S.DISPATCHED: "IN_PROGRESS",
        S.EXECUTED: "EXECUTED_AS_REPORTED",
        S.FAILED: "NOT_EXECUTED",
        S.UNKNOWN: "OUTCOME_UNKNOWN",
        S.COMPENSATED: "UNDONE_AS_REPORTED",
    }[state]


def receipt_payload(
    action: Mapping[str, Any], *, chain: Sequence[str], reservation_state: str | None, revision: int, issued_at: datetime, issuer: str
) -> dict[str, Any]:  # fmt: skip
    """The canonical content of one receipt. Everything in it is an identifier, a digest, a state or a time: no arguments, no names, no personal data."""
    state = ActionState(action["state"])
    return {
        "typ": RECEIPT_TYP,
        "v": RECEIPT_VERSION,
        "iss": issuer,
        "receipt_for": str(action["action_id"]),
        "revision": revision,
        "tenant_id": str(action["tenant_id"]),
        "agent_chain": list(chain),
        "chain_commitment": action["chain_commitment"],
        "tool_id": action["tool_id"],
        "resource": action["resource"],
        "arguments_sha256": action["args_sha256"],
        "decision_id": action["decision_id"],
        "mandate_id": str(action["mandate_id"]) if action["mandate_id"] else None,
        "mandate_hash": action["mandate_hash"],
        "reservation_id": str(action["reservation_id"]) if action["reservation_id"] else None,
        "reservation_state": reservation_state,
        "state": state.value,
        "conclusion": conclusion(state),
        "outcomes": flags(state, reservation_state),
        "downstream_ref": action["downstream_ref"],
        "issued_at": issued_at.isoformat(),
        "limits": LIMITS,
    }


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def receipt_problems(token_claims: Mapping[str, Any], stored: Mapping[str, Any]) -> list[str]:
    """Why a presented receipt does not match what the platform recorded: a changed value, a different action, an old revision."""
    out: list[str] = []
    if payload_digest(token_claims) != stored["digest"]:
        out.append("RECEIPT_ALTERED")
    if str(token_claims.get("receipt_for")) != str(stored["action_id"]):
        out.append("RECEIPT_FOR_ANOTHER_ACTION")
    if int(token_claims.get("revision", -1)) != int(stored["revision"]):
        out.append("RECEIPT_REVISION_MISMATCH")
    return out


# ------------------------------------------------------------------------------- a request, named exactly


def canonical_request_hash(
    *, tool_id: str, resource: str, arguments_digest: str, represented_principal_id: str | None, mandate_id: str | None, reservation_id: str | None, purpose: str | None, value: tuple[str, str] | None
) -> str:  # fmt: skip
    """One name for one exact request. A different payee, amount, resource or mandate is a different hash; the same request is the same hash whoever asks and whenever. Approval, simulation and receipt can all point at it."""
    body = {
        "tool": tool_id, "resource": resource, "arguments": arguments_digest, "on_behalf_of": represented_principal_id, "mandate": mandate_id, "reservation": reservation_id,
        "purpose": purpose, "value": list(value) if value else None,
    }  # fmt: skip
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preflight_decision(*, allowed: bool, codes: Sequence[str], capacity: Mapping[str, Any] | None, unavailable: bool) -> tuple[str, list[str]]:
    """One of ALLOW, DENY, APPROVAL_REQUIRED, UNAVAILABLE, and the codes behind it. A question that could not be answered is UNAVAILABLE and is never turned into an ALLOW; a spend that would not fit is a DENY even if the policy said yes."""
    if unavailable:
        return "UNAVAILABLE", list(codes) or ["POLICY_UNAVAILABLE"]
    if not allowed:
        return ("APPROVAL_REQUIRED" if "HUMAN_APPROVAL_REQUIRED" in codes else "DENY"), list(codes)
    if capacity is not None and not capacity.get("available"):
        return "DENY", [str(capacity.get("reason") or "MANDATE_NOT_ACTIVE")]
    return "ALLOW", []
