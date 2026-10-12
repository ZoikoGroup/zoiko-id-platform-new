"""Revocation cascades over the database (DEC-C2 §3-4; ZID-MANDATE-001 §5.1, §18; AT-07, MAND-02/03/20).

A root change (officer removed, root revoked/superseded) or the end of a person's qualifying
authority is applied to every dependent grant by continuity mode, then to the mandates built on them:

    PERSON_BOUND             -> grant REVOKED; mandates REVOKED
    ROLE_CONTINUOUS          -> grant REVALIDATION_PENDING; mandates PAUSED until revalidated
    ORGANIZATION_CONTINUOUS  -> grant REVALIDATION_PENDING (re-check organizational policy); mandates PAUSED
    CONTRACT_BOUND           -> unaffected by personnel change

No descendant keeps authority its ancestor lost. In production the organization module's
root-revoked event triggers this (event delivery is Phase D); tests call it directly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.domain.grants import (
    TERMINAL_GRANT_STATES,
    ContinuityEffect,
    Grant,
    GrantStatus,
    on_officeholder_change,
    on_personal_authority_ended,
)
from zid_platform.modules.authority.infrastructure.grant_store import load_chain, set_grant_status
from zid_platform.modules.mandate.ports import CascadeEffect, pause_or_revoke_for_grants

_GRANT_TARGET = {
    ContinuityEffect.INEFFECTIVE: GrantStatus.REVOKED,
    ContinuityEffect.REVALIDATE_ROLE: GrantStatus.REVALIDATION_PENDING,
    ContinuityEffect.REVALIDATE_ORGANIZATION: GrantStatus.REVALIDATION_PENDING,
}


async def _subtree(
    conn: AsyncConnection, *, root_id: str | None = None, grantor: str | None = None
) -> dict[str, Grant]:
    """Grants under a root (or issued by a person) plus every descendant, as domain objects."""
    rows = (
        await conn.execute(
            text("""
                WITH RECURSIVE tree(id) AS (
                    SELECT authority_grant_id FROM authz.authority_grant
                    WHERE (CAST(:root AS uuid) IS NOT NULL AND authority_root_assertion_id = CAST(:root AS uuid))
                       OR (CAST(:who AS uuid) IS NOT NULL AND grantor_principal_id = CAST(:who AS uuid))
                    UNION
                    SELECT g.authority_grant_id FROM authz.authority_grant g JOIN tree t ON g.parent_grant_id = t.id
                )
                SELECT id FROM tree
            """),
            {
                "root": uuid.UUID(root_id) if root_id else None,
                "who": uuid.UUID(grantor) if grantor else None,
            },
        )
    ).all()
    grants: dict[str, Grant] = {}
    for r in rows:
        grants |= await load_chain(conn, str(r.id))
    return grants


async def _apply(conn: AsyncConnection, targets: dict[str, GrantStatus]) -> dict[str, GrantStatus]:
    applied: dict[str, GrantStatus] = {}
    revoked: list[str] = []
    paused: list[str] = []
    for gid, target in targets.items():
        chain = await load_chain(conn, gid)
        current = chain[gid].status
        if current in TERMINAL_GRANT_STATES or current is target:
            continue
        if target is GrantStatus.REVALIDATION_PENDING and current is not GrantStatus.ACTIVE:
            continue
        await set_grant_status(conn, gid, target)
        applied[gid] = target
        (revoked if target is GrantStatus.REVOKED else paused).append(gid)
    await pause_or_revoke_for_grants(conn, revoked, CascadeEffect.REVOKE)
    await pause_or_revoke_for_grants(conn, paused, CascadeEffect.PAUSE)
    return applied


async def on_root_changed(conn: AsyncConnection, root_id: str) -> dict[str, GrantStatus]:
    """Officer removed/replaced or root revoked/superseded (MANDATE §5.1)."""
    grants = await _subtree(conn, root_id=root_id)
    effects = on_officeholder_change(root_id, grants)
    return await _apply(
        conn, {g: _GRANT_TARGET[e] for g, e in effects.items() if e in _GRANT_TARGET}
    )


async def on_person_authority_ended(
    conn: AsyncConnection, principal_id: str
) -> dict[str, GrantStatus]:
    """A person loses qualifying authority: their PERSON_BOUND grants and everything below them go."""
    grants = await _subtree(conn, grantor=principal_id)
    hit = on_personal_authority_ended(principal_id, grants)
    return await _apply(conn, dict.fromkeys(hit, GrantStatus.REVOKED))
