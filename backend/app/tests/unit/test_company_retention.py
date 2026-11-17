"""Retention, erasure and legal holds for company-verification records (WF02 K10; acceptance T23, T24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.organization.domain import retention as r

NOW = datetime(2026, 11, 17, tzinfo=UTC)
SCHEDULE = {
    "RAW_DOCUMENTS": 90,
    "EXTRACTED_DATA": 365,
    "DECISION_METADATA": 2555,
    "SCREENING_EVIDENCE": None,
}


def rec(rid, cls, days_old, case="c1", subject="p1"):
    return r.Record(rid, cls, NOW - timedelta(days=days_old), case, subject)


def test_records_past_their_schedule_are_due_and_younger_ones_are_left_alone() -> None:
    plan = r.due_for_deletion(
        [
            rec("a", "RAW_DOCUMENTS", 100),
            rec("b", "RAW_DOCUMENTS", 10),
            rec("c", "EXTRACTED_DATA", 400),
        ],
        SCHEDULE,
        [],
        now=NOW,
    )
    assert plan.delete == ("a", "c") and plan.kept == () and plan.outcome is r.Outcome.COMPLETE


def test_a_class_with_no_schedule_is_never_deleted_and_says_so() -> None:  # T23
    plan = r.due_for_deletion(
        [rec("s", "SCREENING_EVIDENCE", 5000), rec("u", "UNKNOWN_CLASS", 5000)],
        SCHEDULE,
        [],
        now=NOW,
    )
    assert (
        plan.delete == ()
        and plan.kept == (("s", "NO_SCHEDULE"), ("u", "NO_SCHEDULE"))
        and plan.outcome is r.Outcome.PARTIAL
    )


def test_a_legal_hold_stops_deletion_whatever_the_schedule_says() -> None:
    old = [rec("a", "RAW_DOCUMENTS", 100, case="c1"), rec("b", "RAW_DOCUMENTS", 100, case="c2")]
    plan = r.due_for_deletion(old, SCHEDULE, [r.Hold("h1", case_id="c1", placed_at=NOW)], now=NOW)
    assert (
        plan.delete == ("b",)
        and plan.kept == (("a", "LEGAL_HOLD:h1"),)
        and plan.outcome is r.Outcome.PARTIAL
    )
    assert r.due_for_deletion(old, SCHEDULE, [r.Hold("h2", tenant_wide=True)], now=NOW).delete == ()
    by_class = r.due_for_deletion(
        old, SCHEDULE, [r.Hold("h3", record_class="RAW_DOCUMENTS")], now=NOW
    )
    assert by_class.delete == () and len(by_class.kept) == 2


def test_a_released_hold_no_longer_holds_and_a_hold_that_covers_nothing_holds_nothing() -> None:
    old = [rec("a", "RAW_DOCUMENTS", 100)]
    released = r.Hold("h1", case_id="c1", released_at=NOW - timedelta(days=1))
    assert r.due_for_deletion(old, SCHEDULE, [released], now=NOW).delete == ("a",)
    assert r.due_for_deletion(old, SCHEDULE, [r.Hold("h0")], now=NOW).delete == (
        "a",
    )  # no scope given: it holds nothing rather than everything
    assert r.due_for_deletion(old, SCHEDULE, [r.Hold("h4", case_id="other")], now=NOW).delete == (
        "a",
    )


def test_an_erasure_request_deletes_what_it_may_and_says_what_stays_and_why() -> None:  # T24
    records = [
        rec("raw", "RAW_DOCUMENTS", 10),
        rec("ext", "EXTRACTED_DATA", 10),
        rec("dec", "DECISION_METADATA", 10),
        rec("other", "RAW_DOCUMENTS", 10, subject="p2"),
    ]
    plan, backups = r.erasure_plan(
        records,
        subject="p1",
        legal_minimum_days={"DECISION_METADATA": 2555},
        holds=[],
        now=NOW,
        backup_horizon_days=35,
    )
    assert plan.delete == ("raw", "ext") and plan.outcome is r.Outcome.PARTIAL
    assert plan.kept[0][0] == "dec" and plan.kept[0][1].startswith(
        "LEGAL_MINIMUM_RETENTION_UNTIL:2033-"
    )
    assert backups == NOW + timedelta(days=35)
    assert "other" not in plan.delete  # another person's record is not touched
    text = r.promise(plan)
    assert (
        "2 records will be deleted" in text
        and "1 must be kept" in text
        and "everything" not in text.lower()
    )


def test_a_hold_beats_an_erasure_request() -> None:
    plan, _ = r.erasure_plan(
        [rec("raw", "RAW_DOCUMENTS", 10)],
        subject="p1",
        legal_minimum_days={},
        holds=[r.Hold("h1", case_id="c1")],
        now=NOW,
        backup_horizon_days=35,
    )
    assert (
        plan.delete == ()
        and plan.kept == (("raw", "LEGAL_HOLD:h1"),)
        and plan.outcome is r.Outcome.PARTIAL
    )


def test_a_complete_erasure_is_complete_only_when_nothing_stays() -> None:
    plan, _ = r.erasure_plan(
        [rec("raw", "RAW_DOCUMENTS", 10)],
        subject="p1",
        legal_minimum_days={},
        holds=[],
        now=NOW,
        backup_horizon_days=35,
    )
    assert plan.outcome is r.Outcome.COMPLETE and "Copies leave our backups" in r.promise(plan)
    none, _ = r.erasure_plan(
        [], subject="p1", legal_minimum_days={}, holds=[], now=NOW, backup_horizon_days=35
    )
    assert none.outcome is r.Outcome.NOTHING_TO_ERASE and "no records" in r.promise(none)


def test_a_legal_minimum_that_has_passed_no_longer_keeps_the_record() -> None:
    plan, _ = r.erasure_plan(
        [rec("dec", "DECISION_METADATA", 3000)],
        subject="p1",
        legal_minimum_days={"DECISION_METADATA": 2555},
        holds=[],
        now=NOW,
        backup_horizon_days=1,
    )
    assert plan.delete == ("dec",) and plan.outcome is r.Outcome.COMPLETE
