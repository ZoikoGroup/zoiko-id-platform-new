"""WF06 governed source release (CR-113): quarantine, two approvers, rollback, publisher pinning, the UK legacy list (QA-001, QA-004 to QA-011)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import source_release as sr

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
OFAC = "https://sanctionslistservice.ofac.treas.gov/api/download/SDN.XML"


def receipt(**kw):
    base = {
        "source_id": "OFAC_SDN",
        "url": OFAC,
        "sha256": "a" * 64,
        "fetched_at": T0,
        "published_at": T0 - timedelta(hours=2),
        "item_count": 1000,
        "schema_valid": True,
    }
    base.update(kw)
    return sr.Receipt(**base)


def check(r, active_count=1000, active_sha=None):
    return sr.validate(r, active_count=active_count, active_sha256=active_sha)


def staged(rid="r1", by="stager", **kw):
    r = receipt(**kw)
    return sr.stage(rid, r, staged_by=by, validation=check(r))


def test_a_clean_file_from_the_publisher_is_validated_with_no_flags() -> None:
    v = check(receipt())
    assert v.state is sr.State.VALIDATED and v.blocked == () and v.flags == ()  # QA-001


def test_a_source_not_in_the_register_or_a_legacy_source_is_quarantined() -> None:
    assert check(receipt(source_id="MADE_UP")).blocked == ("SOURCE_NOT_REGISTERED",)
    legacy = check(
        receipt(source_id="OFSI_CONSOLIDATED", url="https://ofsi.hmtreasury.gov.uk/x.csv")
    )
    assert (
        "LEGACY_SOURCE_REJECTED" in legacy.blocked and legacy.state is sr.State.QUARANTINED
    )  # QA-004


def test_the_uk_list_published_before_the_cutoff_is_rejected_as_a_stale_legacy_file() -> None:
    uk = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
    old = check(
        receipt(source_id="UK_SANCTIONS", url=uk, published_at=datetime(2026, 1, 20, tzinfo=UTC))
    )
    assert "PUBLISHED_BEFORE_CURRENT_LIST" in old.blocked
    new = check(
        receipt(source_id="UK_SANCTIONS", url=uk, published_at=datetime(2026, 11, 1, tzinfo=UTC))
    )
    assert new.blocked == ()


@pytest.mark.parametrize(
    "url,reason",
    [
        ("http://sanctionslistservice.ofac.treas.gov/SDN.XML", "URL_NOT_HTTPS"),
        (
            "https://mirror.example.com/SDN.XML",
            "HOST_NOT_THE_PUBLISHERS",
        ),  # QA-007: an unofficial redirect target
        (
            "https://sanctionslistservice.ofac.treas.gov.evil.example/SDN.XML",
            "HOST_NOT_THE_PUBLISHERS",
        ),
        ("https://user:pw@sanctionslistservice.ofac.treas.gov/SDN.XML", "URL_HAS_CREDENTIALS"),
    ],
)
def test_an_address_off_the_publisher_is_quarantined(url: str, reason: str) -> None:
    assert reason in check(receipt(url=url)).blocked


def test_a_checksum_mismatch_or_a_bad_schema_is_quarantined() -> None:
    assert "CHECKSUM_MISMATCH" in check(receipt(advertised_sha256="b" * 64)).blocked  # QA-007
    assert check(receipt(advertised_sha256="A" * 64)).blocked == ()  # case is not a difference
    assert (
        "SCHEMA_INVALID" in check(receipt(schema_valid=False)).blocked
    )  # QA-006: a truncated file


def test_zero_rows_and_a_large_drift_are_flags_two_people_must_acknowledge() -> None:
    assert check(receipt(item_count=0)).flags == ("ZERO_ROWS",)  # QA-008
    assert check(receipt(item_count=1500)).flags == ("COUNT_DRIFT_ABOVE_LIMIT",)
    assert check(receipt(item_count=700)).flags == ("COUNT_DRIFT_ABOVE_LIMIT",)
    assert check(receipt(item_count=1100)).flags == ()
    assert (
        check(receipt(item_count=5), active_count=None).flags == ()
    )  # the first release has nothing to drift from


def test_the_same_file_again_is_flagged_as_a_refresh_not_a_change() -> None:
    v = check(receipt(), active_sha="a" * 64)
    assert v.flags == ("IDENTICAL_TO_ACTIVE",)


def test_the_stager_cannot_approve_and_nobody_approves_twice() -> None:
    r = staged()
    with pytest.raises(sr.ReleaseError) as e:
        sr.approve(r, approver="stager", acknowledged=[], at=T0)
    assert e.value.code == "APPROVER_STAGED_THE_RELEASE"
    r = sr.approve(r, approver="p1", acknowledged=[], at=T0)
    with pytest.raises(sr.ReleaseError) as e2:
        sr.approve(r, approver="p1", acknowledged=[], at=T0)
    assert e2.value.code == "APPROVER_ALREADY_APPROVED"


def test_one_approval_is_not_enough_and_a_quarantined_release_is_never_publishable() -> None:
    r = sr.approve(staged(), approver="p1", acknowledged=[], at=T0)
    assert sr.publishable(r) == ["TWO_APPROVERS_REQUIRED"]
    bad = staged(schema_valid=False)
    with pytest.raises(sr.ReleaseError) as e:
        sr.approve(bad, approver="p1", acknowledged=[], at=T0)
    assert e.value.code == "RELEASE_NOT_APPROVABLE"
    with pytest.raises(sr.ReleaseError):
        sr.publish([bad], "r1", at=T0)


def test_every_flag_must_be_acknowledged_by_name_by_each_approver() -> None:
    r = staged(item_count=0)
    with pytest.raises(sr.ReleaseError) as e:
        sr.approve(r, approver="p1", acknowledged=[], at=T0)
    assert e.value.code == "FLAGS_NOT_ACKNOWLEDGED"
    r = sr.approve(r, approver="p1", acknowledged=["ZERO_ROWS"], at=T0)
    r = sr.approve(r, approver="p2", acknowledged=["ZERO_ROWS"], at=T0)
    assert sr.publishable(r) == []


def test_two_independent_approvers_publish_and_the_release_before_is_superseded_not_deleted() -> (
    None
):
    first = sr.publish([_approved("r1")], "r1", at=T0)
    assert sr.active(first, "OFAC_SDN").release_id == "r1"
    second_in = [*first, _approved("r2", sha="c" * 64)]
    both = sr.publish(second_in, "r2", at=T0 + timedelta(days=1))
    assert sr.active(both, "OFAC_SDN").release_id == "r2"
    old = next(r for r in both if r.release_id == "r1")
    assert old.state is sr.State.SUPERSEDED and old.superseded_at == T0 + timedelta(
        days=1
    )  # QA-009: one active snapshot
    assert sum(r.state is sr.State.ACTIVE for r in both) == 1


def test_publishing_a_release_for_one_source_leaves_another_sources_active_release_alone() -> None:
    a = sr.publish([_approved("r1")], "r1", at=T0)
    un = _approved(
        "u1", source_id="UN_SC", url="https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    )
    both = sr.publish([*a, un], "u1", at=T0)
    assert sr.active(both, "OFAC_SDN") is not None and sr.active(both, "UN_SC") is not None


def test_rollback_needs_two_people_who_did_not_stage_it_and_supersedes_the_bad_release() -> None:
    h = sr.publish([_approved("r1")], "r1", at=T0)
    h = sr.publish([*h, _approved("r2", sha="c" * 64)], "r2", at=T0 + timedelta(days=1))
    with pytest.raises(sr.ReleaseError) as e:
        sr.rollback(h, "r1", approvers=["p1"], at=T0)
    assert e.value.code == "TWO_APPROVERS_REQUIRED"
    with pytest.raises(sr.ReleaseError) as e2:
        sr.rollback(h, "r1", approvers=["stager", "p9"], at=T0)
    assert e2.value.code == "APPROVER_STAGED_THE_RELEASE"
    back = sr.rollback(h, "r1", approvers=["p8", "p9"], at=T0 + timedelta(days=2))  # QA-011
    assert sr.active(back, "OFAC_SDN").release_id == "r1"
    assert next(r for r in back if r.release_id == "r2").state is sr.State.SUPERSEDED
    with pytest.raises(sr.ReleaseError):
        sr.rollback(
            h, "r2", approvers=["p8", "p9"], at=T0
        )  # the active one is not a rollback target


def test_freshness_follows_the_release_not_the_last_fetch_attempt() -> None:
    h = sr.publish([_approved("r1")], "r1", at=T0)
    age = timedelta(hours=48)
    assert (
        sr.health(h, "OFAC_SDN", now=T0 + timedelta(hours=47), max_age=age).freshness
        is sr.Freshness.FRESH
    )
    stale = sr.health(h, "OFAC_SDN", now=T0 + timedelta(hours=49), max_age=age)
    assert stale.freshness is sr.Freshness.STALE and stale.last_validated_at == T0  # QA-010
    never = sr.health(
        [_approved("r1")], "OFAC_SDN", now=T0, max_age=age
    )  # approved, never published
    assert never.freshness is sr.Freshness.NEVER_RELEASED and never.snapshot is None
    assert sr.health(h, "UN_SC", now=T0, max_age=age).freshness is sr.Freshness.NEVER_RELEASED


def test_publishing_an_unknown_release_is_refused() -> None:
    with pytest.raises(sr.ReleaseError) as e:
        sr.publish([], "nope", at=T0)
    assert e.value.code == "RELEASE_NOT_FOUND"


def _approved(rid, *, source_id="OFAC_SDN", url=OFAC, sha="a" * 64):
    r = receipt(source_id=source_id, url=url, sha256=sha)
    rel = sr.stage(rid, r, staged_by="stager", validation=check(r))
    rel = sr.approve(rel, approver="p1", acknowledged=[], at=T0)
    return sr.approve(rel, approver="p2", acknowledged=[], at=T0)


def test_rollback_approvals_are_stored_on_the_target_and_spent_by_the_rollback() -> None:
    h = sr.publish([_approved("r1")], "r1", at=T0)
    h = sr.publish([*h, _approved("r2", sha="c" * 64)], "r2", at=T0 + timedelta(days=1))
    target = next(r for r in h if r.release_id == "r1")
    with pytest.raises(sr.ReleaseError) as e:
        sr.approve_rollback(target, approver="stager", at=T0)
    assert e.value.code == "APPROVER_STAGED_THE_RELEASE"
    a = sr.approve_rollback(target, approver="p8", at=T0)
    with pytest.raises(sr.ReleaseError) as dup:
        sr.approve_rollback(a, approver="p8", at=T0)
    assert dup.value.code == "APPROVER_ALREADY_APPROVED"
    b = sr.approve_rollback(a, approver="p9", at=T0)
    assert sr.rollback_approvers(b) == ["p8", "p9"]
    with pytest.raises(sr.ReleaseError) as active:
        sr.approve_rollback(next(r for r in h if r.release_id == "r2"), approver="p8", at=T0)
    assert active.value.code == "ROLLBACK_TARGET_INVALID"  # only a superseded release is a target
    history = [b if r.release_id == "r1" else r for r in h]
    back = sr.rollback(history, "r1", approvers=sr.rollback_approvers(b), at=T0 + timedelta(days=2))
    restored = next(r for r in back if r.release_id == "r1")
    assert restored.state is sr.State.ACTIVE and sr.rollback_approvers(restored) == []  # spent
    assert {a.kind for a in restored.approvals} == {"PUBLISH"}


def test_a_rollback_approval_does_not_count_as_a_publish_approval() -> None:
    r = sr.stage("r9", receipt(), staged_by="stager", validation=check(receipt()))
    r = sr.approve(r, approver="p1", acknowledged=[], at=T0)
    assert sr.publishable(r) == ["TWO_APPROVERS_REQUIRED"]
