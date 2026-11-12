"""Model lifecycle rules (ZID-PRI-004 PR-16): AT-015 no evidence no release, AT-016 drift, AT-017 protected features."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.privacy.domain import models as m

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def metrics(kind: str = "face_match") -> dict:
    return {n: {"value": 0.01, "ci_low": 0.005, "ci_high": 0.02} for n in m.KINDS[kind]}


def evaluation(**over) -> dict:
    return {
        "independent": True,
        "cohorts": 4,
        "accessible_alternative_confirmed": True,
        "metrics": metrics(),
        "created_at": NOW - timedelta(days=10),
        **over,
    }


def test_at017_protected_characteristics_and_proxies_are_never_live_features() -> None:
    assert m.protected_features(["document_quality", "device_age"]) == []
    assert m.protected_features(
        ["Race", "skin_tone_score", "applicant-gender", "nationality_code"]
    ) == [
        "Race",
        "applicant-gender",
        "nationality_code",
        "skin_tone_score",
    ]
    assert m.protected_features(["marital_status_flag", "ethnic_group"]) == [
        "ethnic_group",
        "marital_status_flag",
    ]
    assert m.protected_features(["racetrack_latency"]) == [], "a whole-word match, not a substring"


def test_at015_no_evidence_means_no_promotion() -> None:
    assert m.promotion_blockers(
        kind="face_match", state="REGISTERED", evaluation=None, now=NOW
    ) == ["FAIRNESS_EVIDENCE_MISSING"]
    ok = m.promotion_blockers(
        kind="face_match", state="REGISTERED", evaluation=evaluation(), now=NOW
    )
    assert ok == []


def test_an_evaluation_must_be_independent_comparative_accessible_complete_and_current() -> None:
    def blockers(**over):
        return m.promotion_blockers(
            kind="face_match", state="REGISTERED", evaluation=evaluation(**over), now=NOW
        )

    assert "EVALUATION_NOT_INDEPENDENT" in blockers(independent=False)
    assert "EVALUATION_HAS_NO_COHORT_COMPARISON" in blockers(cohorts=1)
    assert "ACCESSIBLE_ALTERNATIVE_NOT_CONFIRMED" in blockers(
        accessible_alternative_confirmed=False
    )
    assert "EVALUATION_STALE" in blockers(created_at=NOW - timedelta(days=400))
    partial = metrics()
    del partial["fnmr"]
    assert "MEASURE_MISSING_FNMR" in blockers(metrics=partial)
    no_interval = {**metrics(), "fmr": {"value": 0.01}}
    assert "MEASURE_MISSING_FMR" in blockers(metrics=no_interval)
    inverted = {**metrics(), "fmr": {"value": 0.5, "ci_low": 0.6, "ci_high": 0.7}}
    assert "MEASURE_INTERVAL_INVALID_FMR" in blockers(metrics=inverted)


def test_each_kind_needs_its_own_measures() -> None:
    assert set(m.KINDS["pad"]) >= {"apcer", "bpcer"}, "ISO/IEC 30107-3 measures"
    assert set(m.KINDS["face_match"]) >= {"fmr", "fnmr"}
    assert m.evaluation_problems("telepathy", {}) == ["MODEL_KIND_UNKNOWN"]


def test_a_version_in_service_or_suspended_cannot_be_promoted_again() -> None:
    for state in ("ACTIVE", "SUSPENDED", "RETIRED"):
        assert "MODEL_STATE_NOT_PROMOTABLE" in m.promotion_blockers(
            kind="face_match", state=state, evaluation=evaluation(), now=NOW
        )


def test_at016_any_other_version_is_drift_even_a_newer_one() -> None:
    assert m.observe("2.1", "2.1") == "OK"
    assert m.observe("2.1", "2.2") == "DRIFT"
    assert m.observe("2.1", "2.0") == "DRIFT"


def test_a_release_names_only_models_in_service() -> None:
    assert m.release_blockers([{"state": "ACTIVE"}]) == []
    assert m.release_blockers([{"state": "HELD"}, {"state": "REGISTERED"}]) == [
        "MODEL_HELD",
        "MODEL_REGISTERED",
    ]
