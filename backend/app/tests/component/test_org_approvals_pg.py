"""The organization approvals screens' API (ZID-UX-WF-013C screens 2 and 3; acceptance AT-04 to AT-11 and AT-16): an inbox built from
object-level authorization, decisions with If-Match and idempotency, requesting changes, and what a stale or ineligible caller is told."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest
from onboarding_fixture import onboard
from test_approvals_pg import OWNER_TRANSFER, World, code, world  # noqa: F401  (world is a fixture)
from zid_platform.modules.tenant.infrastructure import approvals as store


def inbox(w: World, who: str, **params: Any) -> dict[str, Any]:
    r = w.http.get(f"/v1/organizations/{w.o.tenant}/approvals", headers=w.h(who), params=params)
    assert r.status_code == 200, r.text
    return dict(r.json())


def decide(w: World, who: str, case: dict[str, Any], decision: str, *, reason: str | None = None, proof: bool = True,
           if_match: str | None = "auto", key: str | None = None) -> Any:  # fmt: skip
    headers = dict(w.h(who))
    if if_match == "auto":
        headers["If-Match"] = f'"{case["request_hash"]}"'
    elif if_match is not None:
        headers["If-Match"] = if_match
    if key:
        headers["Idempotency-Key"] = key
    if proof and decision != "REQUEST_CHANGES":
        headers["X-Step-Up-Proof"] = w.proof(
            who, case, "APPROVE" if decision == "APPROVE" else "REJECT"
        )
    return w.http.post(
        f"/v1/organizations/{w.o.tenant}/approvals/{case['case_id']}/decisions",
        headers=headers,
        json={"decision": decision, **({"reason": reason} if reason else {})},
    )


def open_case(w: World, **kw: Any) -> dict[str, Any]:
    target = w.person(None)
    r = w.http.post(
        f"/v1/organizations/{w.o.tenant}/approvals",
        headers=w.h(w.o.owner),
        json={
            "action_id": OWNER_TRANSFER,
            "target_id": f"principal:{target}",
            "payload": {"principal_id": target},
            "reason": "hand over",
            **kw,
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def test_the_inbox_shows_only_authorized_work_and_honest_counts(world: World) -> None:  # noqa: F811
    """AT-04 (a requester cannot approve their own), AT-10 (no rows or counts for the unauthorized), 13C-02A tabs."""
    w = world
    a1, a2 = w.person(), w.person()
    viewer, operator = w.person("VIEWER", passkey=False), w.person("OPERATOR", passkey=False)
    case = open_case(w)
    assert (
        case["level"] == "A2"
        and case["status_label"] == "Pending approval"
        and case["version"] == 1
    )

    assert [i["request_id"] for i in inbox(w, a1)["items"]] == [
        case["case_id"]
    ]  # an approver finds it under "to review"
    assert inbox(w, a1)["counts"] == {"review": 1, "mine": 0, "history": 0}
    mine = inbox(w, w.o.owner, scope="mine")
    assert [i["request_id"] for i in mine["items"]] == [case["case_id"]] and mine["counts"][
        "review"
    ] == 0  # not to review: it is theirs
    assert (
        inbox(w, w.o.owner)["items"] == []
    )  # the requester is never offered their own request to approve

    for nobody in (viewer, operator):  # no rows, no counts, whatever tab or filter is asked for
        for scope in ("review", "mine", "history"):
            got = inbox(w, nobody, scope=scope, risk="A2", q=case["case_id"][:8])
            assert (
                got["items"] == []
                and got["counts"] == {"review": 0, "mine": 0, "history": 0}
                and got["next_cursor"] is None
            )
        assert (
            w.http.get(
                f"/v1/organizations/{w.o.tenant}/approvals/{case['case_id']}", headers=w.h(nobody)
            ).status_code
            == 404
        )
        assert (
            w.http.get(f"/v1/approval-cases/{case['case_id']}", headers=w.h(nobody)).status_code
            == 404
        )
        assert (
            w.http.get(
                f"/v1/approval-cases/{case['case_id']}/evidence", headers=w.h(nobody)
            ).status_code
            == 404
        )
        assert w.http.get("/v1/approval-cases", headers=w.h(nobody)).json()["items"] == []
        assert (
            code(decide(w, nobody, case, "APPROVE", proof=False)) == "APPROVAL_CASE_NOT_FOUND"
        )  # the same answer as "not there"

    # the requester cannot approve, even from a second account linked to them
    alias = w.person("APPROVER")
    w.link(alias, w.o.owner)
    refused = decide(w, alias, case, "APPROVE", proof=False)
    assert refused.status_code == 403 and code(refused) == "VOTER_IS_REQUESTER"
    assert code(decide(w, w.o.owner, case, "APPROVE", proof=False)) == "VOTER_IS_REQUESTER"
    assert a2


def test_another_organizations_ids_and_filters_reveal_nothing(world: World, rt_url: str) -> None:  # noqa: F811
    """AT-10: cross-tenant ids are 'no such organization' or 'no such request', and the other organization's counts are its own."""
    w = world
    case = open_case(w)
    other = onboard(rt_url)
    from fastapi.testclient import TestClient
    from test_step_up_pg import app_for

    with TestClient(app_for(rt_url, other, "shadow"), base_url="https://id.zoiko.test") as foreign:
        h = other.bearer(other.owner)
        assert (
            foreign.get(f"/v1/organizations/{w.o.tenant}/approvals", headers=h).status_code == 404
        )  # not my organization
        assert (
            foreign.get(
                f"/v1/organizations/{other.tenant}/approvals/{case['case_id']}", headers=h
            ).status_code
            == 404
        )
        mine = foreign.get(
            f"/v1/organizations/{other.tenant}/approvals", headers=h, params={"scope": "history"}
        ).json()
        assert mine["items"] == [] and mine["counts"] == {"review": 0, "mine": 0, "history": 0}
        bogus = foreign.post(
            f"/v1/organizations/{other.tenant}/approvals/{case['case_id']}/decisions",
            headers={**h, "If-Match": f'"{case["request_hash"]}"'}, json={"decision": "REQUEST_CHANGES", "reason": "x"},
        )  # fmt: skip
        assert bogus.status_code == 404
    assert (
        w.http.get(
            f"/v1/organizations/{uuid.uuid4()}/approvals", headers=w.h(w.o.owner)
        ).status_code
        == 404
    )


