"""Pure rules behind ZID-VC-001 and ZID-VERIFY-001 acceptance rows (no database, no network).

VC-04   issuer trust is typed per credential type and jurisdiction; policy can widen it explicitly
VC-11   expiry / not-yet-valid windows of the trust entry and (via replay) of the decision time
VC-13   draft pinning: a configuration for another adapter version is PROFILE_VERSION_MISMATCH
VC-24   none / symmetric / unapproved proof algorithms are refused before any key is used
VC-25   a pinned key past its `not_after` cutoff verifies nothing; the overlap is bounded
VC-27   a recorded decision basis replays to the same outcome (and to the same failure)
VC-28   deferred EUDI roles are never enabled by protocol support
VERIFY-01/05/06/08/14/15/18/21/26/29  the deterministic decision, reliance and reconstruction rules
"""

from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_crypto import CryptoError, LocalSigner, sign_jws, verify_external_jws
from zid_platform.modules.credential.application.verifier import (
    PINNED_ADAPTERS,
    live_keys,
    require_pinned_adapter,
)
from zid_platform.modules.credential.domain.credentials import (
    DEFERRED_ROLES,
    ENABLED_ROLES,
    MDOC_ADAPTER,
    SD_JWT_ADAPTER,
    Configuration,
    CredentialError,
    IssuerTrust,
    build_basis,
    disclose,
    replay_decision,
    require_role,
    trust_decision,
)
from zid_platform.modules.credential.domain.vcdm import VCDM_ADAPTER
from zid_platform.modules.verification.domain.verification import (
    ClaimResult,
    HeldAssertion,
    Profile,
    ProviderResult,
    SessionStatus,
    decide,
    decision_basis,
    reconstruct,
    reliance,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
PID, DIPLOMA = "urn:eu.europa.ec.eudi:pid:1", "urn:eu:diploma:1"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def config(**kw: Any) -> Configuration:
    base: dict[str, Any] = {
        "configuration_id": "c1",
        "credential_type": PID,
        "format": "SD_JWT_VC",
        "adapter_version": SD_JWT_ADAPTER,
        "required_claims": ("given_name",),
        "optional_claims": (),
        "holder_binding_required": True,
        "status_method": "TOKEN_STATUS_LIST",
        "status_mandatory": True,
        "risk_class": 2,
        "status": "ACTIVE",
    }
    return Configuration(**{**base, **kw})


def trust(**kw: Any) -> IssuerTrust:
    base: dict[str, Any] = {
        "issuer_identifier": "https://gov.example",
        "credential_types": frozenset({PID}),
        "jurisdictions": frozenset({"GB"}),
        "trust_source": "PINNED",
        "status": "ACTIVE",
        "valid_to": NOW + timedelta(days=30),
    }
    return IssuerTrust(**{**base, **kw})


def code_of(fn: Any, *a: Any, **kw: Any) -> str:
    with pytest.raises(CredentialError) as e:
        fn(*a, **kw)
    return str(e.value.code)


# ------------------------------------------------------------------------------------------ VC-04


def test_vc04_trust_is_typed_by_credential_type_and_jurisdiction() -> None:
    """VC-04: trusted for credential A does not mean trusted for credential B."""
    t = trust()
    trust_decision(t, credential_type=PID, jurisdiction="GB", now=NOW)
    # the issuer is trusted for the PID, not for a diploma it also signs
    assert (
        code_of(trust_decision, t, credential_type=DIPLOMA, jurisdiction="GB", now=NOW)
        == "ISSUER_UNTRUSTED"
    )
    # nor for the PID in a jurisdiction the trust entry does not list
    assert (
        code_of(trust_decision, t, credential_type=PID, jurisdiction="FR", now=NOW)
        == "ISSUER_UNTRUSTED"
    )
    # unless policy permits: an entry that names both types and both jurisdictions
    wide = trust(credential_types=frozenset({PID, DIPLOMA}), jurisdictions=frozenset({"GB", "FR"}))
    trust_decision(wide, credential_type=DIPLOMA, jurisdiction="FR", now=NOW)
    # unknown issuer, suspended / revoked / quarantined entry, expired entry: never trusted
    assert code_of(trust_decision, None, credential_type=PID, jurisdiction="GB", now=NOW)
    for status in ("SUSPENDED", "REVOKED", "QUARANTINED"):
        assert code_of(
            trust_decision, trust(status=status), credential_type=PID, jurisdiction="GB", now=NOW
        )
    expired = trust(valid_to=NOW - timedelta(seconds=1))
    assert code_of(trust_decision, expired, credential_type=PID, jurisdiction="GB", now=NOW)


# ------------------------------------------------------------------------------------------ VC-13


def test_vc13_a_configuration_for_another_draft_is_rejected_not_parsed() -> None:
    """VC-13 / VC §29 "Draft adapter incompatibility": PROFILE_VERSION_MISMATCH, never the newest."""
    assert PINNED_ADAPTERS == {
        "SD_JWT_VC": SD_JWT_ADAPTER,
        "MDOC": MDOC_ADAPTER,
        "VC_JWT": VCDM_ADAPTER,
    }
    assert "draft-19" in SD_JWT_ADAPTER  # SD-JWT VC is pinned to a draft, not "latest"
    require_pinned_adapter(config())
    for stale in ("sd-jwt-vc/draft-20+rfc9901", "sd-jwt-vc/latest", ""):
        assert (
            code_of(require_pinned_adapter, config(adapter_version=stale))
            == "PROFILE_VERSION_MISMATCH"
        )
    # an mdoc configuration pinned to the SD-JWT adapter is the wrong adapter too
    assert (
        code_of(require_pinned_adapter, config(format="MDOC", adapter_version=SD_JWT_ADAPTER))
        == "PROFILE_VERSION_MISMATCH"
    )


def test_vc13_sd_jwt_hash_algorithm_is_pinned() -> None:
    """VC-13: a later SD-JWT revision that changes `_sd_alg` is refused, not silently accepted."""
    d = b64(json.dumps(["salt", "given_name", "Erika"]).encode())
    payload = {"iss": "x", "_sd": ["digest"], "_sd_alg": "sha-512"}
    assert code_of(disclose, payload, [d]) == "SD_JWT_ALG_UNSUPPORTED"


# ------------------------------------------------------------------------------------------ VC-24


def _forged(header: dict[str, Any], key: Any = b"") -> str:
    head = b64(json.dumps(header).encode())
    body = b64(json.dumps({"iss": "https://gov.example", "vct": PID}).encode())
    return f"{head}.{body}.{b64(key)}"


def test_vc24_none_symmetric_and_unapproved_algorithms_are_rejected() -> None:
    """VC-24: algorithm downgrade (none, HS256 with the public key as secret, an alg the
    integration did not approve) is refused before any key is tried."""
    signer = LocalSigner("issuer-key")
    jwks = [signer.public_jwk()]
    for alg in ("none", "None", "HS256", "HS512", "ES512", "RS1", ""):
        token = _forged({"alg": alg, "typ": "dc+sd-jwt", "kid": "issuer-key"})
        with pytest.raises(CryptoError) as e:
            verify_external_jws(token, keys=jwks, algorithms=["ES256"], typ="dc+sd-jwt")
        assert e.value.code == "ALG_NOT_ALLOWED", alg
    # a real signature by an approved-in-general algorithm that THIS issuer entry did not approve
    ed = LocalSigner("ed-key", "Ed25519")
    token = sign_jws(ed, {"iss": "https://gov.example"}, typ="dc+sd-jwt")
    with pytest.raises(CryptoError) as e:
        verify_external_jws(token, keys=[ed.public_jwk()], algorithms=["ES256"], typ="dc+sd-jwt")
    assert e.value.code == "ALG_NOT_ALLOWED"
    # control: the approved algorithm still verifies
    ok = sign_jws(signer, {"iss": "https://gov.example"}, typ="dc+sd-jwt")
    verify_external_jws(ok, keys=jwks, algorithms=["ES256"], typ="dc+sd-jwt")
    # embedded or remote key material in the header is never trusted
    for member in ({"jwk": signer.public_jwk()}, {"jku": "https://evil.example/k"}):
        token = _forged({"alg": "ES256", "typ": "dc+sd-jwt", **member})
        with pytest.raises(CryptoError) as e:
            verify_external_jws(token, keys=jwks, algorithms=["ES256"], typ="dc+sd-jwt")
        assert e.value.code == "JWS_HEADER_REJECTED"


# ------------------------------------------------------------------------------------------ VC-25


def test_vc25_a_pinned_key_past_its_cutoff_verifies_nothing() -> None:
    """VC-25: bounded rollover overlap; the retired key is gone after `not_after`."""
    old, new = LocalSigner("k-old"), LocalSigner("k-new")
    cutoff = NOW + timedelta(days=7)
    jwks = {
        "keys": [
            {**old.public_jwk(), "not_after": cutoff.timestamp()},
            new.public_jwk(),
        ]
    }
    during = live_keys(jwks, NOW)
    assert {k["kid"] for k in during["keys"]} == {"k-old", "k-new"}  # overlap
    assert all("not_after" not in k for k in during["keys"])
    after = live_keys(jwks, cutoff)  # the cutoff instant is already outside the overlap
    assert {k["kid"] for k in after["keys"]} == {"k-new"}
    token = sign_jws(old, {"iss": "x"}, typ="dc+sd-jwt")
    verify_external_jws(token, keys=during["keys"], algorithms=["ES256"], typ="dc+sd-jwt")
    with pytest.raises(CryptoError) as e:
        verify_external_jws(token, keys=after["keys"], algorithms=["ES256"], typ="dc+sd-jwt")
    assert e.value.code == "KEY_UNKNOWN"
    # a malformed cutoff never extends trust
    assert live_keys({"keys": [{**old.public_jwk(), "not_after": "never"}]}, NOW) == {"keys": []}


# ------------------------------------------------------------------------------------------ VC-27


def _basis(**kw: Any) -> dict[str, Any]:
    row = {
        "trust_id": "t1",
        "issuer_identifier": "https://gov.example",
        "trust_source": "PINNED",
        "revision": 3,
        "status": "ACTIVE",
        "valid_to": NOW + timedelta(days=30),
        "credential_types": [PID],
        "jurisdictions": ["GB"],
    }
    from zid_platform.modules.credential.domain.credentials import trust_entry

    base: dict[str, Any] = {
        "credential_status": "VALID",
        "holder_bound": True,
        "jurisdiction": "GB",
        "decided_at": NOW,
    }
    return build_basis(config(), [trust_entry(row)], **{**base, **kw})


def test_vc27_a_recorded_basis_replays_to_the_same_decision() -> None:
    """VC-27: the pinned adapter/profile, trust entry (id + revision), status observation and
    binding result are enough to re-derive the decision, without any credential or claim."""
    basis = _basis()
    assert replay_decision(basis) == "VERIFIED"
    assert replay_decision(json.loads(json.dumps(basis))) == "VERIFIED"  # as stored in jsonb
    assert basis["adapter_version"] == SD_JWT_ADAPTER
    assert basis["trust"][0]["revision"] == 3 and basis["trust"][0]["trust_id"] == "t1"
    assert basis["status"]["result"] == "VALID"
    # the replay is the decision rules, not a rubber stamp: altered facts give the failure
    for edit, code in (
        ({"status": {"result": "REVOKED", "observed_at": "x"}}, "CREDENTIAL_REVOKED"),
        ({"status": {"result": "UNKNOWN", "observed_at": "x"}}, "CREDENTIAL_STATUS_UNAVAILABLE"),
        ({"holder_bound": False}, "HOLDER_BINDING_MISSING"),
        ({"credential_type": DIPLOMA}, "ISSUER_UNTRUSTED"),
        ({"jurisdiction": "FR"}, "ISSUER_UNTRUSTED"),
        ({"trust": []}, "ISSUER_UNTRUSTED"),
    ):
        edited = copy.deepcopy(json.loads(json.dumps(basis)))
        edited.update(edit)
        assert code_of(replay_decision, edited) == code, edit
    text = json.dumps(basis)
    assert "given_name" not in text and "Erika" not in text  # facts about the decision only


# ------------------------------------------------------------------------------------------ VC-28


def test_vc28_deferred_eudi_roles_stay_off() -> None:
    """VC-28: only the verifier and non-qualified issuer roles exist; the rest cannot be enabled
    by protocol support or configuration."""
    assert ENABLED_ROLES == {"VERIFIER", "ISSUER_NON_QUALIFIED"}
    assert DEFERRED_ROLES == {
        "QUALIFIED_EAA_ISSUER",
        "WALLET_PROVIDER",
        "PID_PROVIDER",
        "QTSP",
        "BUSINESS_WALLET_PROVIDER",
    }
    for role in sorted(DEFERRED_ROLES | {"", "ADMIN", "verifier"}):
        assert code_of(require_role, role) == "ROLE_NOT_ENABLED", role
    for role in ENABLED_ROLES:
        require_role(role)


# ---------------------------------------------------------------------------- VERIFY decisions

REQ = {
    "required_claims": ["legal_name", "birthdate"],
    "accepted_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING", "DIGITAL_CREDENTIAL"],
    "binding_required": True,
    "liveness_required": True,
    "biometric_jurisdictions": ["GB"],
    "jurisdictions": ["GB", "FR"],
    "manual_review": False,
    "reliance_purposes": ["onboarding"],
    "freshness_days": 30,
}
PROFILE = Profile.from_json("p1", "t", "uk-onboarding", 4, "IAL-Z2", REQ, "ACTIVE")


