"""Anchored checkpoints are proved again when a partition is verified (EVID-16; DEC-F2 §3), against PostgreSQL.

A checkpoint's RFC 3161 timestamp is not taken on trust once stored: `verify_partition` checks it against the TSA trust list
and reports a stamp over another digest, from an untrusted authority or from a revoked certificate as a problem. Without a
trust list the stamp is counted as unverified, never as verified.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_evidence import Digest, hash_bytes

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "unit")
)  # the timestamp authority the unit tests use
from tsa_authority import authority, make_crl, response
from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.checkpointer import seal_partition
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.domain.ledger import Partition
from zid_platform.modules.evidence.infrastructure.tsa_token import TimestampVerifier


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh_runtime() -> Any:
    rt = development_runtime("uk-1")
    configure_runtime(rt)
    yield rt
    configure_runtime(None)


class Stamper:
    """A TSA that answers with a real token from `auth`; `over` makes it stamp some other digest."""

    def __init__(self, auth: Any, over: Digest | None = None) -> None:
        self.auth, self.over = auth, over

    async def timestamp(self, digest: Digest) -> str:
        der = response(
            self.auth,
            self.over or digest,
            nonce=7,
            gen_time=datetime.now(UTC) - timedelta(seconds=30),
        )
        return "rfc3161:" + base64.b64encode(der).decode()


class NoCrls:
    def __init__(self, crls: list[Any]) -> None:
        self.crls = crls

    async def fetch(self, urls: Any) -> list[Any]:
        return self.crls


async def sealed_partition(
    rt_url: str, owner_url: str, rt: EvidenceRuntime, tsa: Any
) -> tuple[str, str]:
    tenant = uid()
    pid = Partition(tenant, "uk-1").id
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        await ledger.append(
            c,
            tenant_id=tenant,
            record_type="test.record",
            record_id="a",
            record={"a": 1},
            evidence_class="E1",
            cell_id="uk-1",
        )
    async with engine(owner_url) as eng, eng.begin() as c:
        cp = await seal_partition(c, pid, signer=rt.signer, tsa=tsa)
        assert cp is not None and cp.external_timestamp_ref is not None
    return tenant, pid


async def verify(rt_url: str, tenant: str, pid: str, rt: EvidenceRuntime, **kw: Any) -> Any:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        return await ledger.verify_partition(
            c, pid, keys=rt.trusted_keys(), emit_incident=False, **kw
        )


def test_a_stored_timestamp_is_proved_again_and_a_bad_one_is_a_problem(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    auth, stranger = authority(intermediate=True, crl_url="http://crl.test/mid.crl"), authority()

    async def run() -> None:
        tenant, pid = await sealed_partition(rt_url, owner_url, fresh_runtime, Stamper(auth))

        trusted = TimestampVerifier([auth.root], revocation="off")
        ok = await verify(rt_url, tenant, pid, fresh_runtime, timestamps=trusted)
        assert ok.ok and ok.timestamps_verified == 1 and ok.timestamps_unverified == 0

        unchecked = await verify(
            rt_url, tenant, pid, fresh_runtime
        )  # no trust list: counted, never claimed
        assert (
            unchecked.ok
            and unchecked.timestamps_verified == 0
            and unchecked.timestamps_unverified == 1
        )

        untrusted = await verify(
            rt_url,
            tenant,
            pid,
            fresh_runtime,
            timestamps=TimestampVerifier([stranger.root], revocation="off"),
        )
        assert not untrusted.ok
        assert untrusted.problems[0]["kind"] == "checkpoint_timestamp"
        assert "does not chain" in untrusted.problems[0]["detail"]

        revoked = [
            make_crl(
                auth.mid, auth.mid_key, [(auth.tsa_cert, datetime.now(UTC) - timedelta(days=1))]
            ),
        ]
        gone = await verify(
            rt_url,
            tenant,
            pid,
            fresh_runtime,
            timestamps=TimestampVerifier(
                [auth.root], revocation="if_available", crls=NoCrls(revoked)
            ),
        )
        assert not gone.ok and "revoked at" in gone.problems[0]["detail"]

    asyncio.run(run())


def test_a_timestamp_over_another_digest_is_a_problem(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    auth = authority()

    async def run() -> None:
        other = hash_bytes(b"some other checkpoint", alg="sha-256")
        tenant, pid = await sealed_partition(
            rt_url, owner_url, fresh_runtime, Stamper(auth, over=other)
        )
        report = await verify(
            rt_url,
            tenant,
            pid,
            fresh_runtime,
            timestamps=TimestampVerifier([auth.root], revocation="off"),
        )
        assert not report.ok
        assert "different digest" in report.problems[0]["detail"]

    asyncio.run(run())
