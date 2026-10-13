"""PostgreSQL store for authority grants and scopes (ZID-DATA-001 §7.1-7.2; ZID-MANDATE-001 §3, §6-7).

Every algebra dimension has its own column, never a JSON blob (AUTHZ §29). A NULL array is the
explicit unrestricted set (ANY); an empty array is "nothing". Resource and data scopes are
normalized, hashed and reused; a grant's scope can't be edited afterwards (database trigger), so a
material change is a superseding grant. Chains load with one recursive query.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.authority.domain.algebra import (
    ANY,
    Authority,
    SetScope,
    ValueLimit,
    Window,
)
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
    transition,
)


def _arr(s: SetScope) -> list[str] | None:
    return None if s is ANY else sorted(s)  # type: ignore[arg-type]


def _set(v: list[str] | None) -> SetScope:
    return ANY if v is None else frozenset(v)


async def _scope(
    conn: AsyncConnection, table: str, column: str, tenant_id: str, items: SetScope
) -> uuid.UUID:
    expr = "*" if items is ANY else "\n".join(sorted(items))  # type: ignore[arg-type]
    digest = hashlib.sha256(f"{column}\x00{expr}".encode()).digest()
    found = (
        await conn.execute(
            text(
                f"SELECT scope_id FROM authz.{table} WHERE tenant_id = :t AND expression_hash = :h AND version = 1"
            ),
            {"t": uuid.UUID(tenant_id), "h": digest},
        )
    ).first()
    if found:
        return found.scope_id  # type: ignore[no-any-return]
    scope_id = uuid.uuid4()
    await conn.execute(
        text(
            f"INSERT INTO authz.{table} (scope_id, tenant_id, {column}, canonical_expression, expression_hash) "
            "VALUES (:id, :t, :items, :e, :h) ON CONFLICT (tenant_id, expression_hash, version) DO NOTHING"
        ),
        {"id": scope_id, "t": uuid.UUID(tenant_id), "items": _arr(items), "e": expr, "h": digest},
    )
    row = (
        await conn.execute(
            text(
                f"SELECT scope_id FROM authz.{table} WHERE tenant_id = :t AND expression_hash = :h AND version = 1"
            ),
            {"t": uuid.UUID(tenant_id), "h": digest},
        )
    ).one()
    return row.scope_id  # type: ignore[no-any-return]


async def insert_grant(conn: AsyncConnection, g: Grant) -> None:
    a = g.authority
    rs = await _scope(conn, "resource_scope", "patterns", g.tenant_id, a.resources)
    ds = await _scope(conn, "data_scope", "categories", g.tenant_id, a.data)
    await conn.execute(
        text("""
            INSERT INTO authz.authority_grant (authority_grant_id, tenant_id, grant_type, grantor_principal_id,
                grantee_principal_id, represented_principal_id, authority_root_assertion_id, parent_grant_id,
                actions, resource_scope_id, purposes, data_scope_id, jurisdictions, tools, value_limit, currency,
                count_limit, data_limit_bytes, min_assurance, delegation_allowed, max_delegation_depth,
                continuity_mode, status, issued_at, valid_from, valid_to, revision)
            VALUES (:id, :t, CAST(:gt AS authz.grant_type), :gor, :gee, :rep, :root, :parent, :acts, :rs, :pur, :ds,
                    :jur, :tools, :vl, :cur, :cl, :dl, :ma, :da, :depth, CAST(:cm AS authz.continuity_mode),
                    CAST(:st AS authz.grant_status), :iss, :vf, :vt, :rev)
        """),
        {
            "id": uuid.UUID(g.grant_id),
            "t": uuid.UUID(g.tenant_id),
            "gt": g.grant_type.value,
            "gor": uuid.UUID(g.grantor_principal_id),
            "gee": uuid.UUID(g.grantee_principal_id),
            "rep": uuid.UUID(g.represented_principal_id),
            "root": uuid.UUID(g.root_id) if g.root_id else None,
            "parent": uuid.UUID(g.parent_grant_id) if g.parent_grant_id else None,
            "acts": _arr(a.actions),
            "rs": rs,
            "pur": _arr(a.purposes),
            "ds": ds,
            "jur": _arr(a.jurisdictions),
            "tools": _arr(a.tools),
            "vl": a.value.amount if a.value else None,
            "cur": a.value.currency if a.value else None,
            "cl": a.count,
            "dl": a.data_bytes,
            "ma": a.min_assurance,
            "da": g.delegation_allowed,
            "depth": g.max_delegation_depth,
            "cm": g.continuity_mode.value,
            "st": g.status.value,
            "iss": g.issued_at,
            "vf": a.window.valid_from,
            "vt": a.window.valid_to,
            "rev": g.revision,
        },
    )
    await emit(
        conn,
        name=GRANT_EVENTS[g.status],
        tenant_id=g.tenant_id,
        aggregate_type="authority_grant",
        aggregate_id=g.grant_id,
        aggregate_version=g.revision,
        data={
            "authority_grant_id": g.grant_id,
            "status": g.status.value,
            "parent_grant_id": g.parent_grant_id,
            "authority_root_assertion_id": g.root_id,
        },
    )


_SELECT = """
    SELECT g.*, r.patterns, d.categories FROM authz.authority_grant g
    JOIN authz.resource_scope r ON r.scope_id = g.resource_scope_id
    JOIN authz.data_scope d ON d.scope_id = g.data_scope_id