def test_a_decision_needs_the_version_read_and_a_stale_one_changes_nothing(world: World) -> None:  # noqa: F811
    """AT-07 and 13C section 10: If-Match is required, a stale one is 409 VERSION_CONFLICT with no side effects."""
    w = world
    a1 = w.person()
    case = open_case(w)
    assert decide(w, a1, case, "APPROVE", if_match=None, proof=False).status_code == 428
    stale = decide(w, a1, case, "APPROVE", if_match='"' + "0" * 64 + '"', proof=False)
    assert stale.status_code == 409 and code(stale) == "VERSION_CONFLICT"
    assert inbox(w, a1)["items"][0]["approvals_received"] == 0  # nothing was recorded
    reject_without_reason = decide(w, a1, case, "REJECT", proof=False)
    assert reject_without_reason.status_code == 422
    assert (
        decide(w, a1, case, "REQUEST_CHANGES", proof=False).status_code == 422
    )  # a reason is required there too


def test_a_retried_decision_is_one_decision(world: World) -> None:  # noqa: F811
    """AT-11: the same Idempotency-Key returns what was done and records nothing new; a vote after quorum is refused."""
    w = world
    a1, a2 = w.person(), w.person()
    case = open_case(w)
    first = decide(w, a1, case, "APPROVE", key="decide-once-1234")
    assert (
        first.status_code == 201
        and first.json()["approvals_received"] == 1
        and first.json()["state"] == "PARTIALLY_APPROVED"
    )
    again = decide(
        w, a1, case, "APPROVE", key="decide-once-1234", proof=False
    )  # a retry carries no fresh proof, and needs none
    assert again.status_code == 201 and again.json()["approvals_received"] == 1
    other_key = decide(w, a1, case, "APPROVE", key="a-different-key-99", proof=False)
    assert other_key.status_code == 403 and code(other_key) == "VOTER_ALREADY_VOTED"
    assert (
        decide(w, a2, case, "APPROVE", key="second-approver-5678").json()["state"]
        == "APPROVED_READY"
    )
    assert [e["state"] for e in inbox(w, w.o.owner, scope="mine")["items"]] == ["APPROVED_READY"]
    assert inbox(w, w.o.owner, scope="mine")["items"][0]["next_action"] == "Apply the change"
    assert w.execute(w.o.owner, case["case_id"], "run-1").json()["outcome"] == "EXECUTED"
    assert (
        w.execute(w.o.owner, case["case_id"], "run-2").json()["replayed"] is True
    )  # one execution however many retries


