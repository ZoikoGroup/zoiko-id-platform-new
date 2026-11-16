"""API-based agents (CR-093): public-key-only registration, the descriptor digest, and the cap on what such an agent may hold."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from zid_platform.modules.machine.domain import api_agents as a

EC = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    "kid": "k1",
}
ED = {
    "kty": "OKP",
    "crv": "Ed25519",
    "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
    "kid": "e1",
}
RSA_N = base64.urlsafe_b64encode(b"\xc3" + b"\x01" * 255).rstrip(b"=").decode()  # 2048 bits
RSA = {"kty": "RSA", "n": RSA_N, "e": "AQAB", "kid": "r1"}


def test_a_public_ec_ed25519_or_2048_bit_rsa_key_is_accepted():
    for key in (EC, ED, RSA):
        assert a.jwk_problems(key) == [], key
    assert a.jwks_problems({"keys": [EC, ED]}) == []


@pytest.mark.parametrize("member", sorted(a.PRIVATE_MEMBERS))
def test_a_key_carrying_any_private_member_is_refused(member):
    assert "JWK_PRIVATE_MATERIAL" in a.jwk_problems({**EC, member: "secret"})


def test_remote_keys_weak_keys_and_wrong_types_are_refused():
    assert "JWK_REMOTE_REFERENCE" in a.jwk_problems({**EC, "jku": "https://example.invalid/keys"})
    assert "JWK_REMOTE_REFERENCE" in a.jwk_problems({**EC, "x5u": "https://example.invalid/c"})
    assert "JWK_CURVE_NOT_ALLOWED" in a.jwk_problems({**EC, "crv": "secp256k1"})
    assert "JWK_CURVE_NOT_ALLOWED" in a.jwk_problems({**ED, "crv": "Ed448"})
    small = base64.urlsafe_b64encode(b"\xc3" + b"\x01" * 127).rstrip(b"=").decode()
    assert "JWK_RSA_TOO_SMALL" in a.jwk_problems({**RSA, "n": small})
    assert "JWK_RSA_TOO_SMALL" in a.jwk_problems({**RSA, "n": "!!!"})
    assert "JWK_TYPE_NOT_ALLOWED" in a.jwk_problems({"kty": "oct", "kid": "s", "k": "x"})
    assert "JWK_KID_REQUIRED" in a.jwk_problems({k: v for k, v in EC.items() if k != "kid"})
    assert "JWK_NOT_A_SIGNING_KEY" in a.jwk_problems({**EC, "use": "enc"})
    assert "JWK_NOT_A_SIGNING_KEY" in a.jwk_problems({**EC, "alg": "HS256"})


def test_a_key_set_is_bounded_exact_and_has_no_duplicate_ids():
    assert a.jwks_problems({"keys": []}) == ["JWKS_NEEDS_1_TO_5_KEYS"]
    assert a.jwks_problems({"keys": [EC] * 6}) == ["JWKS_NEEDS_1_TO_5_KEYS"]
    assert a.jwks_problems({"keys": "x"}) == ["JWKS_NEEDS_1_TO_5_KEYS"]
    assert a.jwks_problems({"keys": [EC], "extra": 1}) == ["JWKS_UNKNOWN_MEMBER"]
    assert "JWK_KID_DUPLICATE" in a.jwks_problems({"keys": [EC, {**EC, "x": ED["x"]}]})


def test_the_thumbprint_is_rfc_7638_over_the_required_members_only():
    """An independent computation: required members, sorted, no whitespace, SHA-256, base64url without padding. Extra members change nothing."""

    def expected(members: dict[str, str]) -> str:
        canonical = json.dumps(members, separators=(",", ":"), sort_keys=True)
        return (
            base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

    assert a.thumbprint(EC) == expected({"crv": "P-256", "kty": "EC", "x": EC["x"], "y": EC["y"]})
    assert a.thumbprint(ED) == expected({"crv": "Ed25519", "kty": "OKP", "x": ED["x"]})
    assert a.thumbprint(RSA) == expected({"e": "AQAB", "kty": "RSA", "n": RSA_N})
    assert a.thumbprint({**EC, "kid": "other", "use": "sig", "alg": "ES256"}) == a.thumbprint(EC)
    assert a.thumbprint(EC) != a.thumbprint({**EC, "y": ED["x"]})


def test_the_descriptor_digest_names_the_client_its_keys_and_its_audiences():
    base = a.descriptor_digest("zid-api-aaaa", {"keys": [EC]}, ["https://api.example.test"])
    assert base.startswith("sha256:") and len(base) == 71
    assert base == a.descriptor_digest("zid-api-aaaa", {"keys": [EC]}, ["https://api.example.test"])
    assert base != a.descriptor_digest(
        "zid-api-bbbb", {"keys": [EC]}, ["https://api.example.test"]
    )  # another client
    assert base != a.descriptor_digest(
        "zid-api-aaaa", {"keys": [ED]}, ["https://api.example.test"]
    )  # a rotated key is a different build
    assert base != a.descriptor_digest(
        "zid-api-aaaa", {"keys": [EC]}, ["https://other.example.test"]
    )
    assert a.descriptor_digest(
        "zid-api-aaaa", {"keys": [EC, ED]}, ["https://x.test"]
    ) == a.descriptor_digest(
        "zid-api-aaaa", {"keys": [ED, EC]}, ["https://x.test"]
    )  # order does not matter
    assert a.descriptor("c", {"keys": [EC]}, ["https://x.test"])["kind"] == "API_CLIENT"


def test_audiences_must_be_exact_and_few():
    assert a.audience_problems(["https://api.example.test"]) == []
    assert a.audience_problems([]) == ["AUDIENCES_NEED_1_TO_5"]
    assert a.audience_problems(["x"] * 6) == ["AUDIENCES_NEED_1_TO_5"]
    for bad in ("*", "https://*.example.test", "all", " ", "has space"):
        assert a.audience_problems([bad]) == ["AUDIENCE_NOT_EXACT"], bad


def test_an_api_bound_agent_may_hold_only_tools_that_read_or_can_be_undone():
    """The code behind an API client is unproven, so nothing irreversible, nothing that moves money, nothing above the risk ceiling."""
    assert a.api_tool_blockers("NONE", 1) == []
    assert a.api_tool_blockers("REVERSIBLE", 1) == []
    assert a.api_tool_blockers("REVERSIBLE", 2) == [
        "API_AGENT_TOOL_NOT_ALLOWED"
    ]  # ceiling is the token's class (decision pack)
    assert a.api_tool_blockers("IRREVERSIBLE", 1) == ["API_AGENT_TOOL_NOT_ALLOWED"]
    assert a.api_tool_blockers("EXTERNAL_TRANSACTION", 0) == ["API_AGENT_TOOL_NOT_ALLOWED"]
    assert a.api_tool_blockers("NONE", 3) == ["API_AGENT_TOOL_NOT_ALLOWED"]
    assert a.api_tool_blockers("IRREVERSIBLE", 3) == ["API_AGENT_TOOL_NOT_ALLOWED"]  # reported once
    assert a.API_ATTESTATION_CAP == 1
