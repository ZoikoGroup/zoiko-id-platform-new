"""Documents on an approval request (ZID-UX-WF-013C AT-12): scanned, expiring, scoped to the people who decide, outside URLs and logs,
and refused before anything is stored when the file, the scan or the organization's region says no."""

# ruff: noqa: F811  (the `world` fixture of the approvals tests is imported by name and used as a test argument)
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_approvals_pg import OWNER_TRANSFER, World, code, world  # noqa: F401  (the fixture is used by name)
from test_org_claim_proofs_pg import ACTIVE_PDF, EXE, OTHER_PDF, PDF, FakeScanner
from test_step_up_pg import app_for
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.application import approval_attachments as service
from zid_platform.modules.organization.ports import ProofError, ScanStatus
from zid_platform.modules.tenant.application import residency

KIND = "SUPPORTING_DOCUMENT"


def place(w: World) -> None:
    """The organization sits in its home region, so the cell may hold restricted documents."""

    async def work(c: Any) -> None:
        await residency.set_residency(
            c, w.o.tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
            key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=w.o.owner,
        )  # fmt: skip

    w.run(work)


def put(w: World, who: str, case: str, body: bytes, *, kind: str = KIND, name: str | None = "letter.pdf",
        ctype: str = "application/pdf", idem: str | None = None, org: str | None = None) -> Any:  # fmt: skip
    h = {
        **w.h(who),
        "Content-Type": ctype,
        "X-Attachment-Kind": kind,
        "Idempotency-Key": idem or f"att-{uuid.uuid4()}",
    }
    if name:
        h["X-Attachment-Name"] = name
    return w.http.post(
        f"/v1/organizations/{org or w.o.tenant}/approvals/{case}/attachments",
        content=body,
        headers=h,
    )


def counts(w: World, case: str) -> tuple[int, int, int]:
    """(attachment rows, vault objects, rejection audits) for one request, seen by the worker role."""

    async def go() -> tuple[int, int, int]:
        async with engine(w.owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text("SELECT count(*) FROM platform.approval_attachment WHERE case_id = :c"),
                    {"c": uuid.UUID(case)},
                )
            ).scalar_one()
            objs = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM evidence.evidence_object WHERE tenant_id = :t AND object_type = 'approval_attachment'"
                    ),
                    {"t": uuid.UUID(w.o.tenant)},
                )
            ).scalar_one()
            rej = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM evidence.audit_record WHERE event_type = 'approval.attachment.rejected' AND target_ref = :c"
                    ),
                    {"c": case},
                )
            ).scalar_one()
        return int(rows), int(objs), int(rej)

    return asyncio.run(go())


def request(w: World) -> dict[str, Any]:
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert case["case_id"]
    return dict(case)


def test_a_document_is_scanned_recorded_once_and_shown_only_to_those_who_decide(
    world: World,
) -> None:
    w = world
    place(w)
    scanner = FakeScanner()
    w.http.app.state.proof_scanner = scanner
    case = request(w)
    cid = case["case_id"]
    approver, other = w.person("APPROVER"), w.person("OPERATOR")

    made = put(w, w.o.owner, cid, PDF, idem="attach-key-1")
    assert made.status_code == 201, made.text
    a = made.json()
    assert a["scan_status"] == "CLEAN" and a["kind"] == KIND and a["label"] == "letter.pdf"
    assert made.headers["cache-control"] == "no-store"
    assert (
        "url" not in str(a).lower() and "certificate" not in str(a).lower()
    )  # metadata only: no content, no link
    assert scanner.calls == 1

    # a repeat of the key, or of the same bytes, is the same document: nothing is stored or scanned twice
    again = put(w, w.o.owner, cid, PDF, idem="attach-key-1")
    same = put(w, w.o.owner, cid, PDF, idem="attach-key-2")
    assert again.status_code == 200 and same.status_code == 200
    assert again.json()["attachment_id"] == same.json()["attachment_id"] == a["attachment_id"]
    assert (
        put(w, w.o.owner, cid, OTHER_PDF, idem="attach-key-1").json()["error"]["code"]
        == "ATTACHMENT_IDEMPOTENCY_CONFLICT"
    )
    assert counts(w, cid)[:2] == (1, 1) and scanner.calls == 1

    # an approver sees the list and opens the document, and the read is an audit record; a person with no part in it is told there is none
    listed = w.http.get(
        f"/v1/organizations/{w.o.tenant}/approvals/{cid}/attachments", headers=w.h(approver)
    ).json()
    assert [x["attachment_id"] for x in listed["attachments"]] == [a["attachment_id"]]
    assert listed["may_open"] is True and listed["may_attach"] is False
    opened = w.http.get(
        f"/v1/organizations/{w.o.tenant}/approvals/{cid}/attachments/{a['attachment_id']}",
        headers=w.h(approver),
    )
    assert (
        opened.status_code == 200
        and opened.content == PDF
        and opened.headers["x-content-type-options"] == "nosniff"
    )
    assert (
        opened.headers["cache-control"] == "no-store"
        and "attachment" in opened.headers["content-disposition"]
    )
    assert "sandbox" in opened.headers["content-security-policy"]
    reads = [r for r in w.audit("approval.attachment.%") if r.event_type.endswith(".read")]
    assert len(reads) == 1 and reads[0].ec == "E3"
    assert (
        w.http.get(
            f"/v1/organizations/{w.o.tenant}/approvals/{cid}/attachments", headers=w.h(other)
        ).status_code
        == 404
    )
    assert put(w, other, cid, OTHER_PDF).status_code == 404
    # the approver may not attach: only the requester does
    r = put(w, approver, cid, OTHER_PDF)
    assert r.status_code == 403 and code(r) == "ATTACHMENT_NOT_PERMITTED"

    # nothing about the files is in any audit record, event or log line: hashes and ids only
    flat = " ".join(str(x.body) for x in w.audit("approval.attachment.%"))
    assert "certificate" not in flat and "letter.pdf" not in flat and a["content_hash"] in flat

    # once someone has voted the request is what they decided on: no more documents
    assert w.vote(approver, case).status_code == 201
    late = put(w, w.o.owner, cid, OTHER_PDF)
    assert late.status_code == 403 and code(late) == "ATTACHMENT_NOT_PERMITTED"


