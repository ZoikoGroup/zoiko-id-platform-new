"""Evidence uploads held in the cell database (ZID-INT-002 sections 05 and 09; evidence_0005).

The rules proved here: bytes are kept only after a clean scan (and the database itself refuses any other combination); every refusal is
recorded with its reason and keeps no bytes; a deployed cell with no scanner or no retention setting accepts nothing; deleting or expiring
erases the bytes; evidence that is not READY and CLEAN can never back a check; a person's uploads go with their erasure.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_v2_facade_pg import BODY, uid, world
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import scanner as sc
from zid_platform.modules.evidence.application import uploads as svc
from zid_platform.modules.evidence.infrastructure import upload_store as store


def png_with_metadata() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", 0)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", b"\x00" * 13)
        + chunk(b"tEXt", b"Author\x00Dana Lee GPS 51.5N")
        + chunk(b"IDAT", b"\x01\x02\x03")
        + chunk(b"IEND", b"")
    )


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def announce(http, headers, content: bytes, kind: str = "image/png", key: str | None = None):
    return http.post(
        "/v2/evidence/uploads",
        headers={**headers, "Idempotency-Key": key or f"up-{uid()}"},
        json={"declared_type": kind, "size_bytes": len(content), "sha256": sha(content)},
    )


def put(http, headers, announced: dict, content: bytes, token: str | None = None):
    return http.put(
        announced["upload_url"],
        headers={
            **headers,
            "X-Upload-Token": token or announced["upload_token"],
            "Content-Type": "application/octet-stream",
        },
        content=content,
    )


def row(rt_url: str, tenant: str, evidence_ref: str) -> dict:
    async def go() -> dict:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = (
                (
                    await c.execute(
                        text(
                            "SELECT state, scan_state, reject_reason, content, stored_sha256 FROM evidence.upload WHERE upload_id = :i"
                        ),
                        {"i": uuid.UUID(evidence_ref.removeprefix("ev_"))},
                    )
                )
                .mappings()
                .one()
            )
            return dict(r)

    return asyncio.run(go())


def test_a_clean_upload_is_kept_stripped_and_readable_only_as_a_manifest(rt_url: str) -> None:
    content = png_with_metadata()
    with world(rt_url) as (http, mine, tenant, _admin):
        made = announce(http, mine, content, key="up-key-0001")
        assert made.status_code == 201, made.text
        a = made.json()
        assert (
            a["evidence_ref"].startswith("ev_")
            and a["upload_token"]
            and a["max_bytes"] == 5 * 1024 * 1024
        )
        assert datetime.fromisoformat(a["retained_until"]) > datetime.now(UTC) + timedelta(days=29)
        assert row(rt_url, tenant, a["evidence_ref"])["content"] is None, (
            "nothing is held before the bytes arrive"
        )

        # replaying the announcement returns the same upload, not a second one
        assert (
            announce(http, mine, content, key="up-key-0001").json()["evidence_ref"]
            == a["evidence_ref"]
        )

        sent = put(http, mine, a, content)
        assert sent.status_code == 201, sent.text
        m = sent.json()
        assert m["state"] == "READY" and m["scan"] == {
            "state": "CLEAN",
            "scanner": "simulator-eicar",
        }
        assert m["metadata_stripped"] is True and m["sniffed_type"] == "image/png"
        stored = row(rt_url, tenant, a["evidence_ref"])
        assert b"Dana Lee" not in bytes(stored["content"]) and b"GPS" not in bytes(
            stored["content"]
        )
        assert (
            stored["stored_sha256"] == sha(bytes(stored["content"])) == m["sha256"] != sha(content)
        )

        # the manifest is metadata only: no bytes, no claimed text
        got = http.get(f"/v2/evidence/{a['evidence_ref']}/manifest", headers=mine)
        assert got.status_code == 200 and got.json()["state"] == "READY"
        assert "content" not in got.json() and "Dana" not in got.text
        # the same bytes cannot be sent twice
        assert put(http, mine, a, content).status_code == 409


def test_every_refusal_is_recorded_with_its_reason_and_keeps_no_bytes(rt_url: str) -> None:
    good = png_with_metadata()
    with world(rt_url) as (http, mine, tenant, _admin):
        # bytes that are not what was announced: wrong size, wrong digest, wrong type
        for announced_for, sent, code in (
            (good, good + b"x", "evidence_rejected_size_mismatch"),
            (good, good[:-1] + b"X", "evidence_rejected_digest_mismatch"),
            (
                b"%PDF-1.7 a pdf",
                b"%PDF-1.7 a pdf",
                "evidence_rejected_type_mismatch",
            ),  # announced as PNG below
        ):
            a = announce(http, mine, announced_for).json()
            r = put(http, mine, a, sent)
            assert r.status_code == 422 and r.json()["code"] == code, r.text
            held = row(rt_url, tenant, a["evidence_ref"])
            assert (
                held["state"] == "REJECTED"
                and held["content"] is None
                and held["reject_reason"] == code.removeprefix("evidence_rejected_").upper()
            )

        # the EICAR test string is caught by the scanner: INFECTED, recorded, no bytes
        eicar = png_with_metadata() + sc.EICAR
        a = announce(http, mine, eicar).json()
        r = put(http, mine, a, eicar)
        assert r.status_code == 422 and r.json()["code"] == "evidence_rejected_infected"
        held = row(rt_url, tenant, a["evidence_ref"])
        assert (held["state"], held["scan_state"], held["content"]) == (
            "REJECTED",
            "INFECTED",
            None,
        )

        # a scanner that fails is never "clean"
        class Broken:
            name = "broken"

            async def scan(self, content: bytes):
                raise RuntimeError("down")

        http.app.state.evidence_scanner = Broken()
        a = announce(http, mine, good).json()
        r = put(http, mine, a, good)
        assert r.status_code == 422 and r.json()["code"] == "evidence_rejected_scan_error"
        held = row(rt_url, tenant, a["evidence_ref"])
        assert (held["state"], held["scan_state"], held["content"]) == (
            "REJECTED",
            "SCAN_ERROR",
            None,
        )

        # and a cell with no scanner at all accepts no announcement
        http.app.state.evidence_scanner = None
        none = announce(http, mine, good)
        assert none.status_code == 503 and none.json()["code"] == "provider_unavailable"


def test_the_token_is_for_one_upload_and_a_cell_without_retention_accepts_nothing(
    rt_url: str,
) -> None:
    good = png_with_metadata()
    with world(rt_url) as (http, mine, tenant, _admin):
        one, two = announce(http, mine, good).json(), announce(http, mine, good).json()
        assert (
            put(http, mine, one, good, token=two["upload_token"]).status_code == 403
        )  # another upload's token
        assert put(http, mine, one, good, token="x" * 40).status_code == 403
        assert row(rt_url, tenant, one["evidence_ref"])["state"] == "AWAITING_CONTENT", (
            "a refused token leaves the upload as it was"
        )
        assert put(http, mine, one, good).status_code == 201
        assert http.put(
            f"/v2/evidence/uploads/{uid()}/content",
            headers={**mine, "X-Upload-Token": two["upload_token"]},
            content=good,
        ).status_code in (403, 404)
        too_big = http.put(
            one["upload_url"],
            headers={**mine, "X-Upload-Token": one["upload_token"]},
            content=b"0" * (5 * 1024 * 1024 + 1),
        )
        assert too_big.status_code == 413
    with world(rt_url, retention=None) as (http, mine, _tenant, _admin):
        r = announce(http, mine, good)
        assert r.status_code == 503 and "retention" in r.text.lower().replace("_", " ")


def test_only_ready_and_clean_evidence_can_back_a_check_or_be_validated(rt_url: str) -> None:
    pdf = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    with world(rt_url) as (http, mine, tenant, _admin):
        ok = announce(http, mine, pdf, "application/pdf").json()
        assert put(http, mine, ok, pdf).status_code == 201
        rejected = announce(http, mine, pdf, "application/pdf").json()
        assert put(http, mine, rejected, pdf + b"x").status_code == 422
        waiting = announce(http, mine, pdf, "application/pdf").json()

        def verify(refs: list[str], key: str):
            return http.post(
                "/v2/credentials/verify",
                headers={**mine, "Idempotency-Key": key},
                json={**BODY, "evidence_refs": refs},
            )

        assert verify([ok["evidence_ref"]], "ev-key-0001").status_code == 201
        for bad, key in (
            (rejected["evidence_ref"], "ev-key-0002"),
            (waiting["evidence_ref"], "ev-key-0003"),
            (f"ev_{uid()}", "ev-key-0004"),
            ("ev_not-a-uuid", "ev-key-0005"),
        ):
            r = verify([bad], key)
            assert r.status_code == 422 and r.json()["code"] == "evidence_not_usable", (bad, r.text)
        assert (
            verify([ok["evidence_ref"], rejected["evidence_ref"]], "ev-key-0006").status_code == 422
        ), "one bad reference refuses the whole check"

        # the same evidence can be validated by reference, and the answer still never says "authentic"
        v = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "evidence_ref": ok["evidence_ref"],
                "declared_type": "application/pdf",
                "expected_sha256": sha(pdf),
                "checks": ["integrity", "signature"],
            },
        )
        assert (
            v.status_code == 200
            and v.json()["document_results"][0]["status"] == "VERIFIED"
            and v.json()["stored"] is True
        )
        assert (
            http.post(
                "/v2/documents/validate",
                headers=mine,
                json={
                    "evidence_ref": rejected["evidence_ref"],
                    "declared_type": "application/pdf",
                    "checks": ["integrity"],
                },
            ).json()["code"]
            == "evidence_not_usable"
        )
        both = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "evidence_ref": ok["evidence_ref"],
                "content_base64": "AAAA",
                "declared_type": "application/pdf",
                "checks": ["integrity"],
            },
        )
        assert both.status_code == 422


def test_deleting_or_expiring_erases_the_bytes_and_the_row_stays_as_the_record(rt_url: str) -> None:
    good = png_with_metadata()
    with world(rt_url) as (http, mine, tenant, _admin):
        a = announce(http, mine, good).json()
        assert put(http, mine, a, good).status_code == 201
        gone = http.delete(f"/v2/evidence/{a['evidence_ref']}", headers=mine)
        assert (
            gone.status_code == 200
            and gone.json()["state"] == "DELETED"
            and gone.json()["erased_at"]
        )
        held = row(rt_url, tenant, a["evidence_ref"])
        assert held["state"] == "DELETED" and held["content"] is None
        assert (
            http.delete(f"/v2/evidence/{a['evidence_ref']}", headers=mine).status_code == 409
        )  # nothing left to delete
        assert http.delete(f"/v2/evidence/ev_{uid()}", headers=mine).status_code == 404
        r = http.post(
            "/v2/credentials/verify",
            headers={**mine, "Idempotency-Key": "del-key-0001"},
            json={**BODY, "evidence_refs": [a["evidence_ref"]]},
        )
        assert r.status_code == 422, "deleted evidence backs nothing"

        # retention running out erases it too (the worker step calls this)
        b = announce(http, mine, good).json()
        assert put(http, mine, b, good).status_code == 201

        async def expire_then_purge() -> int:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(
                    text(
                        "UPDATE evidence.upload SET expires_at = now() - interval '1 second', created_at = now() - interval '2 days' WHERE upload_id = :i"
                    ),
                    {"i": uuid.UUID(b["evidence_ref"].removeprefix("ev_"))},
                )
                return await svc.purge_expired(c, datetime.now(UTC))

        assert asyncio.run(expire_then_purge()) == 1
        held = row(rt_url, tenant, b["evidence_ref"])
        assert held["state"] == "EXPIRED" and held["content"] is None


def test_the_database_itself_refuses_bytes_for_anything_but_a_ready_clean_upload(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()
    now = datetime.now(UTC)

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.insert(
                    c,
                    upload_id=uid(),
                    tenant_id=tenant,
                    subject=subject,
                    uploaded_by=subject,
                    declared_type="image/png",
                    size_bytes=3,
                    sha256="a" * 64,
                    now=now,
                    token_expires_at=now + timedelta(minutes=5),
                    expires_at=now + timedelta(days=1),
                )
            for sql in (
                "UPDATE evidence.upload SET content = '\\x010203'::bytea",  # bytes with no scan
                "UPDATE evidence.upload SET state = 'READY', scan_state = 'INFECTED', stored_sha256 = repeat('a', 64), content = '\\x01'::bytea",
                "UPDATE evidence.upload SET state = 'READY', scan_state = 'CLEAN', stored_sha256 = repeat('a', 64)",  # READY and CLEAN without bytes
                "UPDATE evidence.upload SET state = 'REJECTED'",  # a refusal with no reason
            ):
                with pytest.raises(DBAPIError):
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        await c.execute(text(sql))

    asyncio.run(go())


def test_another_tenant_sees_nothing_and_erasing_a_person_removes_their_uploads(
    rt_url: str,
) -> None:
    a, b, subject, other = uid(), uid(), uid(), uid()
    now = datetime.now(UTC)
    ids = [uid(), uid()]

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a)) as c:
                for upload_id, person in ((ids[0], subject), (ids[1], other)):
                    await store.insert(
                        c,
                        upload_id=upload_id,
                        tenant_id=a,
                        subject=person,
                        uploaded_by=person,
                        declared_type="image/png",
                        size_bytes=3,
                        sha256="a" * 64,
                        now=now,
                        token_expires_at=now + timedelta(minutes=5),
                        expires_at=now + timedelta(days=1),
                    )
                    await store.accept(
                        c,
                        upload_id,
                        content=b"\x01\x02\x03",
                        sniffed_type="image/png",
                        stored_sha256="b" * 64,
                        exif_stripped=False,
                        scanner="t",
                        now=now,
                    )
            async with tenant_transaction(eng, ctx(b)) as c:  # row-level security
                assert (
                    await store.load(c, ids[0]) is None and await store.by_subject(c, subject) == []
                )
                assert await store.delete_subject(c, b, subject) == 0
            async with tenant_transaction(eng, ctx(a)) as c:
                from zid_platform import evidence_privacy_adapter as adapter

                found = await adapter._locate(c, subject)
                assert [f["object_ref"] for f in found] == [ids[0]] and found[0][
                    "data_category"
                ] == "D3"
                exported = await adapter._export(c, type("E", (), {"object_ref": ids[0]})())
                assert "content" not in exported and exported["state"] == "READY"
                await adapter._dispose(
                    c, type("E", (), {"tenant_id": a, "subject_principal_id": subject})(), "DELETE"
                )
                assert await store.by_subject(c, subject) == []
                assert len(await store.by_subject(c, other)) == 1, (
                    "someone else's upload is untouched"
                )

    asyncio.run(go())


def test_deleting_or_expiring_announces_the_erasure_and_a_stored_validation_goes_with_its_upload(
    rt_url: str,
) -> None:
    """ZID-INT-002 section 13 (`evidence.retention_action`) and section 09 (`GET /v2/documents/{id}`)."""
    pdf = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    with world(rt_url) as (http, mine, tenant, _admin):
        a = announce(http, mine, pdf, "application/pdf").json()
        assert put(http, mine, a, pdf).status_code == 201
        body = {
            "evidence_ref": a["evidence_ref"],
            "declared_type": "application/pdf",
            "expected_sha256": sha(pdf),
            "checks": ["integrity", "issuer", "signature"],
        }
        v = http.post("/v2/documents/validate", headers=mine, json=body).json()
        assert v["stored"] is True and v["validation_id"].startswith("docval_")

        got = http.get(f"/v2/documents/{v['validation_id']}", headers=mine)
        assert got.status_code == 200, got.text
        g = got.json()
        assert (
            g["evidence_ref"] == a["evidence_ref"]
            and g["upload_state"] == "READY"
            and g["sha256"] == sha(pdf)
        )
        assert {r["check"]: r["status"] for r in g["document_results"]} == {
            "integrity": "VERIFIED",
            "issuer": "UNAVAILABLE",
            "signature": "INDETERMINATE",
        }
        assert "content" not in got.text and "%PDF" not in got.text, (
            "the result never carries the document"
        )
        assert http.get(f"/v2/documents/docval_{uid()}", headers=mine).status_code == 404
        assert http.get("/v2/documents/not-a-ref", headers=mine).status_code == 422

        # pasted content leaves no stored result at all
        import base64

        pasted = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "content_base64": base64.b64encode(pdf).decode(),
                "declared_type": "application/pdf",
                "checks": ["integrity"],
            },
        ).json()
        assert pasted["stored"] is False and pasted["validation_id"] is None

        async def events() -> list[dict]:
            import json as _json

            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                rows = (
                    (
                        await c.execute(
                            text(
                                "SELECT payload::text FROM events.outbox_event WHERE event_type LIKE '%evidence.retention_action%' ORDER BY created_at"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return [_json.loads(r) for r in rows]

        assert asyncio.run(events()) == [], "nothing is announced while the bytes are still held"
        gone = http.delete(f"/v2/evidence/{a['evidence_ref']}", headers=mine)
        assert gone.status_code == 200
        sent = asyncio.run(events())
        assert len(sent) == 1
        data = sent[0].get("data", sent[0])
        assert (
            data["evidence_ref"] == a["evidence_ref"]
            and data["action"] == "DELETED"
            and data["bytes_erased"] is True
        )
        assert "sha256" not in data and "content" not in data
        # the stored result stays with the upload's record but says the bytes are gone
        assert (
            http.get(f"/v2/documents/{v['validation_id']}", headers=mine).json()["upload_state"]
            == "DELETED"
        )
        # a second delete announces nothing more
        assert http.delete(f"/v2/evidence/{a['evidence_ref']}", headers=mine).status_code == 409
        assert len(asyncio.run(events())) == 1

        # expiry announces too (the worker step calls this)
        b = announce(http, mine, pdf, "application/pdf").json()
        assert put(http, mine, b, pdf).status_code == 201

        async def expire() -> int:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(
                    text(
                        "UPDATE evidence.upload SET expires_at = now() - interval '1 second', created_at = now() - interval '2 days' WHERE upload_id = :i"
                    ),
                    {"i": uuid.UUID(b["evidence_ref"].removeprefix("ev_"))},
                )
                return await svc.purge_expired(c, datetime.now(UTC))

        assert asyncio.run(expire()) == 1
        actions = [e.get("data", e)["action"] for e in asyncio.run(events())]
        assert actions == ["DELETED", "EXPIRED"]


def test_the_upload_list_is_metadata_only_newest_first_and_filterable_by_person(
    rt_url: str,
) -> None:
    good = png_with_metadata()
    with world(rt_url) as (http, mine, tenant, admin):
        first, second = announce(http, mine, good).json(), announce(http, mine, good).json()
        assert put(http, mine, first, good).status_code == 201
        listed = http.get("/v2/evidence/uploads", headers=mine)
        assert listed.status_code == 200, listed.text
        refs = [i["evidence_ref"] for i in listed.json()["items"]]
        assert refs == [second["evidence_ref"], first["evidence_ref"]], "newest first"
        assert "content" not in listed.text and "Dana" not in listed.text
        states = {i["evidence_ref"]: i["state"] for i in listed.json()["items"]}
        assert states == {
            first["evidence_ref"]: "READY",
            second["evidence_ref"]: "AWAITING_CONTENT",
        }
        assert (
            len(
                http.get(f"/v2/evidence/uploads?subject_ref=sub_{admin}", headers=mine).json()[
                    "items"
                ]
            )
            == 2
        )
        assert (
            http.get(f"/v2/evidence/uploads?subject_ref=sub_{uid()}", headers=mine).json()["items"]
            == []
        )
        assert http.get("/v2/evidence/uploads?subject_ref=nope", headers=mine).status_code == 422


def test_a_really_signed_pdf_is_verified_through_the_api_against_the_configured_anchors(
    rt_url: str,
) -> None:
    """DTV-02 end to end: upload a genuinely signed PDF, validate it by reference, and see exactly what the deployment's trust allows."""
    import base64
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
    from pdf_signing import pki, sign_pdf
    from zid_platform.modules.evidence.application.trust import TrustConfig

    p, stranger = pki(), pki()
    signed = sign_pdf(p)
    tampered = sign_pdf(p, tamper_document=True)
    with world(rt_url) as (http, mine, _tenant, _admin):

        def validate(ref: str, config: TrustConfig):
            http.app.state.trust_config = config
            body = {
                "evidence_ref": ref,
                "declared_type": "application/pdf",
                "expected_sha256": sha(signed),
                "checks": ["integrity", "issuer", "signature", "seal"],
            }
            r = http.post("/v2/documents/validate", headers=mine, json=body)
            assert r.status_code == 200, r.text
            return {x["check"]: x for x in r.json()["document_results"]}, r.json()

        up = announce(http, mine, signed, "application/pdf").json()
        assert put(http, mine, up, signed).status_code == 201
        ref = up["evidence_ref"]

        trusted, full = validate(ref, TrustConfig(anchors=(p.root.cert,)))
        assert trusted["signature"]["status"] == "VERIFIED_LIMITED"
        assert {
            "CHAIN_TO_CONFIGURED_ANCHOR",
            "REVOCATION_NOT_CHECKED",
            "SIGNING_TIME_NOT_TRUSTED",
        } <= set(trusted["signature"]["reasons"])
        assert (
            trusted["integrity"]["status"] == "VERIFIED"
            and trusted["issuer"]["status"] == "UNAVAILABLE"
        ), "the issuer is still not authenticated"
        assert (
            trusted["seal"]["status"] == "UNAVAILABLE"
            and trusted["seal"]["detail"]["qualified_status"] == "NOT_ASSESSED"
        )
        assert "signer_serial" in trusted["signature"]["detail"]["signatures"][
            0
        ] and "chain_certificates" not in str(full)

        nothing, _ = validate(ref, TrustConfig())  # the shipped default: nothing is trusted
        assert (
            nothing["signature"]["status"] == "INDETERMINATE"
            and "NO_TRUST_ANCHORS_CONFIGURED" in nothing["signature"]["reasons"]
        )
        other, _ = validate(ref, TrustConfig(anchors=(stranger.root.cert,)))
        assert (
            other["signature"]["status"] == "INDETERMINATE"
            and "ISSUER_NOT_TRUSTED" in other["signature"]["reasons"]
        )

        # a document changed after signing: not verified, whatever is trusted
        bad = announce(http, mine, tampered, "application/pdf").json()
        assert put(http, mine, bad, tampered).status_code == 201
        changed, _ = validate(bad["evidence_ref"], TrustConfig(anchors=(p.root.cert,)))
        assert (
            changed["signature"]["status"] == "NOT_VERIFIED"
            and "DOCUMENT_ALTERED_AFTER_SIGNING" in changed["signature"]["reasons"]
        )

        # pasted content gets the same real check, and leaves no stored result
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        pasted = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "content_base64": base64.b64encode(signed).decode(),
                "declared_type": "application/pdf",
                "checks": ["signature"],
            },
        ).json()
        assert (
            pasted["stored"] is False
            and pasted["document_results"][0]["status"] == "VERIFIED_LIMITED"
        )
        # the stored result is readable later, and says what it was judged against
        stored = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={"evidence_ref": ref, "declared_type": "application/pdf", "checks": ["signature"]},
        ).json()
        later = http.get(f"/v2/documents/{stored['validation_id']}", headers=mine).json()
        assert (
            later["document_results"][0]["status"] == "VERIFIED_LIMITED"
            and later["document_results"][0]["detail"]["trust_notes"]
        )
