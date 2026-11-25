"""WF03 evidence into signals: the chip verdict, the capture envelope and the printed document (CR-110)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.verification.domain.idv_decision import NfcAccess, Optical, PassiveAuth
from zid_platform.modules.verification.domain.idv_evidence import (
    Envelope,
    IssuedNonce,
    capture_integrity,
    chip_signals,
    compare_printed_to_chip,
    optical_state,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
H = "a" * 64


def env(**kw: object) -> Envelope:
    base = {
        "nonce": "n1",
        "media_sha256": H,
        "captured_at": NOW - timedelta(minutes=1),
        "virtual_camera": False,
        "device_attested": True,
    }
    return Envelope(**{**base, **kw})  # type: ignore[arg-type]


ISSUED = IssuedNonce("n1", "case-1", NOW - timedelta(minutes=2))


def cap(e: Envelope, issued: IssuedNonce | None = ISSUED, **kw: object):
    return capture_integrity(e, issued, case_id="case-1", media_seen_before=False, now=NOW, **kw)  # type: ignore[arg-type]


# ---- the chip


def test_a_verified_chip_with_no_revocation_check_is_not_a_trusted_pass() -> None:
    s = chip_signals(
        "VERIFIED_LIMITED", ("SOD_SIGNED_BY_CONFIGURED_CSCA_CHAIN", "REVOCATION_NOT_CHECKED")
    )
    assert (
        s.nfc is NfcAccess.READ_OK
        and s.passive_auth is PassiveAuth.REVOCATION_UNKNOWN
        and s.mrz_check_digits_ok is True
    )
    s = chip_signals("VERIFIED_LIMITED", ("REVOCATION_CHECKED_GOOD",))
    assert s.passive_auth is PassiveAuth.TRUSTED_PASS and s.document_expired is False


def test_chip_failures_and_missing_trust_are_separate_states() -> None:
    assert (
        chip_signals("INDETERMINATE", ("NO_CSCA_CONFIGURED",)).passive_auth
        is PassiveAuth.CERT_MISSING
    )
    assert (
        chip_signals("INDETERMINATE", ("SIGNER_NOT_ISSUED_BY_A_CONFIGURED_CSCA",)).passive_auth
        is PassiveAuth.CERT_UNTRUSTED
    )
    assert (
        chip_signals("NOT_VERIFIED", ("DATA_GROUP_HASH_MISMATCH",)).passive_auth
        is PassiveAuth.SIGNATURE_FAIL
    )
    assert (
        chip_signals("NOT_VERIFIED", ("DOCUMENT_SIGNER_CERTIFICATE_REVOKED",)).passive_auth
        is PassiveAuth.SIGNATURE_FAIL
    )
    assert chip_signals("INDETERMINATE", ("SOD_UNREADABLE",)).passive_auth is PassiveAuth.NOT_TESTED


def test_dates_and_check_digits_come_through() -> None:
    s = chip_signals(
        "VERIFIED_LIMITED",
        ("MRZ_CHECK_DIGITS_FAILED", "DOCUMENT_EXPIRED", "REVOCATION_CHECKED_GOOD"),
    )
    assert s.mrz_check_digits_ok is False and s.document_expired is True
    s = chip_signals("VERIFIED_LIMITED", ("DG1_NOT_SUPPLIED", "REVOCATION_CHECKED_GOOD"))
    assert s.mrz_check_digits_ok is None and s.document_expired is None


# ---- the capture


def test_a_fresh_one_time_capture_from_a_real_camera_is_established_clean() -> None:
    v = cap(env())
    assert v.injection_risk is False and v.reasons == ()


def test_every_replay_or_injection_finding_is_a_risk() -> None:
    assert cap(env(nonce="other")).reasons == ("CAPTURE_NONCE_UNKNOWN",)
    assert cap(env(), None).injection_risk is True
    assert (
        "CAPTURE_NONCE_ALREADY_USED"
        in cap(env(), IssuedNonce("n1", "case-1", NOW - timedelta(minutes=2), used=True)).reasons
    )
    assert (
        "CAPTURE_NONCE_FOR_ANOTHER_CASE"
        in cap(env(), IssuedNonce("n1", "case-2", NOW - timedelta(minutes=2))).reasons
    )
    assert (
        "CAPTURE_NOT_FRESH"
        in cap(env(), IssuedNonce("n1", "case-1", NOW - timedelta(hours=1))).reasons
    )
    assert "CAPTURE_NOT_FRESH" in cap(env(captured_at=NOW - timedelta(minutes=30))).reasons
    assert "CAPTURE_TIME_IN_THE_FUTURE" in cap(env(captured_at=NOW + timedelta(hours=1))).reasons
    assert "VIRTUAL_CAMERA_DETECTED" in cap(env(virtual_camera=True)).reasons
    assert "DEVICE_ATTESTATION_FAILED" in cap(env(device_attested=False)).reasons
    assert "CAPTURE_MEDIA_DIGEST_INVALID" in cap(env(media_sha256="xyz")).reasons
    replay = capture_integrity(env(), ISSUED, case_id="case-1", media_seen_before=True, now=NOW)
    assert replay.injection_risk is True and "CAPTURE_MEDIA_REPLAYED" in replay.reasons


def test_what_could_not_be_assessed_is_not_established_and_never_clean() -> None:
    assert cap(env(virtual_camera=None)).injection_risk is None
    v = cap(env(device_attested=None))  # integrity unavailable is never "safe" (WF-01 Q18)
    assert v.injection_risk is None and v.reasons == ("DEVICE_ATTESTATION_NOT_AVAILABLE",)


# ---- the printed document


def test_optical_checks_are_all_required_ones_passing() -> None:
    req = frozenset({"TEMPLATE", "FONT", "TAMPER"})
    assert optical_state({"TEMPLATE": "PASS", "FONT": "PASS", "TAMPER": "PASS"}, req) == (
        Optical.PASS,
        (),
    )
    assert optical_state({"TEMPLATE": "PASS"}, req)[0] is Optical.UNKNOWN
    assert (
        optical_state({"TEMPLATE": "PASS", "FONT": "FAIL", "TAMPER": "PASS"}, req)[0]
        is Optical.REVIEW
    )
    assert optical_state({"TEMPLATE": "FAIL", "FONT": "PASS", "TAMPER": "PASS"}, req) == (
        Optical.FAIL,
        ("OPTICAL_TEMPLATE_FAILED",),
    )
    assert (
        optical_state({"TEMPLATE": "PASS", "FONT": "INCONCLUSIVE", "TAMPER": "PASS"}, req)[0]
        is Optical.REVIEW
    )
    assert optical_state({"TEMPLATE": "maybe"}, req)[0] is Optical.UNKNOWN


def test_printed_data_is_compared_to_the_chip_after_normalisation() -> None:
    chip = {
        "surname": "MUELLER",
        "given_names": "ANNA<MARIA",
        "document_number": "X123",
        "birth_date": "1990-01-02",
    }
    same = {
        "surname": "Mueller",
        "given_names": "Anna Maria",
        "document_number": "x123",
        "birth_date": "1990-01-02",
    }
    assert compare_printed_to_chip(same, chip) == ("MATCH", ())
    other = {**same, "document_number": "X124"}
    assert compare_printed_to_chip(other, chip) == ("MISMATCH", ("document_number",))
    assert compare_printed_to_chip({"surname": "MUELLER"}, chip) == ("UNKNOWN", ())
    assert compare_printed_to_chip({}, chip) == ("UNKNOWN", ())
