"""Passive authentication of a passport chip (ICAO Doc 9303 parts 10 to 12): what a signed EF.SOD proves, and what it never does.

Every case starts from a genuine chip (a real CMS signature over real data-group hashes) and changes exactly one property.
"""

from __future__ import annotations

import pytest
from epassport_fixture import OID_DATA, dg1, make_chip, mrz_td3
from pdf_signing import NOW
from zid_platform.modules.evidence.application import epassport as ep


def check(chip, **over):
    args = {"csca": (chip.csca.cert,), "data_groups": chip.data_groups}
    args.update(over)
    return ep.verify(chip.sod, args.pop("data_groups"), **args)


# ------------------------------------------------------------------------------------------------------------------ the good chip


def test_a_genuine_chip_under_a_configured_csca_is_verified_limited_and_never_plain_verified() -> (
    None
):
    v = check(make_chip())
    assert v.status == "VERIFIED_LIMITED" and v.document_authentic
    assert {"HOLDER_NOT_BOUND", "CLONE_NOT_DETECTED", "SOD_SIGNED_BY_CONFIGURED_CSCA_CHAIN"} <= set(
        v.reasons
    )
    assert v.public()["holder_bound"] is False and v.public()["limitations"]
    assert v.detail["data_groups_verified"] == [1, 2, 14]
    assert v.detail["hash_algorithm"] == "SHA-256"


def test_the_document_data_is_read_only_from_the_verified_mrz_group() -> None:
    doc = check(make_chip()).detail["document"]
    assert doc["surname"] == "ERIKSSON" and doc["given_names"] == "ANNA MARIA"
    assert doc["issuing_state"] == "UTO" and doc["birth_date"] == "1974-08-12"
    assert doc["mrz_check_digits_hold"] is True


def test_the_photo_is_not_returned_or_read() -> None:
    v = check(make_chip())
    assert "face" not in repr(v.public()).lower().replace("face image", "")
    assert b"pretend face image" not in repr(v.public()).encode()


@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512"])
def test_each_allowed_hash_algorithm_works(algorithm) -> None:
    assert check(make_chip(algorithm=algorithm)).status == "VERIFIED_LIMITED"


def test_a_bare_content_info_without_the_application_wrapper_is_accepted() -> None:
    assert check(make_chip(wrap=False)).status == "VERIFIED_LIMITED"


# --------------------------------------------------------------------------------------------------------------- trust is explicit


def test_with_no_csca_configured_nothing_is_trusted() -> None:
    v = check(make_chip(), csca=())
    assert v.status == "INDETERMINATE" and v.reasons == ("NO_CSCA_CONFIGURED",)


def test_a_signer_from_another_countrys_csca_is_not_trusted() -> None:
    chip, other = make_chip(), make_chip()
    v = check(chip, csca=(other.csca.cert,))
    assert v.status == "INDETERMINATE" and v.reasons == ("SIGNER_NOT_ISSUED_BY_A_CONFIGURED_CSCA",)


# ------------------------------------------------------------------------------------------------------------------- tampering


def test_a_changed_data_group_is_not_verified() -> None:
    chip = make_chip()
    tampered = {**chip.data_groups, 2: chip.data_groups[2] + b"x"}
    v = check(chip, data_groups=tampered)
    assert (
        v.status == "NOT_VERIFIED"
        and v.reasons == ("DATA_GROUP_HASH_MISMATCH",)
        and v.detail["data_group"] == 2
    )


def test_a_changed_mrz_is_not_verified_even_if_its_check_digits_are_right() -> None:
    chip = make_chip()
    forged = {**chip.data_groups, 1: dg1(mrz_td3(surname="IMPOSTER"))}
    assert check(chip, data_groups=forged).reasons == ("DATA_GROUP_HASH_MISMATCH",)


def test_a_data_group_the_sod_does_not_cover_is_refused() -> None:
    chip = make_chip()
    v = check(chip, data_groups={**chip.data_groups, 3: b"fingerprints that were never signed"})
    assert v.status == "NOT_VERIFIED" and v.reasons == ("DATA_GROUP_NOT_COVERED_BY_SOD",)


def test_a_signature_made_by_a_key_other_than_the_certificates_is_not_verified() -> None:
    v = check(make_chip(sign_with_other_key=True))
    assert v.status == "NOT_VERIFIED"


def test_a_flipped_byte_in_the_signed_object_is_not_verified() -> None:
    chip = make_chip()
    sod = bytearray(chip.sod)
    at = bytes(sod).index(
        bytes.fromhex("0201000"[:6]) + b"\x30"
    )  # inside the signed LDS object, near its start
    sod[at + 12] ^= 0x01
    assert ep.verify(bytes(sod), chip.data_groups, csca=(chip.csca.cert,)).status in {
        "NOT_VERIFIED",
        "INDETERMINATE",
    }
    assert (
        ep.verify(bytes(sod), chip.data_groups, csca=(chip.csca.cert,)).status != "VERIFIED_LIMITED"
    )


