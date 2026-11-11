"""ZID-INT-002 sections 04, 05.1 and 14: the verification case rules (pure)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.credential.domain import cases as cs

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
LATER = NOW + timedelta(days=7)
P, I, C = cs.TaskState.PENDING_INPUT, cs.TaskState.IN_PROGRESS, cs.TaskState.COMPLETE


def test_every_requested_check_is_kept_once_in_order() -> None:
    assert cs.validate_checks(["identity", "degree", "license", "degree", "insurance"]) == [
        "identity", "degree", "license", "insurance",
    ]  # fmt: skip


@pytest.mark.parametrize("bad", [[], ["identity", "wizard"], ["DEGREE"], ["criminal"]])
def test_an_empty_or_unknown_check_creates_nothing(bad: list[str]) -> None:
    with pytest.raises(cs.CaseError) as e:
        cs.validate_checks(bad)
    assert e.value.code == "CHECK_NOT_SUPPORTED"


def test_every_credential_check_has_a_claim_class_and_a_pack_service_and_identity_has_neither() -> (
    None
):
    assert cs.CHECK_KINDS["identity"] is None
    for name, kind in cs.CHECK_KINDS.items():
        if name != "identity":
            assert kind is not None and name in cs.SERVICE_OF


def test_a_case_completes_only_when_every_task_does() -> None:
    f = lambda tasks: cs.case_state(cs.CaseState.PENDING, tasks, now=NOW, expires_at=LATER)
    assert f([C, C]) is cs.CaseState.COMPLETE
    assert f([C, P]) is cs.CaseState.PENDING and f([C, I]) is cs.CaseState.PENDING
    assert f([]) is cs.CaseState.PENDING, "a case with no tasks is never complete"


def test_a_case_past_its_time_with_work_open_is_expired_never_failed() -> None:
    s = cs.case_state(cs.CaseState.PENDING, [C, P], now=LATER, expires_at=LATER)
    assert s is cs.CaseState.EXPIRED and "FAIL" not in s.value
    # but finishing in time wins over the clock
    assert (
        cs.case_state(cs.CaseState.PENDING, [C, C], now=LATER, expires_at=LATER)
        is cs.CaseState.COMPLETE
    )


@pytest.mark.parametrize(
    "final", [cs.CaseState.COMPLETE, cs.CaseState.CANCELED, cs.CaseState.EXPIRED]
)
def test_a_finished_case_is_never_mutated_and_asks_for_a_new_case(final: cs.CaseState) -> None:
    assert cs.case_state(final, [P], now=NOW, expires_at=LATER) is final
    assert cs.case_state(final, [C], now=NOW, expires_at=LATER) is final
    with pytest.raises(cs.CaseError) as e:
        cs.require_open(final)
    assert e.value.code == "INCOMPATIBLE_STATE"
    cs.require_open(cs.CaseState.PENDING)


@pytest.mark.parametrize(
    ("status", "outcome", "state"),
    [
        ("VERIFIED", "VERIFIED", C),
        ("FAILED", "NOT_VERIFIED", C),
        ("EXPIRED", "EXPIRED", C),
        ("CANCELED", "INDETERMINATE", C),
        ("CREATED", None, I),
        ("IN_PROGRESS", None, I),
        ("PENDING_EXTERNAL", None, I),
        ("MANUAL_REVIEW", None, I),
        ("RESUBMISSION_REQUIRED", None, I),
    ],
)
def test_an_identity_task_is_complete_only_when_its_session_is_final(
    status: str, outcome: str | None, state: cs.TaskState
) -> None:
    assert cs.identity_outcome(status) == (outcome, state)


def test_pending_work_names_what_each_open_task_needs_and_hides_finished_ones() -> None:
    work = cs.pending_work(
        [
            cs.TaskView("identity", P, None),
            cs.TaskView("degree", C, "VERIFIED"),
            cs.TaskView("license", P, None),
        ]
    )
    assert [w["check"] for w in work] == ["identity", "license"]
    assert "verification_id" in work[0]["needs"] and "credential_claim" in work[1]["needs"]
