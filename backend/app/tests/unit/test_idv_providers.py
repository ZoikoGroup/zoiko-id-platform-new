"""Identity-proofing providers (ZID-EDR-005 acceptance tests): a callback is authenticated on its raw bytes, a provider's word is
evidence and never authorization, anything half-understood is pending, and the global flow cannot pose as the UK-certified one."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from zid_platform.modules.verification.domain.providers import (
    ProviderKind,
    sumsub_digest_ok,
    sumsub_event_id,
    sumsub_needs_applicant,
    sumsub_ref,
    sumsub_result,
    sumsub_sign,
    validate_provider_config,
    veriff_decision_signature,
    veriff_event_id,
    veriff_ref,
    veriff_result,
    veriff_sign,
    veriff_signature_ok,
)
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure.providers import SumsubClient, VeriffClient

KEY, SECRET = "api-key-1", "shared-secret-1"


def hex_hmac(secret: str, data: bytes, algo: Any = hashlib.sha256) -> str:
    return hmac.new(secret.encode(), data, algo).hexdigest()


# ---- Veriff: authentication on the raw body ----------------------------------------------------------------------------


def decision(status: str = "approved", **verification: Any) -> dict[str, Any]:
    v: dict[str, Any] = {
        "id": "ver-1", "attemptId": "att-1", "status": status, "code": 9001, "decisionTime": "2026-11-02T12:00:00Z",
        "person": {"firstName": "Alice", "lastName": "Smith", "dateOfBirth": "1980-02-01"}, "vendorData": "sess-1",
    }  # fmt: skip
    return {
        "status": "success",
        "verification": {**v, **verification},
        "technicalData": {"ip": "203.0.113.1"},
    }


def test_a_veriff_callback_needs_both_headers_over_the_exact_raw_body() -> None:
    raw = json.dumps(decision()).encode()
    good = {"X-AUTH-CLIENT": KEY, "X-HMAC-SIGNATURE": hex_hmac(SECRET, raw)}
    assert veriff_signature_ok(raw, good, api_key=KEY, shared_secret=SECRET)
    assert veriff_signature_ok(
        raw, {k.lower(): v for k, v in good.items()}, api_key=KEY, shared_secret=SECRET
    )  # case of names
    assert veriff_signature_ok(
        raw,
        {**good, "X-HMAC-SIGNATURE": good["X-HMAC-SIGNATURE"].upper()},
        api_key=KEY,
        shared_secret=SECRET,
    )
    bad = [
        ({}, raw),  # nothing
        ({"X-AUTH-CLIENT": KEY}, raw),  # no signature
        ({"X-HMAC-SIGNATURE": good["X-HMAC-SIGNATURE"]}, raw),  # no client
        ({**good, "X-AUTH-CLIENT": "someone-else"}, raw),  # another integration
        (good, raw + b" "),  # one byte more: a body re-serialized in transit
        (good, raw.replace(b"approved", b"declined")),  # a different decision
        ({**good, "X-HMAC-SIGNATURE": hex_hmac("wrong-secret", raw)}, raw),
    ]
    for headers, body in bad:
        assert not veriff_signature_ok(body, headers, api_key=KEY, shared_secret=SECRET)
    # a missing secret never authenticates anything
    assert not veriff_signature_ok(raw, good, api_key=KEY, shared_secret="")
    assert not veriff_signature_ok(raw, good, api_key="", shared_secret=SECRET)


# ---- Veriff: statuses become normalized facts, never an approval of their own ----------------------------------------


def test_an_approved_session_gives_claims_and_a_biometric_binding_for_the_profile_to_judge() -> (
    None
):
    r = veriff_result(decision())
    assert r.status == "COMPLETED" and r.binding == "BIOMETRIC" and r.liveness_passed is True
    claims = {c.claim: c for c in r.claims}
    assert claims["legal_name"].value == "Alice Smith" and claims["legal_name"].verified
    assert claims["birthdate"].value == "1980-02-01"
    assert set(claims["legal_name"].evidence) == {"GOVERNMENT_ID", "BIOMETRIC_BINDING"}
    assert (
        r.evidence_commitment
        and len(r.evidence_commitment) == 64
        and "ver-1" not in r.evidence_commitment
    )
    assert veriff_ref(decision()) == "ver-1"


@pytest.mark.parametrize(
    ("status", "expected", "code"),
    [
        ("declined", "FAILED", "VERIFF_REASON_102"),
        ("resubmission_requested", "PENDING", "RESUBMISSION_REQUIRED"),
        ("expired", "FAILED", "PROVIDER_SESSION_EXPIRED"),
        ("abandoned", "FAILED", "PROVIDER_SESSION_ABANDONED"),
        ("review", "PENDING", "PROVIDER_MANUAL_REVIEW"),
        (
            "something_new",
            "PENDING",
            "UNKNOWN_PROVIDER_STATUS",
        ),  # an unknown word is never an approval
        ("", "PENDING", "UNKNOWN_PROVIDER_STATUS"),
    ],
)
def test_every_other_status_is_failed_or_pending_never_completed(
    status: str, expected: str, code: str
) -> None:
    r = veriff_result(decision(status, reasonCode=102))
    assert r.status == expected and code in r.reason_codes and r.claims == ()


def test_risk_labels_always_reach_the_engine_as_fraud_signals() -> None:
    r = veriff_result(
        decision(
            riskLabels=[
                {"label": "document_integration", "category": "document"},
                {"label": "device-risk"},
            ]
        )
    )
    assert r.status == "COMPLETED" and r.fraud_signals == (
        "VERIFF_RISK_DOCUMENT_INTEGRATION",
        "VERIFF_RISK_DEVICE_RISK",
    )


def test_a_decision_without_a_verification_id_is_refused() -> None:
    for payload in ({}, {"verification": {}}, {"verification": {"id": ""}}, {"verification": "x"}):
        with pytest.raises(VerificationError, match="CALLBACK_SCHEMA_INVALID"):
            veriff_ref(payload)


def test_the_same_decision_has_the_same_event_id_and_another_decision_has_another() -> None:
    assert veriff_event_id(decision()) == veriff_event_id(
        decision()
    )  # at-least-once delivery is deduplicated
    assert veriff_event_id(decision()) != veriff_event_id(decision("declined"))
    assert veriff_event_id(decision()) != veriff_event_id(decision(attemptId="att-2"))


def test_only_a_configured_certified_flow_carries_the_uk_marker() -> None:
    assert "UK_DVS_CERTIFIED_FLOW" not in veriff_result(decision()).reason_codes
    assert "UK_DVS_CERTIFIED_FLOW" in veriff_result(decision(), uk_certified=True).reason_codes
    assert (
        "UK_DVS_CERTIFIED_FLOW"
        in veriff_result(decision("declined"), uk_certified=True).reason_codes
    )


# ---- Sumsub ----------------------------------------------------------------------------------------------------------


def reviewed(answer: str = "GREEN", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "applicantReviewed", "applicantId": "app-1", "correlationId": "corr-1", "levelName": "basic-kyc",
        "externalUserId": "sess-1", "reviewStatus": "completed", "createdAtMs": "2026-11-02 12:00:00.000",
        "reviewResult": {"reviewAnswer": answer},
    }  # fmt: skip
    return {**body, **over}


def test_a_sumsub_digest_is_checked_over_the_raw_body_with_a_digest_it_names() -> None:
    raw = json.dumps(reviewed()).encode()
    s256 = {"X-Payload-Digest": hex_hmac("whsec", raw), "X-Payload-Digest-Alg": "HMAC_SHA256_HEX"}
    assert sumsub_digest_ok(raw, s256, webhook_secret="whsec")
    s512 = {
        "x-payload-digest": hex_hmac("whsec", raw, hashlib.sha512),
        "x-payload-digest-alg": "HMAC_SHA512_HEX",
    }
    assert sumsub_digest_ok(raw, s512, webhook_secret="whsec")
    sha1 = {
        "X-Payload-Digest": hex_hmac("whsec", raw, hashlib.sha1),
        "X-Payload-Digest-Alg": "HMAC_SHA1_HEX",
    }
    assert not sumsub_digest_ok(
        raw, sha1, webhook_secret="whsec"
    )  # a weak digest is refused outright
    assert not sumsub_digest_ok(
        raw, {**s256, "X-Payload-Digest-Alg": "MD5"}, webhook_secret="whsec"
    )
    assert not sumsub_digest_ok(raw + b"x", s256, webhook_secret="whsec")
    assert not sumsub_digest_ok(raw, s256, webhook_secret="other")
    assert not sumsub_digest_ok(raw, {}, webhook_secret="whsec")
    assert not sumsub_digest_ok(raw, s256, webhook_secret="")


def test_a_green_review_needs_the_applicants_data_for_claims_and_never_invents_them() -> None:
    assert sumsub_needs_applicant(reviewed()) and not sumsub_needs_applicant(reviewed("RED"))
    with_info = sumsub_result(
        reviewed(),
        info={"firstName": "Alice", "lastName": "Smith", "dob": "1980-02-01"},
        biometric_level=True,
    )
    assert (
        with_info.status == "COMPLETED"
        and with_info.binding == "BIOMETRIC"
        and with_info.liveness_passed is True
    )
    assert {c.claim: c.value for c in with_info.claims} == {
        "legal_name": "Alice Smith",
        "birthdate": "1980-02-01",
    }
    no_info = sumsub_result(reviewed(), info=None, biometric_level=True)
    assert (
        no_info.status == "COMPLETED" and no_info.claims == ()
    )  # nothing to satisfy a profile with
    plain = sumsub_result(
        reviewed(), info={"firstName": "A", "lastName": "S"}, biometric_level=False
    )
    assert (
        plain.binding is None and plain.liveness_passed is None
    )  # the level is configuration: nothing is assumed


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        (
            reviewed("RED", reviewResult={"reviewAnswer": "RED", "reviewRejectType": "RETRY"}),
            "PENDING",
            "RESUBMISSION_REQUIRED",
        ),
        (
            reviewed(
                "RED",
                reviewResult={
                    "reviewAnswer": "RED",
                    "reviewRejectType": "FINAL",
                    "rejectLabels": ["BAD_PROOF_OF_IDENTITY"],
                },
            ),
            "FAILED",
            "SUMSUB_REJECT_BAD_PROOF_OF_IDENTITY",
        ),
        (
            reviewed("RED", reviewResult={"reviewAnswer": "RED", "reviewRejectType": "FINAL"}),
            "FAILED",
            "SUMSUB_REJECTED",
        ),
        (reviewed(reviewStatus="pending"), "PENDING", "REVIEW_STATUS_PENDING"),
        (reviewed(type="applicantPending"), "PENDING", "IGNORED_EVENT_APPLICANTPENDING"),
        (reviewed("MAYBE"), "PENDING", "UNKNOWN_PROVIDER_STATUS"),
    ],
)
def test_every_other_sumsub_outcome_is_failed_or_pending(
    payload: dict[str, Any], status: str, code: str
) -> None:
    r = sumsub_result(payload, info=None, biometric_level=True)
    assert r.status == status and code in r.reason_codes and r.claims == ()


def test_a_forgery_label_is_a_fraud_signal_and_event_ids_are_stable() -> None:
    r = sumsub_result(
        reviewed("RED", reviewResult={"reviewAnswer": "RED", "reviewRejectType": "FINAL", "rejectLabels": ["FORGERY", "PEP"]}),
        info=None, biometric_level=True,
    )  # fmt: skip
    assert r.fraud_signals == ("SUMSUB_RISK_FORGERY",) and "SUMSUB_REJECT_PEP" in r.reason_codes
    assert (
        sumsub_event_id(reviewed())
        == sumsub_event_id(reviewed())
        != sumsub_event_id(reviewed("RED"))
    )
    assert sumsub_ref(reviewed()) == "app-1"
    with pytest.raises(VerificationError, match="CALLBACK_SCHEMA_INVALID"):
        sumsub_ref({"type": "applicantReviewed"})


# ---- the clients: only an opaque reference leaves, and every request is signed --------------------------------------


def secrets(values: dict[str, str]) -> Any:
    return lambda name: values.get(name)


def test_veriff_creates_a_session_with_an_opaque_reference_and_a_signed_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, headers=dict(request.headers), body=request.content)
        return httpx.Response(
            201,
            json={
                "status": "success",
                "verification": {
                    "id": "ver-9",
                    "url": "https://magic.veriff.example/s/abc",
                    "sessionToken": "tok",
                    "host": "h",
                    "status": "created",
                    "vendorData": "sess-1",
                    "endUserId": None,
                },
            },
        )

    client = VeriffClient(
        "https://api.veriff.example",
        secrets({"k": KEY, "s": SECRET}),
        "k",
        "s",
        httpx.MockTransport(handler),
    )
    made = asyncio.run(
        client.create_session(
            "sess-1", "https://id.example.test/verification-provider-callbacks/t.veriff"
        )
    )
    assert (
        made is not None
        and made.provider_ref == "ver-9"
        and made.url == "https://magic.veriff.example/s/abc"
        and made.token == "tok"
    )
    assert seen["path"] == "/v1/sessions" and seen["headers"]["x-auth-client"] == KEY
    assert seen["headers"]["x-hmac-signature"] == veriff_sign(seen["body"], SECRET)
    body = json.loads(seen["body"])
    assert body == {
        "verification": {
            "vendorData": "sess-1",
            "callback": "https://id.example.test/verification-provider-callbacks/t.veriff",
        }
    }
    assert (
        "person" not in body["verification"] and "document" not in body["verification"]
    )  # no personal data leaves


def test_a_veriff_call_that_cannot_be_trusted_gives_nothing() -> None:
    def make(response: httpx.Response, **kw: Any) -> VeriffClient:
        return VeriffClient(
            kw.get("base", "https://api.veriff.example"),
            secrets(kw.get("sec", {"k": KEY, "s": SECRET})),
            "k",
            "s",
            httpx.MockTransport(lambda r: response),
        )

    run = lambda c: asyncio.run(c.create_session("sess-1", None))
    assert run(make(httpx.Response(500))) is None
    assert run(make(httpx.Response(302, headers={"location": "https://evil.example"}))) is None
    assert run(make(httpx.Response(200, content=b"not json"))) is None
    assert run(make(httpx.Response(200, json={"verification": {}}))) is None  # no session id
    assert (
        run(make(httpx.Response(200, json={"verification": {"id": "x"}}), sec={})) is None
    )  # no secrets
    assert (
        run(
            make(
                httpx.Response(200, json={"verification": {"id": "x"}}),
                base="http://api.veriff.example",
            )
        )
        is None
    )  # not https
    assert (
        run(
            make(
                httpx.Response(200, json={"verification": {"id": "x"}}),
                base="https://169.254.169.254",
            )
        )
        is None
    )  # an IP literal
    link = run(
        make(
            httpx.Response(
                200, json={"verification": {"id": "x", "url": "http://insecure.example/s"}}
            )
        )
    )
    assert link is not None and link.url is None  # a link that is not https is never handed over


def test_veriff_reconciles_a_pending_session_with_a_signed_decision_lookup() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, headers=dict(request.headers))
        return httpx.Response(200, json=decision())

    client = VeriffClient(
        "https://api.veriff.example",
        secrets({"k": KEY, "s": SECRET}),
        "k",
        "s",
        httpx.MockTransport(handler),
    )
    doc = asyncio.run(client.decision("ver-1"))
    assert doc is not None and seen["path"] == "/v1/sessions/ver-1/decision"
    assert seen["headers"]["x-hmac-signature"] == veriff_decision_signature("ver-1", SECRET)


def test_sumsub_signs_every_request_over_timestamp_method_path_and_body() -> None:
    calls: list[tuple[str, str, bytes, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        calls.append((request.method, path, request.content, dict(request.headers)))
        if path.startswith("/resources/applicants"):
            return httpx.Response(201, json={"id": "app-7"})
        return httpx.Response(200, json={"token": "_act-sbx-1"})

    client = SumsubClient(
        "https://api.sumsub.example",
        secrets({"t": "tok", "s": "sec"}),
        "t",
        "s",
        "basic-kyc",
        httpx.MockTransport(handler),
        clock=lambda: 1607551635.9,
    )
    made = asyncio.run(client.create_session("sess-1", None))
    assert (
        made is not None
        and made.provider_ref == "app-7"
        and made.token == "_act-sbx-1"
        and made.url is None
    )
    (m1, p1, b1, h1), (m2, p2, b2, h2) = calls
    assert (m1, p1) == ("POST", "/resources/applicants?levelName=basic-kyc") and json.loads(b1) == {
        "externalUserId": "sess-1"
    }
    assert (m2, p2) == ("POST", "/resources/accessTokens/sdk") and json.loads(b2) == {
        "ttlInSecs": 600,
        "userId": "sess-1",
        "levelName": "basic-kyc",
    }
    for method, path, body, headers in ((m1, p1, b1, h1), (m2, p2, b2, h2)):
        assert headers["x-app-token"] == "tok" and headers["x-app-access-ts"] == "1607551635"
        assert headers["x-app-access-sig"] == hex_hmac(
            "sec", b"1607551635" + method.encode() + path.encode() + body
        )
        assert headers["x-app-access-sig"] == sumsub_sign("sec", 1607551635, method, path, body)


def test_sumsub_reads_the_applicants_data_and_gives_nothing_on_any_failure() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        assert (
            request.method == "GET"
            and request.url.path == "/resources/applicants/app-1/one"
            and not request.content
        )
        return httpx.Response(
            200, json={"info": {"firstName": "Alice", "lastName": "Smith", "dob": "1980-02-01"}}
        )

    def client(handler: Any, sec: dict[str, str] | None = None) -> SumsubClient:
        return SumsubClient(
            "https://api.sumsub.example",
            secrets(sec if sec is not None else {"t": "tok", "s": "sec"}),
            "t",
            "s",
            "lvl",
            httpx.MockTransport(handler),
        )

    assert asyncio.run(client(ok).applicant_info("app-1")) == {
        "firstName": "Alice",
        "lastName": "Smith",
        "dob": "1980-02-01",
    }
    assert asyncio.run(client(lambda r: httpx.Response(500)).applicant_info("app-1")) is None
    assert (
        asyncio.run(
            client(lambda r: httpx.Response(200, json={"no": "info"})).applicant_info("app-1")
        )
        is None
    )
    assert asyncio.run(client(ok, sec={}).applicant_info("app-1")) is None


# ---- adapter settings: secrets are named, never carried; the UK flow is one adapter for one country ---------------------


def test_adapter_settings_name_their_secrets_and_nothing_else() -> None:
    v = {"api_key_ref": "veriff-api-key", "shared_secret_ref": "veriff-shared-secret"}
    assert validate_provider_config(ProviderKind.VERIFF, v, ["GB", "FR"]) == v
    s = {
        "app_token_ref": "sumsub-app-token",
        "secret_key_ref": "sumsub-secret",
        "webhook_secret_ref": "sumsub-webhook",
        "level_name": "basic-kyc-level",
        "biometric_level": True,
    }
    assert validate_provider_config(ProviderKind.SUMSUB, s, ["DE"]) == s
    assert validate_provider_config(ProviderKind.GENERIC, {}, ["GB"]) == {}
    for kind, bad in (
        (ProviderKind.VERIFF, {"api_key_ref": "k"}),  # a missing setting
        (ProviderKind.VERIFF, {**v, "api_key_ref": "Not A Ref!"}),
        (
            ProviderKind.VERIFF,
            {**v, "shared_secret": "the-actual-secret"},
        ),  # an unknown setting: a secret smuggled in
        (ProviderKind.VERIFF, {**v, "biometric_level": "yes"}),
        (ProviderKind.VERIFF, {**v, "callback_url": "http://x.example"}),
        (ProviderKind.SUMSUB, {**s, "uk_certified": True}),  # only Veriff has a certified UK flow
        (ProviderKind.GENERIC, {"api_key_ref": "k"}),
    ):
        with pytest.raises(VerificationError):
            validate_provider_config(kind, bad, ["GB"])


def test_the_uk_certified_flow_is_gb_only_and_a_global_adapter_cannot_claim_it_elsewhere() -> None:
    cfg = {
        "api_key_ref": "uk-api-key",
        "shared_secret_ref": "uk-shared-secret",
        "uk_certified": True,
    }
    assert validate_provider_config(ProviderKind.VERIFF, cfg, ["GB"])["uk_certified"] is True
    for countries in (["GB", "FR"], ["US"], ["GB", "IE"]):
        with pytest.raises(VerificationError, match="GB only"):
            validate_provider_config(ProviderKind.VERIFF, cfg, countries)
