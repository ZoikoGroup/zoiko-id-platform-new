"""Organization readiness (ZID-UX-WF-013C screen 1) as a pure decision: derived state, plain status words, and what can never open activation."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from zid_platform.modules.onboarding.domain.readiness import (
    Readiness,
    Setup,
    TrustFact,
    project,
)

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
GOOD = Setup(True, True, True, 2)


def facts(**states: str) -> list[TrustFact]:
    base = {
        "company": "COMPLETED",
        "person_assurance": "COMPLETED",
        "authority": "COMPLETED",
        "risk": "COMPLETED",
    }
    base.update(states)
    return [TrustFact(k, v, started=True) for k, v in base.items()]


def run(
    trust: list[TrustFact],
    setup: Setup = GOOD,
    *,
    claim: bool = True,
    activated: bool = False,
    ack: bool = True,
):
    return project(
        trust, setup, claim_started=claim, activated=activated, any_acknowledgement=ack, now=NOW
    )


def test_the_state_is_derived_from_facts_and_walks_forward() -> None:
    assert run([], Setup(False, False, False, 0), claim=False, ack=False).state is Readiness.DRAFT
    assert run(facts(authority="PENDING"), GOOD).state is Readiness.LIMITED_SETUP
    assert (
        run(facts(), Setup(False, True, True, 2)).state is Readiness.LIMITED_SETUP
    )  # trust done, a required item not
    ready = run(facts())
    assert (
        ready.state is Readiness.READY_FOR_ACTIVATION
        and ready.can_activate
        and ready.blockers == ()
    )
    done = run(facts(), activated=True)
    assert (
        done.state is Readiness.ACTIVE and not done.can_activate
    )  # already active: nothing more to activate


def test_a_declined_check_restricts_but_a_held_or_stale_one_is_remediation() -> None:
    declined = run(facts(risk="DECLINED"))
    assert declined.state is Readiness.RESTRICTED and not declined.can_activate
    assert [i.word for i in declined.trust if i.key == "risk"] == ["Restricted"]
    held = run(facts(company="HELD"))
    assert (
        held.state is Readiness.LIMITED_SETUP and not held.can_activate
    )  # held is "In review", never "Verified"
    assert [i.word for i in held.trust if i.key == "company"] == ["In review"]
    stale = project(
        [TrustFact("company", "COMPLETED", NOW - timedelta(days=1), True), *facts()[1:]],
        GOOD, claim_started=True, activated=False, any_acknowledgement=True, now=NOW,
    )  # fmt: skip
    assert stale.state is Readiness.LIMITED_SETUP and [
        i.word for i in stale.trust if i.key == "company"
    ] == ["Needs action"]
    # an activated organization whose evidence went stale is limited, not still fully active
    lapsed = project(
        [TrustFact("company", "COMPLETED", NOW - timedelta(days=1), True), *facts()[1:]],
        GOOD, claim_started=True, activated=True, any_acknowledgement=True, now=NOW,
    )  # fmt: skip
    assert lapsed.state is Readiness.ACTIVE_LIMITED


def test_an_unknown_or_missing_fact_is_never_verified() -> None:
    odd = run([TrustFact("company", "SOMETHING_NEW"), *facts()[1:]])
    assert [i.word for i in odd.trust if i.key == "company"] != [
        "Verified"
    ] and not odd.can_activate
    missing = run(
        [TrustFact("company", "COMPLETED", started=True)]
    )  # the other three were never read
    assert not missing.can_activate and sum(1 for i in missing.trust if i.done) == 1
    pending_started = run(facts(authority="PENDING"))
    assert [i.word for i in pending_started.trust if i.key == "authority"] == ["In review"]
    pending_new = run(
        [TrustFact(k, "PENDING") for k in ("company", "person_assurance", "authority", "risk")],
        claim=False,
    )
    assert {i.word for i in pending_new.trust} == {"Required"}


def test_approvers_are_recommended_and_never_block_ordinary_use_or_activation() -> None:
    solo = run(facts(), Setup(True, True, True, 0))
    assert (
        solo.solo and solo.can_activate and solo.state is Readiness.READY_FOR_ACTIVATION
    )  # absence does not block A0 use
    assert solo.approvers.required is False and solo.approvers.reason.startswith(
        "You can manage your organization on your own."
    )
    assert "second user" not in (solo.approvers.reason or "").lower()  # no fake reviewer
    one = run(facts(), Setup(True, True, True, 1))
    assert (
        not one.solo
        and "two independent approvers" in (one.approvers.reason or "")
        and not one.approvers.done
    )
    assert run(facts(), Setup(True, True, True, 2)).approvers.done


def test_required_items_are_server_derived_and_block_activation_with_a_reason_and_a_next_action() -> (
    None
):
    p = run(facts(), Setup(False, False, False, 2, privileged_without_passkey=2))
    assert not p.can_activate and len(p.blockers) == 3
    by = {i.key: i for i in p.required}
    assert by["privileged_account"].word == "Needs action" and "2 privileged account(s)" in (
        by["privileged_account"].reason or ""
    )
    assert all(i.next_action for i in p.required)
    assert [i.word for i in p.required] == ["Required", "Needs action", "Required"]


def test_nothing_about_payment_or_plans_can_reach_the_decision() -> None:
    """AT-02 and AT-15: the only inputs are trust facts and setup facts, so no plan, bill or toggle can open the gate."""
    params = set(inspect.signature(project).parameters)
    assert params == {"trust", "setup", "claim_started", "activated", "any_acknowledgement", "now"}
    assert set(inspect.signature(Setup).parameters) == {
        "controls_accepted", "privileged_accounts_secured", "region_policy_confirmed", "independent_approvers", "privileged_without_passkey",
    }  # fmt: skip