def test_what_is_not_a_clean_document_is_refused_with_an_audit_record_and_nothing_stored(
    world: World,
) -> None:
    w = world
    place(w)
    scanner = FakeScanner()
    w.http.app.state.proof_scanner = scanner
    cid = request(w)["case_id"]
    cases = [
        (EXE, {"ctype": "application/octet-stream"}, 415, "ATTACHMENT_TYPE_NOT_ALLOWED"),
        (PDF, {"ctype": "image/png"}, 415, "ATTACHMENT_TYPE_MISMATCH"),
        (ACTIVE_PDF, {}, 422, "ATTACHMENT_ACTIVE_CONTENT"),
        (PDF, {"kind": "SELFIE"}, 422, "ATTACHMENT_KIND_UNKNOWN"),
        (b"", {}, 422, "ATTACHMENT_EMPTY"),
    ]
    for body, kw, status, want in cases:
        r = put(w, w.o.owner, cid, body, **kw)
        assert r.status_code == status and code(r) == want, (want, r.text)
    scanner.answer = ScanStatus.INFECTED
    r = put(w, w.o.owner, cid, PDF)
    assert r.status_code == 422 and code(r) == "ATTACHMENT_INFECTED"
    scanner.answer = ScanStatus.UNAVAILABLE
    r = put(w, w.o.owner, cid, PDF)
    assert (
        r.status_code == 503
        and code(r) == "ATTACHMENT_SCAN_UNAVAILABLE"
        and r.json()["error"]["retryable"] is True
    )
    rows, objs, rejected = counts(w, cid)
    assert (
        (rows, objs) == (0, 0) and rejected == 6
    )  # the five bad files and the infected one leave a record; an unavailable scan is only a retry; no file is kept


def test_the_organizations_region_is_checked_before_the_scanner_or_the_vault_sees_the_file(
    world: World,
) -> None:
    w = world
    place(w)
    scanner = FakeScanner()
    w.http.app.state.proof_scanner = scanner
    cid = request(w)["case_id"]

    async def move_region() -> (
        None
    ):  # the company's data region is another cell: this cell may not hold its restricted documents
        async with engine(w.owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE platform.tenant_residency SET home_cell_id = 'eu-1' WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(w.o.tenant)},
            )

    asyncio.run(move_region())
    r = put(w, w.o.owner, cid, PDF)
    assert r.status_code == 403 and code(r) == "ATTACHMENT_RESIDENCY_DENIED"
    assert scanner.calls == 0 and counts(w, cid)[:2] == (0, 0)


def test_a_deployed_environment_without_a_scanner_stores_nothing(world: World) -> None:
    w = world
    place(w)
    cid = request(w)["case_id"]
    w.http.app.state.proof_scanner = None
    w.http.app.state.proof_scanner_address = None
    w.http.app.state.environment = "production"
    try:
        r = put(w, w.o.owner, cid, PDF)
    finally:
        w.http.app.state.environment = "test"
    assert r.status_code == 503 and code(r) == "ATTACHMENT_SCAN_UNAVAILABLE"
    assert counts(w, cid)[:2] == (0, 0)


def test_access_ends_with_the_request_and_the_record_is_never_edited_or_shared(
    world: World, rt_url: str
) -> None:
    w = world
    place(w)
    w.http.app.state.proof_scanner = FakeScanner()
    cid = request(w)["case_id"]
    a = put(w, w.o.owner, cid, PDF).json()

    async def go(now: datetime) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            return await service.read(
                c, w.o.tenant, cid, a["attachment_id"], reader=w.o.owner, now=now
            )

    assert asyncio.run(go(datetime.now(UTC)))[1] == PDF
    after = datetime.fromisoformat(a["access_ends_at"]) + timedelta(seconds=1)
    with pytest.raises(ProofError) as e:
        asyncio.run(go(after))
    assert e.value.code == "ATTACHMENT_EXPIRED"

    for sql in (
        "UPDATE platform.approval_attachment SET kind = 'OTHER' WHERE case_id = :c",
        "DELETE FROM platform.approval_attachment WHERE case_id = :c",
    ):

        async def attempt(statement: str = sql) -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
                await c.execute(text(statement), {"c": uuid.UUID(cid)})

        with pytest.raises(DBAPIError):
            asyncio.run(attempt())

    # another organization sees none of it, by id or by row
    other = onboard(rt_url)
    with TestClient(app_for(rt_url, other, "shadow"), base_url=ISS) as elsewhere:
        r = elsewhere.get(
            f"/v1/organizations/{w.o.tenant}/approvals/{cid}/attachments",
            headers=other.bearer(other.owner),
        )
        assert r.status_code == 404 and r.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"

    async def visible() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other.tenant)) as c:
            return len((await c.execute(text("SELECT 1 FROM platform.approval_attachment"))).all())

    assert asyncio.run(visible()) == 0