"""


def _grant(row: Row[Any]) -> Grant:
    m = row._mapping
    value = (
        ValueLimit(m["value_limit"], m["currency"].strip())
        if m["value_limit"] is not None
        else None
    )
    return Grant(
        grant_id=str(m["authority_grant_id"]),
        tenant_id=str(m["tenant_id"]),
        grant_type=GrantType(m["grant_type"]),
        grantor_principal_id=str(m["grantor_principal_id"]),
        grantee_principal_id=str(m["grantee_principal_id"]),
        represented_principal_id=str(m["represented_principal_id"]),
        authority=Authority(
            actions=_set(m["actions"]),
            resources=_set(m["patterns"]),
            purposes=_set(m["purposes"]),
            data=_set(m["categories"]),
            jurisdictions=_set(m["jurisdictions"]),
            tools=_set(m["tools"]),
            value=value,
            count=m["count_limit"],
            data_bytes=m["data_limit_bytes"],
            min_assurance=m["min_assurance"],
            window=Window(m["valid_from"], m["valid_to"]),
        ),
        continuity_mode=ContinuityMode(m["continuity_mode"]),
        status=GrantStatus(m["status"]),
        issued_at=m["issued_at"],
        root_id=str(m["authority_root_assertion_id"]) if m["authority_root_assertion_id"] else None,
        parent_grant_id=str(m["parent_grant_id"]) if m["parent_grant_id"] else None,
        delegation_allowed=m["delegation_allowed"],
        max_delegation_depth=m["max_delegation_depth"],
        revision=m["revision"],
    )


async def load_chain(conn: AsyncConnection, grant_id: str) -> dict[str, Grant]:
    """The grant and all its ancestors (bounded: cycles stop at 64 hops; the domain rejects them)."""
    rows = (
        await conn.execute(
            text(f"""
                WITH RECURSIVE chain(id, depth) AS (
                    SELECT CAST(:id AS uuid), 0
                    UNION ALL
                    SELECT g.parent_grant_id, c.depth + 1 FROM authz.authority_grant g
                    JOIN chain c ON g.authority_grant_id = c.id
                    WHERE g.parent_grant_id IS NOT NULL AND c.depth < 64
                )
                {_SELECT} WHERE g.authority_grant_id IN (SELECT id FROM chain)
            """),
            {"id": uuid.UUID(grant_id)},
        )
    ).all()
    return {str(r.authority_grant_id): _grant(r) for r in rows}


async def load_grants_for_grantee(conn: AsyncConnection, grantee_id: str) -> list[Grant]:
    rows = (
        await conn.execute(
            text(f"{_SELECT} WHERE g.grantee_principal_id = :p"), {"p": uuid.UUID(grantee_id)}
        )
    ).all()
    return [_grant(r) for r in rows]


async def set_grant_status(conn: AsyncConnection, grant_id: str, target: GrantStatus) -> Grant:
    chain = await load_chain(conn, grant_id)
    if grant_id not in chain:
        raise AuthorityError("AUTHORITY_MISSING")
    current = chain[grant_id]
    transition(current.status, target)
    result = await conn.execute(
        text(
            "UPDATE authz.authority_grant SET status = CAST(:s AS authz.grant_status), revision = revision + 1, "
            "row_version = row_version + 1 WHERE authority_grant_id = :id AND status = CAST(:old AS authz.grant_status)"
        ),
        {"s": target.value, "id": uuid.UUID(grant_id), "old": current.status.value},
    )
    if result.rowcount != 1:
        raise AuthorityError("CONCURRENT_MODIFICATION")
    await emit(
        conn,
        name=GRANT_EVENTS[target],
        tenant_id=current.tenant_id,
        aggregate_type="authority_grant",
        aggregate_id=grant_id,
        aggregate_version=current.revision + 1,
        data={
            "authority_grant_id": grant_id,
            "status": target.value,
            "previous_status": current.status.value,
        },
    )
    return replace(current, status=target, revision=current.revision + 1)


GRANT_EVENTS = {
    GrantStatus.ACTIVE: "authority.grant.activated",
    GrantStatus.REVOKED: "authority.grant.revoked",
    GrantStatus.SUSPENDED: "authority.grant.suspended",
    GrantStatus.SUPERSEDED: "authority.grant.superseded",
    GrantStatus.REVALIDATION_PENDING: "authority.chain.invalidated",
    GrantStatus.EXPIRED: "authority.grant.revoked",
    GrantStatus.PENDING_APPROVAL: "authority.grant.created",
    GrantStatus.DRAFT: "authority.grant.created",
}


async def holds_any_action(
    conn: AsyncConnection, grantee_principal_id: str, actions: list[str], now: datetime
) -> bool:
    """Whether the principal holds a currently valid grant covering any of `actions` (NULL = ANY).

    Used to classify accounts (e.g. a privileged administrator recovers under RC-3, AUTH §14.1);
    it is a coarse classification, never an authorization decision."""
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM authz.authority_grant WHERE grantee_principal_id = :g "
                "AND status = 'ACTIVE' AND (valid_to IS NULL OR valid_to > :now) "
                "AND (actions IS NULL OR actions && :a) LIMIT 1"
            ),
            {"g": uuid.UUID(grantee_principal_id), "now": now, "a": actions},
        )
    ).first()
    return row is not None