def claims(
    *, name_ok: bool = True, dob_ok: bool = True, evidence: tuple[str, ...] = ("GOVERNMENT_ID",)
) -> tuple[ClaimResult, ...]:
    return (
        ClaimResult("legal_name", name_ok, "Barbara Jensen", "document", evidence),
        ClaimResult("birthdate", dob_ok, "1980-02-01", "document", evidence),
    )


def facts(**kw: Any) -> ProviderResult:
    base: dict[str, Any] = {
        "status": "COMPLETED",
        "claims": claims(),
        "binding": "BIOMETRIC",
        "liveness_passed": True,
        "fraud_signals": (),
    }
    return ProviderResult(**{**base, **kw})


def test_verify05_forged_document_text_never_becomes_verified() -> None:
    """VERIFY-05: OCR text is extractable from a forged document, but the claim is only VERIFIED
    when the provider's normalized facts say the authenticity check passed."""
    # control: authentic document
    assert decide(PROFILE, facts(), jurisdiction="GB").status is SessionStatus.VERIFIED
    # authenticity failed: the provider returns the text but verified=False for the claims
    forged = facts(claims=claims(name_ok=False, dob_ok=False))
    d = decide(PROFILE, forged, jurisdiction="GB")
    assert d.status is SessionStatus.FAILED and d.verified_claims == ()
    # one claim not authentic is not rescued by the other: no partial VERIFIED either
    d = decide(PROFILE, facts(claims=claims(dob_ok=False)), jurisdiction="GB")
    assert d.status is SessionStatus.FAILED and "INSUFFICIENT_EVIDENCE" in d.reasons
    assert [c.claim for c in d.verified_claims] == ["legal_name"]  # never creates birthdate
    # a template/tamper signal on an otherwise "verified" result cannot pass automatically
    d = decide(PROFILE, facts(fraud_signals=("DOCUMENT_TAMPERED",)), jurisdiction="GB")
    assert d.status is SessionStatus.FAILED and d.outcome == "FRAUD_SUSPECTED"
    # provider-level failure: never VERIFIED
    assert decide(PROFILE, facts(status="FAILED"), jurisdiction="GB").status is SessionStatus.FAILED


