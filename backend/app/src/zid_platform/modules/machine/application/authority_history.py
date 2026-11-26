"""The authority behind a tool call, written down when the call is allowed (ZID-CP-WF08 v2.0 E02; CR-170).

The edges are read from the live records in the SAME transaction that issues the grant, so the history is made at the time and cannot be a later guess.
Every edge carries its source, version, issuer, when it was obtained, when it applies and a digest of the facts it was built from. What the records do not
hold (for example a mandate has no revision number) is left at its honest default and the evidence module's rules decide whether the result is complete.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_evidence import hash_json

from zid_platform.modules.authority.ports import load_grants_for_grantee
from zid_platform.modules.evidence.ports import record_authority_snapshot
from zid_platform.modules.machine.infrastructure.machine_store import load_agent
from zid_platform.modules.mandate.ports import mandates_executed_by


def _digest(facts: dict[str, Any]) -> str:
    return str(hash_json({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in facts.items()}, alg="sha-256"))


async def edges_for_call(conn: AsyncConnection, *, agent_chain: Sequence[str], agent_id: str, mandate_id: str | None, at: datetime) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for grant in await load_grants_for_grantee(conn, agent_id):
        if grant.status.value != "ACTIVE":
            continue  # a grant that is not live is not authority for this call; listing it beside the live one would only manufacture a contradiction
        facts = {"grant_id": grant.grant_id, "revision": grant.revision, "grantor": grant.grantor_principal_id, "grantee": grant.grantee_principal_id, "represented": grant.represented_principal_id, "type": str(grant.grant_type), "status": grant.status.value, "issued_at": grant.issued_at}
        edges.append(
            {"edge_type": "GRANT", "edge_id": grant.grant_id, "version": grant.revision, "subject": agent_id, "state": grant.status.value, "issuer": grant.grantor_principal_id, "obtained_at": at, "effective_from": grant.issued_at, "effective_until": None, "evidence_digest": _digest(facts)}
        )  # fmt: skip
    for aid in dict.fromkeys(agent_chain or [agent_id]):
        profile = await load_agent(conn, aid)
        if profile is None:
            continue
        issuer = profile.registered_by or profile.operator_principal_id
        facts = {"agent": aid, "revision": profile.security_revision, "status": profile.status.value, "sponsor": profile.sponsor_principal_id or "", "operator": profile.operator_principal_id}
        edges.append({"edge_type": "AGENT", "edge_id": aid, "version": profile.security_revision, "subject": aid, "state": profile.status.value, "issuer": issuer, "obtained_at": at, "effective_from": at, "effective_until": profile.expires_at, "evidence_digest": _digest(facts)})  # fmt: skip
        if profile.sponsor_principal_id:
            edges.append(
                {"edge_type": "SPONSOR", "edge_id": profile.sponsor_principal_id, "version": profile.security_revision, "subject": aid, "state": "ACTIVE" if profile.status.value == "ACTIVE" else profile.status.value, "issuer": issuer, "obtained_at": at, "effective_from": at, "effective_until": None, "evidence_digest": _digest({**facts, "role": "sponsor"})}
            )  # fmt: skip
    if mandate_id:
        for m in await mandates_executed_by(conn, [agent_id], limit=50):
            if str(m["mandate_id"]) != mandate_id:
                continue
            facts = {"mandate": mandate_id, "status": str(m["status"]), "valid_to": m["valid_to"], "executor": agent_id}
            edges.append({"edge_type": "MANDATE", "edge_id": mandate_id, "version": 0, "subject": agent_id, "state": "ACTIVE" if str(m["status"]) == "ACTIVE" else str(m["status"]), "issuer": agent_id, "obtained_at": at, "effective_from": at, "effective_until": m["valid_to"], "evidence_digest": _digest(facts)})  # fmt: skip
    return edges


async def record_for_allowed_call(conn: AsyncConnection, tenant_id: str, *, decision_id: str, agent_id: str, agent_chain: Sequence[str], mandate_id: str | None, at: datetime) -> dict[str, Any]:
    edges = await edges_for_call(conn, agent_chain=agent_chain, agent_id=agent_id, mandate_id=mandate_id, at=at)
    return await record_authority_snapshot(conn, tenant_id, decision_ref=decision_id, subject_ref=agent_id, decision_at=at, edges=edges, created_by=None)
