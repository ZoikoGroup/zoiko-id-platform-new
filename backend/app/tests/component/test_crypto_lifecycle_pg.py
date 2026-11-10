"""KEK rotation, rewrap, dependency-gated retirement and compromise response against PostgreSQL
(ZID-CRYPTO-001 acceptance CRYP-10, CRYP-29; DEC-F5 §7), plus CRYP-11 (a compromised data key is
re-encrypted with bounded blast radius), CRYP-28 (a customer-disabled key fails closed) and CRYP-31
(a restore cannot reactivate a destroyed key).

Every wrapped data key (per-subject keys and DATA_KEK envelopes) follows a KEK rotation in the
background without any plaintext being rewritten, and the old KEK version can only be retired once
nothing depends on it. A compromised KEK version is rotated out, its dependents rewrapped and the
version retired with the counts recorded.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import ctx, engine, fresh_database
from sqlalchemy import text
from zid_crypto import CryptoError
from zid_crypto.envelope import Envelope
from zid_crypto.providers import KeyDestroyedError, KeyState
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.ports import worker_pass

N_SUBJECT, N_PLAIN = 12, 13


# These tests count what depends on a key across the WHOLE database ("nothing depends on the old version any more"). Every test builds its own in-memory
# key provider, so rows other tests left behind are wrapped under keys that no longer exist: they can never be re-wrapped and would be counted forever.
# A database of their own keeps the counts about what these tests wrote.
@pytest.fixture(scope="module")
def isolated() -> Iterator[tuple[str, str]]:
    with fresh_database() as urls:
        yield urls


@pytest.fixture(scope="module")
def owner_url(isolated: tuple[str, str]) -> str:
    return isolated[0]


@pytest.fixture(scope="module")
def rt_url(isolated: tuple[str, str]) -> str:
    return isolated[1]


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def rt() -> Any:
    runtime = development_runtime("uk-1")
    configure_runtime(runtime)
    yield runtime
    configure_runtime(None)


async def seed(rt_url: str, tenant: str) -> list[tuple[str, bytes, bool]]:
    """Objects under per-subject keys (wrapped under the subject KEK) and under the DATA_KEK."""
    objects: list[tuple[str, bytes, bool]] = []
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        for i in range(N_SUBJECT + N_PLAIN):
            subject = i < N_SUBJECT
            content = f"payload {i}".encode()
            object_id, _ = await vault.store_object(
                c,
                tenant,
                content=content,
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=uid() if subject else None,
            )
            objects.append((object_id, content, subject))
    return objects


async def raw_objects(owner_url: str, tenant: str) -> dict[str, tuple[bytes, str]]:
    async with engine(owner_url) as eng, eng.begin() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT evidence_object_id, ciphertext, encryption_key_ref "
                    "FROM evidence.evidence_object WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        ).all()
    return {str(r.evidence_object_id): (bytes(r.ciphertext), r.encryption_key_ref) for r in rows}


async def read_all(rt_url: str, tenant: str, objects: list[tuple[str, bytes, bool]]) -> None:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        for object_id, content, _ in objects:
            assert await vault.read_object(c, tenant, object_id) == content


def test_kek_rotation_rewraps_without_data_rewrite_and_retires_after_closure(
    rt_url: str, owner_url: str, rt: EvidenceRuntime
) -> None:
    """CRYP-10: after a KEK rotation every wrapped DEK (subject keys and DATA_KEK envelopes) moves
    to the successor by background rewrap, no ciphertext is rewritten, and the old KEK version
    refuses to retire while anything still depends on it.

    Proven at 25 objects; the 'millions of DEKs' scale and KMS-side key-material retirement are not."""
    tenant = uid()

    async def run() -> None:
        objects = await seed(rt_url, tenant)
        before = await raw_objects(owner_url, tenant)
        subject_v1, data_v1 = rt.subject_kek.at(1), rt.data_kek.at(1)
        rt.provider.rotate_key(rt.subject_kek)
        rt.provider.rotate_key(rt.data_kek)
        assert (await rt.provider.metadata(subject_v1)).state is KeyState.RETIRED  # unwrap only
        assert (await rt.provider.metadata(rt.data_kek)).version == 2

        async with engine(owner_url) as eng, eng.begin() as c:
            assert await vault.kek_dependents(c, subject_v1) >= 1
            assert await vault.kek_dependents(c, data_v1) == N_PLAIN
            for old in (subject_v1, data_v1):  # dependents remain: retirement is refused
                with pytest.raises(CryptoError, match="CRYPTO_KEK_HAS_DEPENDENTS"):
                    await vault.retire_kek_version(c, old, rt=rt)
        await read_all(rt_url, tenant, objects)  # still readable on the retired versions

        async with engine(owner_url) as eng, eng.begin() as c:  # the background worker
            report = await worker_pass(c, rt=rt)
            assert report.rewrapped >= N_SUBJECT + N_PLAIN - 1  # subject keys + object envelopes
            assert await vault.kek_dependents(c, subject_v1) == 0
            assert await vault.kek_dependents(c, data_v1) == 0
            assert await vault.rewrap_object_envelopes(c, rt=rt) == 0  # converged

        after = await raw_objects(owner_url, tenant)
        for object_id, content, subject in objects:
            old_blob, _ = before[object_id]
            new_blob, new_ref = after[object_id]
            if subject:
                assert new_blob == old_blob  # data encrypted under the subject key is untouched
            else:
                a, b = Envelope.from_bytes(old_blob), Envelope.from_bytes(new_blob)
                assert (b.nonce, b.ciphertext) == (a.nonce, a.ciphertext)  # no data rewrite
                assert b.wrapped_dek != a.wrapped_dek and b.kek.version == 2
                assert new_ref == f"kek:{rt.data_kek.key_id}:2"

        async with engine(owner_url) as eng, eng.begin() as c:  # closure reached: now it retires
            for old in (subject_v1, data_v1):
                meta = await vault.retire_kek_version(c, old, rt=rt)
                assert meta.state is KeyState.DESTROYED
        await read_all(rt_url, tenant, objects)  # nothing needed the retired versions
        with pytest.raises(KeyDestroyedError):
            await rt.provider.unwrap_data_key(data_v1, b"\x00" * 60, {"tenant": tenant})

    asyncio.run(run())


def test_kek_compromise_identifies_dependents_rotates_and_blocks_new_use(
    rt_url: str, owner_url: str, rt: EvidenceRuntime
) -> None:
    """CRYP-29: the response finds what depends on the compromised KEK version, rotates so nothing
    new can use it, rewraps every dependent and retires the version; data stays readable.

    The incident SLO number, the approval/step-up around starting the response and KMS-side
    rotation are not exercised here (local provider only)."""
    tenant = uid()

    async def run() -> None:
        objects = await seed(rt_url, tenant)
        compromised = rt.data_kek.at(1)
        async with engine(owner_url) as eng, eng.begin() as c:
            report = await vault.respond_to_kek_compromise(c, compromised, rt=rt)
        assert report.dependents_before == N_PLAIN  # the dependency inventory
        assert report.rewrapped >= N_PLAIN and report.dependents_after == 0 and report.retired
        assert report.seconds < 30

        meta = await rt.provider.metadata(compromised)
        assert meta.state is KeyState.DESTROYED  # new use is blocked for good
        with pytest.raises(CryptoError):
            await rt.provider.wrap_data_key(compromised, b"\x01" * 32, {"tenant": tenant})
        assert (await rt.provider.metadata(rt.data_kek)).version == 2  # successor is active
        await read_all(rt_url, tenant, objects)
        after = await raw_objects(owner_url, tenant)
        for object_id, _, subject in objects:
            if not subject:
                assert after[object_id][1] == f"kek:{rt.data_kek.key_id}:2"

    asyncio.run(run())


def test_a_compromised_data_key_is_replaced_for_one_subject_and_no_other(
    rt_url: str, owner_url: str, rt: EvidenceRuntime
) -> None:
    """CRYP-11: re-keying subject A re-encrypts only A's objects under a fresh key; B is byte-for-byte untouched
    and everything still reads. The leaked key opens nothing that is stored now."""
    tenant, a, b = uid(), uid(), uid()

    async def run() -> None:
        stored: dict[str, list[str]] = {a: [], b: []}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for subject in (a, b):
                for i in range(2):
                    oid, _ = await vault.store_object(
                        c,
                        tenant,
                        content=f"{subject}-{i}".encode(),
                        object_type="verification.document",
                        classification="D3",
                        retention_class="raw",
                        subject_principal_id=subject,
                    )
                    stored[subject].append(oid)
        before = await raw_objects(owner_url, tenant)
        ref_a = await vault.subject_ref(tenant, a)
        async with engine(owner_url) as eng, eng.begin() as c:
            leaked, _ = await vault._subject_key(c, rt, tenant, ref_a, create=False)  # type: ignore[misc]
            report = await vault.rekey_subject(c, tenant, ref_a, rt=rt)
        assert report is not None and report.objects_reencrypted == 2
        assert report.old_key_id != report.new_key_id
        after = await raw_objects(owner_url, tenant)
        for oid in stored[a]:
            assert after[oid][0] != before[oid][0]
            assert after[oid][1] == f"subject:{report.new_key_id}"
            aad = vault._object_aad(tenant, oid, "verification.document").encode()
            with pytest.raises(Exception):  # noqa: B017 - AES-GCM tag failure
                vault._decrypt(leaked, aad, after[oid][0])  # the leaked key opens nothing now
        for oid in stored[b]:
            assert (
                after[oid] == before[oid]
            )  # the other subject's ciphertext and key reference are unchanged
        objects = [(o, f"{a}-{i}".encode(), True) for i, o in enumerate(stored[a])]
        objects += [(o, f"{b}-{i}".encode(), True) for i, o in enumerate(stored[b])]
        await read_all(rt_url, tenant, objects)
        async with engine(owner_url) as eng, eng.begin() as c:
            nobody = await vault.subject_ref(tenant, uid())
            assert await vault.rekey_subject(c, tenant, nobody, rt=rt) is None

    asyncio.run(run())


def test_a_customer_disabled_key_fails_closed_and_the_platform_key_is_never_used(
    rt_url: str, owner_url: str, rt: EvidenceRuntime
) -> None:
    """CRYP-28: with a customer-held KEK, data is wrapped under it; once the customer disables it, reads are
    refused as KEY_UNAVAILABLE (not reported as erased), new writes are refused, and nothing is re-wrapped under
    the platform key. A tenant on the platform key is unaffected."""
    import dataclasses

    from zid_crypto.providers import KeyRef
    from zid_platform.modules.evidence.ports import LedgerError

    tenant, other, person, newcomer = uid(), uid(), uid(), uid()
    customer = KeyRef(rt.subject_kek.purpose, "customer-kek-1")
    rt.provider.create_key(customer.purpose, customer.key_id)  # type: ignore[attr-defined]
    byok = dataclasses.replace(rt, tenant_keks={tenant: customer})
    configure_runtime(byok)

    async def kek_ids() -> list[str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = await c.execute(
                text("SELECT kek_id FROM evidence.subject_key WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
            return list(rows.scalars().all())

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            oid, _ = await vault.store_object(
                c,
                tenant,
                content=b"held under the customer key",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=person,
            )
            assert await vault.read_object(c, tenant, oid) == b"held under the customer key"
        assert await kek_ids() == ["customer-kek-1"]  # the customer's key, not the platform's

        await byok.provider.destroy_key(customer)  # the customer switches the key off
        with pytest.raises(LedgerError) as e:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await vault.read_object(c, tenant, oid)
        assert (
            e.value.code == "EVIDENCE_KEY_UNAVAILABLE"
        )  # fail closed, and not mistaken for erasure
        with pytest.raises(CryptoError):
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await vault.store_object(
                    c,
                    tenant,
                    content=b"new",
                    object_type="verification.document",
                    classification="D3",
                    retention_class="raw",
                    subject_principal_id=newcomer,
                )
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(
                c, rt=byok
            )  # the background worker does not move it to the platform key
        assert await kek_ids() == ["customer-kek-1"]
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(other)) as c,
        ):  # platform-key tenant
            oid2, _ = await vault.store_object(
                c,
                other,
                content=b"platform key",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=uid(),
            )
            assert await vault.read_object(c, other, oid2) == b"platform key"

    asyncio.run(run())


def test_a_destroyed_subject_key_cannot_be_reactivated_even_by_a_restore(
    rt_url: str, owner_url: str, rt: EvidenceRuntime
) -> None:
    """CRYP-31: once a subject key is DESTROYED the database refuses to make it ACTIVE again, so a restore of
    old rows cannot bring erased data back; the key's KEK version is preserved with the row."""
    from sqlalchemy.exc import DBAPIError

    tenant, person = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await vault.store_object(
                c,
                tenant,
                content=b"to be erased",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=person,
            )
        ref = await vault.subject_ref(tenant, person)
        async with engine(owner_url) as eng, eng.begin() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT kek_id, kek_version FROM evidence.subject_key "
                        "WHERE tenant_id = :t AND subject_ref = :s"
                    ),
                    {"t": uuid.UUID(tenant), "s": ref},
                )
            ).one()
            assert (row.kek_id, row.kek_version) == (rt.subject_kek.key_id, 1)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await vault.erase_subject(c, tenant, person, request_ref=uid(), rt=rt)
        with pytest.raises(DBAPIError):
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE evidence.subject_key SET state = 'ACTIVE' "
                        "WHERE tenant_id = :t AND subject_ref = :s"
                    ),
                    {"t": uuid.UUID(tenant), "s": ref},
                )

    asyncio.run(run())
