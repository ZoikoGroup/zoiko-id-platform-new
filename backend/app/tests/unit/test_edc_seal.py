"""GID-051 / GLB-36: a sealed European Digital Credential. The seal and the claim are separate answers and neither is a licence."""

from __future__ import annotations

import pytest
from pdf_signing import pki
from xades_signing import sign_xml
from zid_platform.modules.credential.application import edc
from zid_platform.modules.evidence import ports as evidence
from zid_platform.modules.evidence.application import trust as tr

BODY = (
    '<learningAchievement><title><text lang="en">Bachelor of Science in Chemistry</text></title></learningAchievement>'
    "<issuer><legalName><text>University of Exampleton</text></legalName></issuer>"
    "<issued>2024-07-12T00:00:00Z</issued>"
    "<credentialSubject><givenName>Dana</givenName><birthDate>1990-02-03</birthDate></credentialSubject>"
)


def sealed(p, body: str = BODY) -> bytes:
    return sign_xml(p.signer, body=body)


def test_the_claim_is_read_and_nothing_about_the_holder_leaves() -> None:
    claim = edc.read(sealed(pki()))
    assert claim.awarding_body == "University of Exampleton"
    assert claim.title == "Bachelor of Science in Chemistry"
    assert claim.issued == "2024-07-12T00:00:00Z"
    assert claim.missing() == []
    assert "Dana" not in repr(claim) and "1990" not in repr(claim)


def test_a_missing_field_is_reported_not_invented() -> None:
    p = pki()
    claim = edc.read(
        sealed(p, "<learningAchievement><title><text>X</text></title></learningAchievement>")
    )
    assert claim.title == "X" and sorted(claim.missing()) == ["awarding_body", "issued"]


def test_a_document_that_is_not_an_edc_or_not_safe_is_refused() -> None:
    with pytest.raises(edc.EdcError) as e:
        edc.read(b"<invoice><total>1</total></invoice>")
    assert e.value.code == "NOT_AN_EDC"
    with pytest.raises(edc.EdcError) as e2:
        edc.read(b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "y">]><d>&x;</d>')
    assert e2.value.code == "EDC_NOT_READABLE"


def test_a_good_seal_from_a_configured_anchor_is_accepted_as_a_claim_only() -> None:
    p = pki()
    check = evidence.check_signed_xml(sealed(p), tr.TrustConfig(anchors=(p.root.cert,)))
    assert check.sealed and check.signature.status.value == "VERIFIED_LIMITED"
    assert check.seal.detail["qualified_status"] == "NOT_ASSESSED"  # no authenticated trusted list


def test_with_no_anchors_nothing_is_trusted() -> None:
    check = evidence.check_signed_xml(sealed(pki()), tr.TrustConfig())
    assert not check.sealed and check.signature.status.value == "INDETERMINATE"


def test_a_credential_changed_after_sealing_is_not_accepted() -> None:
    p = pki()
    xml = sealed(p).replace(b"Chemistry", b"Medicine!")
    check = evidence.check_signed_xml(xml, tr.TrustConfig(anchors=(p.root.cert,)))
    assert not check.sealed and check.signature.status.value == "NOT_VERIFIED"


def test_an_unsealed_credential_is_not_accepted() -> None:
    check = evidence.check_signed_xml(
        b"<europeanDigitalCredential><issuer><legalName>U</legalName></issuer></europeanDigitalCredential>",
        tr.TrustConfig(),
    )
    assert not check.sealed and check.signature.reasons == ("NO_SIGNATURE_FOUND",)


# ------------------------------------------------------------------ issuer binding (the sealer is the awarding body it names)

from datetime import date

from zid_platform.modules.credential.domain import trust_graph as tg

TODAY = date(2026, 11, 12)


def _registry(sha: str | None, **issuer_extra) -> tg.Registry:
    return tg.parse(
        {
            "version": "t",
            "issuers": [
                {
                    "issuer_id": "exampleton",
                    "kind": "UNIVERSITY",
                    "authority": "OFFICIAL",
                    "countries": ["XX"],
                    "claim_types": ["ACADEMIC_AWARD"],
                    "names": [{"name": "University of Exampleton"}],
                    "seal_signers": [{"sha256": sha, "organization_identifier": "VATXX-123"}],
                    **issuer_extra,
                }
            ],
        }
    )


def _sealer(p):
    check = evidence.check_signed_xml(sealed(p), tr.TrustConfig(anchors=(p.root.cert,)))
    assert check.signer_sha256 and len(check.signer_sha256) == 64
    return check


def test_a_sealer_recorded_for_the_named_issuer_is_bound() -> None:
    p = pki()
    c = _sealer(p)
    b = tg.bind_seal(
        _registry(c.signer_sha256), "University of Exampleton", c.signer_sha256, None, on=TODAY
    )
    assert b.result is tg.Binding.BOUND and b.issuer_id == "exampleton"


def test_the_organization_identifier_can_pin_the_sealer_too() -> None:
    b = tg.bind_seal(
        _registry("0" * 64), "University of Exampleton", "f" * 64, "VATXX-123", on=TODAY
    )
    assert b.result is tg.Binding.BOUND


def test_a_known_issuer_sealed_by_some_other_certificate_is_not_bound() -> None:
    p = pki()
    c = _sealer(p)
    b = tg.bind_seal(
        _registry("a" * 64), "University of Exampleton", c.signer_sha256, None, on=TODAY
    )
    assert b.result is tg.Binding.NOT_BOUND and b.issuer_id == "exampleton"


def test_an_issuer_nobody_recorded_is_unknown_and_a_lookalike_never_binds() -> None:
    p = pki()
    c = _sealer(p)
    reg = _registry(c.signer_sha256)
    assert (
        tg.bind_seal(reg, "Some Other College", c.signer_sha256, None, on=TODAY).result
        is tg.Binding.ISSUER_UNKNOWN
    )
    look = tg.bind_seal(reg, "University of Exampletown", c.signer_sha256, None, on=TODAY)
    assert look.result is tg.Binding.AMBIGUOUS and look.candidates == ("exampleton",)
    assert (
        tg.bind_seal(reg, None, c.signer_sha256, None, on=TODAY).result is tg.Binding.ISSUER_UNKNOWN
    )


def test_a_recorded_signer_outside_its_period_or_a_revoked_issuer_does_not_bind() -> None:
    p = pki()
    c = _sealer(p)
    expired = tg.parse(
        {
            "issuers": [
                {
                    "issuer_id": "e",
                    "kind": "UNIVERSITY",
                    "names": [{"name": "University of Exampleton"}],
                    "seal_signers": [{"sha256": c.signer_sha256, "until": "2020-01-01"}],
                }
            ]
        }
    )
    assert (
        tg.bind_seal(expired, "University of Exampleton", c.signer_sha256, None, on=TODAY).result
        is tg.Binding.NOT_BOUND
    )
    revoked = _registry(c.signer_sha256, revoked_on="2025-01-01")
    assert (
        tg.bind_seal(revoked, "University of Exampleton", c.signer_sha256, None, on=TODAY).result
        is tg.Binding.ISSUER_REVOKED
    )


def test_a_seal_signer_with_nothing_to_pin_is_refused_at_load() -> None:
    import pytest

    with pytest.raises(tg.TrustGraphError):
        tg.parse(
            {
                "issuers": [
                    {
                        "issuer_id": "x",
                        "kind": "UNIVERSITY",
                        "names": [{"name": "X"}],
                        "seal_signers": [{}],
                    }
                ]
            }
        )
