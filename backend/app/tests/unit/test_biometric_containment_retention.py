"""Breach containment and biometric retention (ZID-DTV-WF-04 B05, B09, BIO-08; QA-021, QA-033; CR-108): a HIGH or CRITICAL breach pauses biometric
capture in its jurisdictions by default, the pause is released only by another person with a reason, and a method whose asset classes have no
retention period is not available."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from zid_platform.modules.privacy.application.control import CONFIG
from zid_platform.modules.privacy.domain import biometric_assets as ba
from zid_platform.modules.privacy.domain import control as rules
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.domain import biometric_methods as bm

# ------------------------------------------------------------------------------------------------------------------------ breach pause


@pytest.mark.parametrize(
    ("severity", "asked", "paused"),
    [
        ("CRITICAL", None, True),
        ("HIGH", None, True),
        ("MODERATE", None, False),
        ("LOW", None, False),
        ("LOW", True, True),
        ("CRITICAL", False, False),
    ],
)
def test_capture_pause_default_follows_severity_unless_asked(
    severity: str, asked: bool | None, paused: bool
) -> None:
    assert rules.default_capture_pause(severity, asked) is paused


def problem(**over: Any) -> str | None:
    args: dict[str, Any] = {
        "case_state": "OPEN",
        "capture_state": "PAUSED",
        "action": "RELEASE",
        "actor": "ben",
        "paused_by": "ana",
        "reason": "vendor confirmed the exfiltration path is closed",
    }
    args.update(over)
    return rules.capture_problem(**args)


def test_release_by_another_person_with_reason() -> None:
    assert problem() is None


def test_release_by_whoever_paused_is_refused() -> None:
    assert problem(actor="ana") == "RELEASED_BY_WHOEVER_PAUSED"


def test_release_needs_a_reason() -> None:
    assert problem(reason="ok") == "RELEASE_REASON_REQUIRED"


def test_capture_moves_forward_only() -> None:
    assert problem(capture_state="NOT_PAUSED") == "CAPTURE_NOT_PAUSED"
    assert problem(capture_state="RELEASED") == "CAPTURE_NOT_PAUSED"
    assert problem(action="PAUSE", capture_state="NOT_PAUSED") is None
    assert problem(action="PAUSE", capture_state="RELEASED") == "CAPTURE_ALREADY_PAUSED_OR_RELEASED"


def test_closed_case_changes_nothing() -> None:
    assert problem(case_state="CLOSED") == "BREACH_CLOSED"
    assert (
        problem(case_state="CLOSED", action="PAUSE", capture_state="NOT_PAUSED") == "BREACH_CLOSED"
    )


def test_only_open_paused_cases_in_the_country_pause_it() -> None:
    cases = [
        {
            "breach_id": "b1",
            "state": "OPEN",
            "capture_state": "PAUSED",
            "jurisdictions": ["GB", "FR"],
        },
        {
            "breach_id": "b2",
            "state": "CONTAINED",
            "capture_state": "PAUSED",
            "jurisdictions": ["GB"],
        },
        {"breach_id": "b3", "state": "CLOSED", "capture_state": "PAUSED", "jurisdictions": ["GB"]},
        {"breach_id": "b4", "state": "OPEN", "capture_state": "RELEASED", "jurisdictions": ["GB"]},
        {"breach_id": "b5", "state": "OPEN", "capture_state": "PAUSED", "jurisdictions": ["DE"]},
    ]
    assert rules.capture_paused_by_breach(cases, "gb") == ["b1", "b2"]
    assert rules.capture_paused_by_breach(cases, "IT") == []


# ------------------------------------------------------------------------------------------------------------------------ asset classes


def test_shipped_classes_parse_and_cover_both_methods() -> None:
    classes = ba.parse(
        yaml.safe_load((CONFIG / "biometric-asset-classes.yaml").read_text(encoding="utf-8"))
    )
    assert ba.required(classes, "FACE_MATCH") == (
        "biometric.face_template",
        "biometric.live_selfie",
        "biometric.match_result",
    )
    assert ba.required(classes, "LIVENESS") == (
        "biometric.liveness_capture",
        "biometric.liveness_result",
    )


@pytest.mark.parametrize(
    "doc",
    [
        {"classes": [{"id": "selfie", "methods": ["FACE_MATCH"]}]},
        {"classes": [{"id": "biometric.x", "methods": ["IRIS"]}]},
        {"classes": [{"id": "biometric.x", "methods": []}]},
        {
            "classes": [
                {"id": "biometric.x", "methods": ["FACE_MATCH"]},
                {"id": "biometric.x", "methods": ["LIVENESS"]},
            ]
        },
        {"classes": [{"id": "biometric.x", "methods": ["FACE_MATCH"]}]},  # nothing for LIVENESS
    ],
)
def test_a_malformed_file_is_refused_whole(doc: dict[str, Any]) -> None:
    with pytest.raises(ba.AssetClassError):
        ba.parse(doc)


def test_missing_lists_uncovered_classes_only() -> None:
    classes = ba.parse(
        {
            "classes": [
                {"id": "biometric.aa", "methods": ["FACE_MATCH"], "tier": "SOURCE"},
                {"id": "biometric.bb", "methods": ["FACE_MATCH", "LIVENESS"], "tier": "RESULT"},
            ]
        }
    )
    assert ba.missing(classes, "FACE_MATCH", ["biometric.bb"]) == ("biometric.aa",)
    assert ba.missing(classes, "LIVENESS", ["biometric.bb"]) == ()


# ------------------------------------------------------------------------------------------------------------------------ eligibility


def _answer(**over: Any) -> be.MethodAnswer:
    live = bm.Availability(True, bm.State.PRODUCTION_ELIGIBLE, (), None)
    args: dict[str, Any] = {
        "engine_reasons": [],
        "state": live,
        "dpia": {"ready": True, "package_id": "p"},
        "overlay": None,
    }
    args.update(over)
    return be.combine(bm.Method.FACE_MATCH, **args)


def test_breach_pause_refuses_the_method() -> None:
    a = _answer(breaches=("b1",))
    assert not a.available and "BREACH_CAPTURE_PAUSED" in a.reasons and a.breaches == ("b1",)


def test_blank_retention_refuses_and_names_each_class() -> None:
    a = _answer(missing_retention=("biometric.live_selfie", "biometric.face_template"))
    assert not a.available
    assert "RETENTION_NOT_SET:biometric.live_selfie" in a.reasons
    assert "RETENTION_NOT_SET:biometric.face_template" in a.reasons
    assert a.view()["missing_retention"] == ["biometric.live_selfie", "biometric.face_template"]


# ------------------------------------------------------------------------------------------------------------------------ erasure (B05)


def _classes() -> tuple[ba.AssetClass, ...]:
    return ba.parse(
        yaml.safe_load((CONFIG / "biometric-asset-classes.yaml").read_text(encoding="utf-8"))
    )


def test_deletion_order_is_source_then_derived_then_result_then_the_rest() -> None:
    c = _classes()
    types = [
        "verification.document",
        "biometric.match_result",
        "biometric.face_template",
        "biometric.live_selfie",
        "biometric.liveness_capture",
    ]
    ordered = sorted(types, key=lambda t: ba.deletion_rank(c, t))
    assert ordered == [
        "biometric.live_selfie",
        "biometric.liveness_capture",
        "biometric.face_template",
        "biometric.match_result",
        "verification.document",
    ]
    assert ba.class_of(c, "verification.document") is None


def test_backups_are_attested_last() -> None:
    assert ba.backup_problem(["ACKNOWLEDGED", "PENDING"]) == "BACKUP_BEFORE_SOURCES"
    assert ba.backup_problem(["ACKNOWLEDGED", "FAILED"]) == "BACKUP_BEFORE_SOURCES"
    assert ba.backup_problem(["ACKNOWLEDGED", "HELD"]) is None
    assert ba.backup_problem([]) is None


def _task(kind: str, ref: str, state: str, classes: list[str]) -> dict[str, Any]:
    return {"target_kind": kind, "target_ref": ref, "state": state, "asset_classes": classes}


def test_a_class_is_purged_only_when_its_tasks_and_the_backups_are_acknowledged() -> None:  # QA-022
    tasks = [
        _task("FIRST_PARTY_STORE", "vault:1", "ACKNOWLEDGED", ["biometric.live_selfie"]),
        _task("PROVIDER", "acme", "PENDING", ["biometric.live_selfie", "biometric.match_result"]),
        _task("FIRST_PARTY_STORE", "docs:2", "ACKNOWLEDGED", []),
        _task("BACKUP", "cell-backups", "PENDING", []),
    ]
    summary = {r["asset_class"]: r for r in ba.class_summary(tasks)}
    assert set(summary) == {"biometric.live_selfie", "biometric.match_result"}
    assert summary["biometric.live_selfie"]["state"] == "PENDING"
    assert {o["target_ref"] for o in summary["biometric.live_selfie"]["outstanding"]} == {
        "acme",
        "cell-backups",
    }
    tasks[1]["state"] = "ACKNOWLEDGED"
    tasks[3]["state"] = "ACKNOWLEDGED"
    assert all(r["state"] == "PURGED" for r in ba.class_summary(tasks))


def test_a_held_or_failed_task_shows_on_its_class() -> None:  # QA-023, QA-024
    held = [
        _task("FIRST_PARTY_STORE", "vault:1", "HELD", ["biometric.face_template"]),
        _task("BACKUP", "b", "ACKNOWLEDGED", []),
    ]
    failed = [
        _task("PROVIDER", "acme", "FAILED", ["biometric.liveness_result"]),
        _task("BACKUP", "b", "PENDING", []),
    ]
    assert ba.class_summary(held)[0]["state"] == "HELD"
    assert ba.class_summary(failed)[0]["state"] == "FAILED"


def test_a_class_without_a_tier_is_refused() -> None:
    with pytest.raises(ba.AssetClassError):
        ba.parse(
            {
                "classes": [
                    {"id": "biometric.xx", "methods": ["FACE_MATCH", "LIVENESS"], "tier": "RAW"}
                ]
            }
        )


# ------------------------------------------------------------------------------------------------------------- model evidence (CR-109)


def test_method_kinds_map_to_model_kinds() -> None:
    from zid_platform.modules.privacy.application.model_governance import METHOD_KIND
    from zid_platform.modules.privacy.domain.models import KINDS

    assert METHOD_KIND == {"FACE_MATCH": "face_match", "LIVENESS": "pad"}
    assert set(METHOD_KIND.values()) <= set(KINDS)


# ------------------------------------------------------------------------------------------------------------- review checker (CR-109, B08)


def _check(**over: Any) -> str | None:
    args: dict[str, Any] = {
        "state": "AWAITING_CHECKER",
        "requested_by": "req",
        "reviewer": "rev",
        "checker": "chk",
        "note": "agreed",
    }
    args.update(over)
    return rules.checker_problem(**args)


def test_only_a_reversal_needs_a_checker() -> None:  # QA-032
    assert rules.NEEDS_CHECKER == {"OVERTURNED", "CORRECTED"}
    assert "UPHELD" not in rules.NEEDS_CHECKER


def test_the_checker_is_a_third_person() -> None:  # QA-031
    assert _check() is None
    assert _check(checker="req") == "CHECKER_IS_REQUESTER"
    assert _check(checker="rev") == "CHECKER_IS_REVIEWER"


def test_a_check_needs_a_waiting_proposal_and_a_note() -> None:
    assert _check(state="OPEN") == "REVIEW_NOT_AWAITING_CHECKER"
    assert _check(state="OVERTURNED") == "REVIEW_NOT_AWAITING_CHECKER"
    assert _check(note="  ") == "CHECKER_NOTE_REQUIRED"


# ------------------------------------------------------------------------------------------------------------- fairness state (B07, CR-110)


def _measures() -> dict[str, Any]:
    m = {"value": 0.01, "ci_low": 0.005, "ci_high": 0.02}
    return {"fmr": m, "fnmr": m, "failure_to_acquire": m}


def _eval(**over: Any) -> dict[str, Any]:
    from datetime import UTC, datetime

    e: dict[str, Any] = {
        "independent": True,
        "cohorts": 4,
        "metrics": _measures(),
        "accessible_alternative_confirmed": True,
        "created_at": datetime.now(UTC),
    }
    e.update(over)
    return e


def test_fairness_states() -> None:  # QA-016 to QA-019
    from datetime import UTC, datetime, timedelta

    from zid_platform.modules.privacy.domain import models as mr

    now = datetime.now(UTC)
    assert mr.fairness_state("face_match", None, now=now)[0] == "NOT_RUN"
    assert (
        mr.fairness_state("face_match", _eval(created_at=now - timedelta(days=400)), now=now)[0]
        == "REEVALUATION_DUE"
    )
    assert mr.fairness_state("face_match", _eval(cohorts=1), now=now)[0] == "DATA_INSUFFICIENT"
    assert (
        mr.fairness_state("face_match", _eval(metrics={"fmr": _measures()["fmr"]}), now=now)[0]
        == "DATA_INSUFFICIENT"
    )
    assert mr.fairness_state("face_match", _eval(independent=False), now=now) == (
        "FAIL",
        ["EVALUATION_NOT_INDEPENDENT"],
    )
    flagged = {**_measures(), "disparity_flagged": True}
    assert mr.fairness_state("face_match", _eval(metrics=flagged), now=now) == (
        "FAIL",
        ["COHORT_DISPARITY_FLAGGED"],
    )
    assert mr.fairness_state("face_match", _eval(), now=now) == ("PASS_WITH_LIMITS", [])


# ------------------------------------------------------------------------------------------------------------- reauthorization (BIO-20, CR-110)


def test_a_dpia_is_flagged_once_within_its_reassessment_window() -> None:
    from datetime import UTC, datetime, timedelta

    from zid_platform.modules.privacy.application.reauthorization import (
        REASSESSMENT_WINDOW,
        due_for_reassessment,
    )

    now = datetime.now(UTC)
    assert due_for_reassessment(now + REASSESSMENT_WINDOW - timedelta(days=1), None, now=now)
    assert due_for_reassessment(
        now - timedelta(days=1), None, now=now
    )  # already lapsed and never flagged
    assert not due_for_reassessment(now + REASSESSMENT_WINDOW + timedelta(days=1), None, now=now)
    assert not due_for_reassessment(now + timedelta(days=1), now, now=now)  # flagged already
    assert not due_for_reassessment(None, None, now=now)


# ------------------------------------------------------------------------------------------------------------- ecosystem use cases (BIO-19, CR-110)


def test_shipped_use_cases_are_not_certified_until_signed() -> None:
    from datetime import date

    from zid_platform.modules.privacy.domain import biometric_use_cases as uc

    cases = uc.parse(
        yaml.safe_load((CONFIG / "biometric-use-cases.yaml").read_text(encoding="utf-8"))
    )
    today = date(2026, 11, 17)
    assert uc.problems(cases, "ZOIKO_ROOMS_RENTER_IDENTITY", today=today) == [
        "USE_CASE_NOT_CERTIFIED:ZOIKO_ROOMS_RENTER_IDENTITY"
    ]
    assert uc.problems(cases, "ZOIKORUM_PROFESSIONAL_IDENTITY", today=today) == [
        "USE_CASE_NOT_CERTIFIED:ZOIKORUM_PROFESSIONAL_IDENTITY"
    ]
    assert (
        uc.problems(cases, "IDENTITY_VERIFICATION", today=today) == []
    )  # an ordinary tenant purpose is not affected


def test_a_signed_use_case_counts_from_its_effective_date() -> None:
    from datetime import date

    from zid_platform.modules.privacy.domain import biometric_use_cases as uc

    doc = {
        "use_cases": [
            {
                "id": "ROOMS",
                "consumer": "zoiko-rooms",
                "purposes": ["ROOMS_ID"],
                "acceptance": {"approval_ref": "SIGN-1", "effective_from": "2026-12-01"},
            }
        ]
    }
    cases = uc.parse(doc)
    assert uc.problems(cases, "ROOMS_ID", today=date(2026, 11, 17)) == [
        "USE_CASE_NOT_YET_EFFECTIVE:ROOMS"
    ]
    assert uc.problems(cases, "ROOMS_ID", today=date(2026, 12, 2)) == []
    with pytest.raises(uc.UseCaseError):  # one owner per purpose
        uc.parse({"use_cases": [doc["use_cases"][0], {**doc["use_cases"][0], "id": "OTHER"}]})


def test_one_products_approval_never_serves_anothers_purpose() -> None:  # QA-028
    from dataclasses import replace

    from zid_platform.modules.verification.domain import biometric_methods as bm

    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    rooms = bm.Scope(
        bm.Method.FACE_MATCH, "veriff", "2026.11", "GB", "ZOIKO_ROOMS_RENTER_IDENTITY", None
    )
    rec = bm.Record(
        "rooms",
        3,
        rooms,
        bm.State.PRODUCTION_ELIGIBLE,
        "D",
        "E",
        "approved after review",
        "T",
        "ben",
        "ana",
        "p",
        now,
        now,
    )
    call = bm.Call(
        bm.Method.FACE_MATCH, "veriff", "2026.11", "GB", "ZOIKO_ROOMS_RENTER_IDENTITY", "t1"
    )
    assert bm.availability([rec], call).available
    assert not bm.availability(
        [rec], replace(call, purpose="ZOIKORUM_PROFESSIONAL_IDENTITY")
    ).available
