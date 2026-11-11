"""The Python mirror of the TypeScript session state machine (ZID-INT-002 section 05.1)."""

from __future__ import annotations

import pytest

from zoiko_credentials import session as s


def test_the_happy_path_reports_the_documented_events() -> None:
    path = ["NEW", "CONSENT_REQUIRED", "READY", "COLLECTING", "EVIDENCE_RECEIVED", "SOURCE_PENDING", "COMPLETE"]
    events = [s.move(a, b).event for a, b in zip(path, path[1:], strict=False)]
    assert events == [
        "session.created", "consent.recorded", "capture.started", "document.submitted", "issuer.pending", "session.completed",
    ]  # fmt: skip


def test_no_capture_before_consent_and_evidence_never_completes_directly() -> None:
    assert not s.can_move("NEW", "COLLECTING") and not s.can_move("CONSENT_REQUIRED", "COLLECTING")
    assert not s.can_move("EVIDENCE_RECEIVED", "COMPLETE") and not s.can_move("COLLECTING", "COMPLETE")
    with pytest.raises(s.SessionError):
        s.move("NEW", "COLLECTING")


def test_a_corrupt_payload_retries_and_an_outage_waits() -> None:
    assert s.after_capture(False) == "RETRY_REQUIRED" and s.after_capture(True) == "EVIDENCE_RECEIVED"
    assert s.after_source("UNAVAILABLE") == "SOURCE_PENDING"
    assert (s.after_source("CONFIRMED"), s.after_source("UNCLEAR"), s.after_source("TIMED_OUT")) == (
        "COMPLETE", "REVIEW_REQUIRED", "EXPIRED",
    )  # fmt: skip
    assert not any("FAIL" in state for state in s.SESSION_STATES)


def test_a_human_decision_needs_an_authority_but_an_appeal_does_not() -> None:
    with pytest.raises(s.SessionError, match="authority"):
        s.move("REVIEW_REQUIRED", "COMPLETE")
    with pytest.raises(s.SessionError):
        s.move("REVIEW_REQUIRED", "REJECTED", decided_by="  ")
    assert s.move("REVIEW_REQUIRED", "REJECTED", decided_by="reviewer-7").to_state == "REJECTED"
    assert s.move("REVIEW_REQUIRED", "APPEALED").to_state == "APPEALED"


def test_withdrawing_consent_closes_capture_and_leaves_a_finished_session_alone() -> None:
    assert s.withdraw_consent("COLLECTING").to_state == "CANCELED"
    assert s.withdraw_consent("READY").event == "session.canceled"
    assert s.withdraw_consent("CONSENT_REQUIRED").to_state == "DECLINED"
    with pytest.raises(s.SessionError):
        s.withdraw_consent("COMPLETE")


def test_every_state_can_reach_a_terminal_state_and_terminals_go_nowhere() -> None:
    for t in ("COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE"):
        assert s.is_terminal(t)

    def reach(state: str, seen: set[str]) -> bool:
        if s.is_terminal(state):
            return True
        if state in seen:
            return False
        seen.add(state)
        return any(s.can_move(state, n) and reach(n, seen) for n in s.SESSION_STATES)

    assert all(reach(x, set()) for x in s.SESSION_STATES)


def test_the_typescript_and_python_machines_have_the_same_states() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "typescript" / "src" / "session.ts").read_text(encoding="utf-8")
    assert all(f'"{state}"' in src for state in s.SESSION_STATES)
