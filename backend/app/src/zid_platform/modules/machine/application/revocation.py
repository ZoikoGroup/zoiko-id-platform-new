"""Stopping agents by scope: a dry run, the stop, its record, and its closing (ZID-CP-WF07 v2.0 .10; A07-08; CR-169).

What a stop does is deny NEW calls: the agents are suspended (and everything below them), the live mandates they execute are paused, and the tool gateway, which reads that state on every call, refuses from the next call on. It
does not undo a call that already ran, and it is plain about the places it cannot reach. An AGENT or whole-company stop also uses the existing emergency freeze, whose restore needs two independent signers. Every stop is recorded
with an epoch; the agents it reached stay stopped until someone OTHER than the person who stopped them has closed it, and the person who stopped them can never reinstate them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import EvidenceClass
from zid_platform.modules.machine.application import agents as agent_service
from zid_platform.modules.machine.application import governance
from zid_platform.modules.machine.application.agents import _record
from zid_platform.modules.machine.domain import revocation as rv
from zid_platform.modules.machine.domain.agents import AgentStatus, MachineError
from zid_platform.modules.machine.infrastructure import agent_store, idp_store, machine_store, revocation_store
from zid_platform.modules.mandate.ports import mandates_executed_by, pause_mandates_of_executors


def _iso(x: datetime | None) -> str | None:
    return x.isoformat() if x else None


async def _targets(conn: AsyncConnection, scope: str, subject: str | None, version: str | None) -> list[str]:
    """The agents that are ACTIVE now and would be stopped, in the order they would be reached. For an agent, the agent and everything below it."""
    if scope == "AGENT":
        assert subject is not None
        agent = await machine_store.load_agent(conn, subject)
        if agent is None:
            raise MachineError("AGENT_NOT_FOUND", "no such agent")
        ids = [subject, *await agent_store.descendants(conn, subject)]
        out = []
        for i in ids:
            a = await machine_store.load_agent(conn, i)
            if a is not None and a.status is AgentStatus.ACTIVE:
                out.append(i)
        return out
    if scope == "SPONSOR":
        assert subject is not None
        try:
            uuid.UUID(subject)
        except ValueError as e:
            raise MachineError("REVOCATION_INVALID", "a sponsor is a principal id") from e
        return await revocation_store.active_agents_of_sponsor(conn, subject)
    if scope == "BLUEPRINT":
        assert subject is not None
        return await revocation_store.active_agents_of_blueprint(conn, subject, version)
    if scope == "ISSUER":
        assert subject is not None
        return await revocation_store.active_agents_of_issuer(conn, subject, version)
    return await revocation_store.active_agents(conn)


async def _servers(conn: AsyncConnection, agent_ids: list[str]) -> list[str]:
    registry = await machine_store.load_tools(conn)
    listed: set[str] = set()
    for i in agent_ids:
        a = await machine_store.load_agent(conn, i)
        if a is not None:
            listed |= set(a.allowed_tools)
    return [str(registry[t].server_principal_id) for t in listed if t in registry]


async def plan(conn: AsyncConnection, scope: str, subject: str | None, now: datetime | None = None) -> dict[str, Any]:
    """The dry run, for any scope: who would be stopped, what else would be paused, what would NOT be undone, and where the stop would be enforced. Nothing changes."""
    now = now or datetime.now(UTC)
    scope, subject, version = rv.check_shape(scope, subject)
    ids = await _targets(conn, scope, subject, version)
    mandates = await mandates_executed_by(conn, ids, limit=100) if ids else []
    from zid_platform.modules.machine.infrastructure import action_store

    return {
        "dry_run": True, "scope": scope, "subject": subject, "blueprint_version": version if scope == "BLUEPRINT" else None, "key_id": version if scope == "ISSUER" else None,
        "would_stop": {"agents": ids, "agent_count": len(ids), "live_mandates": [str(m["mandate_id"]) for m in mandates], "live_mandate_count": len(mandates), "grants_not_yet_spent": await action_store.live_grants_of(conn, ids, now) if ids else 0},
        "would_not_change": {
            "actions_already_running_or_unknown": await action_store.unresolved_actions_of(conn, ids) if ids else 0,
            "note": "A stop denies new calls. It cannot undo an action that already ran, and it does not release capacity held for an action whose outcome is unknown.",
        },
        "enforcement": rv.enforcement_report(await _servers(conn, ids), epoch=0),
        "as_of": now.isoformat(),
    }  # fmt: skip


def event_view(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(e["event_id"]), "epoch": e["epoch"], "scope": e["scope"], "subject": e["subject_ref"] or None, "reason": e["reason"], "actor": str(e["actor"]), "targets": e["targets"],
        "enforcement": e["enforcement"], "freeze_case_id": str(e["freeze_case_id"]) if e["freeze_case_id"] else None, "effective_at": _iso(e["effective_at"]), "state": "CLOSED" if e["closed_at"] else "OPEN",
        "closed_by": str(e["closed_by"]) if e["closed_by"] else None, "closed_at": _iso(e["closed_at"]), "close_note": e["close_note"],
    }  # fmt: skip


async def execute(conn: AsyncConnection, tenant_id: str, actor: str, scope: str, subject: str | None, reason: str | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    scope, subject, version = rv.check_shape(scope, subject)
    why = rv.clean_reason(reason)
    ids = await _targets(conn, scope, subject, version)
    if not ids:
        raise MachineError("REVOCATION_NO_TARGETS", "no active agent is reached by this stop")
    servers = await _servers(conn, ids)
    freeze_case: str | None = None
    if scope in ("AGENT", "TENANT"):
        got = await governance.pause(conn, tenant_id, actor, scope=scope, agent_id=subject if scope == "AGENT" else None, reason=why)  # the emergency freeze: its restore needs two independent signers
        freeze_case = str(got["case_id"])
    else:
        for i in ids:
            agent = await machine_store.load_agent(conn, i)
            if agent is not None and agent.status is AgentStatus.ACTIVE:  # one already stopped with its parent is not stopped twice
                await agent_service.suspend(conn, tenant_id, i, reason="REVOKED_BY_SCOPE", actor=actor)
    revoked_bindings = 0
    if scope == "ISSUER":  # the provider's trust is withdrawn too: its bindings stop proving anything
        for bid in await revocation_store.bindings_of_issuer(conn, subject or "", version):
            revoked_bindings += int(await idp_store.revoke(conn, bid, by=actor, now=now))
    paused = await pause_mandates_of_executors(conn, ids)
    epoch = await revocation_store.next_epoch(conn, tenant_id)
    event_id = str(uuid.uuid4())
    await revocation_store.insert_event(
        conn, tenant_id, event_id=event_id, epoch=epoch, scope=scope, subject_ref=(subject or "") + (f"{'#' if scope == 'ISSUER' else '@'}{version}" if version else ""), reason=why, actor=actor,
        targets={"agent_ids": ids, "agent_count": len(ids), "mandates_paused": paused, "bindings_revoked": revoked_bindings}, enforcement=rv.enforcement_report(servers, epoch=epoch), freeze_case_id=freeze_case, now=now,
    )  # fmt: skip
    await _record(
        conn, tenant_id, name="agent.revoked_by_scope", event=False, aggregate_type="agent", aggregate_id=ids[0], actor=actor, evidence=EvidenceClass.E3,
        data={"event_id": event_id, "epoch": epoch, "scope": scope, "agent_count": len(ids), "mandates_paused": paused},
    )  # fmt: skip
    got_event = await revocation_store.get_event(conn, event_id)
    assert got_event is not None
    return event_view(got_event)


async def close(conn: AsyncConnection, tenant_id: str, actor: str, event_id: str, note: str | None, now: datetime | None = None) -> dict[str, Any]:
    """The original cause is closed, by someone other than the person who stopped. This does not start the agents again: they are activated through their own gates, which now allow it."""
    now = now or datetime.now(UTC)
    e = await revocation_store.get_event(conn, event_id, lock=True)
    if e is None:
        raise MachineError("REVOCATION_NOT_FOUND", "no such stop")
    problems = rv.closing_problems(actor=actor, event=e)
    if problems:
        raise MachineError(problems[0], "a stop is closed once, by someone other than the person who made it")
    text_note = rv.clean_reason(note)
    await revocation_store.close_event(conn, event_id, by=actor, note=text_note, now=now)
    await _record(
        conn, tenant_id, name="agent.revocation_closed", event=False, aggregate_type="agent", aggregate_id=(e["targets"]["agent_ids"] or [event_id])[0], actor=actor, evidence=EvidenceClass.E3,
        data={"event_id": event_id, "epoch": e["epoch"]},
    )  # fmt: skip
    after = await revocation_store.get_event(conn, event_id)
    assert after is not None
    return event_view(after)


async def list_events(conn: AsyncConnection, *, open_only: bool) -> dict[str, Any]:
    return {"items": [event_view(e) for e in await revocation_store.list_events(conn, open_only=open_only, limit=100)]}


async def read_event(conn: AsyncConnection, event_id: str) -> dict[str, Any]:
    e = await revocation_store.get_event(conn, event_id)
    if e is None:
        raise MachineError("REVOCATION_NOT_FOUND", "no such stop")
    return event_view(e)


async def reinstate_blockers(conn: AsyncConnection, agent_id: str, actor: str) -> list[str]:
    """Called before an agent is activated: a stop that reached it must be closed by someone else, and its maker never reinstates it."""
    events = await revocation_store.events_covering(conn, agent_id)
    return rv.reinstate_problems(actor=actor, open_events=[e for e in events if e["closed_at"] is None], initiators=[str(e["actor"]) for e in events])
