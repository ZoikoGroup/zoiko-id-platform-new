"""Authority use cases over the database (ZID-MANDATE-001 §2.1, §7; ZID-AUTHZ-001 §6.1).

Roots come from the organization module through its port; this module never reads org.* tables.
A root that is STALE or CONFLICTED reaches the resolver as stale (fresh_until in the past), and an
unusable root as revoked, so the domain rules in `grants.py` decide exactly as they do in tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.domain.algebra import (
    ANY,
    Authority,
    Request,
    ValueLimit,
    Window,
)
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    AuthorityRoot,
    Decision,
    Grant,
    GrantStatus,
    Resolution,
    RootAssurance,
    RootStatus,
    authorize,
    resolve_chain,
    validate_child_grant,
)
from zid_platform.modules.authority.infrastructure.grant_store import (
    insert_grant,
    load_chain,
    load_grants_for_grantee,
    set_grant_status,
)
from zid_platform.modules.organization.ports import RootReader, RootView, read_root


def as_authority_root(v: RootView) -> AuthorityRoot:
    fresh_until = v.valid_from if v.stale else v.fresh_until
    value = (
        ValueLimit(v.value_limit, v.currency) if v.value_limit is not None and v.currency else None
    )
    return AuthorityRoot(
        root_id=v.root_id,
        tenant_id=v.tenant_id,
        organization_id=v.organization_id,
        holder_principal_id=v.holder_principal_id,
        assurance=RootAssurance(v.assurance),
        status=RootStatus.ACTIVE if (v.usable or v.stale) else RootStatus.REVOKED,
        window=Window(v.valid_from, v.valid_to),
        fresh_until=fresh_until,
        authority=Authority(
            actions=v.scope_actions if v.scope_actions is not None else ANY, value=value
        ),
    )


async def _roots_for(
    conn: AsyncConnection, tenant_id: str, grants: Iterable[Grant], reader: RootReader
) -> dict[str, AuthorityRoot]:
    out: dict[str, AuthorityRoot] = {}
    for g in grants:
        if g.root_id and g.root_id not in out:
            view = await reader(conn, tenant_id, g.root_id)
            if view is not None:
                out[g.root_id] = as_authority_root(view)
    return out


async def resolve(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    grant_id: str,
    risk_level: int,
    now: datetime,
    reader: RootReader = read_root,
) -> Resolution:
    chain = await load_chain(conn, grant_id)
    roots = await _roots_for(conn, tenant_id, chain.values(), reader)
    return resolve_chain(
        grant_id, tenant_id=tenant_id, grants=chain, roots=roots, now=now, risk_level=risk_level
    )


async def authorize_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    grantee_id: str,
    request: Request,
    risk_level: int,
    reader: RootReader = read_root,
) -> Decision:
    """Default deny; one complete chain must cover the whole request (no stitching)."""
    candidates = await load_grants_for_grantee(conn, grantee_id)
    grants: dict[str, Grant] = {}
    for g in candidates:
        grants |= await load_chain(conn, g.grant_id)
    roots = await _roots_for(conn, tenant_id, grants.values(), reader)
    return authorize(
        request,
        [g.grant_id for g in candidates],
        tenant_id=tenant_id,
        grants=grants,
        roots=roots,
        risk_level=risk_level,
    )


async def issue_grant(
    conn: AsyncConnection,
    grant: Grant,
    *,
    risk_level: int,
    now: datetime,
    reader: RootReader = read_root,
) -> Grant:
    """Issuance gate (MANDATE §2.1): a child must fit inside its parent's effective authority;
    a top grant must resolve to a qualifying root held by the grantor."""
    if grant.parent_grant_id is not None:
        chain = await load_chain(conn, grant.parent_grant_id)
        roots = await _roots_for(conn, grant.tenant_id, chain.values(), reader)
        problems = validate_child_grant(
            grant, grants=chain, roots=roots, now=now, risk_level=risk_level
        )
    else:
        roots = await _roots_for(conn, grant.tenant_id, [grant], reader)
        res = resolve_chain(
            grant.grant_id,
            tenant_id=grant.tenant_id,
            grants={grant.grant_id: grant},
            roots=roots,
            now=now,
            risk_level=risk_level,
        )
        problems = list(res.codes)
    if problems:
        raise AuthorityError(problems[0], ", ".join(problems))
    await insert_grant(conn, grant)
    return grant


async def revoke_grant(conn: AsyncConnection, grant_id: str) -> Grant:
    """Terminal for this grant ID; every descendant stops resolving at once (revocation dominance)."""
    return await set_grant_status(conn, grant_id, GrantStatus.REVOKED)
