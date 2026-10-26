"""Deleting a closed company's data (TENANCY-001 §23; PRIV-001; TEN-25).

A closed tenant's vault is crypto-erased subject by subject through the legal-hold evaluation. A subject under a hold
is kept and reported, the deletion stays HELD, and a later pass finishes once the hold is released. Connector secrets
are destroyed. Only a CLOSED tenant can be deleted, and the request is recorded once.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.onboarding.application.deletion import DeletionError
from zid_platform.modules.onboarding.ports import deletion_pass, request_deletion


@pytest.fixture
def rt() -> Any:
    runtime = development_runtime("uk-1")
    configure_runtime(runtime)
    yield runtime
    configure_runtime(None)


def test_deleting_a_closed_tenant_erases_what_is_not_held(
    rt_url: str, owner_url: str, rt: Any
) -> None:
    o = onboard(rt_url)
    tenant = o.tenant
    kept_subject, gone_subject = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)

    async def store(c: Any, person: str, body: bytes) -> str:
        object_id, _ = await vault.store_object(
            c,
            tenant,
            content=body,
            object_type="verification.document",
            classification="D3",
            retention_class="raw",
            subject_principal_id=person,
        )
        return str(object_id)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            kept_obj = await store(c, kept_subject, b"under hold")
            gone_obj = await store(c, gone_subject, b"not held")
            hold = await vault.create_hold(
                c,
                tenant,
                basis="litigation",
                reference="m-25",
                requested_by=o.owner,
                approved_by=o.second,
                subject_principal_ids=[kept_subject],
                review_at=now + timedelta(days=30),
                now=now,
            )
        # an open tenant cannot be deleted
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE platform.tenant SET status = 'ACTIVE', closed_at = NULL WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
            with pytest.raises(DeletionError) as err:
                await request_deletion(c, tenant, requested_by=o.owner, now=now)
            assert err.value.code == "TENANT_NOT_CLOSED"
            await c.execute(
                text(
                    "UPDATE platform.tenant SET status = 'CLOSED', closed_at = now() WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
            await request_deletion(c, tenant, requested_by=o.owner, now=now)
            await request_deletion(c, tenant, requested_by=o.owner, now=now)  # recorded once

        async with engine(owner_url) as eng, eng.begin() as c:
            first = next(r for r in await deletion_pass(c, now) if r["tenant_id"] == tenant)
        assert first["status"] == "HELD" and first["held_back"] == 1 and first["erased"] >= 1, first
        assert first["dedicated_key_scheduled"] is False

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(LedgerError):
                await vault.read_object(c, tenant, gone_obj, rt=rt)
            assert await vault.read_object(c, tenant, kept_obj, rt=rt) == b"under hold"
            await vault.release_hold(
                c, tenant, hold.legal_hold_id, by=o.second, reason="matter closed", now=now
            )

        async with engine(owner_url) as eng, eng.begin() as c:
            second = next(r for r in await deletion_pass(c, now) if r["tenant_id"] == tenant)
            assert second["status"] == "COMPLETE"
            row = (
                await c.execute(
                    text(
                        "SELECT status, backup_erasure_due, completed_at FROM platform.tenant_deletion "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).one()
            assert (
                row.status == "COMPLETE"
                and row.backup_erasure_due > now
                and row.completed_at is not None
            )
            assert [
                r for r in await deletion_pass(c, now) if r["tenant_id"] == tenant
            ] == []  # nothing left to do

    asyncio.run(run())