def test_verify06_a_valid_document_of_another_person_fails_binding() -> None:
    """VERIFY-06: a genuine document with no person-to-document binding fails the profile."""
    for binding in (None, "NONE", "MISMATCH"):
        d = decide(PROFILE, facts(binding=binding, liveness_passed=None), jurisdiction="GB")
        assert d.status is SessionStatus.FAILED, binding
        assert "BINDING_FAILED" in d.reasons and d.verified_claims != ()  # claims alone: no
    # with manual review enabled it goes to a human, never to VERIFIED
    review = replace(PROFILE, manual_review=True)
    d = decide(review, facts(binding=None, liveness_passed=None), jurisdiction="GB")
    assert d.status is SessionStatus.MANUAL_REVIEW


def test_verify08_pad_and_injection_defence_cannot_be_skipped() -> None:
    """VERIFY-08: a profile that needs liveness/PAD fails synthetic, replayed or unreported
    captures; injection signals can never pass automatically."""
    for passed in (False, None):
        d = decide(PROFILE, facts(liveness_passed=passed), jurisdiction="GB")
        assert d.status is SessionStatus.FAILED and "LIVENESS_FAILED" in d.reasons, passed
    for signal in ("DEEPFAKE_SUSPECTED", "VIRTUAL_CAMERA_INJECTION", "REPLAYED_CAPTURE"):
        d = decide(PROFILE, facts(fraud_signals=(signal,)), jurisdiction="GB")
        assert d.status is SessionStatus.FAILED and d.outcome == "FRAUD_SUSPECTED"
    assert decide(PROFILE, facts(), jurisdiction="GB").status is SessionStatus.VERIFIED


