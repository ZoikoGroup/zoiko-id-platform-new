"""Bulk evidence export: R3-gated, audited per receipt, and gone after its expiry (ZID-API-001 API-13)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_evidence_pg import decide, receipt_of, seed_admin
from zid_db import tenant_transaction
from zid_evidence import verify_bundle
from zid_platform.modules.decision.ports import register_evidence_replayer
from zid_platform.modules.evidence.application import bulk
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.domain.bulk_export import (
    DEFAULT_TTL,
    MAX_TTL,
    MIN_TTL,
    available,
    expiry,
)
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.ports import worker_pass
from zid_platform.protected import catalog


@pytest.fixture(autouse=True)
def runtime() -> Iterator[None]:
    configure_runtime(development_runtime("uk-bulk"))
    register_evidence_replayer(catalog())
    yield
    configure_runtime(None)


def uid() -> str:
    return str(uuid.uuid4())


def test_expiry_is_clamped_and_an_expired_export_is_not_available() -> None:
    """API-13: the lifetime has a floor and a ceiling; at the expiry instant the export is no longer served."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert expiry(now, None) == now + DEFAULT_TTL
    assert expiry(now, 1) == now + MIN_TTL
    assert expiry(now, 10_000_000) == now + MAX_TTL
    exp = now + timedelta(hours=1)
    assert available("AVAILABLE", exp, exp - timedelta(seconds=1))
    assert not available("AVAILABLE", exp, exp)
    assert not available("EXPIRED", exp, now)


def test_bulk_export_is_signed_audited_per_receipt_and_erased_after_expiry(
    rt_url: str, owner_url: str
) -> None:
    """API-13: several receipts export under one signed manifest and each leaves a custody record; the package
    downloads until its expiry, then it is erased, refused, and the row remains as the record of the export."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        receipts: list[str] = []
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            for _ in range(2):
                d = await decide(c, tenant, admin, "evidence.export")
                receipts.append(str((await receipt_of(c, d.decision_id)).receipt_id))
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)

        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            out = await bulk.create(
                c,
                tenant,
                [*receipts, receipts[0]],  # a repeated id is exported once
                mode="CUSTOMER_VERIFY",
                purpose="quarterly audit",
                case_ref="audit-7",
                custodian_principal_id=admin,
                ttl_minutes=60,
                now=now,
            )
            assert out["receipt_count"] == 2
            assert out["manifest"]["expires_at"] == (now + timedelta(minutes=60)).isoformat()
            for pkg in out["packages"]:
                assert verify_bundle(
                    pkg["bundle"], trusted_keys=current_runtime().trusted_keys()
                ).ok
            custody = (
                (await c.execute(text("SELECT custody_action FROM evidence.custody_record")))
                .scalars()
                .all()
            )
            assert custody == ["EXPORTED", "EXPORTED"]  # one custody record per receipt
            export_id = out["export_id"]

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            again = await bulk.fetch(c, export_id, now=now + timedelta(minutes=30))
            assert again["manifest"]["export_id"] == export_id

        async with engine(rt_url) as eng:
            with pytest.raises(LedgerError, match="EVIDENCE_EXPORT_EXPIRED"):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await bulk.fetch(c, export_id, now=now + timedelta(minutes=61))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await bulk.sweep_expired(c, now=now + timedelta(minutes=61)) == 1
                row: Any = (
                    await c.execute(
                        text("SELECT status, package, downloads FROM evidence.bulk_export")
                    )
                ).one()
                assert row.status == "EXPIRED" and row.package is None and row.downloads == 1
                assert await bulk.sweep_expired(c, now=now + timedelta(hours=2)) == 0

    asyncio.run(run())

    async def other_tenant_sees_nothing() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
            assert (
                await c.execute(text("SELECT count(*) FROM evidence.bulk_export"))
            ).scalar_one() == 0

    asyncio.run(other_tenant_sees_nothing())


def test_a_bulk_export_needs_between_one_and_a_hundred_receipts(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
            for ids in ([], [uid() for _ in range(101)]):
                with pytest.raises(LedgerError, match="EVIDENCE_SCHEMA_INVALID"):
                    await bulk.create(
                        c, "t", ids, mode="CUSTOMER_VERIFY", purpose="x y z", case_ref="c",
                        custodian_principal_id="p", ttl_minutes=None,
                    )  # fmt: skip

    asyncio.run(run())
