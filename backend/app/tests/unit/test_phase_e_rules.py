"""Phase E pure-domain rules: SAML profile, OpenID Federation chains and policy, verification
decisions and reliance, recovery classes and gates, credential minimization/SD-JWT/status/roles."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.authentication.domain.recovery import classify, evaluate, normalize_code
from zid_platform.modules.credential.domain.credentials import (
    Configuration,
    CredentialError,
    IssuerTrust,
    disclose,
    minimize,
    require_role,
    set_status,
    split_presentation,
    status_at,
    status_decision,
    trust_decision,
)
from zid_platform.modules.federation.domain.connection import FederationError
from zid_platform.modules.federation.domain.openid_federation import (
    Statement,
    TrustChainError,
    apply_policy,
    check_links,
    merge_policies,
    parse,
)
from zid_platform.modules.federation.domain.saml import AssertionFacts, check_assertion
from zid_platform.modules.verification.domain.verification import (
    ClaimResult,
    HeldAssertion,
    Profile,
    ProviderResult,
    SessionStatus,
    VerificationError,
    decide,
    failover_candidates,
    reliance,
    validate_requirements,
)

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
ACS, SP, IDP = "https://id.example/acs", "https://id.example/sp", "https://idp.example"


# ------------------------------------------------------------------------------------------ SAML

FACTS = AssertionFacts(
    response_destination=ACS,
    response_in_response_to="_r1",
    status="urn:oasis:names:tc:SAML:2.0:status:Success",
    issuer=IDP,
    assertion_id="_a1",
    name_id="nid-1",
    name_id_format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    confirmation_method="urn:oasis:names:tc:SAML:2.0:cm:bearer",
    recipient=ACS,
    confirmation_in_response_to="_r1",
    confirmation_not_on_or_after=NOW + timedelta(minutes=5),
    not_before=NOW - timedelta(minutes=1),
    not_on_or_after=NOW + timedelta(minutes=5),
    audiences=(SP,),
    authn_instant=NOW,
    authn_context=None,
)


def test_saml_profile() -> None:
    assert check_assertion(
        FACTS, issuer=IDP, acs_url=ACS, sp_entity_id=SP, request_id="_r1", now=NOW
    )
    for change, code in (
        ({"recipient": "https://evil/acs"}, "SAML_RECIPIENT_MISMATCH"),
        ({"audiences": ("https://other",)}, "SAML_AUDIENCE_MISMATCH"),
        ({"confirmation_in_response_to": "_x"}, "SAML_IN_RESPONSE_TO_MISMATCH"),
        (
            {"name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"},
            "SAML_NAMEID_NOT_STABLE",
        ),
        ({"not_on_or_after": NOW - timedelta(minutes=5)}, "SAML_ASSERTION_EXPIRED"),
        ({"issuer": "https://other"}, "SAML_ISSUER_MISMATCH"),
        ({"status": "urn:oasis:names:tc:SAML:2.0:status:Requester"}, "SAML_STATUS_NOT_SUCCESS"),
    ):
        with pytest.raises(FederationError) as e:
            check_assertion(
                replace(FACTS, **change),
                issuer=IDP,
                acs_url=ACS,
                sp_entity_id=SP,  # type: ignore[arg-type]
                request_id="_r1",
                now=NOW,
            )
        assert e.value.code == code
    with pytest.raises(FederationError) as e:
        check_assertion(FACTS, issuer=IDP, acs_url=ACS, sp_entity_id=SP, request_id=None, now=NOW)
    assert e.value.code == "SAML_UNSOLICITED"


# ---------------------------------------------------------------------------- OpenID Federation


def stmt(iss: str, sub: str, **kw: object) -> Statement:
    claims = {
        "iss": iss,
        "sub": sub,
        "iat": NOW.timestamp(),
        "exp": (NOW + timedelta(hours=1)).timestamp(),
        **kw,
    }
    return parse(claims, now=NOW)


def test_trust_chain_shape_and_constraints() -> None:
    leaf, ta = "https://op.x.example", "https://ta.example"
    chain = [stmt(leaf, leaf), stmt(ta, leaf), stmt(ta, ta)]
    assert check_links(chain, anchor=ta) == NOW + timedelta(hours=1)
    with pytest.raises(TrustChainError):
        check_links(chain, anchor="https://other-ta.example")
    with pytest.raises(TrustChainError):
        check_links([stmt(leaf, leaf), stmt("https://int.example", leaf), stmt(ta, ta)], anchor=ta)
    excluded = stmt(ta, leaf, constraints={"naming_constraints": {"excluded": [".x.example"]}})
    with pytest.raises(TrustChainError) as e:
        check_links([stmt(leaf, leaf), excluded, stmt(ta, ta)], anchor=ta)
    assert e.value.code == "TRUST_CHAIN_NAMING_CONSTRAINT"
    with pytest.raises(TrustChainError):
        parse(
            {
                "iss": leaf,
                "sub": leaf,
                "iat": NOW.timestamp(),
                "exp": NOW.timestamp() + 60,
                "crit": ["unknown_claim"],
            },
            now=NOW,
        )
    with pytest.raises(TrustChainError):
        parse(
            {"iss": leaf, "sub": leaf, "iat": NOW.timestamp() - 120, "exp": NOW.timestamp() - 60},
            now=NOW,
        )


def test_metadata_policy_merge_and_apply() -> None:
    merged = merge_policies(
        [
            {
                "openid_provider": {
                    "grant_types": {"subset_of": ["authorization_code", "refresh_token"]},
                    "id_token_signing_alg_values_supported": {"add": ["ES256"]},
                }
            },
            {"openid_provider": {"grant_types": {"subset_of": ["authorization_code"]}}},
        ]
    )
    out = apply_policy(
        {"openid_provider": {"grant_types": ["authorization_code", "implicit"]}}, merged
    )
    assert out["openid_provider"]["grant_types"] == ["authorization_code"]
    assert out["openid_provider"]["id_token_signing_alg_values_supported"] == ["ES256"]
    with pytest.raises(TrustChainError):  # a subordinate cannot widen a superior's value
        merge_policies([{"x": {"p": {"value": "a"}}}, {"x": {"p": {"value": "b"}}}])
    with pytest.raises(TrustChainError):
        apply_policy({"x": {}}, {"x": {"p": {"essential": True}}})
    with pytest.raises(TrustChainError):
        apply_policy({"x": {"p": "c"}}, {"x": {"p": {"one_of": ["a", "b"]}}})


# ---------------------------------------------------------------------------------- verification

REQ = {
    "required_claims": ["legal_name", "birthdate"],
    "accepted_evidence": ["GOVERNMENT_ID"],
    "binding_required": True,
    "biometric_jurisdictions": ["GB"],
    "jurisdictions": ["GB", "FR"],
    "manual_review": True,
    "reliance_purposes": ["onboarding"],
}
PROFILE = Profile.from_json("p", "t", "uk", 1, "IAL-Z2", REQ, "ACTIVE")


def result(**kw: object) -> ProviderResult:
    base: dict[str, object] = {
        "status": "COMPLETED",
        "claims": (
            ClaimResult("legal_name", True, "x", "doc", ("GOVERNMENT_ID",)),
            ClaimResult("birthdate", True, "y", "doc", ("GOVERNMENT_ID",)),
        ),
        "binding": "NON_BIOMETRIC",
        "liveness_passed": None,
        "fraud_signals": (),
    }
    return ProviderResult(**{**base, **kw})  # type: ignore[arg-type]


def test_verification_decisions_are_deterministic_and_claim_specific() -> None:
    assert decide(PROFILE, result(), jurisdiction="GB").status is SessionStatus.VERIFIED
    assert (
        decide(PROFILE, result(binding="BIOMETRIC"), jurisdiction="FR").outcome
        == "BIOMETRIC_NOT_PERMITTED"
    )
    d = decide(PROFILE, result(fraud_signals=("x",)), jurisdiction="GB")
    assert d.status is SessionStatus.MANUAL_REVIEW and d.outcome == "FRAUD_SUSPECTED"
    no_review = replace(PROFILE, manual_review=False)
    assert (
        decide(no_review, result(fraud_signals=("x",)), jurisdiction="GB").status
        is SessionStatus.FAILED
    )
    weak = result(claims=(ClaimResult("legal_name", True, "x", "db", ("AUTHORITATIVE_SOURCE",)),))
    assert (
        decide(PROFILE, weak, jurisdiction="GB").status is SessionStatus.FAILED
    )  # wrong evidence type
    assert (
        decide(PROFILE, result(status="PENDING"), jurisdiction="GB").status
        is SessionStatus.PENDING_EXTERNAL
    )
    with pytest.raises(VerificationError):
        validate_requirements("IAL-Z2", {**REQ, "binding_required": False})
    with pytest.raises(VerificationError):
        validate_requirements("IAL-Z1", {**REQ, "biometric_jurisdictions": ["US"]})


def test_failover_stays_inside_the_equivalence_group() -> None:
    adapters = [
        {"adapter_id": "a", "equivalence_group": "doc", "status": "ACTIVE", "open_until": None},
        {
            "adapter_id": "b",
            "equivalence_group": "doc",
            "status": "ACTIVE",
            "open_until": NOW + timedelta(minutes=1),
        },
        {
            "adapter_id": "c",
            "equivalence_group": "doc",
            "status": "QUARANTINED",
            "open_until": None,
        },
        {"adapter_id": "d", "equivalence_group": "db", "status": "ACTIVE", "open_until": None},
    ]
    assert failover_candidates(adapters, group="doc", attempted=[], now=NOW) == ["a"]
    assert failover_candidates(adapters, group="doc", attempted=["a"], now=NOW) == []


def test_reliance_is_a_fresh_decision() -> None:
    held = [
        HeldAssertion(
            "1",
            "legal_name",
            "IAL-Z2",
            "VALID",
            NOW + timedelta(days=1),
            "GB",
            frozenset({"onboarding"}),
        )
    ]
    kw = {"required_level": "IAL-Z2", "jurisdiction": "GB", "now": NOW}
    assert reliance(held, claims=["legal_name"], purpose="onboarding", **kw)[0] == "REUSE_ALLOWED"  # type: ignore[arg-type]
    assert reliance(held, claims=["legal_name"], purpose="ads", **kw)[0] == "REVERIFY_REQUIRED"  # type: ignore[arg-type]
    assert (
        reliance(held, claims=["legal_name", "address"], purpose="onboarding", **kw)[0]
        == "SUPPLEMENT_REQUIRED"
    )  # type: ignore[arg-type]
    stale = [replace(held[0], valid_until=NOW - timedelta(seconds=1))]
    assert reliance(stale, claims=["legal_name"], purpose="onboarding", **kw)[1] == (
        "SOURCE_STALE",
    )  # type: ignore[arg-type]


# -------------------------------------------------------------------------------------- recovery


def test_recovery_classes_and_gates() -> None:
    assert classify(privileged=False, with_code=True) == ("RC-1", "RECOVERY_CODE")
    assert classify(privileged=True, with_code=True) == ("RC-3", "REPROOF")  # AUTH-13
    assert classify(privileged=False, with_code=False) == ("RC-2", "REPROOF")
    assert (
        evaluate(
            "RC-1", reproof_at=None, approval_state=None, cooling_off_until=None, now=NOW
        ).status
        == "READY"
    )
    g = evaluate("RC-3", reproof_at=NOW, approval_state="PENDING", cooling_off_until=None, now=NOW)
    assert g.status == "PENDING_APPROVAL"
    g = evaluate("RC-3", reproof_at=NOW, approval_state="APPROVED", cooling_off_until=None, now=NOW)
    assert g.status == "COOLING_OFF" and g.cooling_off_until == NOW + timedelta(hours=72)
    g = evaluate(
        "RC-2",
        reproof_at=NOW,
        approval_state=None,
        cooling_off_until=NOW - timedelta(seconds=1),
        now=NOW,
    )
    assert g.status == "READY"
    assert normalize_code("abcde fghij-klmno-pqrst") == "ABCDE-FGHIJ-KLMNO-PQRST"


# ----------------------------------------------------------------------------------- credentials

CONFIG = Configuration(
    "c",
    "urn:pid",
    "SD_JWT_VC",
    "v",
    ("given_name", "family_name"),
    ("birthdate",),
    True,
    "TOKEN_STATUS_LIST",
    True,
    2,
    "ACTIVE",
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_minimization_roles_and_typed_trust() -> None:
    assert minimize(["given_name"], CONFIG, ["given_name", "family_name"]) == ("given_name",)
    for claims, allowed, code in (
        (["birthdate"], ["given_name"], "CLAIM_NOT_PERMITTED_FOR_PURPOSE"),
        (["ssn"], ["ssn"], "CLAIM_NOT_DEFINED"),
        ([], ["x"], "CLAIMS_REQUIRED"),
    ):
        with pytest.raises(CredentialError) as e:
            minimize(claims, CONFIG, allowed)
        assert e.value.code == code
    require_role("VERIFIER")
    for role in ("QTSP", "BUSINESS_WALLET_PROVIDER", "WALLET_PROVIDER", "QUALIFIED_EAA_ISSUER"):
        with pytest.raises(CredentialError):
            require_role(role)  # FR-VC-008
    trust = IssuerTrust(
        "https://gov",
        frozenset({"urn:pid"}),
        frozenset({"GB"}),
        "PINNED",
        "ACTIVE",
        NOW + timedelta(days=1),
    )
    trust_decision(trust, credential_type="urn:pid", jurisdiction="GB", now=NOW)
    for bad in (None, replace(trust, status="SUSPENDED")):
        with pytest.raises(CredentialError):
            trust_decision(bad, credential_type="urn:pid", jurisdiction="GB", now=NOW)
    with pytest.raises(CredentialError):
        trust_decision(trust, credential_type="urn:mdl", jurisdiction="GB", now=NOW)  # typed trust


def test_sd_jwt_disclosure_rules() -> None:
    d1 = b64(json.dumps(["s1", "given_name", "Erika"]).encode())
    d2 = b64(json.dumps(["s2", "birthdate", "1984"]).encode())
    digest = lambda d: b64(hashlib.sha256(d.encode()).digest())
    payload = {"iss": "x", "_sd": [digest(d1), digest(d2)], "_sd_alg": "sha-256"}
    assert disclose(payload, [d1]) == {
        "iss": "x",
        "given_name": "Erika",
    }  # undisclosed stays hidden
    with pytest.raises(CredentialError):
        disclose(payload, [d1, d1])
    rogue = b64(json.dumps(["s3", "admin", True]).encode())
    with pytest.raises(CredentialError):
        disclose(payload, [rogue])  # not referenced by the signed payload
    shadow = b64(json.dumps(["s4", "iss", "evil"]).encode())
    with pytest.raises(CredentialError):
        disclose({**payload, "_sd": [*payload["_sd"], digest(shadow)]}, [shadow])  # collision
    with pytest.raises(CredentialError):
        disclose({**payload, "_sd_alg": "sha-1"}, [d1])
    parts = split_presentation("a.b.c~" + d1 + "~x.y.z")
    assert parts.kb_jwt == "x.y.z" and parts.sd_hash_input == "a.b.c~" + d1 + "~"


def test_status_list_and_status_decision() -> None:
    raw = bytearray(16)
    set_status(raw, 2, 5, 1)
    set_status(raw, 2, 6, 2)
    lst = b64(zlib.compress(bytes(raw)))
    assert (status_at(lst, 2, 0), status_at(lst, 2, 5), status_at(lst, 2, 6)) == (
        "VALID",
        "REVOKED",
        "SUSPENDED",
    )
    with pytest.raises(CredentialError):
        status_at(lst, 2, 10_000)
    status_decision("VALID", CONFIG)
    for status in ("REVOKED", "SUSPENDED", "UNKNOWN"):
        with pytest.raises(CredentialError):
            status_decision(status, CONFIG)
    status_decision("UNKNOWN", replace(CONFIG, status_mandatory=False))
