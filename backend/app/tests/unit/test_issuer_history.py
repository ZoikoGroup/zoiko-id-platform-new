"""Issuer and accreditation history (ZID-RCG-003 RCG-G04; CR-075): dated statements with provenance, issuer at award time, corrections kept visible."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.issuer_history import (
    IssuerHistoryError,
    Kind,
    Statement,
    award_check,
    check_statement,
    issuer_of,
    known_issuer,
    registry_as_known_at,
    retroactive_changes,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
ID = "sample-university"


def record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "issuer_id": ID,
        "kind": "UNIVERSITY",
        "authority": "OFFICIAL",
        "countries": ["US"],
        "claim_types": ["ACADEMIC_AWARD"],
        "names": [
            {"name": "Sample State College", "until": "2015-06-30"},
            {"name": "Sample University", "from": "2015-07-01"},
        ],
        "accreditations": [
            {
                "body": "sample-accreditor",
                "program": "BSc Computing",
                "from": "2000-01-01",
                "until": "2012-12-31",
            }
        ],
    }
    base.update(over)
    return base


def stmt(
    seq: int = 1,
    *,
    ago: timedelta = timedelta(days=30),
    kind: Kind = Kind.UPDATE,
    reason: str | None = None,
    **over: Any,
) -> Statement:
    return Statement(
        statement_id=f"s{seq}",
        issuer_id=ID,
        seq=seq,
        kind=kind,
        reason=reason,
        record=over.pop("rec", record()),
        source_url="https://accreditor.example.test/entry/1",
        source_hash="a" * 64,
        evidence_locator="vault:issuers/x",
        recorded_by="reg-ops",
        recorded_at=NOW - ago,
        **over,
    )


def code_of(s: Statement, previous: list[Statement] | None = None) -> str:
    with pytest.raises(IssuerHistoryError) as e:
        check_statement(s, now=NOW, previous=previous or [])
    return e.value.code


def test_a_good_first_statement_passes_and_describes_the_issuer() -> None:
    assert check_statement(stmt(), now=NOW) == []
    assert issuer_of(stmt()).issuer_id == ID


def test_the_award_date_decides_accreditation_not_todays_status() -> None:
    history = [stmt()]
    during = award_check(
        history, program="BSc Computing", awarded_on=date(2010, 6, 1), known_at=NOW
    )
    after = award_check(history, program="BSc Computing", awarded_on=date(2014, 6, 1), known_at=NOW)
    assert (during["outcome"], during["accreditation"], during["name_at_award"]) == (
        "PASS",
        "ACCREDITED",
        "Sample State College",
    )
    assert (after["outcome"], after["accreditation"], after["reasons"]) == (
        "REVIEW",
        "NOT_ACCREDITED",
        ["NOT_ACCREDITED_AT_AWARD_DATE"],
    )
    assert after["name_at_award"] == "Sample State College"  # the name it held on that date
    later = award_check(history, program="BSc Computing", awarded_on=date(2016, 6, 1), known_at=NOW)
    assert later["name_at_award"] == "Sample University"  # the renamed school is the same school


def test_a_program_the_accreditation_does_not_name_is_not_covered() -> None:
    out = award_check([stmt()], program="BA History", awarded_on=date(2010, 6, 1), known_at=NOW)
    assert (out["outcome"], out["accreditation"]) == ("REVIEW", "NOT_ACCREDITED")


def test_an_issuer_nobody_recorded_is_review_never_a_pass() -> None:
    out = award_check([], program="BSc Computing", awarded_on=date(2010, 6, 1), known_at=NOW)
    assert (out["outcome"], out["reasons"]) == ("REVIEW", ["UNKNOWN_HISTORICAL_ISSUER"])
    before_any = award_check(
        [stmt()],
        program="BSc Computing",
        awarded_on=date(2010, 6, 1),
        known_at=NOW - timedelta(days=90),
    )
    assert before_any["outcome"] == "REVIEW"  # asked about a moment before anyone had recorded it


def test_an_unrecorded_authority_or_wrong_claim_type_or_revocation_is_review() -> None:
    unrecorded = stmt(rec=record(authority="UNRECORDED", claim_types=[]))
    assert (
        "AUTHORITY_NOT_RECORDED"
        in award_check(
            [unrecorded], program="BSc Computing", awarded_on=date(2010, 1, 1), known_at=NOW
        )["reasons"]
    )
    assert (
        "NOT_TRUSTED_FOR_CLAIM_TYPE"
        in award_check(
            [stmt()],
            program="BSc Computing",
            awarded_on=date(2010, 1, 1),
            known_at=NOW,
            claim_type="INSURANCE_ASSERTION",
        )["reasons"]
    )
    revoked = stmt(rec=record(revoked_on="2009-01-01"))
    assert (
        "ISSUER_REVOKED_AT_AWARD_DATE"
        in award_check(
            [revoked], program="BSc Computing", awarded_on=date(2010, 1, 1), known_at=NOW
        )["reasons"]
    )


def test_asking_about_a_past_moment_uses_what_was_known_then() -> None:
    first = stmt(1, ago=timedelta(days=60))
    wider = record(  # a later accreditation is a forward fact: it adds, it does not change what was said
        accreditations=record()["accreditations"]
        + [{"body": "other-accreditor", "from": "2019-01-01"}]
    )
    second = stmt(2, ago=timedelta(days=5), rec=wider)
    assert check_statement(second, now=NOW, previous=[first]) == []  # and it is a valid update
    history = [first, second]
    then = award_check(
        history,
        program="BSc Computing",
        awarded_on=date(2020, 1, 1),
        known_at=NOW - timedelta(days=30),
    )
    now = award_check(history, program="BSc Computing", awarded_on=date(2020, 1, 1), known_at=NOW)
    assert then["outcome"] == "REVIEW" and now["outcome"] == "PASS"
    assert known_issuer(history, NOW - timedelta(days=90)) is None


def test_a_record_that_is_not_an_issuer_for_that_id_is_refused() -> None:
    assert code_of(stmt(rec=record(issuer_id="someone-else"))) == "ISSUER_RECORD_INVALID"
    assert code_of(stmt(rec=record(kind="NOT_A_KIND"))) == "ISSUER_RECORD_INVALID"
    assert code_of(stmt(rec=record(names=[]))) == "ISSUER_RECORD_INVALID"
    assert code_of(replace(stmt(), issuer_id="Bad_ID")) == "ISSUER_ID_INVALID"


def test_a_statement_needs_its_official_source_proof_and_a_recorded_authority_to_be_trusted() -> (
    None
):
    assert (
        code_of(replace(stmt(), source_url="http://x.example.test/a"))
        == "ISSUER_SOURCE_URL_INVALID"
    )
    assert code_of(replace(stmt(), source_hash="nothex")) == "ISSUER_PROOF_REQUIRED"
    assert code_of(replace(stmt(), evidence_locator="")) == "ISSUER_EVIDENCE_REQUIRED"
    assert code_of(replace(stmt(), recorded_by=" ")) == "ISSUER_RECORDER_REQUIRED"
    assert (
        code_of(replace(stmt(), recorded_at=NOW + timedelta(hours=1))) == "ISSUER_FROM_THE_FUTURE"
    )
    assert code_of(replace(stmt(), recorded_at=datetime(2026, 1, 1))) == "ISSUER_TIME_INVALID"
    assert code_of(stmt(rec=record(authority="UNRECORDED"))) == "ISSUER_AUTHORITY_UNRECORDED"


def test_statements_rise_by_one_and_each_is_newer_than_the_last() -> None:
    first = stmt(1, ago=timedelta(days=30))
    assert code_of(stmt(3, ago=timedelta(days=1)), [first]) == "ISSUER_OUT_OF_ORDER"
    assert code_of(stmt(2, ago=timedelta(days=40)), [first]) == "ISSUER_OUT_OF_ORDER"


def test_going_forward_is_an_update_but_changing_the_past_is_a_correction_with_a_reason() -> None:
    first = stmt(1, ago=timedelta(days=30))
    # a new accreditation and a new name are additions
    more = record(
        names=record()["names"] + [{"name": "Sample Tech University", "from": "2030-01-01"}],
        accreditations=record()["accreditations"]
        + [{"body": "other-accreditor", "from": "2020-01-01"}],
    )
    assert (
        check_statement(stmt(2, ago=timedelta(days=1), rec=more), now=NOW, previous=[first]) == []
    )

    # shortening, removing or restarting what covered a past date is not an update
    shortened = deepcopy(record())
    shortened["accreditations"][0]["until"] = "2009-12-31"
    assert (
        code_of(stmt(2, ago=timedelta(days=1), rec=shortened), [first])
        == "ISSUER_STATEMENT_RETROACTIVE"
    )
    removed = record(accreditations=[])
    assert (
        code_of(stmt(2, ago=timedelta(days=1), rec=removed), [first])
        == "ISSUER_STATEMENT_RETROACTIVE"
    )

    # as a correction it needs a reason, and then it reports exactly what it changes
    assert (
        code_of(stmt(2, ago=timedelta(days=1), rec=shortened, kind=Kind.CORRECTION), [first])
        == "ISSUER_CORRECTION_REASON_REQUIRED"
    )
    fixed = stmt(
        2,
        ago=timedelta(days=1),
        rec=shortened,
        kind=Kind.CORRECTION,
        reason="the accreditor re-dated the lapse",
    )
    assert check_statement(fixed, now=NOW, previous=[first]) == [
        "accreditation_end_changed:sample-accreditor/BSc Computing"
    ]


def test_a_correction_with_nothing_to_correct_is_refused() -> None:
    first = stmt(1, ago=timedelta(days=30))
    nothing = stmt(
        2, ago=timedelta(days=1), kind=Kind.CORRECTION, reason="no earlier fact changes here"
    )
    assert code_of(nothing, [first]) == "ISSUER_NOTHING_TO_CORRECT"
    assert (
        code_of(stmt(1, kind=Kind.CORRECTION, reason="a correction of nothing at all"), [])
        == "ISSUER_NOTHING_TO_CORRECT"
    )


def test_retroactive_changes_name_each_kind() -> None:
    prev = issuer_of(stmt())
    ended_past = deepcopy(
        record(
            accreditations=[
                {"body": "sample-accreditor", "program": "BSc Computing", "from": "2000-01-01"}
            ]
        )
    )
    open_prev = issuer_of(stmt(rec=ended_past))
    closed_in_past = issuer_of(
        stmt(
            rec=record(
                accreditations=[
                    {
                        "body": "sample-accreditor",
                        "program": "BSc Computing",
                        "from": "2000-01-01",
                        "until": "2020-01-01",
                    }
                ]
            )
        )
    )
    assert retroactive_changes(open_prev, closed_in_past, known_on=date(2026, 11, 12)) == [
        "accreditation_ended_in_the_past:sample-accreditor/BSc Computing"
    ]
    closed_next_year = issuer_of(
        stmt(
            rec=record(
                accreditations=[
                    {
                        "body": "sample-accreditor",
                        "program": "BSc Computing",
                        "from": "2000-01-01",
                        "until": "2027-06-30",
                    }
                ]
            )
        )
    )
    assert (
        retroactive_changes(open_prev, closed_next_year, known_on=date(2026, 11, 12)) == []
    )  # ending it going forward is fine
    renamed = issuer_of(stmt(rec=record(names=[{"name": "Another Name"}])))
    assert retroactive_changes(prev, renamed, known_on=date(2026, 11, 12)) == [
        "name_removed:Sample State College",
        "name_removed:Sample University",
    ]
    backdated = issuer_of(stmt(rec=record(revoked_on="2020-01-01")))
    assert retroactive_changes(prev, backdated, known_on=date(2026, 11, 12)) == [
        "revocation_backdated"
    ]
    assert retroactive_changes(backdated, prev, known_on=date(2026, 11, 12)) == [
        "revocation_changed"
    ]


def test_the_ledger_builds_a_trust_graph_registry_as_known_at_a_moment() -> None:
    base = tg.parse(
        {
            "version": "base",
            "issuers": [
                {"issuer_id": "other", "kind": "REGULATOR", "names": [{"name": "Other Board"}]}
            ],
        }
    )
    histories = {ID: [stmt(1, ago=timedelta(days=30))]}
    now_registry = registry_as_known_at(histories, NOW, base)
    assert set(now_registry.issuers) == {
        "other",
        ID,
    }  # the ledger layers over the configured registry
    earlier = registry_as_known_at(histories, NOW - timedelta(days=60), base)
    assert set(earlier.issuers) == {"other"}
    # and the trust graph resolves a former name to the same issuer
    assert tg.resolve(now_registry, "Sample State College", on=date(2010, 1, 1)).issuer_id == ID
