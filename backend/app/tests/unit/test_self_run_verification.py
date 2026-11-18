"""Self-run identity verification (CR-095): the biometric engine gate, the SELF_RUN adapter, and the CSCA master-list importer.

Every case starts from something that works and changes one property. Nothing here proves a regulator would accept the path: it proves the
engineering rules fail closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import Encoding
from epassport_fixture import integer, make_chip, sod_cms
from pdf_signing import NOW, der, ec_key, make_cert
from zid_platform.modules.evidence.application import csca_master_list as ml
from zid_platform.modules.evidence.application import epassport as ep
from zid_platform.modules.evidence.infrastructure import csca_import
from zid_platform.modules.verification.application import self_run as app
from zid_platform.modules.verification.domain import biometric_engine as be
from zid_platform.modules.verification.domain import self_run as sr
from zid_platform.modules.verification.domain.providers import (
    ProviderKind,
    validate_provider_config,
)
from zid_platform.modules.verification.domain.verification import (
    Profile,
    SessionStatus,
    VerificationError,
    decide,
)
from zid_platform.modules.verification.infrastructure import self_run_config

TODAY = date(2026, 11, 14)
PROFILE = Profile.from_json(
    "p", "t", "self-run", 1, "IAL-Z2",
    {
        "required_claims": ["legal_name", "birthdate"],
        "accepted_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"],
        "binding_required": True,
        "liveness_required": True,
        "biometric_jurisdictions": ["GB"],
        "jurisdictions": ["GB"],
    },
    "ACTIVE",
)  # fmt: skip


def accepted(**over):
    cfg = {
        "enabled": True,
        "mode": "APPROVED_FOR_SCOPE",
        "scope": {"countries": ["GB"]},
        "engine": {
            "id": "deepface-arcface",
            "components": ["deepface", "silent-face-anti-spoofing"],
            "version": "1.4.0",
            "evaluation_id": "EVAL-2026-0042",
            "evaluated_on": "2026-09-01",
            "pad_standard": "ISO/IEC 30107-3",
            "licence_checked": True,
            "dpia_ref": "DPIA-2026-007",
            "measured": {
                "false_accept": 0.0005,
                "false_reject": 0.03,
                "attack_accept": 0.02,
                "bona_fide_reject": 0.05,
            },
        },
        "thresholds": {"face_match_min": 0.8, "liveness_min": 0.9},
    }
    for k, v in over.items():
        if k in ("measured",):
            cfg["engine"]["measured"] = {**cfg["engine"]["measured"], **v}
        elif k in cfg["engine"]:
            cfg["engine"][k] = v
        else:
            cfg[k] = v
    return cfg


def gate(**over) -> be.EngineGate:
    g = be.engine_gate(accepted(**over), today=TODAY)
    assert g is not None
    return g


def chip_evidence(chip=None, **over) -> sr.ChipEvidence:
    chip = chip or make_chip()
    v = ep.verify(chip.sod, chip.data_groups, csca=(chip.csca.cert,))
    assert v.status == "VERIFIED_LIMITED", v
    e = sr.ChipEvidence.from_verdict(v.status, v.reasons, v.detail)
    return replace(e, **over) if over else e


def engine(face=0.95, live=0.97, **kw):
    return (
        be.FakeEngine(face, live, "deepface-arcface", "1.4.0", **kw)
        if kw
        else be.FakeEngine(face, live, "deepface-arcface", "1.4.0")
    )


def run(chip=None, *, g=None, face=0.95, live=0.97, reference=sr.CHIP_PHOTO, **kw):
    g = g or gate()
    e = engine(face, live)
    return sr.self_run_result(
        chip or chip_evidence(),
        session_ref="s1",
        gate=g,
        face=e.match(b"p", b"r"),
        face_reference=reference,
        liveness=e.liveness(b"m"),
        **kw,
    )


# ------------------------------------------------------------------------------------------------------------ the gate


def test_the_shipped_configuration_keeps_the_engine_off():
    assert self_run_config.raw()["enabled"] is False
    assert self_run_config.accepted_engine(TODAY) is None


def test_a_complete_evaluation_opens_the_gate_and_records_what_was_measured():
    g = gate()
    a = g.audit()
    assert a["evaluation_id"] == "EVAL-2026-0042" and a["dpia_ref"] == "DPIA-2026-007"
    assert a["measured"]["attack_accept"] == 0.02 and a["face_threshold"] == 0.8


@pytest.mark.parametrize(
    "over,code",
    [
        ({"evaluation_id": ""}, "EVALUATION_ID_MISSING"),
        ({"dpia_ref": "x"}, "DPIA_REFERENCE_MISSING"),
        ({"licence_checked": False}, "LICENCE_CHECK_NOT_RECORDED"),
        ({"pad_standard": ""}, "PAD_EVALUATION_STANDARD_NOT_RECORDED"),
        ({"id": ""}, "ENGINE_ID_MISSING"),
        ({"version": " "}, "ENGINE_VERSION_MISSING"),
        ({"evaluated_on": "2024-01-01"}, "EVALUATION_TOO_OLD"),
        ({"evaluated_on": "2027-01-01"}, "EVALUATION_DATE_IN_THE_FUTURE"),
        ({"evaluated_on": "not a date"}, "EVALUATION_DATE_INVALID"),
        ({"measured": {"false_accept": 0.01}}, "MEASURED_FALSE_ACCEPT_ABOVE_CEILING"),
        ({"measured": {"attack_accept": 0.2}}, "MEASURED_ATTACK_ACCEPT_ABOVE_CEILING"),
        ({"measured": {"false_reject": None}}, "MEASURED_FALSE_REJECT_MISSING"),
        ({"measured": {"bona_fide_reject": True}}, "MEASURED_BONA_FIDE_REJECT_MISSING"),
        ({"measured": {"false_accept": float("nan")}}, "MEASURED_FALSE_ACCEPT_MISSING"),
        ({"thresholds": {"face_match_min": 0.3, "liveness_min": 0.9}}, "FACE_THRESHOLD_INVALID"),
        (
            {"thresholds": {"face_match_min": 0.8, "liveness_min": None}},
            "LIVENESS_THRESHOLD_INVALID",
        ),
    ],
)
def test_an_engine_switched_on_without_its_evidence_is_refused_and_says_why(over, code):
    cfg = accepted(**over)
    assert code in be.engine_gate_problems(cfg, today=TODAY)
    with pytest.raises(be.EngineGateError) as e:
        be.engine_gate(cfg, today=TODAY)
    assert code in e.value.problems


def test_off_is_the_default_and_is_not_an_error():
    for cfg in ({}, {"enabled": False}, {"enabled": "yes"}, accepted(enabled=False)):
        assert be.engine_gate(cfg, today=TODAY) is None


# ------------------------------------------------------------------------------------------------------- the adapter


def test_a_chip_only_check_is_limited_and_binds_nobody():
    out = sr.self_run_result(chip_evidence(), session_ref="s1")
    r = out.result
    assert r.status == "COMPLETED" and r.binding is None and r.liveness_passed is None
    assert {c.claim for c in r.claims} == {"legal_name", "birthdate"}
    assert all(c.method == "chip" and c.evidence == ("GOVERNMENT_ID",) for c in r.claims)
    assert (
        "CHIP_PASSIVE_AUTH_ONLY" in r.reason_codes and "SELF_RUN_BIOMETRICS_OFF" in r.reason_codes
    )
    d = decide(PROFILE, r, jurisdiction="GB")
    assert (
        d.status is SessionStatus.FAILED and "BINDING_FAILED" in d.reasons
    )  # never VERIFIED on a chip alone


def test_a_chip_that_proves_nothing_gives_no_claim():
    for status, expected in (
        ("NOT_VERIFIED", "FAILED"),
        ("INDETERMINATE", "PENDING"),
        ("SOMETHING_ELSE", "PENDING"),
    ):
        r = sr.self_run_result(
            sr.ChipEvidence(status, ("NO_CSCA_CONFIGURED",), "A B", "1990-01-01", True),
            session_ref="s",
        ).result
        assert r.status == expected and r.claims == () and r.binding is None
    r = run(
        sr.ChipEvidence("NOT_VERIFIED", ("DATA_GROUP_HASH_MISMATCH",), "A B", "1990-01-01", True)
    ).result
    assert r.status == "FAILED" and r.binding is None  # a good face does not rescue a bad chip


def test_a_face_and_liveness_from_the_accepted_engine_against_the_chip_photo_bind_the_person():
    out = run()
    r = out.result
    assert (
        r.status == "COMPLETED"
        and r.binding == "BIOMETRIC"
        and r.liveness_passed is True
        and r.fraud_signals == ()
    )
    assert any(c.method == "chip+biometric" for c in r.claims)
    d = decide(PROFILE, r, jurisdiction="GB")
    assert d.status is SessionStatus.VERIFIED and d.outcome == "VERIFIED"
    assert out.audit["engine"]["evaluation_id"] == "EVAL-2026-0042"
    assert out.audit["face_score"] == 0.95 and out.audit["commitment"] == r.evidence_commitment


def test_the_decision_still_refuses_a_biometric_binding_where_the_profile_does_not_allow_it():
    d = decide(PROFILE, run().result, jurisdiction="DE")
    assert (
        d.outcome in ("BIOMETRIC_NOT_PERMITTED", "ASSURANCE_NOT_MET")
        and d.status is SessionStatus.FAILED
    )


@pytest.mark.parametrize("reference", ["DOCUMENT_IMAGE", None, "", "NAME_ONLY"])
def test_a_photo_that_did_not_come_from_the_verified_chip_never_binds(reference):
    r = run(reference=reference).result
    assert r.binding is None and "SELF_RUN_PHOTO_NOT_CHIP_VERIFIED" in r.reason_codes
    assert decide(PROFILE, r, jurisdiction="GB").status is SessionStatus.FAILED


def test_a_chip_photo_whose_hash_was_not_verified_never_binds():
    r = run(chip_evidence(photo_verified=False)).result
    assert r.binding is None and "SELF_RUN_PHOTO_NOT_CHIP_VERIFIED" in r.reason_codes


def test_a_face_under_the_threshold_fails_and_is_never_favourable():
    r = run(face=0.79).result
    assert (
        r.status == "FAILED"
        and r.claims == ()
        and r.binding is None
        and "SELF_RUN_FACE_NO_MATCH" in r.reason_codes
    )
    assert run(face=0.8).result.binding == "BIOMETRIC"  # the threshold itself passes


def test_a_liveness_under_its_threshold_raises_a_presentation_attack_signal():
    r = run(live=0.5).result
    assert (
        r.binding == "BIOMETRIC"
        and r.liveness_passed is False
        and "SELF_RUN_PAD_SUSPECTED" in r.fraud_signals
    )
    d = decide(PROFILE, r, jurisdiction="GB")
    assert d.status is SessionStatus.FAILED and d.outcome == "FRAUD_SUSPECTED"


def test_missing_biometric_evidence_leaves_the_person_unbound():
    e = engine()
    for kw in ({"face": e.match(b"", b"")}, {"liveness": e.liveness(b"")}, {}):
        r = sr.self_run_result(
            chip_evidence(), session_ref="s", gate=gate(), face_reference=sr.CHIP_PHOTO, **kw
        ).result
        assert r.binding is None and "SELF_RUN_BIOMETRIC_EVIDENCE_MISSING" in r.reason_codes


def test_a_result_from_another_engine_or_version_is_not_trusted():
    for other in (
        be.FakeEngine(0.99, 0.99, "other-engine", "1.4.0"),
        be.FakeEngine(0.99, 0.99, "deepface-arcface", "9.9"),
    ):
        r = sr.self_run_result(
            chip_evidence(),
            session_ref="s",
            gate=gate(),
            face=other.match(b"", b""),
            face_reference=sr.CHIP_PHOTO,
            liveness=other.liveness(b""),
        ).result
        assert r.binding is None and "SELF_RUN_ENGINE_NOT_THE_ACCEPTED_ONE" in r.reason_codes


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1, True, "0.9", None])
def test_a_score_that_is_not_a_number_in_range_is_pending(bad):
    g = gate()
    face = be.FaceMatch(bad, g.engine_id, g.engine_version)  # type: ignore[arg-type]
    live = be.Liveness(0.99, g.engine_id, g.engine_version)
    r = sr.self_run_result(
        chip_evidence(),
        session_ref="s",
        gate=g,
        face=face,
        face_reference=sr.CHIP_PHOTO,
        liveness=live,
    ).result
    assert r.status == "PENDING" and r.binding is None and r.claims == ()


def test_risk_signals_reach_the_engine_as_fraud_signals():
    r = run(extra_fraud=("document check: altered", "")).result
    assert r.fraud_signals == ("SELF_RUN_RISK_DOCUMENT_CHECK__ALTERED",)
    assert decide(PROFILE, r, jurisdiction="GB").outcome == "FRAUD_SUSPECTED"


def test_a_profile_that_needs_a_fraud_engine_verdict_never_passes_on_this_path():
    needy = Profile.from_json(
        "p", "t", "n", 1, "IAL-Z2",
        {"required_claims": ["legal_name"], "accepted_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"], "binding_required": True,
         "fraud_check_required": True, "biometric_jurisdictions": ["GB"], "jurisdictions": ["GB"]},
        "ACTIVE",
    )  # fmt: skip
    r = run().result
    assert r.fraud_check is None
    assert decide(needy, r, jurisdiction="GB").outcome == "FRAUD_CHECK_UNAVAILABLE"


def test_a_name_comes_only_from_a_verified_data_group_that_holds_together_and_is_in_date():
    chip = make_chip()
    v = ep.verify(chip.sod, chip.data_groups, csca=(chip.csca.cert,))
    detail = dict(v.detail)
    assert sr.ChipEvidence.from_verdict(v.status, v.reasons, detail).legal_name
    no_dg1 = {**detail, "data_groups_verified": [2]}
    assert sr.ChipEvidence.from_verdict(v.status, v.reasons, no_dg1).legal_name is None
    broken = {**detail, "document": {**detail["document"], "mrz_check_digits_hold": False}}
    assert sr.ChipEvidence.from_verdict(v.status, v.reasons, broken).legal_name is None
    assert (
        sr.ChipEvidence.from_verdict(v.status, (*v.reasons, "DOCUMENT_EXPIRED"), detail).legal_name
        is None
    )
    assert (
        sr.ChipEvidence.from_verdict("INDETERMINATE", ("NO_CSCA_CONFIGURED",), detail).legal_name
        is None
    )
    assert sr.ChipEvidence.from_verdict(v.status, v.reasons, detail).photo_verified is True
    assert (
        sr.ChipEvidence.from_verdict(
            v.status, v.reasons, {**detail, "data_groups_verified": [1]}
        ).photo_verified
        is False
    )


def test_the_audit_keeps_scores_and_versions_and_no_media_or_name():
    out = run()
    blob = repr(out.audit).upper()
    chip = chip_evidence()
    assert chip.legal_name and chip.legal_name.upper() not in blob  # no name, no birth date
    assert chip.birth_date and chip.birth_date not in blob
    assert "PRETEND FACE IMAGE" not in blob
    assert set(out.audit) >= {
        "chip_status",
        "engine",
        "face_score",
        "liveness_score",
        "commitment",
        "codes",
    }


# ------------------------------------------------------------------------------------------------- the application layer


def test_the_application_layer_scores_with_the_injected_engine_and_calls_it_only_for_a_verified_chip():
    calls = []

    class Spy:
        engine_id, engine_version = "deepface-arcface", "1.4.0"

        def match(self, probe, reference):
            calls.append("match")
            return be.FaceMatch(0.95, self.engine_id, self.engine_version)

        def liveness(self, media):
            calls.append("live")
            return be.Liveness(0.97, self.engine_id, self.engine_version)

    args = {"probe": b"p", "chip_photo": b"r", "media": b"m", "gate": gate(), "session_ref": "s"}
    ok = app.assess(chip_evidence(), engine=Spy(), **args)
    assert ok.result.binding == "BIOMETRIC" and calls == ["match", "live"]
    calls.clear()
    bad = app.assess(
        sr.ChipEvidence("INDETERMINATE", ("NO_CSCA_CONFIGURED",)), engine=Spy(), **args
    )
    assert bad.result.status == "PENDING" and calls == []


def test_an_engine_that_raises_is_pending_never_a_pass():
    class Broken:
        engine_id, engine_version = "deepface-arcface", "1.4.0"

        def match(self, probe, reference):
            raise RuntimeError("model file missing")

        def liveness(self, media):
            raise RuntimeError("never reached")

    out = app.assess(
        chip_evidence(),
        engine=Broken(),
        probe=b"p",
        chip_photo=b"r",
        media=b"m",
        gate=gate(),
        session_ref="s",
    )
    assert (
        out.result.status == "PENDING"
        and out.result.claims == ()
        and "SELF_RUN_ENGINE_UNAVAILABLE" in out.result.reason_codes
    )
    assert "model file" not in repr(out.audit)


def test_with_the_engine_off_the_application_layer_is_chip_only():
    out = app.assess(
        chip_evidence(),
        engine=engine(),
        probe=b"p",
        chip_photo=b"r",
        media=b"m",
        session_ref="s",
        today=TODAY,
    )
    assert out.result.binding is None and "SELF_RUN_BIOMETRICS_OFF" in out.result.reason_codes


def test_self_run_is_not_an_adapter_and_takes_no_callback():
    assert ProviderKind.SELF_RUN.value == "SELF_RUN"
    with pytest.raises(VerificationError):
        validate_provider_config(ProviderKind.SELF_RUN, {}, ["GB"])


# --------------------------------------------------------------------------------------------- the master-list importer

OID_ML = ml.OID_MASTER_LIST


def master_list(*, signer_issuer=None, extra=(), content_type=OID_ML, corrupt=False):
    anchor = make_cert(
        "Utopia CSCA",
        ec_key(),
        ca=True,
        not_before=NOW - timedelta(days=3000),
        not_after=NOW + timedelta(days=3000),
    )
    signer = make_cert(
        "Utopia ML Signer",
        ec_key(),
        signer_issuer or anchor,
        not_before=NOW - timedelta(days=30),
        not_after=NOW + timedelta(days=300),
    )
    cscas = [
        make_cert(
            f"Country {i} CSCA",
            ec_key(),
            ca=True,
            not_before=NOW - timedelta(days=900),
            not_after=NOW + timedelta(days=900),
        )
        for i in range(3)
    ]
    members = [c.cert for c in cscas] + list(extra)
    econtent = der(
        0x30, integer(0) + der(0x31, b"".join(c.public_bytes(Encoding.DER) for c in members))
    )
    blob = sod_cms(econtent, signer, content_type=content_type, wrap=False)
    if corrupt:
        i = blob.index(econtent) + 12
        blob = blob[:i] + bytes([blob[i] ^ 1]) + blob[i + 1 :]
    return blob, anchor, cscas


def test_a_master_list_signed_under_the_supplied_anchor_yields_its_ca_certificates():
    blob, anchor, cscas = master_list()
    r = ml.read_master_list(blob, anchors=[anchor.cert])
    assert r.ok and len(r.certificates) == 3
    assert {ml.fingerprint(c) for c in r.certificates} == {ml.fingerprint(c.cert) for c in cscas}


def test_no_anchor_means_nothing_is_read():
    blob, _, _ = master_list()
    assert ml.read_master_list(blob, anchors=[]).status == "INDETERMINATE"


def test_a_list_signed_by_a_signer_the_anchor_never_issued_is_not_trusted():
    blob, _, _ = master_list()
    stranger = make_cert("Other CSCA", ec_key(), ca=True)
    r = ml.read_master_list(blob, anchors=[stranger.cert])
    assert r.status == "INDETERMINATE" and r.certificates == ()


def test_a_list_whose_content_was_changed_after_signing_is_not_verified():
    blob, anchor, _ = master_list(corrupt=True)
    r = ml.read_master_list(blob, anchors=[anchor.cert])
    assert r.status == "NOT_VERIFIED" and r.certificates == ()


def test_a_cms_that_is_not_a_master_list_or_is_unreadable_is_refused():
    blob, anchor, _ = master_list(content_type="2.23.136.1.1.1")
    assert ml.read_master_list(blob, anchors=[anchor.cert]).status == "INDETERMINATE"
    assert (
        ml.read_master_list(b"\x30\x03\x02\x01\x00", anchors=[anchor.cert]).status
        == "INDETERMINATE"
    )
    assert ml.read_master_list(b"", anchors=[anchor.cert]).status == "INDETERMINATE"
    assert (
        ml.read_master_list(b"x" * (ml.MAX_BYTES + 1), anchors=[anchor.cert]).status
        == "INDETERMINATE"
    )


def test_only_ca_certificates_are_taken_from_a_list():
    leaf = make_cert("Not a CA", ec_key(), ca=False)
    blob, anchor, _ = master_list(extra=(leaf.cert,))
    r = ml.read_master_list(blob, anchors=[anchor.cert])
    assert (
        r.ok
        and len(r.certificates) == 3
        and ml.fingerprint(leaf.cert) not in {ml.fingerprint(c) for c in r.certificates}
    )


def test_the_importer_writes_a_verified_list_once_and_never_overwrites_or_deletes(
    tmp_path: Path, capsys
):
    blob, anchor, _ = master_list()
    ml_file, anchor_file, out = tmp_path / "list.ml", tmp_path / "anchor.pem", tmp_path / "csca"
    ml_file.write_bytes(blob)
    anchor_file.write_bytes(anchor.pem())
    args = ["--master-list", str(ml_file), "--anchor", str(anchor_file), "--out", str(out)]
    assert csca_import.main([*args, "--dry-run"]) == 0 and not out.exists()
    assert csca_import.main(args) == 0
    files = sorted(out.glob("*.pem"))
    assert len(files) == 3
    stamp = {f: f.read_bytes() for f in files}
    (out / "keep-me.pem").write_text("operator's own file")
    assert csca_import.main(args) == 0  # second run adds nothing
    assert {f: f.read_bytes() for f in files} == stamp and (
        out / "keep-me.pem"
    ).read_text() == "operator's own file"
    assert (
        hashlib.sha256(anchor.cert.public_bytes(Encoding.DER)).hexdigest()
        in capsys.readouterr().out
    )


def test_the_importer_writes_nothing_for_a_list_that_does_not_verify(tmp_path: Path):
    blob, _, _ = master_list()
    stranger = make_cert("Other CSCA", ec_key(), ca=True)
    (tmp_path / "list.ml").write_bytes(blob)
    (tmp_path / "a.pem").write_bytes(stranger.pem())
    out = tmp_path / "csca"
    assert (
        csca_import.main(
            [
                "--master-list",
                str(tmp_path / "list.ml"),
                "--anchor",
                str(tmp_path / "a.pem"),
                "--out",
                str(out),
            ]
        )
        == 1
    )
    assert not out.exists()


def test_the_importer_refuses_to_run_without_an_anchor(tmp_path: Path):
    with pytest.raises(SystemExit):
        csca_import.main(["--master-list", str(tmp_path / "list.ml")])


def test_a_imported_csca_is_what_the_chip_check_then_trusts():
    chip = make_chip()
    econtent = der(0x30, integer(0) + der(0x31, chip.csca.cert.public_bytes(Encoding.DER)))
    anchor = make_cert(
        "Utopia ML anchor",
        ec_key(),
        ca=True,
        not_before=NOW - timedelta(days=3000),
        not_after=NOW + timedelta(days=3000),
    )
    signer = make_cert(
        "ML signer",
        ec_key(),
        anchor,
        not_before=NOW - timedelta(days=30),
        not_after=NOW + timedelta(days=300),
    )
    r = ml.read_master_list(
        sod_cms(econtent, signer, content_type=OID_ML, wrap=False), anchors=[anchor.cert]
    )
    assert r.ok
    assert ep.verify(chip.sod, chip.data_groups, csca=r.certificates).status == "VERIFIED_LIMITED"
    assert ep.verify(chip.sod, chip.data_groups, csca=()).status == "INDETERMINATE"


# ------------------------------------------------------------------------------------ engine mode and country scope (WF01 O1, T02; CR-107)


def test_an_engine_switched_on_without_a_mode_is_refused_and_a_lab_engine_is_never_routed() -> None:
    cfg = accepted()
    del cfg["mode"]
    assert be.engine_gate_problems(cfg, today=TODAY) == ["ENGINE_MODE_MISSING"]
    with pytest.raises(be.EngineGateError):
        be.engine_gate(cfg, today=TODAY)  # switched on, not accepted: the owner is told
    assert be.engine_mode(cfg) == "DISABLED"
    # T02: LAB_ONLY is never routed to a live decision, even with a complete file and the flag on
    lab = accepted(mode="LAB_ONLY")
    assert (
        be.engine_gate_problems(lab, today=TODAY) == [] and be.engine_gate(lab, today=TODAY) is None
    )
    assert be.engine_mode(lab) == "LAB_ONLY"
    # a lab engine is only identifiable: no evaluation is needed to be a lab engine, and no id is not a lab engine
    bare = {"enabled": True, "mode": "LAB_ONLY", "engine": {"id": "silent-face", "version": "1.0"}}
    assert (
        be.engine_gate_problems(bare, today=TODAY) == []
        and be.engine_gate(bare, today=TODAY) is None
    )
    assert "ENGINE_ID_MISSING" in be.engine_gate_problems(
        {"enabled": True, "mode": "LAB_ONLY", "engine": {}}, today=TODAY
    )
    # DISABLED stays off whatever else the file says
    assert be.engine_gate(accepted(mode="DISABLED"), today=TODAY) is None
    assert be.engine_mode({"enabled": False, "mode": "APPROVED_FOR_SCOPE"}) == "DISABLED"
    assert be.engine_mode(accepted(mode="NONSENSE")) == "DISABLED"
    assert be.engine_gate_problems(accepted(mode="NONSENSE"), today=TODAY) == [
        "ENGINE_MODE_MISSING"
    ]


def test_an_approved_engine_needs_named_countries_and_runs_only_there() -> None:
    for bad in (
        None,
        {},
        {"countries": []},
        {"countries": ["gb"]},
        {"countries": ["GBR"]},
        {"countries": "GB"},
        {"countries": [7]},
    ):
        cfg = accepted()
        cfg["scope"] = bad
        assert "ENGINE_SCOPE_MISSING" in be.engine_gate_problems(cfg, today=TODAY), bad
    gate = be.engine_gate(accepted(), today=TODAY)
    assert (
        gate is not None and gate.allows("GB") and not gate.allows("FR") and not gate.allows(None)
    )
    assert gate.audit()["mode"] == "APPROVED_FOR_SCOPE" and gate.audit()["countries"] == ["GB"]


def test_outside_its_countries_the_engine_is_off_for_the_session_and_the_reason_says_so() -> None:
    gate = be.engine_gate(accepted(), today=TODAY)
    chip = chip_evidence()
    inside = app.assess(chip, session_ref="s", gate=gate, country="GB", scoped=True)
    assert "SELF_RUN_ENGINE_NOT_APPROVED_FOR_COUNTRY" not in inside.audit["codes"]
    outside = app.assess(chip, session_ref="s", gate=gate, country="FR", scoped=True)
    assert "SELF_RUN_ENGINE_NOT_APPROVED_FOR_COUNTRY" in outside.audit["codes"]
    assert "SELF_RUN_BIOMETRICS_OFF" in outside.audit["codes"]  # nothing binds the holder there
    assert outside.result.binding is None
    unknown = app.assess(chip, session_ref="s", gate=gate, country=None, scoped=True)
    assert (
        "SELF_RUN_ENGINE_NOT_APPROVED_FOR_COUNTRY" in unknown.audit["codes"]
    )  # no country, no approval


# ------------------------------------------------------------------------------------------- the licence and component register (WF01-011; CR-107)

VERIFIED = {
    "commercial_use": "YES",
    "model_licence_confirmed": True,
    "licence_verified_on": "2026-11-01",
}


def reg(*components: dict) -> dict:
    return {"components": list(components)}


def test_an_engine_is_refused_unless_every_component_is_in_the_register_with_its_licences_confirmed() -> (
    None
):
    both = reg({"id": "deepface", **VERIFIED}, {"id": "silent-face-anti-spoofing", **VERIFIED})
    assert be.engine_gate_problems(accepted(), today=TODAY, register=both) == []
    assert be.engine_gate(accepted(), today=TODAY, register=both) is not None

    missing = reg({"id": "deepface", **VERIFIED})
    assert be.engine_gate_problems(accepted(), today=TODAY, register=missing) == [
        "COMPONENT_SILENT-FACE-ANTI-SPOOFING_NOT_IN_LICENCE_REGISTER"
    ]
    for field, code in (
        ({"commercial_use": "UNKNOWN"}, "COMMERCIAL_USE_NOT_CONFIRMED"),
        ({"commercial_use": "NO"}, "COMMERCIAL_USE_NOT_CONFIRMED"),
        ({"commercial_use": False}, "COMMERCIAL_USE_NOT_CONFIRMED"),  # a bare NO in YAML is false
        ({"model_licence_confirmed": False}, "MODEL_LICENCE_NOT_CONFIRMED"),
        ({"licence_verified_on": None}, "LICENCE_NOT_VERIFIED"),
    ):
        bad = reg(
            {"id": "deepface", **VERIFIED, **field}, {"id": "silent-face-anti-spoofing", **VERIFIED}
        )
        assert be.engine_gate_problems(accepted(), today=TODAY, register=bad) == [
            f"COMPONENT_DEEPFACE_{code}"
        ], field
        with pytest.raises(be.EngineGateError):
            be.engine_gate(accepted(), today=TODAY, register=bad)
    assert (
        be.engine_gate_problems(
            accepted(),
            today=TODAY,
            register=reg(
                {"id": "deepface", **VERIFIED, "commercial_use": True},
                {"id": "silent-face-anti-spoofing", **VERIFIED},
            ),
        )
        == []
    )  # a bare YES is yes


def test_an_engine_with_no_components_named_is_refused_when_a_register_is_supplied() -> None:
    for bad in (None, [], "deepface", [7]):
        cfg = accepted()
        cfg["engine"]["components"] = bad
        assert "ENGINE_COMPONENTS_MISSING" in be.engine_gate_problems(
            cfg, today=TODAY, register=reg({"id": "deepface", **VERIFIED})
        )
    # without a register (a pure rule test) nothing is added: the deployment's loader always supplies one
    cfg = accepted()
    del cfg["engine"]["components"]
    assert be.engine_gate_problems(cfg, today=TODAY) == []


def test_the_shipped_register_verifies_nothing_so_no_engine_can_go_live_from_it() -> None:
    from zid_platform.modules.verification.infrastructure import engine_register

    shipped = engine_register.register()
    ids = [c["id"] for c in shipped["components"]]
    assert {"deepface", "silent-face-anti-spoofing", "insightface"} <= set(ids) and len(ids) == len(
        set(ids)
    )
    for c in shipped["components"]:
        assert {
            "role",
            "repository",
            "code_licence",
            "model_licence",
            "commercial_use",
            "model_licence_confirmed",
            "licence_verified_on",
            "status",
            "sbom_ref",
        } <= set(c), c["id"]
        assert c["licence_verified_on"] is None, c["id"]  # nobody has verified anything yet
        assert be.register_problems([c["id"]], shipped), c[
            "id"
        ]  # so every component still blocks an engine
    insight = next(c for c in shipped["components"] if c["id"] == "insightface")
    assert (
        insight["commercial_use"] == "NO" and insight["status"] == "NOT_FOR_COMMERCIAL_USE"
    )  # pretrained models are non-commercial
    # and the shipped files together keep an enabled engine shut
    cfg = accepted()
    with pytest.raises(be.EngineGateError) as e:
        be.engine_gate(cfg, today=TODAY, register=shipped)
    assert any("NOT_CONFIRMED" in p or "NOT_VERIFIED" in p for p in e.value.problems)