def test_verify14_provider_score_scales_do_not_move_the_canonical_mapping() -> None:
    """VERIFY-14: scores are an input the adapter normalizes; the canonical decision depends only
    on normalized facts, so a changed provider scale cannot change it without a mapping release."""
    from zid_platform.modules.verification.application.service import _result

    def payload(score: float, scale: str) -> dict[str, Any]:
        return {
            "status": "COMPLETED",
            "binding": "BIOMETRIC",
            "liveness_passed": True,
            "fraud_signals": [],
            "claims": [
                {
                    "claim": "legal_name",
                    "verified": True,
                    "method": "document",
                    "evidence": ["GOVERNMENT_ID"],
                },
                {
                    "claim": "birthdate",
                    "verified": True,
                    "method": "document",
                    "evidence": ["GOVERNMENT_ID"],
                },
            ],
            "match_score": score,
            "score_scale": scale,
            "risk_score": score * 3,
        }

    outcomes = {
        decide(PROFILE, _result(payload(score, scale)), jurisdiction="GB").outcome
        for score, scale in ((0.99, "0-1"), (99, "0-100"), (0.01, "0-1"), (1, "0-100"))
    }
    assert outcomes == {"VERIFIED"}  # a "low" raw score is not read; only the normalized fact is
    parsed = _result(payload(0.5, "0-1"))
    assert not any("score" in f for f in ProviderResult.__dataclass_fields__)
    assert parsed.claims[0].verified is True
    # the adapter's mapping version is what pins the interpretation: it is recorded with the facts
    basis = decision_basis(parsed, adapter_id="beta", mapping_version="v3", salt="s")
    assert basis["mapping_version"] == "v3" and "score" not in json.dumps(basis)


