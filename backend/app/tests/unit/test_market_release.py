"""Market release gates (ZID-RCG-003 RCG-G10): seven evidence gates, four independent approvals of the exact hash, a launch, and a market that
is open only while a launched release still covers the packs in force."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.verification.domain.market_release import (
    ATTESTOR_ROLES,
    GATES,
    RELEASE_APPROVER_ROLES,
    GateStatus,
    MarketKey,
    MarketRelease,
    PackRef,
    ReleaseError,
    ReleaseStatus,
    approve_release,
    compute_hash,
    current_results,
    freeze,
    launch,
    market_decision,
    new_release,
    record_gate,
    revise,
    withdraw,
)

AT = datetime(2026, 11, 12, tzinfo=UTC)
MARKET = MarketKey("GB", "INDIVIDUAL_VERIFICATION", "STANDARD")
PACK = PackRef("gb-individual", 1, "a" * 64)
RECORDERS = {
    "legal": "lena",
    "regulatory_ops": "ravi",
    "security": "sam",
    "privacy": "pia",
    "qa": "quinn",
    "vendor_mgmt": "vera",
}
APPROVERS = {"legal": "al", "security_grc": "gus", "qa": "qa-lead", "product_owner": "pat"}


def _record(
    r: MarketRelease,
    gate: str,
    *,
    status: GateStatus = GateStatus.PASSED,
    evidence: str = "EVID-1",
    role: str | None = None,
    by: str | None = None,
) -> MarketRelease:
    spec_role = next(g.owner_role for g in GATES if g.gate_id == gate)
    return record_gate(
        r,
        gate=gate,
        status=status,
        evidence_ref=evidence,
        role=role or spec_role,
        by=by or RECORDERS[spec_role],
        at=AT,
    )


def _all_gates(r: MarketRelease) -> MarketRelease:
    for g in GATES:
        r = _record(r, g.gate_id, evidence=f"EVID-{g.gate_id}")
    return r


def _frozen(refs: tuple[PackRef, ...] = (PACK,)) -> MarketRelease:
    return freeze(_all_gates(new_release("rel-gb-iv", MARKET, author="maker")), refs)


def _approved(r: MarketRelease | None = None) -> MarketRelease:
    r = r or _frozen()
    for role in RELEASE_APPROVER_ROLES:
        r = approve_release(
            r, role=role, approver=APPROVERS[role], signed_hash=r.release_hash or ""
        )
    return r


def _code(fn: object) -> str:
    with pytest.raises(ReleaseError) as e:
        fn()  # type: ignore[operator]
    return e.value.code


# ---- the gates ---------------------------------------------------------------------------------------------------------


def test_the_seven_evidence_gates_of_the_document_each_have_an_owner_role() -> None:
    assert [g.gate_id for g in GATES] == [f"RG-0{i}" for i in range(1, 8)]
    assert {g.owner_role for g in GATES} <= set(ATTESTOR_ROLES)
    assert RELEASE_APPROVER_ROLES == ("legal", "security_grc", "qa", "product_owner")  # RG-08


def test_a_gate_is_attested_by_its_own_role_and_never_by_the_author() -> None:
    r = new_release("rel", MARKET, author="maker")
    assert _code(lambda: _record(r, "RG-01", role="qa", by="quinn")) == "RELEASE_ROLE_MISMATCH"
    assert _code(lambda: _record(r, "RG-01", by="maker")) == "RELEASE_RECORDER_IS_AUTHOR"
    assert (
        _code(
            lambda: record_gate(
                r,
                gate="RG-99",
                status=GateStatus.PASSED,
                evidence_ref="EVID-1",
                role="legal",
                by="lena",
                at=AT,
            )
        )
        == "RELEASE_GATE_UNKNOWN"
    )


@pytest.mark.parametrize("ref", ["", "  ", "ab", "has spaces in it", "x" * 300])
def test_a_gate_result_points_at_evidence_by_reference(ref: str) -> None:
    assert (
        _code(lambda: _record(new_release("rel", MARKET, author="maker"), "RG-01", evidence=ref))
        == "RELEASE_EVIDENCE_REQUIRED"
    )


def test_a_later_result_supersedes_the_earlier_one_and_the_history_is_kept() -> None:
    r = _record(new_release("rel", MARKET, author="maker"), "RG-03", evidence="EVID-A")
    r = _record(r, "RG-03", status=GateStatus.FAILED, evidence="EVID-B")
    r = _record(r, "RG-03", evidence="EVID-C")
    assert len(r.results) == 3
    assert (current_results(r)["RG-03"].evidence_ref, current_results(r)["RG-03"].status) == (
        "EVID-C",
        GateStatus.PASSED,
    )


# ---- freezing -----------------------------------------------------------------------------------------------------------


def test_a_release_with_a_gate_missing_or_failed_cannot_be_frozen() -> None:
    r = new_release("rel", MARKET, author="maker")
    for g in GATES[:-2]:
        r = _record(r, g.gate_id)
    r = _record(r, "RG-06", status=GateStatus.FAILED)
    with pytest.raises(ReleaseError) as e:
        freeze(r, (PACK,))
    assert e.value.code == "RELEASE_NOT_FREEZABLE"
    assert "RELEASE_GATE_FAILED:RG-06" in str(e.value) and "RELEASE_GATE_MISSING:RG-07" in str(
        e.value
    )


def test_no_approved_pack_means_nothing_to_release_even_with_every_gate_passed() -> None:
    with pytest.raises(ReleaseError) as e:
        freeze(_all_gates(new_release("rel", MARKET, author="maker")), ())
    assert "RELEASE_NO_APPROVED_PACK" in str(e.value)


def test_freezing_pins_the_packs_and_the_evidence_in_one_hash() -> None:
    f = _frozen()
    assert (
        f.status is ReleaseStatus.FROZEN
        and f.release_hash == compute_hash(f)
        and f.pack_refs == (PACK,)
    )
    other_pack = _frozen((PackRef("gb-individual", 1, "b" * 64),))
    assert other_pack.release_hash != f.release_hash  # a different pack hash is a different release
    r = new_release("rel-gb-iv", MARKET, author="maker")
    for g in GATES:
        r = _record(
            r, g.gate_id, evidence="EVID-CHANGED" if g.gate_id == "RG-04" else f"EVID-{g.gate_id}"
        )
    assert (
        freeze(r, (PACK,)).release_hash != f.release_hash
    )  # different evidence behind a gate is a different release


def test_the_hash_does_not_depend_on_the_order_the_packs_were_given() -> None:
    p2 = PackRef("gb-nurse", 2, "c" * 64)
    assert _frozen((PACK, p2)).release_hash == _frozen((p2, PACK)).release_hash


def test_a_frozen_release_takes_no_more_gate_results() -> None:
    assert _code(lambda: _record(_frozen(), "RG-01", evidence="EVID-LATE")) == "RELEASE_NOT_DRAFT"


# ---- RG-08: independent approvals -----------------------------------------------------------------------------------------


def test_the_author_and_anyone_who_recorded_evidence_cannot_approve() -> None:
    f = _frozen()
    h = f.release_hash or ""
    assert (
        _code(lambda: approve_release(f, role="legal", approver="maker", signed_hash=h))
        == "RELEASE_APPROVER_IS_AUTHOR"
    )
    for evidence_person in RECORDERS.values():
        assert (
            _code(
                lambda p=evidence_person: approve_release(
                    f, role="legal", approver=p, signed_hash=h
                )
            )
            == "RELEASE_APPROVER_NOT_INDEPENDENT"
        )


def test_one_person_holds_one_approval_and_it_must_sign_the_exact_hash() -> None:
    f = _frozen()
    h = f.release_hash or ""
    one = approve_release(f, role="legal", approver="al", signed_hash=h)
    assert (
        _code(lambda: approve_release(one, role="qa", approver="al", signed_hash=h))
        == "RELEASE_APPROVER_NOT_DISTINCT"
    )
    assert (
        _code(lambda: approve_release(f, role="qa", approver="qa-lead", signed_hash="0" * 64))
        == "RELEASE_APPROVAL_HASH_MISMATCH"
    )
    assert (
        _code(lambda: approve_release(f, role="intern", approver="x", signed_hash=h))
        == "RELEASE_APPROVER_ROLE_UNKNOWN"
    )


def test_nobody_approves_a_release_that_is_not_frozen() -> None:
    assert (
        _code(
            lambda: approve_release(
                new_release("rel", MARKET, author="maker"),
                role="legal",
                approver="al",
                signed_hash="x",
            )
        )
        == "RELEASE_NOT_FROZEN"
    )


def test_three_approvals_are_not_enough_and_the_fourth_makes_it_releaseable() -> None:
    f = _frozen()
    h = f.release_hash or ""
    r = f
    for role in ("legal", "security_grc", "qa"):
        r = approve_release(r, role=role, approver=APPROVERS[role], signed_hash=h)
        assert r.status is ReleaseStatus.FROZEN
    assert (
        approve_release(r, role="product_owner", approver="pat", signed_hash=h).status
        is ReleaseStatus.RELEASEABLE
    )


# ---- launch, withdraw, revise ----------------------------------------------------------------------------------------------


def test_only_a_releaseable_release_can_be_launched_and_withdrawing_closes_it() -> None:
    assert _code(lambda: launch(_frozen())) == "RELEASE_NOT_RELEASEABLE"
    assert _code(lambda: withdraw(_frozen())) == "RELEASE_NOT_WITHDRAWABLE"
    live = launch(_approved())
    assert live.status is ReleaseStatus.LAUNCHED
    assert withdraw(live).status is ReleaseStatus.WITHDRAWN


def test_a_revision_is_a_new_draft_with_nothing_carried_over() -> None:
    live = launch(_approved())
    v2 = revise(live, author="maker2")
    assert (
        v2.version,
        v2.status,
        v2.results,
        dict(v2.approvals),
        v2.release_hash,
        v2.pack_refs,
    ) == (2, ReleaseStatus.DRAFT, (), {}, None, ())
    assert (
        live.status is ReleaseStatus.LAUNCHED and live.release_hash
    )  # the launched version is untouched
    assert _code(lambda: revise(v2, author="m3")) == "RELEASE_DRAFT_OPEN"


# ---- the market ---------------------------------------------------------------------------------------------------------------


def test_a_market_is_open_only_while_a_launched_release_covers_the_packs_in_force() -> None:
    none = market_decision([], MARKET, [PACK])
    assert (none.open, none.reason, none.release) == (False, "no_release", None)
    assert not market_decision([_approved()], MARKET, [PACK]).open  # approved is not launched
    assert market_decision([_approved()], MARKET, [PACK]).reason == "not_launched"
    live = launch(_approved())
    assert market_decision([live], MARKET, [PACK]).open
    assert market_decision([withdraw(live)], MARKET, [PACK]).reason == "withdrawn"


def test_t03_a_pack_published_or_changed_after_the_release_closes_the_market_until_it_is_released_again() -> (
    None
):
    live = launch(_approved())
    changed = PackRef("gb-individual", 2, "d" * 64)
    d = market_decision([live], MARKET, [changed])
    assert (d.open, d.reason) == (False, "pack_changed_after_release")
    extra = PackRef("gb-nurse", 1, "e" * 64)
    assert (
        market_decision([live], MARKET, [PACK, extra]).reason == "pack_changed_after_release"
    )  # a pack the release never saw


def test_retiring_a_pack_does_not_invalidate_a_release_that_pinned_more() -> None:
    p2 = PackRef("gb-nurse", 1, "f" * 64)
    live = launch(_approved(_frozen((PACK, p2))))
    assert market_decision([live], MARKET, [PACK]).open  # p2 retired: what is left is still covered


def test_the_highest_launched_version_decides_and_other_markets_are_ignored() -> None:
    v1 = launch(_approved())
    other = launch(
        _approved(
            freeze(
                _all_gates(
                    new_release(
                        "rel-fr",
                        MarketKey("FR", "INDIVIDUAL_VERIFICATION", "STANDARD"),
                        author="maker",
                    )
                ),
                (PACK,),
            )
        )
    )
    assert market_decision([other], MARKET, [PACK]).reason == "no_release"
    assert market_decision([v1, other], MARKET, [PACK]).release == v1
