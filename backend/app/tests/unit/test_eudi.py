"""EUDI Wallet relying-party obligations, attestation classes and interoperability conformance (ZID-RCG-003 RCG-G11; CR-080)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.credential.domain import eudi

NOW = datetime(2027, 3, 1, 12, tzinfo=UTC)
PID = "eu.europa.ec.eudi.pid.1"
CERT = "abc123hash"
REG = eudi.Registration(
    member_state="DE",
    registration_id="DE-RP-1",
    status=eudi.RegistrationStatus.ACTIVE,
    valid_from=NOW - timedelta(days=30),
    valid_until=NOW + timedelta(days=300),
    access_certificate_sha256=CERT,
    registration_certificate_ref="DE-RC-1",
    intended_uses={"KYC_ONBOARDING": {PID: frozenset({"family_name", "given_name", "birth_date"})}},
)


def check(regs: list[eudi.Registration] | None = None, **over: Any) -> list[str]:
    args: dict[str, Any] = {
        "member_state": "DE",
        "intended_use": "KYC_ONBOARDING",
        "attestation_type": PID,
        "claims": ["family_name", "birth_date"],
        "client_id": f"x509_hash:{CERT}",
        "at": NOW,
    } | over
    return eudi.check_request([REG] if regs is None else regs, **args)


def test_a_registered_use_and_attributes_under_the_registered_certificate_may_be_requested() -> (
    None
):
    assert check() == []


def test_eudi_types_are_recognised_and_others_are_not() -> None:
    assert (
        eudi.is_eudi_type(PID)
        and eudi.is_eudi_type("urn:eudi:pid:de:1")
        and eudi.is_eudi_type("eu.europa.ec.eudi.loyalty.1")
    )
    assert not eudi.is_eudi_type("org.iso.18013.5.1.mDL") and not eudi.is_eudi_type(
        "OpenBadgeCredential"
    )


def test_no_registration_in_that_member_state_is_refused() -> None:
    assert check(member_state="FR") == ["WALLET_RP_NOT_REGISTERED"]
    assert check(member_state="US") == ["NOT_A_MEMBER_STATE"]
    assert check(regs=[]) == ["WALLET_RP_NOT_REGISTERED"]


@pytest.mark.parametrize(
    "reg",
    [
        replace(REG, status=eudi.RegistrationStatus.SUSPENDED),
        replace(REG, status=eudi.RegistrationStatus.REVOKED),
        replace(REG, valid_until=NOW - timedelta(seconds=1)),
        replace(REG, valid_from=NOW + timedelta(days=1)),
    ],
)
def test_a_suspended_revoked_or_out_of_date_registration_is_refused(reg: eudi.Registration) -> None:
    assert check(regs=[reg]) == ["WALLET_RP_REGISTRATION_NOT_ACTIVE"]


def test_asking_for_an_attribute_not_registered_is_refused_not_trimmed() -> None:
    assert check(claims=["family_name", "nationality"]) == ["ATTRIBUTE_NOT_REGISTERED"]


def test_an_unregistered_use_or_type_is_refused() -> None:
    assert check(intended_use="MARKETING") == ["INTENDED_USE_NOT_REGISTERED"]
    assert check(attestation_type="eu.europa.ec.eudi.loyalty.1") == [
        "ATTESTATION_TYPE_NOT_REGISTERED"
    ]


def test_the_request_must_be_signed_under_the_registered_access_certificate() -> None:
    assert check(client_id="x509_hash:someone-else") == ["ACCESS_CERTIFICATE_MISMATCH"]
    assert check(client_id="openid_federation:https://id.zoiko.test") == [
        "ACCESS_CERTIFICATE_MISSING"
    ]


# ---- attestation classes


def test_qeaa_is_claimed_only_with_qualified_status_at_issuance() -> None:
    assert eudi.classify(
        "eu.europa.ec.eudi.diploma.1", issuer_is_public_body=False, qualified_at_issuance=True
    ) == (eudi.AttestationClass.QEAA, ())
    assert eudi.classify(
        "eu.europa.ec.eudi.diploma.1", issuer_is_public_body=False, qualified_at_issuance=False
    ) == (
        eudi.AttestationClass.EAA,
        ("QEAA_STATUS_NOT_ESTABLISHED",),
    )
    assert (
        eudi.classify(
            "eu.europa.ec.eudi.diploma.1", issuer_is_public_body=False, qualified_at_issuance=None
        )[0]
        is eudi.AttestationClass.EAA
    )


def test_a_public_body_issuer_is_pub_eaa_and_a_pid_needs_a_listed_provider() -> None:
    assert (
        eudi.classify(
            "eu.europa.ec.eudi.register.1", issuer_is_public_body=True, qualified_at_issuance=None
        )[0]
        is eudi.AttestationClass.PUB_EAA
    )
    assert eudi.classify(
        PID, issuer_is_public_body=True, qualified_at_issuance=None, pid_provider_listed=True
    ) == (eudi.AttestationClass.PID, ())
    assert eudi.classify(
        PID, issuer_is_public_body=True, qualified_at_issuance=None, pid_provider_listed=None
    ) == (
        eudi.AttestationClass.NOT_TRUSTED,
        ("PID_PROVIDER_NOT_LISTED",),
    )


# ---- interoperability conformance


def good() -> tuple[dict[str, Any], dict[str, Any]]:
    header = {"alg": "ES256", "typ": "oauth-authz-req+jwt", "x5c": ["MIIB..."]}
    payload = {
        "client_id": f"x509_hash:{CERT}",
        "response_type": "vp_token",
        "response_mode": "direct_post.jwt",
        "response_uri": "https://id.zoiko.test/presentations/response/t",
        "nonce": "n" * 43,
        "state": "s",
        "dcql_query": {"credentials": [{"id": "pid", "format": "mso_mdoc"}]},
        "client_metadata": {"jwks": {"keys": [{"kty": "EC", "use": "enc", "alg": "ECDH-ES"}]}},
        "iat": 1000,
        "exp": 1300,
    }
    return header, payload


def test_a_profile_conformant_request_object_passes() -> None:
    h, p = good()
    assert eudi.conformance(h, p, now_epoch=1100) == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda h, p: p.update(response_mode="direct_post"), "RESPONSE_NOT_ENCRYPTED"),
        (lambda h, p: p.update(client_id="openid_federation:https://x"), "CLIENT_ID_NOT_X509"),
        (lambda h, p: h.pop("x5c"), "CERTIFICATE_CHAIN_MISSING"),
        (
            lambda h, p: p.update(dcql_query={"credentials": [{"format": "jwt_vc_json"}]}),
            "FORMAT_NOT_INTEROPERABLE",
        ),
        (lambda h, p: p.pop("dcql_query"), "DCQL_MISSING"),
        (lambda h, p: p.update(nonce="short"), "NONCE_TOO_SHORT"),
        (lambda h, p: p.update(exp=1000 + 3600), "REQUEST_LIFETIME_INVALID"),
        (lambda h, p: p.update(response_uri="http://id.zoiko.test/r"), "RESPONSE_URI_NOT_HTTPS"),
        (lambda h, p: p.update(client_metadata={}), "ENCRYPTION_KEY_MISSING"),
        (lambda h, p: p.update(response_type="code"), "RESPONSE_TYPE_INVALID"),
    ],
)
def test_each_departure_from_the_profile_is_named(change: Any, reason: str) -> None:
    h, p = good()
    change(h, p)
    assert reason in eudi.conformance(h, p, now_epoch=1100)


def test_an_expired_request_object_is_not_conformant() -> None:
    h, p = good()
    assert eudi.conformance(h, p, now_epoch=1300) == ["REQUEST_EXPIRED"]


# ---- configuration


def test_parse_and_refuse_a_malformed_file() -> None:
    doc = {
        "registrations": [
            {
                "member_state": "de",
                "registration_id": "DE-RP-1",
                "valid_from": "2027-01-01T00:00:00+00:00",
                "access_certificate_sha256": CERT,
                "registration_certificate_ref": "DE-RC-1",
                "intended_uses": {"KYC_ONBOARDING": {PID: ["family_name"]}},
            }
        ]
    }
    (r,) = eudi.parse(doc)
    assert (
        r.member_state == "DE"
        and r.status is eudi.RegistrationStatus.ACTIVE
        and r.intended_uses["KYC_ONBOARDING"][PID] == frozenset({"family_name"})
    )
    for bad in (
        {**doc["registrations"][0], "member_state": "US"},
        {**doc["registrations"][0], "valid_from": "2027-01-01T00:00:00"},
        {k: v for k, v in doc["registrations"][0].items() if k != "access_certificate_sha256"},
    ):
        with pytest.raises(eudi.EudiError) as e:
            eudi.parse({"registrations": [bad]})
        assert e.value.code == "EUDI_REGISTRATIONS_INVALID"


def test_the_shipped_file_lists_no_registration() -> None:
    from zid_platform.modules.credential.infrastructure.eudi_registrations import eudi_registrations

    assert eudi_registrations() == ()