def test_verify01_two_providers_give_the_same_canonical_assertion_and_no_vendor_fields_leak() -> (
    None
):
    """VERIFY-01: the same facts reported by two vendors (each with its own extra fields) normalize to equal
    results, the same decision and the same recorded basis; no vendor field survives into any of them."""
    from zid_platform.modules.verification.application.service import _result

    facts: dict[str, Any] = {
        "status": "COMPLETED",
        "binding": "NON_BIOMETRIC",
        "liveness_passed": True,
        "fraud_signals": [],
        "claims": [
            {
                "claim": "legal_name",
                "verified": True,
                "method": "document",
                "evidence": ["GOVERNMENT_ID"],
            },
            {
                "claim": "birthdate",
                "verified": True,
                "method": "document",
                "evidence": ["GOVERNMENT_ID"],
            },
        ],
    }
    acme = {**facts, "acme_txn": "tx-1", "acme_confidence": 91, "acme_doc_class": "passport"}
    beta = {**facts, "beta_ref": "b-9", "beta_risk": "LOW", "vendor_session_cookie": "c=1"}
    a, b = _result(acme), _result(beta)
    assert a == b  # compatible canonical results
    assert decide(PROFILE, a, jurisdiction="GB") == decide(PROFILE, b, jurisdiction="GB")
    basis_a = decision_basis(a, adapter_id="acme", mapping_version="v1", salt="s")
    basis_b = decision_basis(b, adapter_id="beta", mapping_version="v1", salt="s")
    assert {k: v for k, v in basis_a.items() if k != "adapter_id"} == {
        k: v for k, v in basis_b.items() if k != "adapter_id"
    }
    dumped = json.dumps([basis_a, basis_b]) + repr([a, b])
    for vendor_field in ("acme_", "beta_", "vendor_session", "confidence", "tx-1", "b-9"):
        assert vendor_field not in dumped, vendor_field
    assert not [f for f in ProviderResult.__dataclass_fields__ if "acme" in f or "beta" in f]


