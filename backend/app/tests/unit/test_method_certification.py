"""The method certification board (WF03 P10; CR-119): honest levels, nothing approved without records, and the engine row follows the readiness record."""

from __future__ import annotations

from zid_platform.modules.verification.domain import method_certification as mc

OFF = {
    "mode": "OFF",
    "enabled": False,
    "approvable": False,
    "problems_for_approval": ["ENGINE_ID_MISSING", "DPIA_REF_MISSING"],
    "countries": [],
}
APPROVED = {
    "mode": "APPROVED_FOR_SCOPE",
    "enabled": True,
    "approvable": True,
    "problems_for_approval": [],
    "countries": ["GB", "FR"],
}


def rows(**kw):
    base = {
        "trust_list_active": False,
        "engine": OFF,
        "optical_checks_produced": ("RESOLUTION", "ELA_ANOMALY"),
        "apple_attestation_configured": False,
        "imaging_configured": True,
    }
    base.update(kw)
    return {r.method: r for r in mc.board(**base)}


def test_nothing_the_platform_can_do_alone_reaches_approved() -> None:
    r = rows(trust_list_active=True)
    for method in (
        "CHIP_PASSIVE_AUTHENTICATION",
        "CHIP_CLONE_CHECK",
        "PRINTED_DOCUMENT_QUALITY",
        "DEVICE_ATTESTATION",
    ):
        assert r[method].level is mc.Level.BUILT_UNPROVEN and r[method].needs, (
            method
        )  # each still names what a real run or a lab must supply


def test_without_an_active_country_list_the_chip_check_is_incomplete_and_says_so() -> None:
    r = rows(trust_list_active=False)["CHIP_PASSIVE_AUTHENTICATION"]
    assert r.level is mc.Level.EVIDENCE_INCOMPLETE and any("country list" in n for n in r.needs)


def test_printed_authenticity_is_not_built_while_no_such_check_is_produced() -> None:
    assert rows()["PRINTED_DOCUMENT_AUTHENTICITY"].level is mc.Level.NOT_BUILT
    assert (
        rows(optical_checks_produced=("TEMPLATE",))["PRINTED_DOCUMENT_AUTHENTICITY"].level
        is mc.Level.BUILT_UNPROVEN
    )


def test_the_engine_row_lists_what_is_missing_and_is_approved_only_with_every_record() -> None:
    off = rows()["FACE_AND_LIVENESS_ENGINE"]
    assert off.level is mc.Level.EVIDENCE_INCOMPLETE and off.needs == (
        "ENGINE_ID_MISSING",
        "DPIA_REF_MISSING",
    )
    done = rows(engine=APPROVED)["FACE_AND_LIVENESS_ENGINE"]
    assert done.level is mc.Level.APPROVED and "GB, FR" in done.summary and done.needs == ()
    on_without_records = rows(
        engine={**APPROVED, "approvable": False, "problems_for_approval": ["LICENCE_NOT_READ"]}
    )["FACE_AND_LIVENESS_ENGINE"]
    assert (
        on_without_records.level is mc.Level.EVIDENCE_INCOMPLETE
        and on_without_records.needs == ("LICENCE_NOT_READ",)
    )


def test_an_engine_that_is_on_but_not_approved_is_never_shown_approved() -> None:
    row = rows(
        engine={
            "mode": "SANDBOX",
            "enabled": True,
            "approvable": True,
            "problems_for_approval": [],
            "countries": ["GB"],
        }
    )["FACE_AND_LIVENESS_ENGINE"]
    assert row.level is not mc.Level.APPROVED


def test_the_summary_counts_every_level() -> None:
    counts = mc.summary(
        mc.board(
            trust_list_active=False,
            engine=OFF,
            optical_checks_produced=(),
            apple_attestation_configured=False,
            imaging_configured=True,
        )
    )
    assert sum(counts.values()) == 6 and counts["APPROVED"] == 0 and counts["NOT_BUILT"] == 1


def test_without_the_photo_checking_service_the_quality_row_is_incomplete_and_names_what_to_do() -> (
    None
):
    row = rows(imaging_configured=False)["PRINTED_DOCUMENT_QUALITY"]
    assert row.level is mc.Level.EVIDENCE_INCOMPLETE and any("ZID_OCR_URL" in n for n in row.needs)
    assert "not available" in row.summary
    assert (
        rows(imaging_configured=True)["PRINTED_DOCUMENT_QUALITY"].level is mc.Level.BUILT_UNPROVEN
    )
