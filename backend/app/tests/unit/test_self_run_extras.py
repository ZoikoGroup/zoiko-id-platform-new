"""The evaluation harness, the engine adapters and Active Authentication (CR-102): arithmetic that cannot flatter a small sample, adapters that raise
rather than invent a score, and a clone check that fails closed."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from zid_platform.modules.evidence.application import active_auth as aa
from zid_platform.modules.verification.domain import biometric_engine as be
from zid_platform.modules.verification.domain import biometric_evaluation as ev
from zid_platform.modules.verification.domain.biometric_evaluation import Trial
from zid_platform.modules.verification.infrastructure import engine_adapters as ad


# ------------------------------------------------------------------------------------------------------ the evaluation harness
def trials(
    n_each: int,
    *,
    cohort: str = "ALL",
    impostor_hits: int = 0,
    genuine_misses: int = 0,
    attack_hits: int = 0,
    bona_misses: int = 0,
):
    out = []
    out += [Trial("IMPOSTOR", 0.9 if k < impostor_hits else 0.1, cohort) for k in range(n_each)]
    out += [Trial("GENUINE", 0.2 if k < genuine_misses else 0.95, cohort) for k in range(n_each)]
    out += [
        Trial("ATTACK", 0.95 if k < attack_hits else 0.1, cohort, "PRINT") for k in range(n_each)
    ]
    out += [Trial("BONA_FIDE", 0.2 if k < bona_misses else 0.97, cohort) for k in range(n_each)]
    return out


def test_a_small_clean_sample_cannot_show_the_ceiling_but_a_large_one_can() -> None:
    small = ev.evaluate(trials(30), face_min=0.8, liveness_min=0.9)
    assert small.overall["false_accept"].point == 0.0
    assert (
        small.overall["false_accept"].upper > 0.1
    )  # zero errors in 30 trials is an upper bound near 11%, not zero
    assert not small.within_ceilings and "FALSE_ACCEPT_NOT_SHOWN_UNDER_CEILING" in small.problems
    big = ev.evaluate(trials(4000), face_min=0.8, liveness_min=0.9)
    assert big.overall["false_accept"].upper < be.MAX_FALSE_ACCEPT
    assert big.within_ceilings and big.problems == ()


def test_the_rates_are_counted_at_the_proposed_thresholds_and_attacks_use_the_worst_species() -> (
    None
):
    rows = trials(1000, impostor_hits=2, genuine_misses=30, bona_misses=50)
    rows += [Trial("ATTACK", 0.99, "ALL", "MASK")] * 40 + [Trial("ATTACK", 0.1, "ALL", "MASK")] * 60
    r = ev.evaluate(rows, face_min=0.8, liveness_min=0.9)
    assert r.overall["false_accept"].errors == 2 and r.overall["false_reject"].errors == 30
    assert r.overall["bona_fide_reject"].errors == 50
    assert r.overall["attack_accept"].point == pytest.approx(
        0.4
    )  # the MASK species, not the clean PRINT one
    assert "ATTACK_ACCEPT_NOT_SHOWN_UNDER_CEILING" in r.problems


def test_a_higher_threshold_trades_false_accepts_for_false_rejects() -> None:
    rows = [Trial("IMPOSTOR", s / 100) for s in range(100)] + [
        Trial("GENUINE", s / 100) for s in range(50, 150 - 50)
    ]
    lax = ev.evaluate(rows, face_min=0.5, liveness_min=0.9)
    strict = ev.evaluate(rows, face_min=0.9, liveness_min=0.9)
    assert strict.overall["false_accept"].errors < lax.overall["false_accept"].errors
    assert strict.overall["false_reject"].errors > lax.overall["false_reject"].errors


def test_a_cohort_with_too_few_trials_is_reported_insufficient_and_never_dropped_or_averaged_away() -> (
    None
):
    rows = trials(1000, cohort="A") + trials(10, cohort="B", impostor_hits=5)
    r = ev.evaluate(rows, face_min=0.8, liveness_min=0.9)
    by = {c.cohort: c for c in r.cohorts}
    assert (
        set(by) == {"A", "B"}
        and by["B"].sufficient["false_accept"] is False
        and by["A"].sufficient["false_accept"] is True
    )
    assert r.disparities == ()  # one usable cohort is not a comparison


def test_a_disparity_between_cohorts_is_flagged_for_a_person_to_look_at() -> None:
    rows = trials(1000, cohort="A", impostor_hits=2) + trials(1000, cohort="B", impostor_hits=10)
    r = ev.evaluate(rows, face_min=0.8, liveness_min=0.9)
    d = next(x for x in r.disparities if x.rate == "false_accept")
    assert d.best == "A" and d.worst == "B" and d.ratio == pytest.approx(5.0) and d.flagged
    assert "COHORT_DISPARITY_FALSE_ACCEPT" in r.problems
    even = ev.evaluate(
        trials(1000, cohort="A", impostor_hits=4) + trials(1000, cohort="B", impostor_hits=5),
        face_min=0.8,
        liveness_min=0.9,
    )
    assert not next(x for x in even.disparities if x.rate == "false_accept").flagged
    zero = ev.evaluate(
        trials(1000, cohort="A") + trials(1000, cohort="B", impostor_hits=3),
        face_min=0.8,
        liveness_min=0.9,
    )
    assert next(
        x for x in zero.disparities if x.rate == "false_accept"
    ).flagged  # errors in one cohort and none in the other


def test_the_result_does_not_depend_on_the_order_of_the_trials() -> None:
    rows = trials(500, cohort="A", impostor_hits=1) + trials(500, cohort="B", impostor_hits=3)
    a = ev.evaluate(rows, face_min=0.8, liveness_min=0.9)
    b = ev.evaluate(list(reversed(rows)), face_min=0.8, liveness_min=0.9)
    assert a.measured == b.measured and a.disparities == b.disparities


@pytest.mark.parametrize(
    "bad",
    [
        Trial("GENUINE", float("nan")),
        Trial("GENUINE", 1.5),
        Trial("GENUINE", True),
        Trial("OTHER", 0.5),
    ],  # type: ignore[arg-type]
)
def test_an_invalid_trial_refuses_the_whole_evaluation(bad: Trial) -> None:
    with pytest.raises(ev.EvaluationError):
        ev.evaluate([bad], face_min=0.8, liveness_min=0.9)


def test_thresholds_under_the_floor_are_refused_and_an_empty_set_never_passes() -> None:
    with pytest.raises(ev.EvaluationError):
        ev.evaluate([], face_min=0.3, liveness_min=0.9)
    r = ev.evaluate([], face_min=0.8, liveness_min=0.9)
    assert (
        not r.within_ceilings
        and "NO_TRIALS_FOR_FALSE_ACCEPT" in r.problems
        and ev.wilson_upper(0, 0) == 1.0
    )


def test_a_draft_record_carries_numbers_only_and_never_switches_the_engine_on() -> None:
    r = ev.evaluate(trials(4000), face_min=0.8, liveness_min=0.9)
    draft = r.draft_record(engine_id="deepface-arcface", engine_version="1.4.0")
    assert draft["within_ceilings"] is True and draft["thresholds"] == {
        "face_match_min": 0.8,
        "liveness_min": 0.9,
    }
    assert (
        "enabled" not in draft
        and "evaluation_id" not in draft["engine"]
        and "dpia_ref" not in draft["engine"]
    )
    assert set(draft["engine"]["measured"]) == {
        "false_accept",
        "false_reject",
        "attack_accept",
        "bona_fide_reject",
    }
    cfg = {  # the numbers it drafts are accepted by the gate once a person adds the references
        "enabled": True,
        "engine": {
            **draft["engine"], "evaluation_id": "EVAL-2026-0042", "evaluated_on": "2026-11-01", "pad_standard": "ISO/IEC 30107-3",
            "licence_checked": True, "dpia_ref": "DPIA-2026-007",
        },
        "thresholds": draft["thresholds"],
    }  # fmt: skip
    from datetime import date

    assert be.engine_gate(cfg, today=date(2026, 11, 16)) is not None


# ------------------------------------------------------------------------------------------------------------ engine adapters
def test_a_missing_library_raises_and_never_returns_a_score() -> None:
    eng = ad.DeepFaceEngine(
        "deepface-arcface", "1.4.0", loader=lambda name: (_ for _ in ()).throw(ImportError(name))
    )
    with pytest.raises(ad.EngineUnavailable, match="ENGINE_LIBRARY_UNAVAILABLE"):
        eng.match(b"a", b"b")
    with pytest.raises(ad.EngineUnavailable):
        eng.liveness(b"a")  # DeepFace offers no liveness


def test_the_real_import_path_fails_closed_when_the_library_is_not_installed() -> None:
    import importlib.util

    if importlib.util.find_spec("deepface") is None:
        with pytest.raises(ad.EngineUnavailable, match="ENGINE_LIBRARY_UNAVAILABLE"):
            ad.DeepFaceEngine("deepface-arcface", "1.4.0").match(b"a", b"b")


def test_a_liveness_predictor_is_checked_and_translated() -> None:
    ok = ad.SilentFaceEngine("silentface", "2.0", lambda media: 0.93)
    got = ok.liveness(b"frame")
    assert got.score == 0.93 and got.engine_id == "silentface" and got.engine_version == "2.0"
    for bad in (1.2, -0.1, float("nan"), True, "0.9"):
        with pytest.raises(ad.EngineUnavailable):
            ad.SilentFaceEngine("silentface", "2.0", lambda media, b=bad: b).liveness(b"f")  # type: ignore[misc]
    with pytest.raises(ad.EngineUnavailable):
        ad.SilentFaceEngine("silentface", "2.0", None).liveness(b"f")
    with pytest.raises(ad.EngineUnavailable, match="ENGINE_FAILED"):
        ad.SilentFaceEngine("silentface", "2.0", lambda media: 1 / 0).liveness(b"f")  # type: ignore[arg-type, return-value]


def test_a_composed_engine_speaks_with_one_identity_and_the_gate_only_trusts_that_identity() -> (
    None
):
    matcher = be.FakeEngine(0.9, 0.0, "deepface-arcface", "1.4.0")
    live = ad.SilentFaceEngine("silentface", "2.0", lambda media: 0.95)
    both = ad.ComposedEngine("composite", "1.0.0", matcher, live)
    face, liveness = both.match(b"p", b"r"), both.liveness(b"m")
    assert (face.engine_id, face.engine_version, liveness.engine_id, liveness.engine_version) == (
        "composite",
        "1.0.0",
        "composite",
        "1.0.0",
    )
    assert face.score == 0.9 and liveness.score == 0.95


# ------------------------------------------------------------------------------------------------------ Active Authentication
CHALLENGE = bytes(range(1, 9))


def keypair(curve=None):  # type: ignore[no-untyped-def]
    key = ec.generate_private_key(curve or ec.SECP256R1())
    dg15 = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return key, dg15


def plain(key, challenge: bytes, hash_cls=hashes.SHA256, half: int = 32) -> bytes:  # type: ignore[no-untyped-def]
    r, s = utils.decode_dss_signature(key.sign(challenge, ec.ECDSA(hash_cls())))
    return r.to_bytes(half, "big") + s.to_bytes(half, "big")


def test_a_chip_that_signs_the_challenge_with_the_key_in_dg15_holds_the_private_key() -> None:
    key, dg15 = keypair()
    v = aa.verify(dg15, CHALLENGE, plain(key, CHALLENGE), dg15_verified=True)
    assert v.status == "GENUINE_KEY_HELD" and v.clone_checked and "HOLDER_NOT_BOUND" in v.reasons
    wrapped = (
        b"\x6f" + bytes([len(dg15)]) + dg15
        if len(dg15) < 0x80
        else b"\x6f\x81" + bytes([len(dg15)]) + dg15
    )
    assert (
        aa.verify(wrapped, CHALLENGE, plain(key, CHALLENGE), dg15_verified=True).status
        == "GENUINE_KEY_HELD"
    )


def test_a_copy_of_the_data_without_the_private_key_is_a_suspected_clone() -> None:
    _, dg15 = keypair()
    other, _ = keypair()
    v = aa.verify(dg15, CHALLENGE, plain(other, CHALLENGE), dg15_verified=True)
    assert (
        v.status == "CLONE_SUSPECTED"
        and v.reasons == ("AA_SIGNATURE_INVALID",)
        and not v.clone_checked
    )
    key, dg15 = keypair()
    assert (
        aa.verify(dg15, CHALLENGE, plain(key, bytes(range(9, 17))), dg15_verified=True).status
        == "CLONE_SUSPECTED"
    )  # an old response replayed
    assert aa.verify(dg15, CHALLENGE, b"\x00" * 10, dg15_verified=True).reasons == (
        "AA_SIGNATURE_WRONG_LENGTH",
    )


def test_nothing_is_assessed_when_dg15_was_not_vouched_for_or_the_inputs_are_bad() -> None:
    key, dg15 = keypair()
    sig = plain(key, CHALLENGE)
    assert aa.verify(dg15, CHALLENGE, sig, dg15_verified=False).status == "NOT_ASSESSED"
    assert aa.verify(dg15, b"\x00" * 8, sig, dg15_verified=True).reasons == ("CHALLENGE_INVALID",)
    assert aa.verify(dg15, b"\x01" * 7, sig, dg15_verified=True).reasons == ("CHALLENGE_INVALID",)
    assert aa.verify(b"garbage", CHALLENGE, sig, dg15_verified=True).reasons == ("DG15_UNREADABLE",)
    assert aa.verify(dg15, CHALLENGE, sig, dg15_verified=True, hash_name="MD5").reasons == (
        "AA_HASH_NOT_SUPPORTED",
    )


def rsa_signer(bits: int = 1024):  # type: ignore[no-untyped-def]
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(65537, bits)
    return key, key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def rsa_response(
    key, challenge: bytes, *, name: str = "sha256", trailer: bytes = b"\x34\xcc"
) -> bytes:  # type: ignore[no-untyped-def]
    """An ISO/IEC 9796-2 scheme 1 signature the way the standard describes it, written here from the same reading the verifier follows."""
    import hashlib
    import os

    numbers = key.private_numbers()
    n, d = numbers.public_numbers.n, numbers.d
    k = (n.bit_length() + 7) // 8
    hlen = hashlib.new(name).digest_size
    m1 = os.urandom(k - 1 - hlen - len(trailer))
    digest = hashlib.new(name, m1 + challenge).digest()
    em = b"\x6a" + m1 + digest + trailer
    s = pow(int.from_bytes(em, "big"), d, n)
    return min(s, n - s).to_bytes(k, "big")


def test_an_rsa_chip_that_signs_the_challenge_holds_the_private_key_and_a_wrong_key_or_challenge_does_not() -> (
    None
):
    key, dg15 = rsa_signer()
    ok = aa.verify(dg15, CHALLENGE, rsa_response(key, CHALLENGE), dg15_verified=True)
    assert ok.status == "GENUINE_KEY_HELD" and "HOLDER_NOT_BOUND" in ok.reasons
    other, _ = rsa_signer()
    assert (
        aa.verify(dg15, CHALLENGE, rsa_response(other, CHALLENGE), dg15_verified=True).status
        == "CLONE_SUSPECTED"
    )
    replay = rsa_response(key, bytes(range(9, 17)))
    assert (
        aa.verify(dg15, CHALLENGE, replay, dg15_verified=True).status == "CLONE_SUSPECTED"
    )  # an answer to another challenge
    assert aa.verify(dg15, CHALLENGE, b"\x00" * 10, dg15_verified=True).reasons == (
        "AA_SIGNATURE_WRONG_LENGTH",
    )
    flipped = bytearray(rsa_response(key, CHALLENGE))
    flipped[5] ^= 1
    assert (
        aa.verify(dg15, CHALLENGE, bytes(flipped), dg15_verified=True).status == "CLONE_SUSPECTED"
    )
    assert (
        aa.verify(dg15, CHALLENGE, rsa_response(key, CHALLENGE), dg15_verified=False).status
        == "NOT_ASSESSED"
    )


def test_each_rsa_hash_trailer_is_read_and_an_unknown_one_is_not_accepted() -> None:
    key, dg15 = rsa_signer(1280)
    for name, trailer in (
        ("sha1", b"\xbc"),
        ("sha1", b"\x33\xcc"),
        ("sha256", b"\x34\xcc"),
        ("sha512", b"\x35\xcc"),
        ("sha384", b"\x36\xcc"),
        ("sha224", b"\x38\xcc"),
    ):
        got = aa.verify(
            dg15,
            CHALLENGE,
            rsa_response(key, CHALLENGE, name=name, trailer=trailer),
            dg15_verified=True,
        )
        assert got.status == "GENUINE_KEY_HELD", (name, trailer)
    unknown = aa.verify(
        dg15, CHALLENGE, rsa_response(key, CHALLENGE, trailer=b"\x77\xcc"), dg15_verified=True
    )
    assert unknown.status == "CLONE_SUSPECTED"


def test_a_larger_curve_uses_the_hash_and_the_signature_size_of_that_curve() -> None:
    key, dg15 = keypair(ec.SECP384R1())
    sig = plain(key, CHALLENGE, hashes.SHA384, 48)
    assert aa.verify(dg15, CHALLENGE, sig, dg15_verified=True).status == "GENUINE_KEY_HELD"
    assert (
        aa.verify(dg15, CHALLENGE, sig, dg15_verified=True, hash_name="SHA-256").status
        == "CLONE_SUSPECTED"
    )


# ------------------------------------------------------------------------------------------------------------- the chip photo
def test_the_chip_photo_is_the_first_image_in_dg2_and_nothing_is_invented() -> None:
    from zid_platform.modules.evidence.application.dg2 import MAX_IMAGE, chip_photo

    jpeg = b"\xff\xd8\xff\xe0face-bytes\xff\xd9"
    assert chip_photo(b"\x75\x82\x00\x20" + b"\x7f\x61" * 4 + jpeg) == jpeg
    jp2 = b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"j2k-bytes"
    assert chip_photo(b"\x75" * 12 + jp2) == jp2
    assert (
        chip_photo(b"\x75" * 40) is None
        and chip_photo(b"") is None
        and chip_photo(b"\xff\xd8\xff") is None
    )
    assert chip_photo(b"\x00" * (2 * MAX_IMAGE + 1)) is None
    assert chip_photo("text") is None  # type: ignore[arg-type]