def _held(**kw: Any) -> HeldAssertion:
    base: dict[str, Any] = {
        "assertion_id": "a1",
        "claim_type": "legal_name",
        "assurance_level": "IAL-Z2",
        "status": "VALID",
        "valid_until": NOW + timedelta(days=1),
        "jurisdiction": "GB",
        "profile_purposes": frozenset({"onboarding"}),
    }
    return HeldAssertion(**{**base, **kw})


def test_verify15_an_expired_assertion_is_not_reused_for_reliance() -> None:
    """VERIFY-15: past fresh_until the assertion cannot satisfy reliance at any level; fresh
    proof is required (REVERIFY_REQUIRED / SOURCE_STALE)."""
    kw: dict[str, Any] = {
        "claims": ["legal_name"],
        "purpose": "onboarding",
        "jurisdiction": "GB",
        "now": NOW,
    }
    for level in ("IAL-Z2", "IAL-Z3"):  # R2 / R3 style levels
        fresh = reliance([_held(assurance_level="IAL-Z3")], required_level=level, **kw)
        assert fresh[0] == "REUSE_ALLOWED"
        stale = reliance(
            [_held(assurance_level="IAL-Z3", valid_until=NOW)], required_level=level, **kw
        )  # fresh_until == now is already expired
        assert stale == ("REVERIFY_REQUIRED", ("SOURCE_STALE",), ())
    # revoked / stale-status assertions are no better
    for status in ("REVOKED", "EXPIRED", "STALE", "FAILED"):
        out = reliance([_held(status=status)], required_level="IAL-Z2", **kw)
        assert out[0] == "REVERIFY_REQUIRED", status
    # an expired old assertion does not block a newer valid one
    both = [_held(assertion_id="old", valid_until=NOW), _held(assertion_id="new")]
    assert reliance(both, required_level="IAL-Z2", **kw)[2] == ("new",)


def test_verify18_conflicting_authoritative_sources_never_resolve_in_the_applicants_favour() -> (
    None
):
    """VERIFY-18: two accepted sources that disagree on a claim give SOURCE_CONFLICT (review or
    failure); neither the favourable nor the last value is picked."""
    conflicting = (
        ClaimResult("legal_name", True, "Barbara Jensen", "document", ("GOVERNMENT_ID",)),
        ClaimResult("legal_name", True, "Barbara Jensson", "database", ("DIGITAL_CREDENTIAL",)),
        ClaimResult("birthdate", True, "1980-02-01", "document", ("GOVERNMENT_ID",)),
    )
    d = decide(PROFILE, facts(claims=conflicting), jurisdiction="GB")
    assert d.status is SessionStatus.FAILED and d.outcome == "SOURCE_CONFLICT"
    assert "CONFLICT:legal_name" in d.reasons
    assert [c.claim for c in d.verified_claims] == ["birthdate"]  # never legal_name
    review = replace(PROFILE, manual_review=True)
    d = decide(review, facts(claims=conflicting), jurisdiction="GB")
    assert d.status is SessionStatus.MANUAL_REVIEW and d.outcome == "SOURCE_CONFLICT"
    # same value from two sources is corroboration, not a conflict
    agree = conflicting[:1] + (replace(conflicting[1], value="Barbara Jensen"),) + conflicting[2:]
    assert decide(PROFILE, facts(claims=agree), jurisdiction="GB").status is SessionStatus.VERIFIED
    # verified by one source, refuted by another: also a conflict, whichever comes last
    for order in (1, -1):
        mixed = (
            ClaimResult("legal_name", True, "Barbara Jensen", "document", ("GOVERNMENT_ID",)),
            ClaimResult("legal_name", False, "Barbara Jensen", "database", ("DIGITAL_CREDENTIAL",)),
            ClaimResult("birthdate", True, "1980-02-01", "document", ("GOVERNMENT_ID",)),
        )[::order]
        assert decide(PROFILE, facts(claims=mixed), jurisdiction="GB").outcome == "SOURCE_CONFLICT"


