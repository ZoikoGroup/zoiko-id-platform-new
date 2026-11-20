"""Proof documents for a company claim over HTTP (WF-13B section 06, acceptance AT-12).

A proof is judged by its own bytes, checked against the tenant's residency before the scanner or the vault sees it, scanned before it is
stored, stored encrypted, and recorded once. A refused file leaves an audit record and nothing else. An applicant never gets a document
back; a reviewer with the protected action does, and every read is audited. A proof never changes what a company is."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from sqlalchemy import text
from test_org_claims_api_pg import REVIEW_ACTIONS, REVIEW_RESOURCES, Register, error
from test_org_claims_trust_pg import CLAIM, app_for, key
from zid_db import tenant_transaction
from zid_platform.modules.organization.infrastructure.proof_scan import ScanStatus
from zid_platform.modules.tenant.application import residency

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% Acme Widgets Limited certificate of incorporation\n%%EOF\n"
OTHER_PDF = PDF.replace(b"Acme", b"Beta")
EXE = b"MZ\x90\x00" + b"\x00" * 200
ACTIVE_PDF = PDF.replace(
    b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >>"
)


class FakeScanner:
    def __init__(self, answer: ScanStatus = ScanStatus.CLEAN) -> None:
        self.answer, self.calls = answer, 0

    async def scan(self, content: bytes) -> ScanStatus:
        self.calls += 1
        return self.answer


def put(api: Any, headers: dict[str, str], claim: str, body: bytes, *, kind: str = "CERTIFICATE_OF_INCORPORATION", name: str | None = "certificate.pdf",
        ctype: str = "application/pdf", idem: str | None = None) -> Any:  # fmt: skip
    h = {
        **headers,
        "Content-Type": ctype,
        "X-Proof-Kind": kind,
        "Idempotency-Key": idem or key("proof")["Idempotency-Key"],
    }
    if name:
        h["X-Proof-Name"] = name
    return api.post(f"/v1/org-claims/{claim}/proofs", content=body, headers=h)


def stored(owner_url: str, tenant: str, claim: str) -> tuple[int, int, int, int]:
    """(proof rows, vault objects, rejection audits, added events) for one tenant's claim, seen by the worker role."""

    async def go() -> tuple[int, int, int, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            q = lambda sql, **p: c.execute(text(sql), p)
            rows = (
                await q("SELECT count(*) FROM org.kyb_proof WHERE case_id = :c", c=claim)
            ).scalar_one()
            objs = (
                await q(
                    "SELECT count(*) FROM evidence.evidence_object WHERE tenant_id = :t AND object_type = 'kyb_proof'",
                    t=uuid.UUID(tenant),
                )
            ).scalar_one()
            rej = (
                await q(
                    "SELECT count(*) FROM evidence.audit_record WHERE event_type = 'organization.kyb.proof_rejected' AND target_ref = :c",
                    c=claim,
                )
            ).scalar_one()
            ev = (
                await q(
                    "SELECT count(*) FROM events.outbox_event WHERE event_type = 'com.zoiko.id.organization.kyb.proof_added.v1' AND tenant_id = :t",
                    t=uuid.UUID(tenant),
                )
            ).scalar_one()
        return int(rows), int(objs), int(rej), int(ev)

    return asyncio.run(go())


def make(
    rt_url: str, scanner: FakeScanner | None, **extra: Any
) -> tuple[Any, TestClient, str, dict[str, str]]:
    """An onboarded organization placed in its home region (a regional profile in uk-1), a client and the owner's headers."""
    o = onboard(rt_url, **extra)

    async def place() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await residency.set_residency(
                c, o.tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
                key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=o.owner,
            )  # fmt: skip

    asyncio.run(place())
    app = app_for(o, rt_url, Register())
    app.state.proof_scanner = scanner
    api = TestClient(app)
    return o, api, o.tenant, o.bearer(o.owner)


def test_a_proof_is_judged_scanned_encrypted_recorded_once_and_never_returned_to_the_applicant(
    rt_url: str, owner_url: str
) -> None:
    scanner = FakeScanner()
    _o, api, tenant, owner = make(rt_url, scanner)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        before = api.get(f"/v1/org-claims/{claim}/status", headers=owner).json()

        r = put(api, owner, claim, PDF, name="../../My <Certificate>.pdf", idem="proof-key-0001")
        assert r.status_code == 201, r.text
        shown = r.json()
        assert (
            shown["media_type"] == "application/pdf"
            and shown["scan_status"] == "CLEAN"
            and shown["size_bytes"] == len(PDF)
        )
        assert shown["label"] == "My Certificate.pdf" and shown["content_hash"].startswith(
            "sha-256:"
        )
        assert (
            "uploaded_by" not in r.text
            and "object" not in r.text
            and PDF.decode("latin-1")[:20] not in r.text
        )
        assert scanner.calls == 1

        # a replay with the same key, or the same file under a new key, is the same proof: nothing is stored twice
        again = put(api, owner, claim, PDF, idem="proof-key-0001")
        assert again.status_code == 200 and again.json()["proof_id"] == shown["proof_id"]
        same = put(api, owner, claim, PDF)
        assert same.status_code == 200 and same.json()["proof_id"] == shown["proof_id"]
        clash = put(api, owner, claim, OTHER_PDF, idem="proof-key-0001")
        assert clash.status_code == 409 and error(clash) == "PROOF_IDEMPOTENCY_CONFLICT"
        assert stored(owner_url, tenant, claim) == (1, 1, 0, 1)

        # it is on the claim as metadata only, and the vault holds it encrypted
        listed = api.get(f"/v1/org-claims/{claim}/proofs", headers=owner).json()
        assert [p["proof_id"] for p in listed["proofs"]] == [
            shown["proof_id"]
        ] and "ciphertext" not in str(listed)

        async def raw() -> bytes:
            async with engine(owner_url) as eng, eng.begin() as c:
                return bytes(
                    (
                        await c.execute(
                            text(
                                "SELECT ciphertext FROM evidence.evidence_object WHERE tenant_id = :t AND object_type = 'kyb_proof'"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    ).scalar_one()
                )

        blob = asyncio.run(raw())
        assert b"%PDF" not in blob and b"Acme" not in blob, "the document is not readable at rest"

        # a proof is evidence for a reviewer: the company is exactly what it was
        after = api.get(f"/v1/org-claims/{claim}/status", headers=owner).json()
        assert (after["outcome"], after["checks"], after["company"]) == (
            before["outcome"],
            before["checks"],
            before["company"],
        )


def test_what_is_not_a_clean_document_is_refused_with_an_audit_record_and_nothing_stored(
    rt_url: str, owner_url: str
) -> None:
    scanner = FakeScanner()
    _o, api, tenant, owner = make(rt_url, scanner)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        cases = [
            (EXE, {"ctype": "application/pdf"}, 415, "PROOF_TYPE_NOT_ALLOWED"),
            (b"%PDF-1.4\n%%EOF\n", {"ctype": "image/png"}, 415, "PROOF_TYPE_MISMATCH"),
            (ACTIVE_PDF, {}, 422, "PROOF_ACTIVE_CONTENT"),
            (PDF.replace(b"%%EOF\n", b""), {}, 422, "PROOF_MALFORMED"),
            (b"", {}, 422, "PROOF_EMPTY"),
            (PDF, {"kind": "SELFIE"}, 422, "PROOF_KIND_UNKNOWN"),
        ]
        for body, kw, status, expected in cases:
            r = put(api, owner, claim, body, **kw)
            assert r.status_code == status and error(r) == expected, (
                expected,
                r.status_code,
                r.text,
            )
        too_big = put(api, owner, claim, b"%PDF-" + b"0" * (10 * 1024 * 1024 + 1))
        assert too_big.status_code == 413 and error(too_big) == "PROOF_TOO_LARGE"
        assert scanner.calls == 0, "a file that fails the rules never reaches the scanner"
        rows, objects, rejected, events = stored(owner_url, tenant, claim)
        assert (rows, objects, events) == (0, 0, 0) and rejected >= 5

        # infected: refused and audited; scanner down: refused as retryable and nothing is stored either way
        scanner.answer = ScanStatus.INFECTED
        r = put(api, owner, claim, PDF)
        assert r.status_code == 422 and error(r) == "PROOF_INFECTED"
        scanner.answer = ScanStatus.UNAVAILABLE
        r = put(api, owner, claim, PDF)
        assert (
            r.status_code == 503
            and error(r) == "PROOF_SCAN_UNAVAILABLE"
            and r.json()["error"].get("retryable") is True
        )
        assert stored(owner_url, tenant, claim)[:2] == (0, 0)

        # a deployed environment with no scanner stores nothing unscanned; local and test record that it was not scanned
        api.app.state.environment = "production"
        api.app.state.proof_scanner = None
        r = put(api, owner, claim, PDF)
        assert r.status_code == 503 and error(r) == "PROOF_SCAN_UNAVAILABLE"
        assert stored(owner_url, tenant, claim)[:2] == (0, 0)
        api.app.state.environment = "test"
        r = put(api, owner, claim, PDF)
        assert r.status_code == 201 and r.json()["scan_status"] == "NOT_SCANNED"


def test_the_tenants_residency_is_checked_before_the_scanner_or_the_vault_sees_the_file(
    rt_url: str, owner_url: str
) -> None:
    scanner = FakeScanner()
    _o, api, tenant, owner = make(rt_url, scanner)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]

        # a request that lands in a cell that is not the organization's home cell is refused by the platform before any of this runs
        api.app.state.home_cell = "eu-1"
        r = put(api, owner, claim, PDF)
        assert r.status_code == 421 and error(r) == "CELL_NOT_AUTHORITATIVE", r.text
        api.app.state.home_cell = "uk-1"

        # CR-134: in the home cell, an organization with no recorded residency keeps its document in that cell: nothing leaves it, so nothing is refused
        async def forget_residency() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text("DELETE FROM platform.tenant_residency WHERE tenant_id = :t"),
                    {"t": uuid.UUID(tenant)},
                )

        asyncio.run(forget_residency())
        r = put(api, owner, claim, PDF)
        assert r.status_code == 201, r.text
        rows, objects, rejected, events = stored(owner_url, tenant, claim)
        assert rows == 1 and objects == 1 and rejected == 0


def test_another_organization_reaches_none_of_it_and_only_a_reviewer_may_open_a_document(
    rt_url: str, owner_url: str
) -> None:
    scanner = FakeScanner()
    o, api, _tenant, owner = make(
        rt_url, scanner, extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES
    )
    second = o.bearer(o.second)
    _other, api_b, tenant_b, owner_b = make(rt_url, FakeScanner())
    with api, api_b:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        proof = put(api, owner, claim, PDF).json()["proof_id"]

        # the other organization sees the same 404 for this claim as for one that never existed
        ghost = str(uuid.uuid4())
        for claim_id in (claim, ghost):
            assert (
                api_b.get(f"/v1/org-claims/{claim_id}/proofs", headers=owner_b).status_code == 404
            )
            assert put(api_b, owner_b, claim_id, PDF).status_code == 404
        assert api_b.get(
            f"/v1/reviews/kyb-cases/{claim}/proofs/{proof}", headers=owner_b
        ).status_code in (401, 403, 404)

        # T12: the denied attempts are audited in the ATTACKER's own organization (and only there), with the id they supplied and the
        # kind of attempt; the other organization's record shows nothing, and the answers above stay the plain 404
        async def denied(url: str, tenant_id: str) -> list[Any]:
            async with engine(url) as eng, eng.begin() as c:
                return list(
                    (
                        await c.execute(
                            text(
                                "SELECT outcome, reason_code, target_ref, body::text AS body FROM evidence.audit_record "
                                "WHERE event_type = 'organization.kyb.proof_access_denied' AND tenant_id = :t ORDER BY created_at"
                            ),
                            {"t": uuid.UUID(tenant_id)},
                        )
                    ).all()
                )

        mine = asyncio.run(denied(owner_url, tenant_b))
        assert [(r.outcome, r.reason_code, r.target_ref) for r in mine] == [
            ("DENIED", "CASE_NOT_VISIBLE", claim),
            ("DENIED", "CASE_NOT_VISIBLE", claim),
            ("DENIED", "CASE_NOT_VISIBLE", ghost),
            ("DENIED", "CASE_NOT_VISIBLE", ghost),
        ]  # a list and an upload for each id: indistinguishable from a claim that never existed
        assert asyncio.run(denied(owner_url, _tenant)) == []

        # being the owner is not being a reviewer: an organization whose people hold no review action cannot open its own document
        _c, api_c, _t, owner_c = make(rt_url, FakeScanner())
        with api_c:
            claim_c = api_c.post("/v1/org-claims", headers={**owner_c, **key()}, json=CLAIM).json()[
                "claim_id"
            ]
            own = put(api_c, owner_c, claim_c, PDF).json()["proof_id"]
            assert api_c.get(
                f"/v1/reviews/kyb-cases/{claim_c}/proofs/{own}", headers=owner_c
            ).status_code in (401, 403)
            assert api_c.get(f"/v1/reviews/kyb-cases/{claim_c}/proofs/{own}").status_code == 401
        # the reviewer can open it, and it is audited
        r = api.get(f"/v1/reviews/kyb-cases/{claim}/proofs/{proof}", headers=second)
        assert (
            r.status_code == 200
            and r.content == PDF
            and r.headers["content-type"] == "application/pdf"
        )
        assert (
            r.headers["x-content-type-options"] == "nosniff"
            and "attachment" in r.headers["content-disposition"]
            and r.headers["cache-control"] == "no-store"
        )
        assert (
            api.get(
                f"/v1/reviews/kyb-cases/{claim}/proofs/{uuid.uuid4()}", headers=second
            ).status_code
            == 404
        )

        async def reads() -> int:
            async with engine(owner_url) as eng, eng.begin() as c:
                return int(
                    (
                        await c.execute(
                            text(
                                "SELECT count(*) FROM evidence.audit_record WHERE event_type = 'organization.kyb.proof_read' AND target_ref = :c"
                            ),
                            {"c": claim},
                        )
                    ).scalar_one()
                )

        assert asyncio.run(reads()) == 1


def test_a_reviewer_lists_the_documents_on_a_case_and_the_retention_policy_sets_when_they_expire(
    rt_url: str, owner_url: str, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from zid_platform.modules.onboarding.api import org_claim_proofs as api_module

    original = api_module.proof_policy()
    monkeypatch.setattr(api_module, "proof_policy", lambda: replace(original, retention_days=30))
    o, api, tenant, owner = make(
        rt_url, FakeScanner(), extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES
    )
    second = o.bearer(o.second)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        proof = put(api, owner, claim, PDF).json()["proof_id"]
        listed = api.get(f"/v1/reviews/kyb-cases/{claim}/proofs", headers=second)
        assert listed.status_code == 200 and [p["proof_id"] for p in listed.json()["proofs"]] == [
            proof
        ]
        assert api.get(f"/v1/reviews/kyb-cases/{claim}/proofs").status_code == 401
        assert (
            api.get(f"/v1/reviews/kyb-cases/{uuid.uuid4()}/proofs", headers=second).status_code
            == 404
        )

        async def expires() -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return (
                    await c.execute(
                        text(
                            "SELECT expires_at FROM evidence.evidence_object WHERE tenant_id = :t AND object_type = 'kyb_proof'"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()

        assert asyncio.run(expires()) is not None, (
            "a set retention period gives the stored document an expiry"
        )


CERT_TEXT = (
    "Certificate of Incorporation\nof a Private Limited Company\nCompany Number 00123456\n"
    "The Registrar of Companies for England and Wales hereby certifies that\nACME WIDGETS LIMITED\n"
    "is this day incorporated under the Companies Act 2006 as a private company, limited by shares.\nGiven at Companies House, on 3rd March 2019\n"
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 400


def test_a_certificate_image_is_read_and_a_matching_one_completes_the_company_check(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zid_platform.modules.onboarding.api import org_claim_proofs as route

    said = {"text": CERT_TEXT}

    async def read_page_text(image: bytes, **_: Any) -> str:
        return said["text"]

    monkeypatch.setattr(route, "read_page_text", read_page_text)
    _o, api, _tenant, owner = make(rt_url, FakeScanner())
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()["claim_id"]
        before = api.get(f"/v1/org-claims/{claim}/status", headers=owner).json()
        said["text"] = CERT_TEXT.replace("00123456", "00999999")
        other = put(api, owner, claim, JPEG + b"", ctype="image/jpeg", name="other.jpg")
        assert other.json()["read"]["verdict"] == "CONTRADICTS" and other.json()["read"]["number"] == "MISMATCH"
        # a certificate that contradicts the claim completes nothing
        assert api.get(f"/v1/org-claims/{claim}/status", headers=owner).json()["checks"]["company"] == before["checks"]["company"]
        said["text"] = CERT_TEXT
        good = put(api, owner, claim, JPEG, ctype="image/jpeg", name="cert.jpg")
        assert good.status_code == 201, good.text
        assert good.json()["read"]["verdict"] == "CONSISTENT" and good.json()["read"]["incorporated_on"] == "2019-03-03"
        assert "ACME" not in str(good.json()["read"])  # codes and a date, never the text
        # a PDF is stored as before and answered plainly: the reader takes pictures only
        pdf = put(api, owner, claim, PDF, name="c.pdf")
        assert pdf.status_code == 201 and pdf.json()["read"]["reading"].startswith("PDF_NOT_READABLE")
        # a letter of authority is not read at all
        letter = put(api, owner, claim, JPEG + b"\x02", ctype="image/jpeg", kind="LETTER_OF_AUTHORITY", name="l.jpg")
        assert "read" not in letter.json()
        # CR-132: a stored certificate that matches the claim completes the company check, and the status says what it rests on
        after = api.get(f"/v1/org-claims/{claim}/status", headers=owner).json()
        assert after["checks"]["company"] == "COMPLETED" and after["company_basis"] == "DOCUMENT_OCR"
        assert before["company_basis"] is None


def test_a_certificate_can_be_checked_without_being_saved(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check-only route reads and compares in memory and stores nothing, so it needs no place in the data region."""
    from zid_platform.modules.onboarding.api import org_claim_proofs as route

    async def read_page_text(image: bytes, **_: Any) -> str:
        return CERT_TEXT

    monkeypatch.setattr(route, "read_page_text", read_page_text)
    scanner = FakeScanner()
    _o, api, tenant, owner = make(rt_url, scanner)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()["claim_id"]
        h = {**owner, "Content-Type": "image/jpeg"}
        ok = api.post(f"/v1/org-claims/{claim}/certificate:check", content=JPEG, headers=h)
        assert ok.status_code == 200, ok.text
        assert ok.json()["stored"] is False and ok.json()["read"]["verdict"] == "CONSISTENT"
        assert scanner.calls == 1  # it is scanned like any upload
        # nothing was stored
        listed = api.get(f"/v1/org-claims/{claim}/proofs", headers=owner).json()
        assert listed["proofs"] == []
        # a PDF or an empty file is refused with a reason
        assert api.post(f"/v1/org-claims/{claim}/certificate:check", content=PDF, headers={**owner, "Content-Type": "application/pdf"}).status_code == 415
        assert api.post(f"/v1/org-claims/{claim}/certificate:check", content=b"", headers=h).status_code == 422
