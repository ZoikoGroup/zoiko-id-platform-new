"""The engine readiness view (WF01-011/012): a read-only account of what keeps a face engine from being approved, from the versioned configuration."""

from __future__ import annotations

from datetime import date

from zid_platform.modules.verification.domain import biometric_engine as be
from zid_platform.modules.verification.infrastructure import engine_register, self_run_config

TODAY = date(2026, 11, 17)

REGISTER = {
    "version": "1",
    "status": "VERIFIED",
    "components": [
        {
            "id": "facelib",
            "role": "face_match",
            "code_licence": "MIT",
            "commercial_use": "YES",
            "model_licence_confirmed": True,
            "licence_verified_on": "2026-11-01",
            "status": "IN_USE",
        },
        {
            "id": "other",
            "role": "liveness",
            "commercial_use": "NO",
            "model_licence_confirmed": False,
            "licence_verified_on": None,
            "status": "LAB_ONLY",
        },
    ],
}

COMPLETE = {
    "enabled": True,
    "mode": "APPROVED_FOR_SCOPE",
    "scope": {"countries": ["GB", "DE"]},
    "engine": {
        "id": "engine-a",
        "version": "1.2",
        "components": ["facelib"],
        "evaluation_id": "EVAL-2026-001",
        "evaluated_on": "2026-09-01",
        "pad_standard": "ISO/IEC 30107-3",
        "licence_checked": True,
        "dpia_ref": "DPIA-2026-007",
        "measured": {
            "false_accept": 0.0001,
            "false_reject": 0.02,
            "attack_accept": 0.01,
            "bona_fide_reject": 0.03,
        },
    },
    "thresholds": {"face_match_min": 0.7, "liveness_min": 0.8},
}


def test_a_disabled_engine_shows_its_checklist_and_is_not_approvable() -> None:
    view = be.readiness({"enabled": False}, REGISTER, today=TODAY)
    assert view["mode"] == "DISABLED" and view["enabled"] is False and view["approvable"] is False
    assert (
        "ENGINE_ID_MISSING" in view["problems_for_approval"]
        and "DPIA_REFERENCE_MISSING" in view["problems_for_approval"]
    )
    assert [c["id"] for c in view["register"]["components"]] == ["facelib", "other"]
    assert view["countries"] == [] and view["engine"]["id"] is None


def test_a_complete_record_is_approvable_and_says_nothing_else() -> None:
    view = be.readiness(COMPLETE, REGISTER, today=TODAY)
    assert view["approvable"] is True and view["problems_for_approval"] == []
    assert view["mode"] == "APPROVED_FOR_SCOPE" and view["countries"] == ["DE", "GB"]
    assert (
        view["engine"]["evaluated_on"] == "2026-09-01"
        and view["engine"]["measured"]["false_accept"] == 0.0001
    )
    assert [c["in_engine"] for c in view["register"]["components"]] == [True, False]
    assert view["ceilings"]["false_accept"] == be.MAX_FALSE_ACCEPT


def test_each_missing_piece_is_named_not_summarised() -> None:
    for change, code in (
        ({"engine": {**COMPLETE["engine"], "dpia_ref": ""}}, "DPIA_REFERENCE_MISSING"),
        (
            {"engine": {**COMPLETE["engine"], "licence_checked": False}},
            "LICENCE_CHECK_NOT_RECORDED",
        ),
        (
            {"engine": {**COMPLETE["engine"], "components": ["other"]}},
            "COMPONENT_OTHER_COMMERCIAL_USE_NOT_CONFIRMED",
        ),
        ({"engine": {**COMPLETE["engine"], "evaluated_on": "2024-01-01"}}, "EVALUATION_TOO_OLD"),
        ({"scope": {"countries": []}}, "ENGINE_SCOPE_MISSING"),
        (
            {
                "engine": {
                    **COMPLETE["engine"],
                    "measured": {**COMPLETE["engine"]["measured"], "false_accept": 0.5},
                }
            },
            "MEASURED_FALSE_ACCEPT_ABOVE_CEILING",
        ),
    ):
        view = be.readiness({**COMPLETE, **change}, REGISTER, today=TODAY)
        assert view["approvable"] is False and code in view["problems_for_approval"], code


def test_garbage_in_the_file_gives_a_view_not_an_error() -> None:
    view = be.readiness(
        {"engine": "x", "scope": 3, "thresholds": [], "enabled": "yes"}, None, today=TODAY
    )
    assert (
        view["mode"] == "DISABLED"
        and view["approvable"] is False
        and view["register"]["components"] == []
    )


def test_the_deployments_own_files_are_not_approvable_until_people_fill_them_in() -> None:
    view = be.readiness(self_run_config.raw(), engine_register.register(), today=TODAY)
    assert view["approvable"] is False and view["enabled"] is False
    assert view["register"]["components"], "the licence register lists the components to read"
    assert all(
        c["licence_verified_on"] is None for c in view["register"]["components"]
    )  # nobody has verified a licence yet: nothing here invents one
