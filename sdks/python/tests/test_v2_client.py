"""The /v2 client without a platform: a fake transport stands in for the API (ZID-INT-002)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zoiko_credentials.v2 import PATHS, V2Client, V2Error

BASE = "https://api.example.test"


class Fake:
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status, self.body, self.calls = status, body if body is not None else {"ok": True}, []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append((method, url, dict(headers), data))
        return self.status, json.dumps(self.body).encode()


def client(status: int = 200, body: dict | None = None):
    fake = Fake(status, body)
    return V2Client(BASE, "tok-9", transport=fake), fake


def test_a_post_sends_the_bearer_the_idempotency_key_and_json_and_returns_the_plain_document():
    c, fake = client(body={"credential_ref": "cred_1", "dimensions": []})
    out = c.verify_credential(
        "professional_license",
        {"name": "Board"},
        {"license_ref": "L-1"},
        {"country": "US"},
        idempotency_key="k-0001",
        purpose="professional_onboarding",
    )
    method, url, headers, data = fake.calls[0]
    assert (method, url) == ("POST", BASE + "/v2/credentials/verify")
    assert headers["Authorization"] == "Bearer tok-9" and headers["Idempotency-Key"] == "k-0001"
    assert json.loads(data)["purpose"] == "professional_onboarding" and out == {
        "credential_ref": "cred_1",
        "dimensions": [],
    }


def test_the_idempotency_key_is_required_so_a_retry_is_always_deliberate():
    c, fake = client()
    with pytest.raises(TypeError):
        c.verify_credential("professional_license", {}, {}, {"country": "US"})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        c.open_case("p", {"country": "US"}, {"profile_id": "x", "version": "1"}, ["identity"])  # type: ignore[call-arg]
    assert fake.calls == []


def test_a_problem_document_becomes_a_v2_error_with_the_documents_code():
    c, _ = client(
        409,
        {
            "type": "https://api.zoikoid.com/problems/idempotency-conflict",
            "code": "idempotency_conflict",
            "status": 409,
            "detail": "reused",
            "trace_id": "req_9",
        },
    )
    with pytest.raises(V2Error) as e:
        c.evaluate_eligibility({"country": "US"}, "LAW", "legal.advice", idempotency_key="k-0002")
    assert (e.value.code, e.value.status, e.value.trace_id) == ("idempotency_conflict", 409, "req_9")


def test_a_non_json_failure_is_still_a_v2_error():
    fake = lambda *a: (502, b"<html>bad gateway</html>")
    with pytest.raises(V2Error) as e:
        V2Client(BASE, "t", transport=fake).capabilities()
    assert e.value.status == 502 and e.value.code == "http_error"


def test_every_read_and_cancel_goes_to_its_documented_route():
    c, fake = client()
    c.capabilities()
    c.coverage("US", "LAW")
    c.credential("cred_1")
    c.eligibility("elig_1")
    c.case("case_1")
    c.cancel_case("case_1")
    c.evidence_manifest("ev_1")
    c.delete_evidence("ev_1")
    c.document_result("docval_1")
    c.issuer_check("ichk_1")
    c.presentation("pres_1")
    c.webhook_endpoints()
    c.event("e1")
    c.replay_event("e1")
    seen = [(m, u.removeprefix(BASE)) for m, u, _, _ in fake.calls]
    assert seen == [
        ("GET", "/v2/capabilities"), ("GET", "/v2/coverage?country=US&profession=LAW"), ("GET", "/v2/credentials/cred_1"),
        ("GET", "/v2/eligibility/elig_1"), ("GET", "/v2/verifications/case_1"), ("POST", "/v2/verifications/case_1/cancel"),
        ("GET", "/v2/evidence/ev_1/manifest"), ("DELETE", "/v2/evidence/ev_1"), ("GET", "/v2/documents/docval_1"),
        ("GET", "/v2/issuer-checks/ichk_1"), ("GET", "/v2/presentations/pres_1"), ("GET", "/v2/webhook-endpoints"),
        ("GET", "/v2/events/e1"), ("POST", "/v2/events/e1/replay"),
    ]  # fmt: skip


def test_an_upload_announces_the_exact_size_and_digest_then_sends_the_raw_bytes_with_the_token():
    content = b"%PDF-1.7 a document"
    calls: list = []

    def transport(method, url, headers, data, timeout):
        calls.append((method, url.removeprefix(BASE), dict(headers), data))
        if method == "POST":
            return 201, json.dumps(
                {"evidence_ref": "ev_1", "upload_url": "/v2/evidence/uploads/u-1/content", "upload_token": "tok.en.sig"}
            ).encode()
        return 201, json.dumps({"evidence_ref": "ev_1", "state": "READY"}).encode()

    out = V2Client(BASE, "t", transport=transport).upload_evidence(
        content, "application/pdf", idempotency_key="k-0003", subject_ref="sub_x"
    )
    assert out["state"] == "READY"
    post, put = calls
    announced = json.loads(post[3])
    assert announced == {
        "declared_type": "application/pdf",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "subject_ref": "sub_x",
    }
    assert post[2]["Idempotency-Key"] == "k-0003"
    assert put[:2] == ("PUT", "/v2/evidence/uploads/u-1/content") and put[3] == content
    assert put[2]["X-Upload-Token"] == "tok.en.sig" and put[2]["Content-Type"] == "application/octet-stream"


def test_a_rejected_upload_raises_with_the_reason_and_no_bytes_are_retried():
    def transport(method, url, headers, data, timeout):
        if method == "POST":
            return 201, json.dumps({"upload_url": "/u", "upload_token": "t"}).encode()
        return 422, json.dumps({"code": "evidence_rejected_infected", "status": 422, "detail": "rejected"}).encode()

    with pytest.raises(V2Error) as e:
        V2Client(BASE, "t", transport=transport).upload_evidence(b"x", "image/png", idempotency_key="k-0004")
    assert e.value.code == "evidence_rejected_infected"


def test_document_validation_takes_exactly_one_source_and_pastes_base64():
    c, fake = client()
    with pytest.raises(V2Error):
        c.validate_document("application/pdf", ["integrity"])
    with pytest.raises(V2Error):
        c.validate_document("application/pdf", ["integrity"], evidence_ref="ev_1", content=b"x")
    c.validate_document("application/pdf", ["integrity"], content=b"%PDF-")
    c.validate_document("application/pdf", ["integrity"], evidence_ref="ev_1")
    pasted, stored = (json.loads(call[3]) for call in fake.calls)
    assert pasted["content_base64"] == "JVBERi0=" and "evidence_ref" not in pasted
    assert stored["evidence_ref"] == "ev_1" and "content_base64" not in stored


def test_a_presentation_request_carries_the_configuration_and_only_a_shorter_life():
    c, fake = client()
    c.request_presentation(
        "cfg-1", "rp-1", "age", ["age_over_18"], {"country": "GB"}, idempotency_key="k-0005", expires_in_seconds=120
    )
    body = json.loads(fake.calls[0][3])
    assert body["configuration_id"] == "cfg-1" and body["expires_in_seconds"] == 120 and fake.calls[0][3] is not None


def test_a_case_and_its_task_use_their_documented_routes_and_keys():
    c, fake = client()
    c.open_case(
        "professional_onboarding",
        {"country": "US"},
        {"profile_id": "p", "version": "2.0"},
        ["identity", "license"],
        idempotency_key="k-0006",
        consent_ref="c1",
    )
    c.submit_task(
        "case_1",
        "license",
        {"issuer_claim": {"name": "B"}, "credential_claim": {"license_ref": "L"}},
        idempotency_key="k-0007",
    )
    assert [(m, u.removeprefix(BASE), h["Idempotency-Key"]) for m, u, h, _ in fake.calls] == [
        ("POST", "/v2/verifications", "k-0006"), ("POST", "/v2/verifications/case_1/tasks/license", "k-0007"),
    ]  # fmt: skip


def test_the_issuer_check_documents_that_the_nonce_is_shown_once():
    assert "ONCE" in V2Client.open_issuer_check.__doc__


def test_every_route_the_python_client_calls_is_also_in_the_typescript_client():
    ts = (Path(__file__).resolve().parents[2] / "typescript" / "src" / "v2.ts").read_text(encoding="utf-8")
    missing = [p for p in PATHS if p not in ts]
    assert missing == [], f"the TypeScript v2 client lacks: {missing}"


def test_every_route_the_client_calls_exists_in_the_servers_published_contract():
    """The SDK cannot drift from the server: each (method, path) must be in contracts/openapi/public/*.json."""
    import re

    root = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "public"
    published: set[tuple[str, str]] = set()
    for doc in root.glob("*.json"):
        for path, ops in json.loads(doc.read_text(encoding="utf-8")).get("paths", {}).items():
            for method in ops:
                published.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", path)))
    missing = []
    for entry in PATHS:
        method, path = entry.split(" ", 1)
        if (method, re.sub(r"\{[^}]+\}", "{}", path)) not in published:
            missing.append(entry)
    assert missing == [], f"the SDK calls routes the server does not publish: {missing}"


def test_capture_alternatives_is_a_plain_get_with_the_country_and_an_optional_reason():
    c, fake = client(body={"routes": [], "counts_as_failed_attempt": False})
    out = c.capture_alternatives("GB")
    c.capture_alternatives("GB", "no_camera")
    assert [(m, u.removeprefix(BASE)) for m, u, _, _ in fake.calls] == [
        ("GET", "/v2/capture-alternatives?country=GB"),
        ("GET", "/v2/capture-alternatives?country=GB&reason=no_camera"),
    ]
    assert out["counts_as_failed_attempt"] is False
