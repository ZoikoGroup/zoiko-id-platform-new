"""The approval tiers, independence and quorum (ZID-ENG-DEC-010, 011, 012): the record's rules as a pure decision, and the registry that
classifies every action. Database behaviour is in tests/component/test_approvals_pg.py."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.domain.approvals import (
    RECORD_A2,
    RECORD_NEVER,
    TERMINAL,
    TRANSITIONS,
    ApprovalError,
    Authority,
    Ballot,
    CaseFacts,
    Scope,
    State,
    Tier,
    Voter,
    build_registry,
    canonical_payload,
    governance_hold,
    move,
    organization_governance,
    request_hash,
    state_after,
    tally,
    voter_refusals,
)
from zid_platform.modules.tenant.infrastructure import approvals as store
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
LONG_AGO = T0 - timedelta(days=30)


def _doc() -> dict:
    return yaml.safe_load((CONFIG / "approvals" / "action-tiers.yaml").read_text(encoding="utf-8"))


def _reg():
    return store.registry()


def _facts(action: str = "ownership-transfer-or-replacement", **kw) -> CaseFacts:
    base: dict = {
        "requester_person": "p-req",
        "editor_persons": frozenset({"p-req"}),
        "beneficiary_person": None,
        "created_at": T0,
        "policy": _reg().classify(action),
    }
    base.update(kw)
    return CaseFacts(**base)


def _owner(
    person: str, *, since=LONG_AGO, granted_by=None, approved_by=None, human=True, role="OWNER"
) -> Voter:
    return Voter(
        f"acct-{person}", person, human, (Authority(role, since, granted_by, approved_by),)
    )


# ------------------------------------------------------------------------------------------- the registry


def test_the_shipped_registry_loads_and_holds_the_records_lists() -> None:
    reg = _reg()
    assert reg.policy_version.startswith("ZID-ENG-DEC-010-011-012-v1.0#")
    a2 = {a.action_id for a in reg.actions.values() if a.tier is Tier.A2}
    assert (
        RECORD_A2 <= a2 and len(RECORD_A2) == 18
    )  # nine customer, eight platform, and re-enabling after an incident
    assert {a.action_id for a in reg.actions.values() if a.tier is Tier.FORBIDDEN} == set(
        RECORD_NEVER
    )
    assert reg.classify("ownership-transfer-or-replacement").required_votes == 2
    assert reg.classify("appoint-independent-reviewer").required_votes == 1
    assert reg.classify("update-organization-presentation-details").required_votes == 0


def test_an_action_nobody_classified_is_refused_not_treated_as_routine() -> None:
    with pytest.raises(ApprovalError) as e:
        _reg().classify("some-new-sensitive-thing")
    assert e.value.code == "APPROVAL_ACTION_UNKNOWN"


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda d: _set(d, "ownership-transfer-or-replacement", tier="A1"), "weakened"),
        (
            lambda d: _set(d, "disable-core-audit", tier="A1", approvers=["OWNER"]),
            "forbidden action made votable",
        ),
        (
            lambda d: _set(d, "disable-core-audit", executor="owner.add"),
            "forbidden action given an executor",
        ),
        (
            lambda d: _set(d, "suspend-tenant-access", approvers=["OWNER"]),
            "containment queued behind a vote",
        ),
        (
            lambda d: _set(d, "suspend-tenant-access", executor=None),
            "containment with nothing to run",
        ),
        (
            lambda d: _set(
                d,
                "manual-replacement-of-signing-keys-or-trust-anchors",
                approvers=["OWNER", "REVIEWER"],
            ),
            "customer reviewer votes a platform action",
        ),
        (
            lambda d: _set(d, "privileged-role-grant-or-elevation", approvers=[]),
            "voted action with no approvers",
        ),
        (
            lambda d: _set(d, "privileged-role-grant-or-elevation", approvers=["EMPLOYEE"]),
            "unknown approver role",
        ),
        (lambda d: d["actions"].append(copy.deepcopy(d["actions"][0])), "listed twice"),
        (
            lambda d: d["actions"].__delitem__(
                next(
                    i
                    for i, a in enumerate(d["actions"])
                    if a["id"] == "disable-mandatory-multi-factor"
                )
            ),
            "a never-action removed",
        ),
    ],
)
def test_a_registry_that_breaks_the_record_does_not_load(mutate, why) -> None:
    doc = copy.deepcopy(_doc())
    mutate(doc)
    with pytest.raises(ApprovalError) as e:
        build_registry(doc, "x" * 64)
    assert e.value.code == "REGISTRY_INVALID", why


def _set(doc: dict, action: str, **changes) -> None:
    row = next(a for a in doc["actions"] if a["id"] == action)
    row.update(changes)


def test_every_voted_action_has_an_executor_or_says_why_none_can_exist() -> None:
    import zid_platform.approval_wiring  # noqa: F401  (the composition root registers the agent executor)

    reg, wired = _reg(), svc.executor_names()
    named = {a.executor for a in reg.actions.values() if a.executor}
    assert named <= wired, named - wired
    voted = [a for a in reg.actions.values() if a.tier in (Tier.A1, Tier.A2)]
    bare = {a.action_id for a in voted if a.executor is None}
    # nothing is approved that cannot run, and "not built yet" is not a reason: each of these has a stated platform rule or no capability
    assert bare == {
        "weaken-authentication-recovery-or-approval-policy",
        "credential-issuer-or-authority-root-change",
        "organization-wide-sensitive-export-or-impersonation",
        "issue-or-change-delegated-authority",
    }
    assert all(a.no_operation for a in voted if a.executor is None)
    assert all(a.executor == "platform.authorize" for a in voted if a.scope is Scope.PLATFORM)


def test_a_voted_action_with_no_executor_must_say_why_and_the_two_cannot_disagree() -> None:
    doc = copy.deepcopy(_doc())
    row = next(
        a for a in doc["actions"] if a["id"] == "weaken-authentication-recovery-or-approval-policy"
    )
    row.pop("no_operation")
    with pytest.raises(ApprovalError):
        build_registry(doc, "x" * 64)
    doc = copy.deepcopy(_doc())
    next(a for a in doc["actions"] if a["id"] == "ownership-transfer-or-replacement")[
        "no_operation"
    ] = "x"
    with pytest.raises(ApprovalError):
        build_registry(doc, "x" * 64)


# ------------------------------------------------------------------------------ the immutable request


def test_the_hash_binds_everything_a_vote_was_for() -> None:
    p = canonical_payload({"principal_id": "x", "n": 1})
    base = {
        "tenant_id": "t",
        "action_id": "a",
        "target_id": "tgt",
        "payload_json": p,
        "policy_version": "v1",
        "region": "uk-1",
    }
    h = request_hash(**base)
    assert h == request_hash(**base) and len(h) == 64
    for key, other in {
        "tenant_id": "t2", "action_id": "a2", "target_id": "tgt2", "policy_version": "v2", "region": "us-1",
        "payload_json": canonical_payload({"principal_id": "y", "n": 1}),
    }.items():  # fmt: skip
        assert request_hash(**{**base, key: other}) != h, key


def test_a_payload_has_one_reading() -> None:
    assert canonical_payload({"b": 1, "a": [1, "x", None, True]}) == canonical_payload(
        {"a": [1, "x", None, True], "b": 1}
    )
    for bad in ({"x": 1.5}, {"x": b"bytes"}, {1: "numeric key"}, {"x": {"y": {1, 2}}}):
        with pytest.raises(ApprovalError) as e:
            canonical_payload(bad)  # type: ignore[arg-type]
        assert e.value.code == "APPROVAL_PAYLOAD_INVALID"


# --------------------------------------------------------------------------------------- who may vote


def test_an_eligible_independent_owner_may_vote() -> None:
    assert voter_refusals(_facts(), _owner("p-other")) == ()


def test_the_requester_cannot_vote_whatever_their_seniority() -> None:
    assert voter_refusals(_facts(), _owner("p-req")) == (
        "VOTER_IS_REQUESTER",
    )  # Decision 10 T05, Decision 11 T01


def test_two_accounts_of_one_person_are_one_person() -> None:
    a, b = (
        _owner("p-same"),
        Voter("acct-second", "p-same", True, (Authority("APPROVER", LONG_AGO),)),
    )
    t = tally([Ballot(a.person_id, True), Ballot(b.person_id, True)], {"p-same": True})
    assert t.approvals == 1  # Decision 10 T06, Decision 11 T02
    # and the requester's other account is the requester
    assert "VOTER_IS_REQUESTER" in voter_refusals(
        _facts(), Voter("acct-alias", "p-req", True, (Authority("OWNER", LONG_AGO),))
    )


def test_an_editor_and_the_beneficiary_cannot_vote() -> None:
    f = _facts(editor_persons=frozenset({"p-req", "p-editor"}), beneficiary_person="p-new")
    assert voter_refusals(f, _owner("p-editor")) == ("VOTER_IS_EDITOR",)
    assert voter_refusals(f, _owner("p-new")) == ("VOTER_IS_BENEFICIARY",)


def test_a_bot_an_agent_a_service_or_an_ai_is_never_a_human_approval() -> None:
    assert "VOTER_NOT_HUMAN" in voter_refusals(_facts(), _owner("p-bot", human=False))


def test_no_authority_no_vote_and_a_viewer_or_operator_is_not_an_approver() -> None:
    assert voter_refusals(_facts(), Voter("a", "p-x", True, ())) == ("VOTER_NO_AUTHORITY",)
    assert voter_refusals(_facts(), _owner("p-x", role="OPERATOR")) == ("VOTER_NO_AUTHORITY",)


def test_no_privilege_manufacture() -> None:
    """A user cannot create an approver and use them at once (Decision 10 section 03)."""
    made_by_requester_after_the_request = _owner(
        "p-new-approver", since=T0 + timedelta(minutes=5), granted_by="p-req"
    )
    assert voter_refusals(_facts(), made_by_requester_after_the_request) == (
        "VOTER_AUTHORITY_MANUFACTURED",
    )
    approved_by_requester = _owner(
        "p-new-approver", since=T0 + timedelta(minutes=5), approved_by="p-req"
    )
    assert voter_refusals(_facts(), approved_by_requester) == ("VOTER_AUTHORITY_MANUFACTURED",)
    # an approver the requester made before the request began, or somebody else made, is fine
    assert voter_refusals(_facts(), _owner("p-old", since=LONG_AGO, granted_by="p-req")) == ()
    assert (
        voter_refusals(
            _facts(), _owner("p-new", since=T0 + timedelta(minutes=5), granted_by="p-someone-else")
        )
        == ()
    )


def test_a_reviewer_votes_without_being_an_employee_unless_conflicted_or_the_action_is_the_platforms() -> (
    None
):
    reviewer = Voter("acct-r", "p-rev", True, (), reviewer=True)
    assert voter_refusals(_facts(), reviewer) == ()
    conflicted = Voter(
        "acct-r", "p-rev", True, (), reviewer=True, reviewer_conflicts=frozenset({"p-req"})
    )
    assert voter_refusals(_facts(), conflicted) == ("VOTER_REVIEWER_CONFLICT",)
    benefit = Voter(
        "acct-r", "p-rev", True, (), reviewer=True, reviewer_conflicts=frozenset({"p-ben"})
    )
    assert voter_refusals(_facts(beneficiary_person="p-ben"), benefit) == (
        "VOTER_REVIEWER_CONFLICT",
    )
    platform = _facts("manual-replacement-of-signing-keys-or-trust-anchors")
    assert voter_refusals(platform, reviewer) == ("VOTER_NO_AUTHORITY",)
    # an unappointed person is not a reviewer
    assert voter_refusals(_facts(), Voter("acct-r", "p-rev", True, (), reviewer=False)) == (
        "VOTER_NO_AUTHORITY",
    )


# --------------------------------------------------------------------------------------- quorum and states


def test_a1_one_independent_approval_is_enough_and_the_owner_may_be_that_approver() -> None:
    a1 = _reg().classify("appoint-independent-reviewer")
    assert (
        state_after(a1, tally([Ballot("p-owner", True)], {"p-owner": True})) is State.APPROVED_READY
    )  # Decision 10 T02


def test_a2_the_first_vote_never_makes_it_ready_and_the_second_does() -> None:
    a2 = _reg().classify("ownership-transfer-or-replacement")
    none = tally([], {})
    first = tally([Ballot("p1", True)], {"p1": True})
    second = tally([Ballot("p1", True), Ballot("p2", True)], {"p1": True, "p2": True})
    assert state_after(a2, none) is State.PENDING_APPROVAL
    assert state_after(a2, first) is State.PARTIALLY_APPROVED  # Decision 10 T03, Decision 11 T03
    assert state_after(a2, second) is State.APPROVED_READY  # Decision 10 T04, Decision 11 T04


def test_a_rejection_ends_it_and_a_vote_of_someone_who_lost_authority_counts_for_nothing() -> None:
    a2 = _reg().classify("ownership-transfer-or-replacement")
    assert (
        state_after(a2, tally([Ballot("p1", True), Ballot("p2", False)], {"p1": True, "p2": True}))
        is State.REJECTED
    )
    revoked = tally([Ballot("p1", True), Ballot("p2", True)], {"p1": True, "p2": False})
    assert revoked.approvals == 1 and revoked.lost == ("p2",)  # Decision 11 T06
    assert state_after(a2, revoked) is State.PARTIALLY_APPROVED
    # a rejection from someone who is no longer eligible does not end the case
    assert not tally([Ballot("p3", False)], {"p3": False}).rejected


# ------------------------------------------------------------------------------------------ transitions


def test_final_states_are_final_and_execution_only_follows_readiness() -> None:
    for final in TERMINAL:
        assert not TRANSITIONS.get(final)
    with pytest.raises(ApprovalError):
        move(State.PENDING_APPROVAL, State.EXECUTED)  # no execution before quorum
    with pytest.raises(ApprovalError):
        move(State.PARTIALLY_APPROVED, State.EXECUTED)
    assert move(State.APPROVED_READY, State.EXECUTED) is State.EXECUTED
    assert (
        move(State.FAILED, State.EXECUTED) is State.EXECUTED
    )  # the unchanged request may be run again
    for lapse in (State.EXPIRED, State.INVALIDATED, State.CANCELLED):
        for open_state in (
            State.PENDING_APPROVAL,
            State.PARTIALLY_APPROVED,
            State.APPROVED_READY,
            State.FAILED,
        ):
            assert move(open_state, lapse) is lapse
    with pytest.raises(ApprovalError):
        move(State.EXPIRED, State.APPROVED_READY)  # no stale-vote reuse
    with pytest.raises(ApprovalError):
        move(State.REJECTED, State.PENDING_APPROVAL)


# --------------------------------------------------------------------------------------- solo organizations


def test_a_solo_organization_can_start_a2_but_it_is_held_never_waived() -> None:
    a2, a1 = (
        _reg().classify("ownership-transfer-or-replacement"),
        _reg().classify("appoint-independent-reviewer"),
    )
    assert governance_hold(a2, 0) == "BLOCKED_INSUFFICIENT_APPROVERS"
    assert (
        governance_hold(a2, 1) == "BLOCKED_INSUFFICIENT_APPROVERS"
    )  # one independent person is still not two
    assert governance_hold(a2, 2) is None
    assert governance_hold(a1, 0) == "PENDING_INDEPENDENT_REVIEW"
    assert governance_hold(a1, 1) is None
    assert (
        governance_hold(_reg().classify("update-organization-presentation-details"), 0) is None
    )  # A0 needs no one


def test_solo_status_is_computed_and_never_loosens_a_requirement() -> None:
    one = organization_governance(
        eligible_persons=["p1"], region="uk-1", policy_version="v", reviewers=0
    )
    two = organization_governance(
        eligible_persons=["p1", "p2", "p2"], region="uk-1", policy_version="v", reviewers=1
    )
    assert one.solo_mode and one.eligible_human_member_count == 1 and not one.legal_form_resolved
    assert (
        not two.solo_mode
        and two.eligible_human_member_count == 2
        and two.independent_routes == ("ORGANIZATION_APPOINTED_REVIEWER",)
    )
    a2 = _reg().classify("ownership-transfer-or-replacement")
    assert (
        a2.required_votes == 2
    )  # the same for both: switching from one member to two changes nothing about A2 (Decision 12 test 10)


def test_governance_never_looks_at_plan_or_payment_state() -> None:
    """Decision 12 test 11: quorum, separation of duties and the protective controls do not depend on a plan, a bill or a suspension,
    because nothing in the approval service can even see them."""
    import pathlib

    root = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "zid_platform" / "modules" / "tenant"
    )
    files = [
        root / "domain" / "approvals.py",
        root / "infrastructure" / "approvals.py",
        root / "application" / "approvals.py",
        root / "application" / "approval_executors.py",
        root / "application" / "reviewers.py",
        root / "api" / "approvals.py",
    ]
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "modules.commercial" not in text and "entitlement" not in text.lower(), f.name


def test_the_only_operator_tool_that_calls_the_platform_guard_today_is_bootstrap_operator() -> None:
    """Decision 11 T09, honestly: the guard is built and each platform operation must call it. This pins which tools do today, so a new
    operator tool cannot be added to the list of guarded ones by accident, and a removed guard is noticed."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "zid_platform"
    users = sorted(
        str(p.relative_to(src)).replace("\\", "/")
        for p in src.rglob("*.py")
        if "consume_authorization(" in p.read_text(encoding="utf-8")
        and p.name not in {"approvals.py", "ports.py"}
    )
    assert users == ["entrypoints/bootstrap_operator.py"]