def test_verify21_a_non_biometric_path_reaches_the_same_assurance() -> None:
    """VERIFY-21: when the biometric path is unavailable to the applicant, an approved
    non-biometric binding completes at the same level, with the same claims and freshness."""
    bio = decide(PROFILE, facts(), jurisdiction="GB")
    alt = decide(
        PROFILE,
        facts(
            binding="NON_BIOMETRIC",
            liveness_passed=None,
            claims=claims(evidence=("DIGITAL_CREDENTIAL",)),
        ),
        jurisdiction="GB",
    )
    assert bio.status is alt.status is SessionStatus.VERIFIED
    assert {c.claim for c in alt.verified_claims} == {c.claim for c in bio.verified_claims}
    # the level is the profile's level either way (assertions take profile.level)
    assert PROFILE.level == "IAL-Z2"
    # and in a jurisdiction where biometrics are not permitted, only the alternative works
    assert decide(PROFILE, facts(), jurisdiction="FR").outcome == "BIOMETRIC_NOT_PERMITTED"
    in_fr = decide(PROFILE, facts(binding="NON_BIOMETRIC", liveness_passed=None), jurisdiction="FR")
    assert in_fr.status is SessionStatus.VERIFIED


def test_verify26_a_profile_that_needs_the_fraud_engine_never_fails_open() -> None:
    """VERIFY-26: no fraud verdict (engine down, field absent, UNAVAILABLE) is never VERIFIED."""
    needs = replace(PROFILE, fraud_check_required=True)
    for check in (None, "UNAVAILABLE", "", "SKIPPED"):
        d = decide(needs, facts(fraud_check=check), jurisdiction="GB")
        assert d.status is SessionStatus.FAILED and d.outcome == "FRAUD_CHECK_UNAVAILABLE", check
        assert d.verified_claims == ()
    assert decide(needs, facts(fraud_check="PERFORMED"), jurisdiction="GB").status is (
        SessionStatus.VERIFIED
    )
    # a profile that does not require it is unchanged
    assert decide(PROFILE, facts(), jurisdiction="GB").status is SessionStatus.VERIFIED
    # a performed check that flags fraud still blocks
    flagged = decide(needs, facts(fraud_check="PERFORMED", fraud_signals=("X",)), jurisdiction="GB")
    assert flagged.status is SessionStatus.FAILED


def test_verify29_a_decision_is_reconstructed_from_pinned_profile_and_normalized_facts() -> None:
    """VERIFY-29: profile version + recorded normalized facts (claims as commitments, binding,
    liveness, signals, adapter/mapping version, evidence commitment) re-derive the same outcome."""
    cases = (
        facts(),
        facts(binding=None, liveness_passed=None),
        facts(liveness_passed=False),
        facts(fraud_signals=("DOCUMENT_TAMPERED",)),
        facts(claims=claims(dob_ok=False)),
        facts(status="PENDING"),
    )
    for result in cases:
        recorded = decide(PROFILE, result, jurisdiction="GB")
        basis = decision_basis(result, adapter_id="beta", mapping_version="v3", salt="session-1")
        stored = json.loads(json.dumps(basis))  # through jsonb
        again = reconstruct(PROFILE, stored, jurisdiction="GB")
        assert (again.status, again.outcome, again.reasons) == (
            recorded.status,
            recorded.outcome,
            recorded.reasons,
        )
        assert [c.claim for c in again.verified_claims] == [
            c.claim for c in recorded.verified_claims
        ]
        text = json.dumps(stored)
        assert "Barbara" not in text and "1980" not in text  # commitments, never values
        assert stored["adapter_id"] == "beta" and stored["mapping_version"] == "v3"
    # a different pinned profile version can legitimately decide differently: the profile is part
    # of the basis (id + version are recorded with the session)
    stricter = replace(PROFILE, version=5, fraud_check_required=True)
    basis = decision_basis(facts(), adapter_id="beta", mapping_version="v3", salt="s")
    assert reconstruct(stricter, basis, jurisdiction="GB").outcome == "FRAUD_CHECK_UNAVAILABLE"
    assert reconstruct(PROFILE, basis, jurisdiction="GB").outcome == "VERIFIED"
    # equal values keep equal commitments within a session, so conflicts remain detectable
    conflicted = facts(
        claims=(
            ClaimResult("legal_name", True, "A", "d", ("GOVERNMENT_ID",)),
            ClaimResult("legal_name", True, "B", "d", ("GOVERNMENT_ID",)),
            ClaimResult("birthdate", True, "1980-02-01", "d", ("GOVERNMENT_ID",)),
        )
    )
    b = decision_basis(conflicted, adapter_id="beta", mapping_version="v3", salt="s")
    assert reconstruct(PROFILE, b, jurisdiction="GB").outcome == "SOURCE_CONFLICT"
