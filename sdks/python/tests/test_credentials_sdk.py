"""The credentials SDK and its consumer packs (GID-063), with no platform: a fake transport stands in for the API.

GLB-33  a decision for one consumer is not usable by another
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from conftest import Keys

from zoiko_credentials import (
    ASSERTION_TYP,
    DECISION_TYP,
    CredentialsClient,
    CredentialsError,
    RoomsPack,
    ScopeError,
    ZoikorumPack,
    assertion_digest,
    verify_assertion,
)

BASE = "https://id.example"


class Fake:
    """Records every call and answers from a queue of (status, body) pairs."""

    def __init__(self, *answers: tuple[int, Any]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, Mapping[str, str], dict[str, Any] | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        status, doc = self.answers.pop(0)
        return status, json.dumps(doc).encode()


def ok(data: Any) -> tuple[int, Any]:
    return 201, {"success": True, "data": data}


def err(code: str, status: int = 422, **extra: Any) -> tuple[int, Any]:
    return status, {"success": False, "error": {"code": code, "message": code.lower(), **extra}}


def client(*answers: tuple[int, Any]) -> tuple[CredentialsClient, Fake]:
    fake = Fake(*answers)
    return CredentialsClient(BASE, "tok-1", transport=fake), fake


def decision_claims(**over: Any) -> dict[str, Any]:
    base = {
        "consumer": "zoikorum", "decision_id": "dec-1", "engagement": "READY", "tier": "A", "iat": 1,
        "dimensions": {"IDENTITY": "VERIFIED", "CREDENTIAL": "VERIFIED"}, "reason_codes": [],
    }  # fmt: skip
    base.update(over)
    return base


# --- the client ------------------------------------------------------------------------------------------------------


def test_a_check_posts_to_its_route_with_the_bearer_token_and_unwraps_the_envelope():
    c, fake = client(ok({"check_id": "c1", "decision": "VERIFIED"}))
    out = c.check("license", {"license_ref": "L-1"}, {"country": "US", "subdivision": "CA"}, profession_code="LAW")
    assert out == {"check_id": "c1", "decision": "VERIFIED"}
    method, url, headers, body = fake.calls[0]
    assert (method, url) == ("POST", f"{BASE}/v1/credentials/professional-licenses/checks")
    assert headers["Authorization"] == "Bearer tok-1"
    assert body == {
        "claim": {"license_ref": "L-1"},
        "jurisdiction": {"country": "US", "subdivision": "CA"},
        "profession_code": "LAW",
    }


def test_every_claim_kind_has_its_own_route_and_an_unknown_kind_never_reaches_the_network():
    kinds = {"academic": "academic", "license": "professional-licenses", "certification": "certifications",
             "employment": "experience", "cpd": "continuing-education", "insurance": "insurance"}  # fmt: skip
    c, fake = client(*[ok({}) for _ in kinds])
    for kind in kinds:
        c.check(kind, {"x": "y"}, {"country": "US"})
    assert [call[1] for call in fake.calls] == [f"{BASE}/v1/credentials/{route}/checks" for route in kinds.values()]
    with pytest.raises(ScopeError) as e:
        c.check("astrology", {}, {"country": "US"})
    assert e.value.code == "CLAIM_KIND_UNKNOWN" and len(fake.calls) == len(kinds)


def test_an_error_becomes_a_credentials_error_with_the_platforms_code():
    c, _ = client(err("PACK_UNAVAILABLE"), err("DEPENDENCY_UNAVAILABLE", 503, retryable=True))
    with pytest.raises(CredentialsError) as e:
        c.check("academic", {"a": "b"}, {"country": "FR"})
    assert (e.value.code, e.value.status, e.value.retryable) == ("PACK_UNAVAILABLE", 422, False)
    with pytest.raises(CredentialsError) as e:
        c.assertion("c1")
    assert e.value.retryable is True


def test_a_non_json_failure_is_still_a_credentials_error():
    fake = lambda *a: (502, b"<html>bad gateway</html>")
    with pytest.raises(CredentialsError) as e:
        CredentialsClient(BASE, "t", transport=fake).assertion("c1")
    assert e.value.code == "HTTP_ERROR" and e.value.status == 502


def test_the_public_verifier_call_sends_no_credentials():
    c, fake = client((200, {"success": True, "data": {"decision": "VERIFIED"}}))
    assert c.verify_public("t1", "c1", "g1") == {"decision": "VERIFIED"}
    method, url, headers, _ = fake.calls[0]
    assert (
        method == "GET"
        and url.endswith("/v1/credentials/assertions/t1.c1/verify?grant=g1")
        and "Authorization" not in headers
    )


def test_disclosure_calls_name_their_terms():
    c, fake = client(ok({"grant_id": "g1"}), ok({"withdrawn": True}), ok({"disclosed": {}}))
    c.create_disclosure("acme", "zoikorum", "ENGAGEMENT", ["decision"], days=5, parent_grant_id="g0")
    c.withdraw_disclosure("g1")
    c.read_disclosure("g1", "c1", "acme", "zoikorum", "ENGAGEMENT")
    assert fake.calls[0][3] == {
        "recipient": "acme",
        "consumer": "zoikorum",
        "purpose": "ENGAGEMENT",
        "fields": ["decision"],
        "days": 5,
        "parent_grant_id": "g0",
    }
    assert fake.calls[1][1].endswith("/v1/credentials/disclosures/g1:withdraw")
    assert fake.calls[2][3] == {"check_id": "c1", "recipient": "acme", "consumer": "zoikorum", "purpose": "ENGAGEMENT"}


def test_the_sensitive_check_always_names_a_reviewer_and_other_calls_are_routed_correctly():
    c, fake = client(ok({}), ok({}), ok({}), ok({}), ok({}), ok({}))
    c.disciplinary("Sample Board", {"country": "US"}, "reviewer-1", consent_ref="consent-1")
    c.verify_signed_credential("a.b.c", holder_proof="d.e.f", jurisdiction="US")
    c.evaluate_eligibility("US", "LAW", "legal.advice", subdivision="CA")
    c.evaluate_engagement("US", "LAW", "legal.advice", insurance_required=False)
    c.packs("US", "LAW")
    c.appeal("c1", "the record is wrong", {"license_ref": "L-1"})
    paths = [call[1].removeprefix(BASE) for call in fake.calls]
    assert paths == ["/v1/credentials/disciplinary/checks", "/v1/credentials/vc/verify", "/v1/eligibility/professional/evaluate",
                     "/v1/eligibility/engagement/evaluate", "/v1/credential-packs/US/LAW", "/v1/credentials/cases/c1/appeals"]  # fmt: skip
    assert fake.calls[0][3]["reviewer_principal_id"] == "reviewer-1"
    assert fake.calls[1][3] == {"credential": "a.b.c", "holder_proof": "d.e.f", "jurisdiction": "US"}


# --- Zoikorum and Rooms -----------------------------------------------------------------------------------------------


def test_a_signed_zoikorum_decision_is_verified_offline_and_ready_means_ready_only():
    keys = Keys()
    token = keys.sign(decision_claims(), typ=DECISION_TYP)
    c, fake = client(
        ok({"signature": token, "engagement": "BLOCKED", "tier": "C"})
    )  # the unsigned body lies; only claims count
    d = ZoikorumPack(c, keys.jwks()).decide("US", "LAW", "legal.advice", subdivision="CA")
    assert d.ready and d.engagement == "READY" and d.tier == "A" and d.consumer == "zoikorum"
    assert d.dimensions["IDENTITY"] == "VERIFIED" and d.token == token
    assert fake.calls[0][1].endswith("/v1/consumers/zoikorum/decisions")
    hold = ZoikorumPack.verify_decision(
        keys.sign(decision_claims(engagement="HOLD", tier="NONE"), typ=DECISION_TYP), keys.jwks()
    )
    assert not hold.ready


@pytest.mark.parametrize(
    "make",
    [
        lambda k: k.sign(decision_claims(), typ="action-grant+jwt"),  # wrong token type
        lambda k: k.sign(decision_claims(), typ=DECISION_TYP, kid="other"),  # unknown key
        lambda k: k.sign(decision_claims(), typ=DECISION_TYP, alg="none"),  # unsigned
        lambda k: "not-a-token",
    ],
)
def test_a_decision_that_does_not_verify_is_refused(make):
    keys = Keys()
    with pytest.raises(ScopeError) as e:
        ZoikorumPack.verify_decision(make(keys), keys.jwks())
    assert e.value.code == "DECISION_SIGNATURE_INVALID"


def test_a_tampered_or_foreign_key_decision_is_refused():
    keys, other = Keys(), Keys()
    forged = other.sign(decision_claims(), typ=DECISION_TYP)  # signed by someone else under the same kid
    with pytest.raises(ScopeError):
        ZoikorumPack.verify_decision(forged, keys.jwks())


def test_glb_33_a_decision_for_another_consumer_is_refused_and_rooms_never_accepts_zoikorums():
    keys = Keys()
    with pytest.raises(ScopeError) as e:
        ZoikorumPack.verify_decision(keys.sign(decision_claims(consumer="rooms"), typ=DECISION_TYP), keys.jwks())
    assert e.value.code == "DECISION_FOR_ANOTHER_CONSUMER"
    good = ZoikorumPack.verify_decision(keys.sign(decision_claims(), typ=DECISION_TYP), keys.jwks())
    with pytest.raises(ScopeError) as e:
        RoomsPack.accept_decision(good)
    assert e.value.code == "DECISION_FOR_ANOTHER_CONSUMER"


def test_each_pack_can_only_share_for_its_own_consumer():
    c, fake = client(ok({"grant_id": "g1"}), ok({"grant_id": "g2"}))
    ZoikorumPack(c, {"keys": []}).share("acme", "ENGAGEMENT", ["decision"])
    RoomsPack(c).share("landlord", "TENANCY", ["decision"])
    assert [call[3]["consumer"] for call in fake.calls] == ["zoikorum", "rooms"]


# --- offline verification of a signed assertion ---------------------------------------------------------------------------

GOLDEN_BODY = {
    "check_id": "c1",
    "assertion_id": "asrt_c1",
    "decision": "VERIFIED",
    "jurisdiction": {"country": "US", "subdivision": "CA"},
    "reason_codes": [],
    "policy_ref": {"id": "p", "version": 1},
    "explanation": {"limitations": ["Does not prove x \u2014 \u00e9 \u4e2d"]},
    "primary_source_verified": True,
    "expires_at": None,
    "superseded": True,
    "signature": "zzz",
}
# computed by the server (assertion_contract.body_digest); the same vector is pinned in the backend and TypeScript tests
GOLDEN_DIGEST = "afd153fe44c3678a55e6b8e6f2f9d6b4d0a33b5f854f82ea49d49a8f3a141789"


def test_the_assertion_digest_equals_the_servers_for_the_same_body():
    assert assertion_digest(GOLDEN_BODY) == GOLDEN_DIGEST


def _signed(keys, **over):
    body = {k: v for k, v in GOLDEN_BODY.items() if k not in ("signature", "superseded")}
    claims = {
        "typ": "zid-assertion",
        "check_id": body["check_id"],
        "assertion_id": body["assertion_id"],
        "body_sha256": assertion_digest(body),
    }
    return {**body, **over, "signature": keys.sign(claims, typ=ASSERTION_TYP)}


def test_a_signed_assertion_verifies_offline_and_a_replaced_flag_does_not_matter():
    keys = Keys()
    a = _signed(keys)
    assert verify_assertion(a, keys.jwks()) is a
    assert verify_assertion({**a, "superseded": True}, keys.jwks())  # not part of the signed body


@pytest.mark.parametrize(
    "change",
    [{"decision": "NOT_VERIFIED"}, {"policy_ref": {"id": "p", "version": 2}}, {"extra": 1}, {"reason_codes": ["X"]}],
)
def test_any_change_to_a_signed_assertion_is_tampering(change):
    keys = Keys()
    with pytest.raises(ScopeError) as e:
        verify_assertion({**_signed(keys), **change}, keys.jwks())
    assert e.value.code == "ASSERTION_TAMPERED"


def test_an_unsigned_wrongly_typed_or_foreign_assertion_is_refused():
    keys = Keys()
    for a, code in (
        ({**_signed(keys), "signature": None}, "ASSERTION_UNSIGNED"),
        ({k: v for k, v in _signed(keys).items() if k != "signature"}, "ASSERTION_UNSIGNED"),
        (
            {**_signed(keys), "signature": keys.sign({"typ": "zid-assertion"}, typ="zid-decision+jwt")},
            "ASSERTION_SIGNATURE_INVALID",
        ),
        (_signed(Keys()), "ASSERTION_SIGNATURE_INVALID"),  # signed by someone else under the same key id
    ):
        with pytest.raises(ScopeError) as e:
            verify_assertion(a, keys.jwks())
        assert e.value.code == code
