"""SpiceDB projection against real SpiceDB + PostgreSQL (DEC-C4; AT-05, AUTHZ-07, DM-AT-13; P0-04).

Needs ZID_TEST_PG_URL and ZID_TEST_SPICEDB_URL (SpiceDB HTTP gateway, preshared key "test-key"):

    docker run -d --rm --name zid-spicedb-test -p 58443:8443 authzed/spicedb:v1.39.1 serve \\
        --grpc-preshared-key test-key --http-enabled --datastore-engine memory --telemetry-endpoint=""
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipStatus,
    RelationshipType,
)
from zid_platform.modules.relationship.infrastructure.edge_store import change_edge, record_edge
from zid_platform.modules.relationship.infrastructure.spicedb import (
    SpiceDB,
    check_relation,
    project,
    rebuild,
)

SPICEDB_URL = os.environ.get("ZID_TEST_SPICEDB_URL")
pytestmark = pytest.mark.skipif(
    not SPICEDB_URL, reason="set ZID_TEST_SPICEDB_URL to a disposable SpiceDB"
)
NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def spicedb(url: str | None = None) -> SpiceDB:
    return SpiceDB(url or SPICEDB_URL or "", "test-key")


def test_projection_freshness_by_risk_class(rt_url: str) -> None:
    tenant, alice, org = uid(), uid(), uid()
    sdb = spicedb()

    async def run() -> None:
        await sdb.write_schema()
        async with engine(rt_url) as eng:  # noqa: SIM117 - one transaction spans the whole scenario
            async with tenant_transaction(eng, ctx(tenant)) as c:
                edge = await record_edge(
                    c,
                    Relationship(
                        uid(),
                        tenant,
                        alice,
                        tenant,
                        RelationshipType.MEMBER_OF,
                        org,
                        tenant,
                        "scim",
                        2,
                        RelationshipStatus.ACTIVE,
                        NOW,
                        None,
                        relation_revision=0,
                    ),
                )
                # Not projected yet: R2 waits briefly, then denies; R3 is unavailable. Never an allow.
                r2 = await check_relation(
                    c,
                    sdb,
                    tenant_id=tenant,
                    obj=org,
                    relation=RelationshipType.MEMBER_OF,
                    subject=alice,
                    risk_level=2,
                )
                assert not r2.allowed and "AUTHORITY_STALE" in r2.codes
                r3 = await check_relation(
                    c,
                    sdb,
                    tenant_id=tenant,
                    obj=org,
                    relation=RelationshipType.MEMBER_OF,
                    subject=alice,
                    risk_level=3,
                )
                assert not r3.allowed and r3.codes == ("STATE_UNAVAILABLE",)

                assert await project(c, sdb, tenant) == edge.relation_revision
                # R0 uses minimize_latency and may read a slightly older snapshot (bounded staleness is
                # allowed for R0); R1-R3 must see the projected write.
                for risk in (1, 2, 3):
                    r = await check_relation(
                        c,
                        sdb,
                        tenant_id=tenant,
                        obj=org,
                        relation=RelationshipType.MEMBER_OF,
                        subject=alice,
                        risk_level=risk,
                    )
                    assert r.allowed and r.relation_revision == edge.relation_revision, (risk, r)

                # Revoked in PostgreSQL but the projector hasn't run: AT-05, no new R2/R3 allow.
                revoked = await change_edge(
                    c, edge.relationship_id, RelationshipStatus.REVOKED, NOW
                )
                for risk in (2, 3):
                    r = await check_relation(
                        c,
                        sdb,
                        tenant_id=tenant,
                        obj=org,
                        relation=RelationshipType.MEMBER_OF,
                        subject=alice,
                        risk_level=risk,
                    )
                    assert not r.allowed, (risk, r)
                # Once projected, the revocation is visible everywhere.
                assert await project(c, sdb, tenant) == revoked.relation_revision
                r = await check_relation(
                    c,
                    sdb,
                    tenant_id=tenant,
                    obj=org,
                    relation=RelationshipType.MEMBER_OF,
                    subject=alice,
                    risk_level=3,
                )
                assert not r.allowed and r.codes == ("RELATIONSHIP_MISSING",)

    asyncio.run(run())


def test_spicedb_outage_fails_closed_for_r2_r3(rt_url: str) -> None:
    tenant, alice, org = uid(), uid(), uid()
    dead = spicedb("http://127.0.0.1:9")  # nothing listens here

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await record_edge(
                c,
                Relationship(
                    uid(),
                    tenant,
                    alice,
                    tenant,
                    RelationshipType.MEMBER_OF,
                    org,
                    tenant,
                    "scim",
                    2,
                    RelationshipStatus.ACTIVE,
                    NOW,
                    None,
                    relation_revision=0,
                ),
            )
            await project(c, spicedb(), tenant)
            r = await check_relation(
                c,
                dead,
                tenant_id=tenant,
                obj=org,
                relation=RelationshipType.MEMBER_OF,
                subject=alice,
                risk_level=2,
            )
            assert not r.allowed and r.codes == ("STATE_UNAVAILABLE",)

    asyncio.run(run())


def test_projection_rebuilds_from_postgres(rt_url: str) -> None:
    """DR drill (DEC-C4): the projection is disposable; PostgreSQL rebuilds it."""
    tenant, alice, org = uid(), uid(), uid()
    sdb = spicedb()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await record_edge(
                c,
                Relationship(
                    uid(),
                    tenant,
                    alice,
                    tenant,
                    RelationshipType.OWNS,
                    org,
                    tenant,
                    "admin",
                    1,
                    RelationshipStatus.ACTIVE,
                    NOW,
                    None,
                    relation_revision=0,
                ),
            )
            await project(c, sdb, tenant)
            token = await rebuild(c, sdb, tenant)
            assert token
            r = await check_relation(
                c,
                sdb,
                tenant_id=tenant,
                obj=org,
                relation=RelationshipType.OWNS,
                subject=alice,
                risk_level=3,
            )
            assert r.allowed

    asyncio.run(run())