def test_asking_for_changes_voids_every_vote_and_the_requester_revises(world: World) -> None:  # noqa: F811
    """AT-07, 13C-03D: request changes ends the version, no prior vote is reused, and the requester resubmits a new one."""
    w = world
    a1, a2 = w.person(), w.person()
    case = open_case(w)
    assert decide(w, a1, case, "APPROVE").status_code == 201
    asked = decide(
        w, a2, case, "REQUEST_CHANGES", reason="The successor should be a different person."
    )
    assert (
        asked.status_code == 201
        and asked.json()["state"] == "NEEDS_CHANGES"
        and asked.json()["status_label"] == "Needs changes"
    )
    # the version ended: nobody can vote on it, and it cannot be applied
    assert decide(w, a2, case, "APPROVE", proof=False).status_code == 409
    assert code(w.execute(w.o.owner, case["case_id"])) == "APPROVAL_NOT_READY"
    # the requester revises into version 2, with every vote gone and the old proof of no use
    other = w.person(None)
    revised = w.http.post(
        f"/v1/approval-cases/{case['case_id']}:revise",
        headers=w.h(w.o.owner),
        json={"payload": {"principal_id": other}, "reason": "changed"},
    )
    assert (
        revised.status_code == 201
        and revised.json()["version"] == 2
        and revised.json()["approvals_received"] == 0
    )
    v2 = revised.json()
    assert (
        v2["request_hash"] != case["request_hash"] and v2["supersedes_case_id"] == case["case_id"]
    )
    assert (
        decide(w, a1, v2, "APPROVE").status_code == 201
    )  # a1 votes again, on the new version, with a fresh proof
    # a requester cannot ask for changes to their own request (they revise), and an ineligible person cannot either
    assert decide(w, w.o.owner, v2, "REQUEST_CHANGES", reason="x").status_code == 403
    hist = inbox(w, w.o.owner, scope="history")
    assert [i["state"] for i in hist["items"]] == ["NEEDS_CHANGES"] and hist["items"][0][
        "status_label"
    ] == "Needs changes"


def test_filters_search_and_pages_are_applied_after_authorization(world: World) -> None:  # noqa: F811
    """13C-02A: filters by risk, state, date and scoped id, with a cursor; counts are over what the caller may see."""
    w = world
    a1 = w.person()
    cases = [open_case(w) for _ in range(3)]
    page1 = inbox(w, a1, limit=2)
    assert len(page1["items"]) == 2 and page1["next_cursor"] and page1["counts"]["review"] == 3
    page2 = inbox(w, a1, limit=2, cursor=page1["next_cursor"])
    assert len(page2["items"]) == 1 and page2["next_cursor"] is None
    assert {i["request_id"] for i in page1["items"] + page2["items"]} == {
        c["case_id"] for c in cases
    }
    assert inbox(w, a1, risk="A1")["items"] == [] and len(inbox(w, a1, risk="A2")["items"]) == 3
    assert [i["request_id"] for i in inbox(w, a1, q=cases[1]["case_id"][:23])["items"]] == [
        cases[1]["case_id"]
    ]
    assert inbox(w, a1, state="EXECUTED")["items"] == []
    assert inbox(w, a1, created_from="2999-01-01T00:00:00Z")["items"] == []
    assert (
        w.http.get(
            f"/v1/organizations/{w.o.tenant}/approvals",
            headers=w.h(a1),
            params={"scope": "everything"},
        ).status_code
        == 422
    )


