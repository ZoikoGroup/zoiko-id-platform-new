"""Per-class encryption of biometric objects in the evidence vault (ZID-DTV-WF-04 QA-021; CR-113), on PostgreSQL.

A biometric asset class (`biometric.<name>`) is stored under its own key per subject, derived from the subject's erasable key: the stored key
reference says so, the object reads back, the subject key alone does not open it, re-keying the subject keeps it readable, and erasing the subject
still erases every class. Other object types are unchanged.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from conftest import ctx, engine, fresh_database
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    development_runtime,
)


@pytest.fixture(scope="module")
def isolated() -> Iterator[tuple[str, str]]:
    with (
        fresh_database() as urls
    ):  # keys live in a per-test provider; keep the rows out of the shared database
        yield urls


@pytest.fixture
def rt() -> Iterator[EvidenceRuntime]:
    runtime = development_runtime("uk-1")
    configure_runtime(runtime)
    yield runtime
    configure_runtime(None)


def test_each_biometric_class_is_under_its_own_key(
    isolated: tuple[str, str], rt: EvidenceRuntime
) -> None:
    owner_url, rt_url = isolated
    tenant, subject = str(uuid.uuid4()), str(uuid.uuid4())
    contents = {
        "biometric.face_template": b"template-bytes",
        "biometric.match_result": b"match-result",
        "verification.document": b"document-bytes",
    }

    async def run() -> None:
        ids: dict[str, str] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for object_type, content in contents.items():
                ids[object_type], _ = await vault.store_object(
                    c, tenant, content=content, object_type=object_type, classification="D4", retention_class="raw",
                    subject_principal_id=subject,
                )  # fmt: skip
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = {
                str(r.evidence_object_id): r
                for r in (
                    await c.execute(
                        text(
                            "SELECT evidence_object_id, object_type, ciphertext, encryption_key_ref FROM evidence.evidence_object WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            }
            ref = await vault.subject_ref(tenant, subject)
            got = await vault._subject_key(c, rt, tenant, ref, create=False)
            assert got is not None
            subject_key, _ = got
        for object_type, oid in ids.items():
            r = rows[oid]
            assert r.encryption_key_ref.endswith(vault.CLASS_KEY_MARK) is object_type.startswith(
                "biometric."
            )
            aad = vault._object_aad(tenant, oid, object_type).encode()
            if object_type.startswith("biometric."):
                with pytest.raises(Exception):  # noqa: B017 - the subject key alone does not open a class-keyed object
                    vault._decrypt(subject_key, aad, bytes(r.ciphertext))
                other = (
                    "biometric.match_result"
                    if object_type == "biometric.face_template"
                    else "biometric.face_template"
                )
                with pytest.raises(Exception):  # noqa: B017 - nor does another class's key
                    vault._decrypt(
                        vault.class_key(subject_key, tenant, other), aad, bytes(r.ciphertext)
                    )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for object_type, oid in ids.items():
                assert await vault.read_object(c, tenant, oid) == contents[object_type]

        # re-keying the subject keeps every class readable and keeps the class marking
        async with engine(owner_url) as eng, eng.begin() as c:
            report = await vault.rekey_subject(c, tenant, ref, rt=rt)
        assert report is not None and report.objects_reencrypted == 3
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for object_type, oid in ids.items():
                assert await vault.read_object(c, tenant, oid) == contents[object_type]
        async with engine(owner_url) as eng, eng.begin() as c:
            refs = {
                r.object_type: r.encryption_key_ref
                for r in (
                    await c.execute(
                        text(
                            "SELECT object_type, encryption_key_ref FROM evidence.evidence_object WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            }
        assert (
            refs["biometric.face_template"] == f"subject:{report.new_key_id}{vault.CLASS_KEY_MARK}"
        )
        assert refs["verification.document"] == f"subject:{report.new_key_id}"

        # erasing the subject still erases every class at once
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await vault.erase_subject(c, tenant, subject, request_ref="erasure-1")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for oid in ids.values():
                with pytest.raises(Exception):  # noqa: B017 - EVIDENCE_RAW_OBJECT_ERASED
                    await vault.read_object(c, tenant, oid)

    asyncio.run(run())
