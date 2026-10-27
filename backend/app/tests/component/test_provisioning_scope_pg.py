"""A connector's identifiers mean something only inside that connector (ZID-OVERLAY-001 OVL-12).

Two HR systems may both call a different person "E-1". Each externalId is scoped to the connector that sent it, so one
connector can neither find, read nor overwrite the other's user, and a repeat inside one connector is a conflict.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.provisioning.infrastructure import scim_store as store

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def uid() -> str:
    return str(uuid.uuid4())


def user(
    tenant: str, source: str, principal: str, user_name: str, external_id: str
) -> store.Resource:
    return store.Resource(
        resource_id=uid(),
        tenant_id=tenant,
        source_id=source,
        resource_type="User",
        external_id=external_id,
        principal_id=principal,
        user_name=user_name,
        display_name=None,
        active=True,
        attributes={},
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_the_same_external_id_in_two_connectors_never_links_their_users(rt_url: str) -> None:
    """OVL-12: connector A's "E-1" and connector B's "E-1" are two people; neither connector can see, load or
    collide with the other's, while a duplicate inside one connector is refused."""
    tenant, source_a, source_b, person_a, person_b = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (person_a, person_b):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            for source, client in (
                (source_a, f"hris-a-{uid()[:8]}"),
                (source_b, f"hris-b-{uid()[:8]}"),
            ):
                await store.create_source(
                    c, store.Source(source, tenant, client, f"HRIS {client}", "ACTIVE")
                )
            a = user(tenant, source_a, person_a, "alice@a.example", "E-1")
            b = user(tenant, source_b, person_b, "bob@b.example", "E-1")
            await store.insert(c, a)
            await store.insert(c, b)  # the same externalId in another connector is not a collision
            total_a, found_a = await store.query(
                c, source_a, "User", attribute="externalId", value="E-1", start=1, count=10
            )
            total_b, found_b = await store.query(
                c, source_b, "User", attribute="externalId", value="E-1", start=1, count=10
            )
            assert (total_a, [r.principal_id for r in found_a]) == (1, [person_a])
            assert (total_b, [r.principal_id for r in found_b]) == (1, [person_b])
            with pytest.raises(
                store.ScimError
            ) as missing:  # B cannot load A's resource, even by its id
                await store.load(c, source_b, "User", a.resource_id)
            assert missing.value.status == 404
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            clash = user(tenant, source_a, person_b, "alice2@a.example", "E-1")
            with pytest.raises(store.ScimError) as dup:  # inside one connector it is a conflict
                await store.insert(c, clash)
            assert dup.value.status == 409

    asyncio.run(run())