def test_an_approved_request_is_held_when_policy_or_trust_changes(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT-08 and AT-09: the earlier quorum does not run a changed world. The hold is derived and visible; the votes stay."""
    w = world
    a1, a2 = w.person(), w.person()
    case = open_case(w)
    assert (
        decide(w, a1, case, "APPROVE").status_code == 201
        and decide(w, a2, case, "APPROVE").json()["state"] == "APPROVED_READY"
    )
    read = w.http.get(
        f"/v1/organizations/{w.o.tenant}/approvals/{case['case_id']}", headers=w.h(w.o.owner)
    ).json()
    assert (
        read["execution_hold"] is None and read["status_label"] == "Approved, waiting to be applied"
    )
    real = store.registry()
    monkeypatch.setattr(store, "registry", lambda: replace(real, digest="e" * 64))
    held = w.http.get(
        f"/v1/organizations/{w.o.tenant}/approvals/{case['case_id']}", headers=w.h(w.o.owner)
    ).json()
    assert held["execution_hold"] == "POLICY_CHANGED" and held["approvals_received"] == 2
    assert inbox(w, w.o.owner, scope="mine")["items"][0]["next_action"] == "Held: POLICY_CHANGED"
    blocked = w.execute(w.o.owner, case["case_id"])
    assert blocked.status_code == 409 and code(blocked) == "APPROVAL_INVALIDATED"  # nothing ran
    assert w.owner_roles().count(case["beneficiary_principal_id"]) == 0


def test_a_solo_organization_sees_a_shortage_not_a_fake_reviewer(world: World) -> None:  # noqa: F811
    """AT-03 and 13C-02D: with no independent approver the card says so and links to remediation; nothing is downgraded or simulated."""
    w = world
    case = open_case(w)
    card = inbox(w, w.o.owner, scope="mine")["items"][0]
    assert (
        card["shortage"] == "Independent approvers required"
        and card["next_action"] == "Invite or appoint independent approvers"
    )
    assert (
        card["level"] == "A2"
        and card["approvals_required"] == 2
        and card["approvals_received"] == 0
    )
    assert (
        w.http.get(
            f"/v1/organizations/{w.o.tenant}/approvals/{case['case_id']}", headers=w.h(w.o.owner)
        ).json()["hold"]
        == "BLOCKED_INSUFFICIENT_APPROVERS"
    )
    a1 = w.person()  # one independent person is still not two
    assert (
        inbox(w, w.o.owner, scope="mine")["items"][0]["shortage"]
        == "Independent approvers required"
        and a1
    )
    w.person()
    assert inbox(w, w.o.owner, scope="mine")["items"][0]["shortage"] is None


def test_every_decision_and_denial_leaves_an_attributable_audit_record(world: World) -> None:  # noqa: F811
    """AT-16: success and denial both write E3 records naming the case, the version hash and the policy version."""
    w = world
    a1 = w.person()
    case = open_case(w)
    decide(w, a1, case, "APPROVE")
    decide(w, w.o.owner, case, "APPROVE", proof=False)  # denied: the requester
    decide(
        w, a1, case, "REQUEST_CHANGES", reason="please narrow it"
    )  # a1 already voted; still eligible to send it back
    rows = w.audit()
    kinds = {(r.event_type, r.outcome) for r in rows}
    assert {("governance.approval.case_created", "SUCCESS"), ("governance.approval.vote_recorded", "SUCCESS"),
            ("governance.approval.vote_denied", "DENIED"), ("governance.approval.changes_requested", "SUCCESS")} <= kinds  # fmt: skip
    denied = next(r for r in rows if r.event_type == "governance.approval.vote_denied")
    assert denied.reason_code == "VOTER_IS_REQUESTER" and denied.ec == "E3"
    details = next(r for r in rows if r.event_type == "governance.approval.vote_recorded").body[
        "details"
    ]
    assert (
        details["request_hash"] == case["request_hash"]
        and details["policy_version"] == case["policy_version"]
        and details["region"] == "uk-1"
    )
    assert "please narrow it" not in str(
        [r.body for r in rows if r.event_type != "governance.approval.changes_requested"]
    )
