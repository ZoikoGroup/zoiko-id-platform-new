"""Phase C persistence against real PostgreSQL: principals, roots, relationships, grants.

Covers DM-AT-01/02/12, INV-11 (database-enforced monotonic revisions), ROOT-06/24, MAND-01/05,
AUTHZ-05 and revocation dominance end to end through the stores and the authority service.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.authority.application.service import (
    authorize_request,
    issue_grant,
    resolve,
    revoke_grant,
)
from zid_platform.modules.authority.domain.algebra import (
    Authority,
    Request,
    ValueLimit,
    Window,
    scope,
)
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    OrgEvent,
    RootAssertion,
    RootStatus,
    RootType,
    SourceEvent,
)
from zid_platform.modules.organization.infrastructure.root_store import (
    apply_source_event,
    create_organization,
    load_roots,
    root_revision,
    save_root,
)
from zid_platform.modules.principal.domain.principal import (
    DomainError,
    Identifier,
    Principal,
    PrincipalStatus,
    PrincipalType,
    UniquenessScope,
    normalize,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    add_identifier,
    create_principal,
    load_principal,
    set_status,
)
from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipError,
    RelationshipStatus,
    RelationshipType,
)
from zid_platform.modules.relationship.infrastructure.edge_store import (
    change_edge,
    current_revision,
    edges_since,
    record_edge,
)

NOW = datetime.now(UTC)
YEAR = Window(NOW - timedelta(days=30), NOW + timedelta(days=335))


def uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- principals


def test_principal_lifecycle_and_identifier_scoping(rt_url: str) -> None:
    t1, t2 = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            alice1, alice2 = uid(), uid()
            async with tenant_transaction(eng, ctx(t1)) as c:
                await create_principal(
                    c, Principal(alice1, t1, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            async with tenant_transaction(eng, ctx(t2)) as c:
                await create_principal(
                    c, Principal(alice2, t2, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            email = normalize("email", "alice@example.com")
            async with tenant_transaction(eng, ctx(t1)) as c:
                await add_identifier(
                    c, Identifier(uid(), alice1, t1, "email", email, UniquenessScope.TENANT)
                )
            async with tenant_transaction(
                eng, ctx(t2)
            ) as c:  # DM-AT-02: same email, other tenant, no conflict
                await add_identifier(
                    c, Identifier(uid(), alice2, t2, "email", email, UniquenessScope.TENANT)
                )
            with pytest.raises(DomainError) as e:  # but the same tenant collides, and never merges
                async with tenant_transaction(eng, ctx(t1)) as c:
                    other = uid()
                    await create_principal(
                        c, Principal(other, t1, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                    )
                    await add_identifier(
                        c, Identifier(uid(), other, t1, "email", email, UniquenessScope.TENANT)
                    )
            assert e.value.code == "IDENTIFIER_CONFLICT"

            async with tenant_transaction(eng, ctx(t1)) as c:  # DM-AT-01: id stable, revision up
                p = await set_status(c, alice1, PrincipalStatus.SUSPENDED)
                assert p.principal_id == alice1 and p.security_revision == 1
            with pytest.raises(
                DBAPIError, match="security_revision may not decrease"
            ):  # INV-11 in the database
                async with tenant_transaction(eng, ctx(t1)) as c:
                    await c.execute(
                        text(
                            "UPDATE core.principal SET security_revision = 0 WHERE principal_id = :p"
                        ),
                        {"p": uuid.UUID(alice1)},
                    )
            async with tenant_transaction(
                eng, ctx(t2)
            ) as c:  # RLS: tenant 2 can't see tenant 1's principal
                with pytest.raises(DomainError):
                    await load_principal(c, alice1)

    asyncio.run(run())


# ---------------------------------------------------------------- relationships


def test_relation_revisions_are_unique_and_monotonic_under_concurrency(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url, size=20) as eng:

            async def add(i: int) -> int:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    rel = await record_edge(
                        c,
                        Relationship(
                            uid(),
                            tenant,
                            uid(),
                            tenant,
                            RelationshipType.MEMBER_OF,
                            uid(),
                            tenant,
                            "scim",
                            1,
                            RelationshipStatus.ACTIVE,
                            NOW,
                            None,
                            relation_revision=0,
                        ),
                    )
                    return rel.relation_revision

            revs = await asyncio.gather(*(add(i) for i in range(200)))
            assert sorted(revs) == list(range(1, 201))  # no repeats, no gaps
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await current_revision(c, tenant) == 200
                first = (await edges_since(c, tenant, 0))[0]
                revoked = await change_edge(
                    c, first.relationship_id, RelationshipStatus.REVOKED, NOW
                )
                assert revoked.relation_revision == 201
                assert [e.relation_revision for e in await edges_since(c, tenant, 200)] == [201]
            with pytest.raises(RelationshipError):  # DM-AT-12, before anything reaches the database
                Relationship(
                    uid(),
                    tenant,
                    uid(),
                    tenant,
                    RelationshipType.OWNS,
                    uid(),
                    uid(),
                    "x",
                    1,
                    RelationshipStatus.ACTIVE,
                    NOW,
                    None,
                    relation_revision=0,
                )

    asyncio.run(run())


# ---------------------------------------------------------------- roots and grants


async def _org_with_root(c: object, tenant: str, holder: str) -> tuple[str, str]:
    org, root_id = uid(), uid()
    await create_organization(
        c,
        organization_id=org,
        tenant_id=tenant,
        legal_name="Acme Ltd",
        jurisdiction="GB",  # type: ignore[arg-type]
        valid_from=NOW - timedelta(days=400),
    )
    await save_root(
        c,
        tenant,
        RootAssertion(  # type: ignore[arg-type]
            root_id=root_id,
            organization_id=org,
            holder_principal_id=holder,
            root_type=RootType.REGISTRY,
            facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
            assurance=Assurance.AR3,
            status=RootStatus.ACTIVE,
            jurisdiction="GB",
            valid_from=NOW - timedelta(days=100),
            valid_to=None,
            fresh_until=NOW + timedelta(days=1),
        ),
    )
    return org, root_id


def _grant(gid: str, tenant: str, **kw: object) -> Grant:
    base: dict[str, object] = {
        "grant_id": gid,
        "tenant_id": tenant,
        "grant_type": GrantType.ROLE_ISSUED,
        "grantor_principal_id": uid(),
        "grantee_principal_id": uid(),
        "represented_principal_id": uid(),
        "authority": Authority(
            actions=scope("invoice.approve", "payment.release"),
            resources=scope("invoice:*"),
            purposes=scope("accounts_payable"),
            value=ValueLimit(Decimal(50_000), "GBP"),
            window=YEAR,
        ),
        "continuity_mode": ContinuityMode.ROLE_CONTINUOUS,
        "status": GrantStatus.ACTIVE,
        "issued_at": NOW - timedelta(hours=1),
        "delegation_allowed": True,
        "max_delegation_depth": 2,
    } | kw
    return Grant(**base)  # type: ignore[arg-type]


def test_grant_chain_issuance_resolution_revocation_and_root_events(rt_url: str) -> None:
    tenant, cfo, controller, agent = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                org, root_id = await _org_with_root(c, tenant, cfo)
                top = _grant(
                    uid(),
                    tenant,
                    grantor_principal_id=cfo,
                    grantee_principal_id=controller,
                    represented_principal_id=org,
                    root_id=root_id,
                )
                await issue_grant(c, top, risk_level=3, now=NOW)
                child = _grant(
                    uid(),
                    tenant,
                    grantor_principal_id=controller,
                    grantee_principal_id=agent,
                    represented_principal_id=org,
                    parent_grant_id=top.grant_id,
                    max_delegation_depth=1,
                    delegation_allowed=False,
                    grant_type=GrantType.AGENT_DELEGATION,
                    authority=Authority(
                        actions=scope("invoice.approve"),
                        resources=scope("invoice:*"),
                        purposes=scope("accounts_payable"),
                        value=ValueLimit(Decimal(5_000), "GBP"),
                        window=YEAR,
                    ),
                )
                await issue_grant(c, child, risk_level=3, now=NOW)

                res = await resolve(
                    c, tenant_id=tenant, grant_id=child.grant_id, risk_level=3, now=NOW
                )
                assert (
                    res.valid
                    and res.chain_ids == (child.grant_id, top.grant_id)
                    and res.root_id == root_id
                )
                req = Request(
                    "invoice.approve",
                    "invoice:7",
                    NOW,
                    purpose="accounts_payable",
                    value=ValueLimit(Decimal(100), "GBP"),
                )
                assert (
                    await authorize_request(
                        c, tenant_id=tenant, grantee_id=agent, request=req, risk_level=3
                    )
                ).allowed

                widening = _grant(
                    uid(),
                    tenant,
                    grantor_principal_id=agent,
                    grantee_principal_id=uid(),
                    represented_principal_id=org,
                    parent_grant_id=child.grant_id,
                    max_delegation_depth=0,
                )
                with pytest.raises(
                    AuthorityError
                ):  # MAND-01: child can't widen / parent forbids delegation
                    await issue_grant(c, widening, risk_level=3, now=NOW)

            with pytest.raises(
                DBAPIError, match="immutable"
            ):  # material mutation needs a superseding grant
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await c.execute(
                        text(
                            "UPDATE authz.authority_grant SET value_limit = 999999 "
                            "WHERE authority_grant_id = :g"
                        ),
                        {"g": uuid.UUID(child.grant_id)},
                    )

            async with tenant_transaction(
                eng, ctx(tenant)
            ) as c:  # ROOT-06/AUTHZ-05: officer leaves
                out = await apply_source_event(
                    c, tenant, SourceEvent("reg-ev-1", org, OrgEvent.OFFICER_CESSATION, cfo)
                )
                assert out.applied and await root_revision(c, org) == 1
                again = await apply_source_event(
                    c, tenant, SourceEvent("reg-ev-1", org, OrgEvent.OFFICER_CESSATION, cfo)
                )
                assert not again.applied and await root_revision(c, org) == 1  # ROOT-24 idempotent
                assert {r.status for r in await load_roots(c, org)} == {RootStatus.REVOKED}
                res = await resolve(
                    c, tenant_id=tenant, grant_id=child.grant_id, risk_level=3, now=NOW
                )
                assert not res.valid and res.codes == ("AUTHORITY_REVOKED",)

    asyncio.run(run())


def test_revoking_a_parent_stops_every_descendant(rt_url: str) -> None:
    tenant, cfo, controller, agent = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            org, root_id = await _org_with_root(c, tenant, cfo)
            top = _grant(
                uid(),
                tenant,
                grantor_principal_id=cfo,
                grantee_principal_id=controller,
                represented_principal_id=org,
                root_id=root_id,
            )
            await issue_grant(c, top, risk_level=2, now=NOW)
            child = _grant(
                uid(),
                tenant,
                grantor_principal_id=controller,
                grantee_principal_id=agent,
                represented_principal_id=org,
                parent_grant_id=top.grant_id,
                max_delegation_depth=1,
            )
            await issue_grant(c, child, risk_level=2, now=NOW)
            await revoke_grant(c, top.grant_id)
            res = await resolve(c, tenant_id=tenant, grant_id=child.grant_id, risk_level=2, now=NOW)
            assert res.codes == ("AUTHORITY_REVOKED",)
            with pytest.raises(AuthorityError):  # terminal: REVOKED never returns to ACTIVE
                from zid_platform.modules.authority.infrastructure.grant_store import (
                    set_grant_status,
                )

                await set_grant_status(c, top.grant_id, GrantStatus.ACTIVE)

    asyncio.run(run())
