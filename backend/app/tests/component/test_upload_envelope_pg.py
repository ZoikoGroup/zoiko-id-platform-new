"""Uploaded evidence is stored encrypted under the tenant's key (ZID-PRI-004 PR-05, PRI-SEC-01), and erasing it erases the key too."""

from __future__ import annotations

import asyncio
import uuid

from conftest import ctx, engine
from sqlalchemy import text
from test_v2_authority_pg import UNSIGNED_PDF
from test_v2_facade_pg import world
from test_v2_uploads_pg import announce, put
from zid_db import tenant_transaction


def stored(rt_url: str, tenant: str, ref: str) -> dict:
    async def go() -> dict:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = (
                await c.execute(
                    text(
                        "SELECT content, content_encrypted, wrapped_dek, dek_kek_id, state FROM evidence.upload WHERE upload_id = :i"
                    ),
                    {"i": uuid.UUID(ref.removeprefix("ev_"))},
                )
            ).one()
            return {
                "content": bytes(r[0]) if r[0] is not None else None,
                "encrypted": r[1],
                "wrapped": r[2],
                "kek": r[3],
                "state": r[4],
            }

    return asyncio.run(go())


def test_bytes_at_rest_are_ciphertext_still_validate_and_leave_no_key_after_erasure(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, tenant, _admin):
        a = announce(http, mine, UNSIGNED_PDF, "application/pdf").json()
        assert put(http, mine, a, UNSIGNED_PDF).status_code == 201
        ref = a["evidence_ref"]

        row = stored(rt_url, tenant, ref)
        assert row["state"] == "READY" and row["encrypted"] is True
        assert row["content"] is not None and b"%PDF" not in row["content"], "no plaintext at rest"
        assert row["content"] != UNSIGNED_PDF and len(row["content"]) >= len(UNSIGNED_PDF) + 28
        assert row["wrapped"] and row["kek"]

        # the bytes still open for a check (the read path decrypts)
        v = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={"evidence_ref": ref, "declared_type": "application/pdf", "checks": ["integrity"]},
        )
        assert v.status_code == 200 and v.json()["document_results"], v.text

        # erasing the upload removes the bytes and the wrapped key: nothing is left that could open them
        assert http.delete(f"/v2/evidence/{ref}", headers=mine).status_code == 200
        gone = stored(rt_url, tenant, ref)
        assert gone["content"] is None and gone["wrapped"] is None
