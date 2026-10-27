"""Signing-key compromise: rotate, record the incident, keep unaffected history verifiable (EVID-22, AT-12)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_crypto import LocalSigner
from zid_crypto.purposes import KeyPurpose
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.checkpointer import seal_partition
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.domain.key_compromise import KeyCompromiseError
from zid_platform.modules.evidence.domain.ledger import Partition


@pytest.fixture(autouse=True)
def runtime() -> Iterator[None]:
    configure_runtime(development_runtime("uk-keycomp"))
    yield
    configure_runtime(None)


def uid() -> str:
    return str(uuid.uuid4())


async def _append(c, tenant: str, record_id: str) -> None:  # type: ignore[no-untyped-def]
    await ledger.append(
        c, tenant_id=tenant, record_type="test.record", record_id=record_id,
        record={"n": record_id}, evidence_class="E3", cell_id="uk-1",
    )  # fmt: skip


def test_compromise_rotates_the_key_and_only_history_before_it_is_trusted(
    rt_url: str, owner_url: str
) -> None:
    """EVID-22 / AT-12: after a compromise the new key signs, the incident is recorded, entries signed before the
    compromise still verify, and anything the old key signed after it is rejected. Nothing is rewritten."""
    tenant = uid()
    pid = Partition(tenant, "uk-1").id

    async def run() -> None:
        old_rt = current_runtime()
        old_kid = str(old_rt.signer.public_jwk()["kid"])
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _append(c, tenant, "before-1")
            await _append(c, tenant, "before-2")
        async with engine(owner_url) as eng, eng.begin() as c:
            await seal_partition(c, pid, signer=old_rt.signer)

        # the key is declared compromised from a moment after everything above was signed
        after = datetime.now(UTC) + timedelta(seconds=5)
        new_signer = LocalSigner("dev-evidence-uk-1-v2", purpose=KeyPurpose.EVIDENCE_SIGNING)
        new_rt = old_rt.after_compromise(new_signer, compromised_at=after)
        configure_runtime(new_rt)
        assert new_rt.signer.public_jwk()["kid"] != old_kid
        assert old_kid in {
            str(k["kid"]) for k in new_rt.trusted_keys()
        }  # still published for verification

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await ledger.declare_signing_key_compromise(
                c, Partition(tenant, "uk-1"), compromised_kid=old_kid,
                new_kid=str(new_signer.public_jwk()["kid"]), compromised_at=after, reason="KEY_EXPOSED",
            )  # fmt: skip
            await _append(c, tenant, "after-1")
            report = await ledger.verify_partition(
                c, pid, keys=new_rt.trusted_keys(), compromised=new_rt.compromised
            )
            assert report.ok, report.problems  # history before the compromise is unaffected
            recorded = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE event_type = "
                        "'com.zoiko.id.evidence.key.rotated.v1' AND payload::text LIKE '%COMPROMISE%'"
                    )
                )
            ).scalar_one()
            assert recorded == 1

            # had the key been compromised earlier, what it signed since is no longer evidence
            earlier = old_rt.after_compromise(
                new_signer, compromised_at=datetime.now(UTC) - timedelta(hours=1)
            )
            bad = await ledger.verify_partition(
                c,
                pid,
                keys=earlier.trusted_keys(),
                compromised=earlier.compromised,
                emit_incident=False,
            )
            assert not bad.ok
            assert {p["kind"] for p in bad.problems} >= {"signed_by_compromised_key"}

    asyncio.run(run())


def test_a_compromised_key_cannot_be_its_own_replacement() -> None:
    rt = development_runtime("uk-keycomp")
    with pytest.raises(KeyCompromiseError):
        rt.after_compromise(rt.signer, compromised_at=datetime.now(UTC))
