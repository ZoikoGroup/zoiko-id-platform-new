"""Signed extracts and QR codes (WF02 K03; acceptance T06, T07)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from zid_platform.modules.organization.domain import extract_authenticity as x
from zid_platform.modules.organization.infrastructure.extract_verifier import (
    verify_with_cryptography,
)

NOW = datetime(2026, 11, 17, tzinfo=UTC)
PAYLOAD = b'{"company_number":"00123456","status":"active"}'


def pem(public) -> bytes:
    return public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


ED = ed25519.Ed25519PrivateKey.generate()
EC = ec.generate_private_key(ec.SECP256R1())
RSA = rsa.generate_private_key(65537, 2048)


def key(kid, alg, public, **kw) -> x.TrustedKey:
    return x.TrustedKey(kid, alg, pem(public), NOW - timedelta(days=100), **kw)


ISSUER = x.Issuer(
    "uk-ch",
    keys=(
        key("ed1", "Ed25519", ED.public_key()),
        key("ec1", "ES256", EC.public_key()),
        key("rsa1", "PS256", RSA.public_key()),
    ),
    trusted_hosts=frozenset({"find-and-update.company-information.service.gov.uk"}),
    path_prefixes=("/company/", "/extract/"),
)
ISSUERS = {"uk-ch": ISSUER}


def verify(
    payload=PAYLOAD, sig=None, *, issuer="uk-ch", kid="ed1", alg="Ed25519", issuers=ISSUERS, now=NOW
):
    sig = ED.sign(PAYLOAD) if sig is None else sig
    return x.verify_signed_extract(
        payload,
        sig,
        claimed_issuer=issuer,
        key_id=kid,
        algorithm=alg,
        issuers=issuers,
        now=now,
        verifier=verify_with_cryptography,
    )


def test_a_signature_from_a_trusted_key_makes_the_extract_the_issuers_word() -> None:
    assert verify().result is x.Authenticity.ISSUER_SIGNED
    assert (
        verify(sig=EC.sign(PAYLOAD, ec.ECDSA(hashes.SHA256())), kid="ec1", alg="ES256").result
        is x.Authenticity.ISSUER_SIGNED
    )
    pss = RSA.sign(
        PAYLOAD,
        padding.PSS(padding.MGF1(hashes.SHA256()), padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    assert verify(sig=pss, kid="rsa1", alg="PS256").result is x.Authenticity.ISSUER_SIGNED


def test_an_edited_registration_number_breaks_the_signature_and_is_not_proven() -> None:  # T06
    forged = PAYLOAD.replace(b"00123456", b"00999999")
    f = verify(payload=forged)
    assert f.result is x.Authenticity.NOT_PROVEN and f.reasons == ("SIGNATURE_DOES_NOT_VERIFY",)


def test_the_key_material_comes_from_the_trust_list_never_from_the_file() -> None:
    attacker = ed25519.Ed25519PrivateKey.generate()
    assert verify(sig=attacker.sign(PAYLOAD)).reasons == (
        "SIGNATURE_DOES_NOT_VERIFY",
    )  # signed with a key that is not the issuer's
    assert verify(issuer="made-up-registry").reasons == ("ISSUER_NOT_TRUSTED",)
    assert verify(kid="not-listed").reasons == ("KEY_NOT_IN_TRUST_LIST",)


def test_weak_algorithms_and_mismatched_algorithms_are_refused() -> None:
    for alg in ("none", "RS256", "HS256", "ES256K", "MD5"):
        assert verify(alg=alg).reasons == ("ALGORITHM_NOT_ALLOWED",), alg
    assert verify(alg="ES256").reasons == (
        "ALGORITHM_MISMATCH",
    )  # the listed key is an Ed25519 key


def test_a_revoked_or_expired_or_future_key_does_not_count() -> None:
    revoked = x.Issuer(
        "uk-ch", keys=(key("ed1", "Ed25519", ED.public_key(), revoked_at=NOW - timedelta(days=1)),)
    )
    assert verify(issuers={"uk-ch": revoked}).reasons == ("KEY_REVOKED",)
    expired = x.Issuer(
        "uk-ch", keys=(key("ed1", "Ed25519", ED.public_key(), valid_to=NOW - timedelta(days=1)),)
    )
    assert verify(issuers={"uk-ch": expired}).reasons == ("KEY_NOT_VALID_NOW",)
    future = x.Issuer(
        "uk-ch",
        keys=(x.TrustedKey("ed1", "Ed25519", pem(ED.public_key()), NOW + timedelta(days=1)),),
    )
    assert verify(issuers={"uk-ch": future}).reasons == ("KEY_NOT_VALID_NOW",)
    later_revocation = x.Issuer(
        "uk-ch", keys=(key("ed1", "Ed25519", ED.public_key(), revoked_at=NOW + timedelta(days=1)),)
    )
    assert (
        verify(issuers={"uk-ch": later_revocation}).result is x.Authenticity.ISSUER_SIGNED
    )  # revoked in the future is still good now


def test_an_empty_payload_or_signature_is_not_proven() -> None:
    assert verify(payload=b"").reasons == ("SIGNATURE_MISSING",)
    assert verify(sig=b" ").reasons == (
        "SIGNATURE_DOES_NOT_VERIFY",
    )  # a signature that is present but wrong is a failed verification, not a missing one
    assert x.verify_signed_extract(
        PAYLOAD,
        b"",
        claimed_issuer="uk-ch",
        key_id="ed1",
        algorithm="Ed25519",
        issuers=ISSUERS,
        now=NOW,
        verifier=verify_with_cryptography,
    ).reasons == ("SIGNATURE_MISSING",)


GOOD_URL = "https://find-and-update.company-information.service.gov.uk/company/00123456"


def test_a_qr_that_resolves_to_the_issuers_host_and_returns_the_same_content_is_confirmed() -> None:
    f = x.judge_qr(GOOD_URL, ISSUER, extract_digest="sha256:aa", fetched_digest="sha256:aa")
    assert f == x.Finding(x.Authenticity.ISSUER_CONFIRMED_BY_QR, ())


def test_a_qr_pointing_at_a_mirror_or_a_disguise_is_no_proof() -> None:  # T07
    for url, why in (
        (
            "https://company-information-service.gov.uk.evil.example/company/1",
            "QR_HOST_NOT_THE_ISSUERS",
        ),
        ("https://evil.example/company/1", "QR_HOST_NOT_THE_ISSUERS"),
        (
            "https://find-and-update.company-information.service.gov.uk@evil.example/company/1",
            "QR_HAS_CREDENTIALS_IN_ADDRESS",
        ),
        ("http://find-and-update.company-information.service.gov.uk/company/1", "QR_NOT_HTTPS"),
        ("https://203.0.113.9/company/1", "QR_HOST_IS_AN_IP_ADDRESS"),
        (
            "https://find-and-update.company-information.service.gov.uk:8443/company/1",
            "QR_UNEXPECTED_PORT",
        ),
        (
            "https://find-and-update.company-information.service.gov.uk/login",
            "QR_PATH_NOT_THE_ISSUERS",
        ),
        (
            "https://sub.find-and-update.company-information.service.gov.uk/company/1",
            "QR_HOST_NOT_THE_ISSUERS",
        ),  # a subdomain is not listed, so it is not the issuer's
        ("https://bit.ly/abc", "QR_HOST_NOT_THE_ISSUERS"),
        ("https://", "QR_URL_UNREADABLE"),
        ("not a url", "QR_NOT_HTTPS"),
    ):
        f = x.judge_qr(url, ISSUER, extract_digest="sha256:aa", fetched_digest="sha256:aa")
        assert f.result is x.Authenticity.NOT_PROVEN and f.reasons == (why,), url


def test_a_look_alike_international_host_is_compared_only_in_its_ascii_form() -> None:
    f = x.judge_qr(
        "https://fïnd-and-update.company-information.service.gov.uk/company/1",
        ISSUER,
        extract_digest="a",
        fetched_digest="a",
    )
    assert f.result is x.Authenticity.NOT_PROVEN and f.reasons in (
        ("QR_HOST_NOT_ASCII",),
        ("QR_HOST_NOT_THE_ISSUERS",),
    )


def test_the_issuers_destination_returning_something_else_says_the_file_is_not_the_issuers_record() -> (
    None
):
    f = x.judge_qr(GOOD_URL, ISSUER, extract_digest="sha256:aa", fetched_digest="sha256:bb")
    assert f.result is x.Authenticity.CONTENT_MISMATCH


def test_an_issuer_that_cannot_be_reached_is_not_proven_and_not_a_finding_against_the_file() -> (
    None
):
    f = x.judge_qr(GOOD_URL, ISSUER, extract_digest="sha256:aa", fetched_digest=None)
    assert f == x.Finding(x.Authenticity.NOT_PROVEN, ("ISSUER_DESTINATION_UNAVAILABLE",))
    assert x.judge_qr(GOOD_URL, None, extract_digest="a", fetched_digest="a").reasons == (
        "ISSUER_NOT_TRUSTED",
    )