def test_a_sod_whose_content_is_not_an_lds_security_object_is_refused() -> None:
    v = check(make_chip(content_type=OID_DATA))
    assert v.status == "INDETERMINATE" and v.reasons == (
        "SOD_CONTENT_TYPE_NOT_LDS_SECURITY_OBJECT",
    )


def test_a_sha1_security_object_is_refused_as_weak() -> None:
    v = check(make_chip(algorithm="sha1"))
    assert v.status == "NOT_VERIFIED" and v.reasons == ("HASH_ALGORITHM_WEAK",)


@pytest.mark.parametrize(
    "junk",
    [b"", b"\x00" * 10, b"\x77\x03abc", b"\x30\x82\xff\xff", b"x" * 70_000],
    ids=["empty", "zeros", "short-wrapper", "bad-length", "oversized"],
)
def test_garbage_is_never_a_pass_and_never_crashes(junk) -> None:
    v = ep.verify(junk, {1: b"x"}, csca=())
    assert v.status in {"INDETERMINATE", "NOT_VERIFIED"} and not v.document_authentic


def test_no_data_groups_supplied_is_indeterminate() -> None:
    chip = make_chip()
    assert ep.verify(chip.sod, {}, csca=(chip.csca.cert,)).reasons == ("NO_DATA_GROUPS_SUPPLIED",)


# ---------------------------------------------------------------------------------------------------- signer certificate lifetimes


def test_a_document_signer_that_has_expired_is_judged_in_its_own_window_and_says_so() -> None:
    chip = make_chip(
        ds_not_before_days=-2000, ds_not_after_days=-1800
    )  # long expired; the passport it signed is still in date
    v = check(chip)
    assert v.status == "VERIFIED_LIMITED"
    assert "SIGNER_CERTIFICATE_EXPIRED_JUDGED_IN_ITS_OWN_VALIDITY_WINDOW" in v.reasons


def test_a_signer_certificate_that_is_not_yet_valid_is_not_rescued() -> None:
    v = check(make_chip(ds_not_before_days=100, ds_not_after_days=300), at=NOW)
    assert v.status in {"INDETERMINATE", "NOT_VERIFIED"} and not v.document_authentic


# ------------------------------------------------------------------------------------------------------------ what the chip says


def test_an_expired_document_is_reported_beside_an_authentic_chip() -> None:
    chip = make_chip(mrz_text=mrz_td3(expiry="150415"))
    v = check(chip)
    assert v.status == "VERIFIED_LIMITED" and "DOCUMENT_EXPIRED" in v.reasons


def test_a_failing_mrz_check_digit_is_reported_beside_an_authentic_chip() -> None:
    bad = mrz_td3()[:-1] + "0" if mrz_td3()[-1] != "0" else mrz_td3()[:-1] + "1"
    v = check(make_chip(mrz_text=bad))
    assert v.status == "VERIFIED_LIMITED" and "MRZ_CHECK_DIGITS_FAILED" in v.reasons


def test_a_chip_without_dg1_is_authentic_but_says_nothing_about_the_holder() -> None:
    chip = make_chip(groups={2: b"photo", 14: b"info"})
    v = check(chip)
    assert (
        v.status == "VERIFIED_LIMITED"
        and "DG1_NOT_SUPPLIED" in v.reasons
        and "document" not in v.detail
    )


def test_an_unreadable_dg1_is_reported_without_failing_the_signature_check() -> None:
    chip = make_chip(groups={1: b"\x61\x03abc", 2: b"photo"})
    v = check(chip)
    assert v.status == "VERIFIED_LIMITED" and any(r.startswith("DG1_") for r in v.reasons)


def test_revocation_is_reported_as_not_checked_when_no_crl_was_supplied() -> None:
    assert "REVOCATION_NOT_CHECKED" in check(make_chip()).reasons


def test_the_trust_folder_for_csca_certificates_is_read_and_ships_empty(tmp_path) -> None:
    from cryptography.hazmat.primitives.serialization import Encoding
    from zid_platform.modules.evidence.application import trust as tr

    assert tr.load_trust(tmp_path).csca == ()
    chip = make_chip()
    folder = tmp_path / "csca"
    folder.mkdir()
    (folder / "utopia.pem").write_bytes(chip.csca.cert.public_bytes(Encoding.PEM))
    loaded = tr.load_trust(tmp_path)
    assert loaded.csca == (chip.csca.cert,) and loaded.anchors == ()
    # configured through the folder, the chip verifies; the folder is the only place a country's CA enters
    assert ep.verify(chip.sod, chip.data_groups, csca=loaded.csca).status == "VERIFIED_LIMITED"
    shipped = tr.load_trust(
        __import__("pathlib").Path(__file__).resolve().parents[3] / "config" / "trust"
    )
    assert shipped.csca == (), "the shipped configuration trusts no country"
