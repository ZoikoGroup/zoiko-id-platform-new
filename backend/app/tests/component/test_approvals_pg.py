"""The approval service on real routes and a real database (ZID-ENG-DEC-010 T01-T12, ZID-ENG-DEC-011 T01-T14, ZID-ENG-DEC-012 tests 01-12).

People vote with real WebAuthn proofs from a software authenticator, so the binding of a vote to one request hash is proved
cryptographically, not by a stub. Two accounts of one human are linked by the person link of slice L-7, and the database refuses a
second vote by the same person even if the application did not.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, Onboarded, _session, onboard
from softauthn import SoftAuthenticator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_step_up_pg import app_for, give_passkey
from zid_db import tenant_transaction
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.application import reviewers as reviewer_svc
from zid_platform.modules.tenant.domain.approvals import ApprovalError, State
from zid_platform.modules.tenant.domain.lifecycle import Role, RoleOrigin, RoleStatus
from zid_platform.modules.tenant.infrastructure import approvals as store
from zid_platform.modules.tenant.infrastructure import tenant_store

STEP = "execute-sensitive-approval-rejection"
LONG_AGO = datetime.now(UTC) - timedelta(days=30)
OWNER_TRANSFER = "ownership-transfer-or-replacement"


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class World:
    o: Onboarded
    rt_url: str
    owner_url: str
    http: TestClient
    keys: dict[str, SoftAuthenticator] = field(default_factory=dict)

    def run(self, work: Any) -> Any:
        async def go() -> Any:
            async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.o.tenant)) as c:
                return await work(c)

        return asyncio.run(go())

    def person(
        self,
        role: str | None = "APPROVER",
        *,
        passkey: bool = True,
        kind: PrincipalType = PrincipalType.HUMAN,
    ) -> str:
        p = uid()

        async def work(c: Any) -> None:
            await create_principal(
                c, Principal(p, self.o.tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
            )
            self.o.sessions[p] = await _session(c, self.o.tenant, p)
            if role:
                owner = (
                    role == "OWNER"
                )  # an Owner only ever comes from the owner change, approved by someone else
                await tenant_store.insert_role(
                    c, tenant_id=self.o.tenant, principal_id=p, role=Role(role), status=RoleStatus.ACTIVE,
                    origin=RoleOrigin.OWNER_CHANGE if owner else RoleOrigin.INVITATION,
                    granted_by=self.o.owner if owner else None, approved_by=self.o.second if owner else None, now=LONG_AGO,
                )  # fmt: skip

        self.run(work)
        if passkey:
            self.keys[p] = give_passkey(self.rt_url, self.o.tenant, p)
        return p

    def link(self, alias: str, to: str) -> None:
        async def work(c: Any) -> None:
            person = await tenant_store.person_of(c, to)
            await tenant_store.link_person(
                c,
                tenant_id=self.o.tenant,
                principal_id=alias,
                person_id=person,
                source="PLATFORM_REVIEW",
            )

        self.run(work)

    def revoke_role(self, principal: str) -> None:
        async def work(c: Any) -> None:
            await c.execute(
                text(
                    "UPDATE platform.tenant_role_assignment SET status = 'REVOKED', revoked_at = now() WHERE principal_id = :p"
                ),
                {"p": uuid.UUID(principal)},
            )

        self.run(work)

    def audit(self, event_like: str = "governance.%") -> list[Any]:
        async def work(c: Any) -> list[Any]:
            return list(
                (
                    await c.execute(
                        text(
                            "SELECT event_type, outcome, reason_code, evidence_class::text AS ec, body FROM evidence.audit_record "
                            "WHERE tenant_id = :t AND event_type LIKE :e ORDER BY created_at"
                        ),
                        {"t": uuid.UUID(self.o.tenant), "e": event_like},
                    )
                ).all()
            )

        return self.run(work)

    # ---- the API as a person uses it
    def h(self, who: str) -> dict[str, str]:
        return self.o.bearer(who)

    def create(
        self,
        who: str,
        action: str,
        target: str,
        payload: dict[str, Any],
        reason: str = "because",
        **kw: Any,
    ) -> Any:
        return self.http.post(
            "/v1/approval-cases", headers=self.h(who),
            json={"action_id": action, "target_id": target, "payload": payload, "reason": reason, **kw},
        )  # fmt: skip

    def proof(self, who: str, case: dict[str, Any], vote: str) -> str:
        ch = self.http.post(
            "/v1/step-up/challenges", headers=self.h(who),
            json={"action": STEP, "resource": f"approval-case:{case['case_id']}", "critical": case["tier"] == "A2",
                  "change": {"case_id": case["case_id"], "request_hash": case["request_hash"], "vote": vote}},
        )  # fmt: skip
        assert ch.status_code == 201, ch.text
        c = ch.json()
        done = self.http.post(
            f"/v1/step-up/challenges/{c['challenge_id']}/verify", headers=self.h(who),
            json={"credential": self.keys[who].get(c["public_key"])},
        )  # fmt: skip
        assert done.status_code == 200, done.text
        return str(done.json()["proof_id"])

    def vote(
        self,
        who: str,
        case: dict[str, Any],
        vote: str = "APPROVE",
        *,
        proof: str | bool = True,
        request_hash: str | None = None,
    ) -> Any:
        headers = self.h(who)
        if proof is True:
            headers = {**headers, "X-Step-Up-Proof": self.proof(who, case, vote)}
        elif isinstance(proof, str):
            headers = {**headers, "X-Step-Up-Proof": proof}
        return self.http.post(
            f"/v1/approval-cases/{case['case_id']}/votes", headers=headers,
            json={"vote": vote, "request_hash": request_hash or case["request_hash"]},
        )  # fmt: skip

    def get(self, who: str, case_id: str) -> dict[str, Any]:
        r = self.http.get(f"/v1/approval-cases/{case_id}", headers=self.h(who))
        assert r.status_code == 200, r.text
        return dict(r.json())

    def execute(self, who: str, case_id: str, key: str = "k1") -> Any:
        return self.http.post(
            f"/v1/approval-cases/{case_id}/execute", headers={**self.h(who), "Idempotency-Key": key}
        )

    def owner_roles(self) -> list[str]:
        async def work(c: Any) -> list[str]:
            return [
                a.principal_id
                for a in await tenant_store.roles(c)
                if a.role is Role.OWNER and a.status is RoleStatus.ACTIVE
            ]

        return list(self.run(work))


@pytest.fixture
def world(rt_url: str, owner_url: str) -> Any:
    o = onboard(rt_url)
    w = World(o, rt_url, owner_url, TestClient(app_for(rt_url, o, "shadow"), base_url=ISS))
    w.keys[o.owner] = give_passkey(rt_url, o.tenant, o.owner)
    with w.http:
        yield w


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def reasons(r: Any) -> set[str]:
    return {d["issue"] for d in r.json()["error"].get("details", [])}


# ---------------------------------------------------------------------------------------------- A2, end to end


def test_a2_ownership_transfer_end_to_end(world: World) -> None:
    """Decision 10 T03, T04, T05, T10, T11; Decision 11 T01, T03, T04, T08, T14."""
    w = world
    a1, a2, newcomer = w.person(), w.person(), w.person(None)
    made = w.create(
        w.o.owner,
        OWNER_TRANSFER,
        f"principal:{newcomer}",
        {"principal_id": newcomer},
        "Hand over day-to-day ownership",
    )
    assert made.status_code == 201, made.text
    case = made.json()
    assert (
        case["tier"] == "A2"
        and case["state"] == "PENDING_APPROVAL"
        and case["approvals_required"] == 2
    )
    assert case["beneficiary_principal_id"] == newcomer and len(case["request_hash"]) == 64
    assert "No changes have taken effect" in case["notice"] and case["gate"].startswith(
        "Reverify incoming Owner"
    )

    # the requester cannot vote, the beneficiary cannot vote, and the refusal explains itself
    mine = w.get(w.o.owner, case["case_id"])["you"]
    assert mine["may_vote"] is False and mine["reasons"] == ["VOTER_IS_REQUESTER"]
    refused = w.vote(w.o.owner, case, proof=False)
    assert refused.status_code == 403 and code(refused) == "VOTER_IS_REQUESTER"
    assert w.vote(newcomer, case, proof=False).status_code == 403

    # a vote needs a fresh proof for this request, even though the platform runs step-up in shadow
    assert code(w.vote(a1, case, proof=False)) == "STEP_UP_REQUIRED"
    assert code(w.vote(a1, case, request_hash="0" * 64)) == "APPROVAL_HASH_MISMATCH"

    first = w.vote(a1, case)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["state"] == "PARTIALLY_APPROVED" and body["approvals_received"] == 1
    assert body["notice"] == "1 of 2 independent approvals received. No changes have taken effect."
    assert newcomer not in w.owner_roles()  # the first approval changes nothing
    assert code(w.execute(w.o.owner, case["case_id"])) == "APPROVAL_NOT_READY"
    assert code(w.vote(a1, case, proof=False)) == "VOTER_ALREADY_VOTED"

    second = w.vote(a2, case)
    assert second.status_code == 201 and second.json()["state"] == "APPROVED_READY"
    assert newcomer not in w.owner_roles()  # approved is not applied
    assert second.json()["applied_at"] is None and second.json()["approved_at"]

    done = w.execute(w.o.owner, case["case_id"], "run-1")
    assert done.status_code == 200, done.text
    d = done.json()
    assert (
        d["outcome"] == "EXECUTED" and d["replayed"] is False and d["case"]["state"] == "EXECUTED"
    )
    assert d["case"]["applied_at"] and newcomer in w.owner_roles()

    # a retry, with the same key or another, is the same single outcome and runs nothing
    for key in ("run-1", "run-2"):
        again = w.execute(w.o.owner, case["case_id"], key).json()
        assert (
            again["outcome"] == "EXECUTED" and again["replayed"] is True and again["attempt"] == 1
        )
    assert w.owner_roles().count(newcomer) == 1

    # the replay: version, maker, both distinct checkers, both proofs, the hash, times and the result
    ev = w.http.get(f"/v1/approval-cases/{case['case_id']}/evidence", headers=w.h(w.o.owner)).json()
    assert ev["request_hash"] == case["request_hash"] and ev["maker"]["principal_id"] == w.o.owner
    assert ev["distinct_checkers"] == 2 and {
        c["approver_principal_id"] for c in ev["checkers"]
    } == {a1, a2}
    assert all(c["step_up_tier"] == "S2" and c["step_up_proof_ref"] for c in ev["checkers"])
    assert [t["to"] for t in ev["transitions"]] == [
        "SUBMITTED",
        "PENDING_APPROVAL",
        "PARTIALLY_APPROVED",
        "APPROVED_READY",
        "EXECUTED",
    ]
    assert [x["outcome"] for x in ev["executions"]] == ["EXECUTED"] and ev["executions"][0][
        "quorum"
    ]["required"] == 2
    kinds = [(a.event_type, a.outcome) for a in w.audit()]
    assert ("governance.approval.case_created", "SUCCESS") in kinds and (
        "governance.approval.executed",
        "SUCCESS",
    ) in kinds
    assert [k for k in kinds if k[0] == "governance.approval.vote_recorded"] == [
        ("governance.approval.vote_recorded", "SUCCESS")
    ] * 2
    assert {a.reason_code for a in w.audit() if a.outcome == "DENIED"} >= {
        "VOTER_IS_REQUESTER",
        "APPROVAL_HASH_MISMATCH",
        "VOTER_ALREADY_VOTED",
    }
    assert all(a.ec == "E3" for a in w.audit())


def test_a_vote_needs_a_proof_for_that_approver_and_that_request(world: World) -> None:
    """Decision 10 T07; Decision 11 T05: a missing, borrowed, reused or wrong-binding proof never records a vote."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    other = w.create(w.o.owner, OWNER_TRANSFER, "u", {"principal_id": target}).json()
    assert code(w.vote(a1, case, proof=False)) == "STEP_UP_REQUIRED"
    wrong_case = w.proof(a1, other, "APPROVE")  # a real proof, for a different request
    assert w.vote(a1, case, proof=wrong_case).status_code == 403
    borrowed = w.proof(a2, case, "APPROVE")  # a real proof, for another person
    assert w.vote(a1, case, proof=borrowed).status_code == 403
    wrong_vote = w.proof(a1, case, "REJECT")  # a real proof, for the opposite vote
    assert w.vote(a1, case, proof=wrong_vote).status_code == 403
    assert w.get(a1, case["case_id"])["approvals_received"] == 0
    good = w.proof(a1, case, "APPROVE")
    assert w.vote(a1, case, proof=good).status_code == 201
    assert (
        w.vote(a2, case, proof=good).status_code == 403
    )  # an S2 proof is spent and is not transferable


def test_two_accounts_of_one_human_are_one_vote(world: World) -> None:
    """Decision 10 T06; Decision 11 T02; Decision 12 test 03."""
    w = world
    a1, alias = w.person(), w.person()
    w.link(alias, a1)
    owner_alias = w.person("APPROVER")
    w.link(owner_alias, w.o.owner)
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert w.vote(a1, case).status_code == 201
    second = w.vote(alias, case, proof=False)
    assert second.status_code == 403 and code(second) == "VOTER_ALREADY_VOTED"
    assert w.get(w.o.owner, case["case_id"])["state"] == "PARTIALLY_APPROVED"
    # the requester's other account is the requester
    aliased_requester = w.vote(owner_alias, case, proof=False)
    assert aliased_requester.status_code == 403 and code(aliased_requester) == "VOTER_IS_REQUESTER"
    # the database refuses it as well, whatever the application checked
    person = w.run(lambda c: tenant_store.person_of(c, a1))

    async def duplicate(c: Any) -> None:
        async with c.begin_nested():
            await c.execute(
                text(
                    "INSERT INTO platform.approval_vote (vote_id, tenant_id, case_id, request_hash, tier, approver_principal_id, "
                    "approver_person_id, authority_basis, vote) VALUES (:v, :t, :c, :h, 'A2', :p, :person, 'OWNER', 'APPROVE')"
                ),
                {"v": uuid.uuid4(), "t": uuid.UUID(w.o.tenant), "c": uuid.UUID(case["case_id"]), "h": case["request_hash"],
                 "p": uuid.UUID(alias), "person": uuid.UUID(person)},
            )  # fmt: skip

    with pytest.raises(DBAPIError):
        w.run(duplicate)


def test_a_changed_request_is_a_new_case_and_the_editor_cannot_vote(world: World) -> None:
    """Decision 10 T08 and the immutability rule: any material change voids every vote; nothing edits an approved payload."""
    w = world
    a1, a2, t1, t2 = (
        w.person(),
        w.person(),
        w.person(None),
        w.person(None),
    )
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": t1}).json()
    assert w.vote(a1, case).status_code == 201
    revised = w.http.post(
        f"/v1/approval-cases/{case['case_id']}:revise",
        headers=w.h(w.o.owner),
        json={"payload": {"principal_id": t2}, "reason": "wrong person"},
    )
    assert revised.status_code == 201, revised.text
    new = revised.json()
    assert (
        new["version"] == 2
        and new["supersedes_case_id"] == case["case_id"]
        and new["approvals_received"] == 0
    )
    assert new["request_hash"] != case["request_hash"] and new["beneficiary_principal_id"] == t2
    assert w.get(w.o.owner, case["case_id"])["state"] == "INVALIDATED"
    # a vote cast for the old version means nothing now
    assert code(w.vote(a2, case, proof=False)) == "APPROVAL_STATE_INVALID"
    assert w.vote(a1, new).status_code == 201  # and a1 must vote again, on the new hash

    # the stored request cannot be edited in place, by anyone
    async def tamper(c: Any) -> None:
        await c.execute(
            text("UPDATE platform.approval_case SET payload_json = :p WHERE case_id = :c"),
            {"p": '{"principal_id":"x"}', "c": uuid.UUID(new["case_id"])},
        )

    with pytest.raises(DBAPIError):
        w.run(tamper)


def test_a_revoked_approver_blocks_execution_and_the_quorum_is_not_remembered(world: World) -> None:
    """Decision 10 T09; Decision 11 T06."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert (
        w.vote(a1, case).status_code == 201 and w.vote(a2, case).json()["state"] == "APPROVED_READY"
    )
    w.revoke_role(a1)
    blocked = w.execute(w.o.owner, case["case_id"])
    assert blocked.status_code == 409 and code(blocked) == "APPROVAL_QUORUM_LOST"
    assert target not in w.owner_roles()
    back = w.get(w.o.owner, case["case_id"])
    assert back["state"] == "PARTIALLY_APPROVED" and back["approvals_received"] == 1
    # a replacement approver restores the quorum for the unchanged request
    a3 = w.person()
    assert w.vote(a3, case).json()["state"] == "APPROVED_READY"
    assert w.execute(w.o.owner, case["case_id"], "again").json()["outcome"] == "EXECUTED"


def test_an_approver_cannot_be_manufactured_by_the_requester(world: World) -> None:
    """Decision 10 section 03: create an approver and use them at once."""
    w = world
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    puppet = w.person(None)

    async def grant(c: Any) -> None:
        await tenant_store.insert_role(
            c, tenant_id=w.o.tenant, principal_id=puppet, role=Role.APPROVER, status=RoleStatus.ACTIVE,
            origin=RoleOrigin.INVITATION, granted_by=w.o.owner, approved_by=None, now=datetime.now(UTC),
        )  # fmt: skip

    w.run(grant)
    r = w.vote(puppet, case, proof=False)
    assert r.status_code == 403 and code(r) == "VOTER_AUTHORITY_MANUFACTURED"


def test_rejection_expiry_and_cancellation_never_execute(world: World) -> None:
    """Decision 10 T09; Decision 12 test 12: the evidence of a denied, cancelled or expired case survives."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    rejected = w.create(w.o.owner, OWNER_TRANSFER, "r", {"principal_id": target}).json()
    assert w.vote(a1, rejected, "REJECT").json()["state"] == "REJECTED"
    assert code(w.execute(w.o.owner, rejected["case_id"])) == "APPROVAL_NOT_READY"
    assert code(w.vote(a2, rejected, proof=False)) == "APPROVAL_STATE_INVALID"

    cancelled = w.create(w.o.owner, OWNER_TRANSFER, "c", {"principal_id": target}).json()
    assert (
        w.http.post(
            f"/v1/approval-cases/{cancelled['case_id']}:cancel", headers=w.h(a1)
        ).status_code
        == 403
    )  # only the requester
    assert (
        w.http.post(
            f"/v1/approval-cases/{cancelled['case_id']}:cancel", headers=w.h(w.o.owner)
        ).json()["state"]
        == "CANCELLED"
    )
    assert code(w.execute(w.o.owner, cancelled["case_id"])) == "APPROVAL_NOT_READY"

    lapsing = w.create(w.o.owner, OWNER_TRANSFER, "e", {"principal_id": target}).json()
    assert (
        w.vote(a1, lapsing).status_code == 201
        and w.vote(a2, lapsing).json()["state"] == "APPROVED_READY"
    )
    future = datetime.now(UTC) + timedelta(days=30)

    async def late(c: Any) -> str:
        try:
            await svc.execute_case(
                c, w.o.tenant, w.o.owner, lapsing["case_id"], idempotency_key="late", now=future
            )
        except ApprovalError as e:
            assert e.persist
            return e.code
        return "RAN"

    assert w.run(late) == "APPROVAL_EXPIRED"
    assert w.get(w.o.owner, lapsing["case_id"])["state"] == "EXPIRED"  # persisted by the refusal
    assert target not in w.owner_roles()
    assert code(w.vote(a1, lapsing, proof=False)) == "APPROVAL_EXPIRED"  # no stale-vote reuse
    types = {a.event_type for a in w.audit()}
    assert {"governance.approval.expired", "governance.approval.cancelled"} <= types


def test_concurrent_votes_and_retried_execution_have_one_outcome(world: World) -> None:
    """Decision 10 T10; Decision 11 T08: parallel votes and parallel runs meet one authoritative row."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()

    async def ok(_c: Any) -> dict[str, Any]:
        return {"required": True, "tier": "S2", "proof_id": "test-proof"}

    async def cast(who: str) -> str:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            try:
                v = await svc.cast_vote(
                    c,
                    w.o.tenant,
                    who,
                    case["case_id"],
                    vote="APPROVE",
                    presented_hash=case["request_hash"],
                    step_up=ok,
                )
            except ApprovalError as e:
                return e.code
            return v.case.state.value

    async def run_all(coros: list[Any]) -> list[Any]:
        return list(await asyncio.gather(*coros))

    results = asyncio.run(run_all([cast(a1), cast(a2), cast(a1)]))  # a1 votes twice at once
    refused = [
        r
        for r in results
        if r in {"VOTER_ALREADY_VOTED", "APPROVAL_DUPLICATE_PERSON", "APPROVAL_STATE_INVALID"}
    ]
    assert len(refused) == 1 and sorted(r for r in results if r not in refused) == [
        "APPROVED_READY",
        "PARTIALLY_APPROVED",
    ], results
    assert w.get(w.o.owner, case["case_id"])["approvals_received"] == 2

    async def run(key: str) -> tuple[str, bool]:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            r = await svc.execute_case(
                c, w.o.tenant, w.o.owner, case["case_id"], idempotency_key=key
            )
            return r.outcome, r.replayed

    outcomes = asyncio.run(run_all([run("a"), run("b"), run("a")]))
    assert [o for o, _ in outcomes] == ["EXECUTED"] * 3 and sum(
        1 for _, replayed in outcomes if not replayed
    ) == 1
    assert w.owner_roles().count(target) == 1
    assert [
        e["outcome"]
        for e in w.http.get(
            f"/v1/approval-cases/{case['case_id']}/evidence", headers=w.h(w.o.owner)
        ).json()["executions"]
    ] == ["EXECUTED"]


# ---------------------------------------------------------------------------------- what cannot be requested


def test_forbidden_platform_unexecutable_and_routine_actions_are_refused_at_the_door(
    world: World,
) -> None:
    """Decision 12 matrix (never, even with signatures); Decision 11 section 07; Decision 10 T01."""
    w = world
    r = w.create(w.o.owner, "disable-core-audit", "tenant", {})
    assert r.status_code == 403 and code(r) == "APPROVAL_ACTION_FORBIDDEN"
    assert w.create(w.o.owner, "unlawful-cross-border-transfer", "x", {}).status_code == 403
    assert (
        code(w.create(w.o.owner, "manual-replacement-of-signing-keys-or-trust-anchors", "k", {}))
        == "APPROVAL_PLATFORM_ACTION"
    )
    assert (
        code(w.create(w.o.owner, "weaken-authentication-recovery-or-approval-policy", "a", {}))
        == "APPROVAL_ACTION_NOT_EXECUTABLE"
    )
    routine = w.create(w.o.owner, "update-organization-presentation-details", "org", {})
    assert (
        routine.status_code == 422 and code(routine) == "APPROVAL_NOT_REQUIRED"
    )  # A0: the owner acts alone, no vote
    assert code(w.create(w.o.owner, "invent-a-new-power", "x", {})) == "APPROVAL_ACTION_UNKNOWN"
    assert code(w.create(w.o.owner, "suspend-tenant-access", "t", {})) == "APPROVAL_USE_CONTAINMENT"
    denied = {a.reason_code for a in w.audit() if a.outcome == "DENIED"}
    assert {
        "APPROVAL_ACTION_FORBIDDEN",
        "APPROVAL_PLATFORM_ACTION",
        "APPROVAL_ACTION_NOT_EXECUTABLE",
    } <= denied
    # only an Owner or Administrator asks, and only a person
    viewer = w.person("VIEWER", passkey=False)
    assert (
        code(w.create(viewer, OWNER_TRANSFER, "t", {"principal_id": viewer}))
        == "APPROVAL_REQUEST_NOT_PERMITTED"
    )
    agent = w.person("ADMIN", passkey=False, kind=PrincipalType.SERVICE)
    assert (
        code(w.create(agent, OWNER_TRANSFER, "t", {"principal_id": viewer}))
        == "APPROVAL_REQUESTER_NOT_HUMAN"
    )
    # removing the last Owner is refused at the door: add the successor first
    assert (
        code(
            w.create(
                w.o.owner,
                "removal-of-last-owner-or-control-chain",
                "o",
                {"principal_id": w.o.owner},
            )
        )
        == "APPROVAL_WOULD_ORPHAN_TENANT"
    )


def test_the_policy_is_published_for_the_ui_to_explain(world: World) -> None:
    w = world
    p = w.http.get("/v1/approval-policy", headers=w.h(w.o.owner)).json()
    assert (
        p["enforcement"] == "SHADOW"
        and p["assured_review"] == "INACTIVE"
        and p["tiers"] == {"A0": 0, "A1": 1, "A2": 2}
    )
    rows = {a["action_id"]: a for a in p["actions"]}
    assert (
        rows[OWNER_TRANSFER]["votes_required"] == 2 and rows[OWNER_TRANSFER]["executable"] is True
    )
    assert rows["manual-replacement-of-signing-keys-or-trust-anchors"]["scope"] == "PLATFORM"
    weaken = rows["weaken-authentication-recovery-or-approval-policy"]
    assert weaken["executable"] is False and "shortened only" in weaken["no_operation"]
    assert (
        w.http.get(f"/v1/approval-policy/actions/{OWNER_TRANSFER}", headers=w.h(w.o.owner)).json()[
            "tier"
        ]
        == "A2"
    )
    assert w.http.get("/v1/approval-policy/actions/nope", headers=w.h(w.o.owner)).status_code == 422
    assert w.http.get("/v1/approval-policy").status_code == 401


# --------------------------------------------------------------------------------------------- solo organizations


def test_a_solo_owner_is_held_never_waived_and_reviewers_are_the_route_to_independence(
    world: World,
) -> None:
    """Decision 12 tests 01-05, 10; Decision 11 T12."""
    w = world
    s = w.http.get("/v1/governance/solo-status", headers=w.h(w.o.owner)).json()
    assert (
        s["solo_mode"] is True
        and s["eligible_human_member_count"] == 1
        and s["independent_routes"] == []
    )
    assert (
        s["message"].startswith("You can manage your organization on your own.")
        and s["legal_form_resolved"] is False
    )
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert (
        case["hold"] == "BLOCKED_INSUFFICIENT_APPROVERS"
        and case["independent_people_available"] == 0
    )
    assert (
        w.vote(w.o.owner, case, proof=False).status_code == 403
    )  # no self-approval, no solo exception
    assert code(w.execute(w.o.owner, case["case_id"])) == "APPROVAL_NOT_READY"

    # one independent person is still not two: A2 stays held, and being "less solo" loosens nothing
    a1 = w.person()
    s = w.http.get("/v1/governance/solo-status", headers=w.h(w.o.owner)).json()
    assert s["solo_mode"] is False and s["eligible_human_member_count"] == 2
    held = w.get(w.o.owner, case["case_id"])
    assert held["hold"] == "BLOCKED_INSUFFICIENT_APPROVERS" and held["approvals_required"] == 2
    a2 = w.person()
    assert w.get(w.o.owner, case["case_id"])["hold"] is None

    # an outside reviewer, appointed by the owner, is proposed until an independent route makes it real
    outsider = w.person("VIEWER")
    appoint = w.http.post(
        "/v1/governance/reviewers", headers=w.h(w.o.owner),
        json={"principal_id": outsider, "relationship": "EXTERNAL_PROFESSIONAL", "authority_basis": "Practising accountant, licence on file",
              "independence_declaration": "No financial or family tie to the organization"},
    )  # fmt: skip
    assert appoint.status_code == 201, appoint.text
    rv = appoint.json()
    assert (
        rv["status"] == "PROPOSED"
        and rv["kind"] == "REVIEWER"
        and rv["activation_case"]["tier"] == "A1"
    )
    # the same person cannot appoint themselves as their own independent check
    own = w.http.post(
        "/v1/governance/reviewers",
        headers=w.h(w.o.owner),
        json={
            "principal_id": w.o.owner,
            "relationship": "TRUSTEE",
            "authority_basis": "x",
            "independence_declaration": "x",
        },
    )
    assert own.status_code == 403 and code(own) == "REVIEWER_IS_REQUESTER"
    # a proposed reviewer cannot vote yet
    assert w.vote(outsider, case, proof=False).status_code == 403
    verified = w.run(
        lambda c: reviewer_svc.verify_by_platform(
            c, rv["reviewer_id"], evidence_ref="trust-ops/case-1"
        )
    )
    assert verified.status == "ACTIVE" and verified.activated_via == "PLATFORM_VERIFICATION"
    assert w.http.get("/v1/governance/solo-status", headers=w.h(w.o.owner)).json()[
        "independent_routes"
    ] == ["ORGANIZATION_APPOINTED_REVIEWER"]
    w.keys[outsider] = give_passkey(w.rt_url, w.o.tenant, outsider)
    assert (
        w.vote(outsider, case).status_code == 201
    )  # not an employee, still a valid independent approver
    assert w.vote(a1, case).json()["state"] == "APPROVED_READY"
    assert w.execute(w.o.owner, case["case_id"]).json()["outcome"] == "EXECUTED"
    # ending the appointment takes effect at once
    assert (
        w.http.post(
            f"/v1/governance/reviewers/{rv['reviewer_id']}:revoke", headers=w.h(w.o.owner)
        ).json()["status"]
        == "REVOKED"
    )
    assert a2


def test_a_reviewer_with_a_conflict_is_denied_and_the_activation_route_needs_evidence(
    world: World,
) -> None:
    """Decision 12 test 05."""
    w = world
    target = w.person(None)
    outsider = w.person("VIEWER")
    r = w.http.post(
        "/v1/governance/reviewers", headers=w.h(w.o.owner),
        json={"principal_id": outsider, "relationship": "CO_DIRECTOR", "authority_basis": "Co-director of record",
              "independence_declaration": "Declared: works with the requester", "declared_conflicts": [w.o.owner]},
    ).json()  # fmt: skip
    w.run(lambda c: reviewer_svc.verify_by_platform(c, r["reviewer_id"], evidence_ref="ops/1"))
    w.keys[outsider] = give_passkey(w.rt_url, w.o.tenant, outsider)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    denied = w.vote(outsider, case, proof=False)
    assert denied.status_code == 403 and code(denied) == "VOTER_REVIEWER_CONFLICT"

    async def no_evidence(c: Any) -> None:
        await reviewer_svc.verify_by_platform(c, r["reviewer_id"], evidence_ref="  ")

    with pytest.raises(ApprovalError):
        w.run(no_evidence)
    missing = w.http.post(
        "/v1/governance/reviewers",
        headers=w.h(w.o.owner),
        json={
            "principal_id": w.person(),
            "relationship": "TRUSTEE",
            "authority_basis": " ",
            "independence_declaration": "ok",
        },
    )
    assert missing.status_code == 403 and code(missing) == "REVIEWER_EVIDENCE_REQUIRED"


# ------------------------------------------------------------------------ recovery, containment, execution


def test_privileged_recovery_is_an_a2_case_with_a_narrow_expiring_capability(world: World) -> None:
    """Decision 11 T11 and section 07; Decision 12 test 08."""
    w = world
    admin = w.person("ADMIN")
    lost = w.person("APPROVER", passkey=False)
    a1, a2 = w.person(), w.person()
    bad = w.http.post(
        "/v1/recovery-cases",
        headers=w.h(admin),
        json={"principal_id": lost, "identity_reproof_ref": "", "reason": "lost"},
    )
    assert bad.status_code == 422  # re-proofing must be named
    nobody = w.http.post(
        "/v1/recovery-cases",
        headers=w.h(admin),
        json={"principal_id": w.person(None), "identity_reproof_ref": "idv/1", "reason": "lost"},
    )
    assert nobody.status_code == 422  # no privileged role: that is ordinary recovery, not this case
    r = w.http.post(
        "/v1/recovery-cases",
        headers=w.h(admin),
        json={
            "principal_id": lost,
            "identity_reproof_ref": "idv/case-7",
            "reason": "All authenticators lost",
        },
    )
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["tier"] == "A2" and case["action_id"] == "privileged-owner-or-admin-recovery"
    assert (
        w.vote(lost, case, proof=False).status_code == 403
    )  # the person being recovered cannot approve their own recovery
    assert w.vote(admin, case, proof=False).status_code == 403
    assert (
        w.vote(a1, case).status_code == 201 and w.vote(a2, case).json()["state"] == "APPROVED_READY"
    )
    done = w.execute(admin, case["case_id"]).json()
    assert (
        done["outcome"] == "EXECUTED"
        and done["result"]["capability"] == "SINGLE_USE_ENROLLMENT_TICKET"
    )
    assert done["shown_once"]["enrollment_ticket"]
    again = w.execute(admin, case["case_id"], "other").json()
    assert (
        again["replayed"] is True and "shown_once" not in again
    )  # the secret is never stored or repeated
    assert "enrollment_ticket" not in str([a.body for a in w.audit()])


def test_containment_runs_at_once_and_restoring_is_a_critical_case(world: World) -> None:
    """Decision 11 T10, T11; Decision 12 test 09: protection never waits, restoration does."""
    w = world
    a1, a2 = w.person(), w.person()
    admin = w.person("ADMIN", passkey=False)
    stranger = w.person("VIEWER", passkey=False)
    refused = w.http.post(
        "/v1/containment-actions",
        headers=w.h(stranger),
        json={"action_id": "suspend-tenant-access", "reason": "x"},
    )
    assert refused.status_code == 403
    assert (
        w.http.post(
            "/v1/containment-actions",
            headers=w.h(admin),
            json={"action_id": OWNER_TRANSFER, "reason": "x"},
        ).status_code
        == 422
    )
    done = w.http.post(
        "/v1/containment-actions",
        headers=w.h(admin),
        json={
            "action_id": "suspend-tenant-access",
            "payload": {"reason_code": "COMPROMISE"},
            "reason": "Credential theft suspected",
        },
    )
    assert done.status_code == 201, done.text
    assert (
        done.json()["post_incident_review"] == "REQUIRED"
        and done.json()["tenant_status"] == "SUSPENDED"
    )
    rec = next(a for a in w.audit("governance.containment.%") if a.outcome == "SUCCESS")
    assert (
        rec.body["details"]["post_incident_review"] == "REQUIRED"
        and rec.body["details"]["region"] == "uk-1"
    )

    # restoring access is not immediate: it is A2 and the earlier two-person route is not a shortcut once enforced
    case = w.create(
        w.o.owner,
        "reinstate-suspended-tenant-access",
        "tenant",
        {"reason_code": "INCIDENT_CLOSED"},
        "Incident reviewed",
    )
    assert case.status_code == 201, case.text
    c = case.json()
    assert c["tier"] == "A2" and w.vote(a1, c).json()["state"] == "PARTIALLY_APPROVED"
    assert w.vote(a2, c).json()["state"] == "APPROVED_READY"
    assert w.execute(w.o.owner, c["case_id"]).json()["case"]["state"] == "EXECUTED"
    status = w.run(lambda conn: tenant_store.load_tenant(conn, w.o.tenant))
    assert status.status.value != "SUSPENDED"


def test_a_failed_run_is_recorded_changes_nothing_and_may_be_retried(world: World) -> None:
    """Decision 10 section 04: failure is not retroactively success, and the unchanged request may run again."""
    w = world
    a1, a2 = w.person(), w.person()
    co_owner = w.person("OWNER", passkey=False)
    case = w.create(
        w.o.owner, "removal-of-last-owner-or-control-chain", "o", {"principal_id": co_owner}
    ).json()
    assert (
        w.vote(a1, case).status_code == 201 and w.vote(a2, case).json()["state"] == "APPROVED_READY"
    )
    w.revoke_role(co_owner)  # the target stopped being an Owner after the request was approved
    first = w.execute(w.o.owner, case["case_id"], "try-1")
    assert (
        first.status_code == 200
        and first.json()["outcome"] == "FAILED"
        and "guidance" in first.json()
    )
    assert first.json()["case"]["state"] == "FAILED" and first.json()["reason_code"]
    same = w.execute(w.o.owner, case["case_id"], "try-1").json()
    assert same["outcome"] == "FAILED" and same["replayed"] is True and same["attempt"] == 1
    second = w.execute(w.o.owner, case["case_id"], "try-2").json()
    assert second["outcome"] == "FAILED" and second["attempt"] == 2 and second["replayed"] is False
    ev = w.http.get(f"/v1/approval-cases/{case['case_id']}/evidence", headers=w.h(w.o.owner)).json()
    assert [e["outcome"] for e in ev["executions"]] == ["FAILED", "FAILED"] and ev[
        "state"
    ] == "FAILED"
    assert {a.event_type for a in w.audit()} >= {"governance.approval.failed"}


def test_privileged_role_grant_gives_the_enrollment_link_once(world: World) -> None:
    """Decision 11 section 02 row 4, through the case instead of the earlier two-person route."""
    w = world
    a1, a2 = w.person(), w.person()
    case = w.create(
        w.o.owner,
        "privileged-role-grant-or-elevation",
        "role:APPROVER",
        {"role": "APPROVER"},
        "New checker",
    ).json()
    assert w.vote(a1, case).status_code == 201 and w.vote(a2, case).status_code == 201
    done = w.execute(w.o.owner, case["case_id"]).json()
    assert (
        done["outcome"] == "EXECUTED"
        and done["shown_once"]["enrollment_ticket"]
        and done["result"]["role"] == "APPROVER"
    )
    assert (
        w.create(
            w.o.owner, "privileged-role-grant-or-elevation", "role:OWNER", {"role": "OWNER"}
        ).status_code
        == 422
    )


def test_a_changed_policy_or_region_invalidates_what_was_voted(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 11 T07: a different policy version blocks the earlier votes and a new request is needed."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert w.vote(a1, case).status_code == 201
    real = store.registry()
    from dataclasses import replace

    monkeypatch.setattr(store, "registry", lambda: replace(real, digest="f" * 64))
    r = w.vote(a2, case, proof=False)
    assert r.status_code == 409 and code(r) == "APPROVAL_INVALIDATED"
    monkeypatch.setattr(store, "registry", lambda: real)
    assert w.get(w.o.owner, case["case_id"])["state"] == "INVALIDATED"  # persisted, not rolled back
    ev = w.http.get(f"/v1/approval-cases/{case['case_id']}/evidence", headers=w.h(w.o.owner)).json()
    assert ev["transitions"][-1]["reason_code"] == "STALE_POLICY"


def test_the_earlier_two_person_path_is_refused_for_a2_once_the_owners_enforce(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 11 T09: the owners' switch. Shadow keeps the earlier path; enforce closes it for every A2 action."""
    w = world
    monkeypatch.setattr(store, "enforcement", lambda: "ENFORCE")
    target = w.person(None)
    r = w.http.post(
        "/v1/tenant/owners",
        headers=w.h(w.o.owner),
        json={"principal_id": target, "approval_request_id": "x"},
    )
    assert r.status_code == 409 and code(r) == "APPROVAL_CASE_REQUIRED"
    assert (
        w.http.post(
            "/v1/tenant/role-grants",
            headers=w.h(w.o.owner),
            json={"role": "ADMIN", "approval_request_id": "x"},
        ).status_code
        == 409
    )
    assert (
        w.http.post(
            "/v1/tenant:resume",
            headers=w.h(w.o.owner),
            json={"reason_code": "INCIDENT_CLOSED", "approval_request_id": "x"},
        ).status_code
        == 409
    )


def test_a_case_is_tenant_scoped(world: World, rt_url: str) -> None:
    """Decision 11 T06-era rule: a cross-tenant case or approver is never visible or countable."""
    w = world
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    other = onboard(rt_url)
    foreign = TestClient(app_for(rt_url, other, "shadow"), base_url=ISS)
    with foreign:
        r = foreign.get(f"/v1/approval-cases/{case['case_id']}", headers=other.bearer(other.owner))
        assert r.status_code == 404
        assert (
            foreign.get("/v1/approval-cases", headers=other.bearer(other.owner)).json()["items"]
            == []
        )
        assert (
            foreign.post(
                f"/v1/approval-cases/{case['case_id']}/execute",
                headers={**other.bearer(other.owner), "Idempotency-Key": "k"},
            ).status_code
            == 404
        )
    assert State.PENDING_APPROVAL.value == w.get(w.o.owner, case["case_id"])["state"]


def test_a1_is_one_independent_approval_and_an_owner_may_be_that_approver(world: World) -> None:
    """Decision 10 T02, Decision 12 test 02: an Owner who did not make the request satisfies A1; the requester never can."""
    w = world
    admin = w.person("ADMIN", passkey=False)
    outsider = w.person("VIEWER")
    made = w.http.post(
        "/v1/governance/reviewers", headers=w.h(admin),
        json={"principal_id": outsider, "relationship": "TRUSTEE", "authority_basis": "Trustee under the trust deed",
              "independence_declaration": "No tie to the requester"},
    )  # fmt: skip
    assert made.status_code == 201, made.text
    case = made.json()["activation_case"]
    assert case["tier"] == "A1" and case["approvals_required"] == 1 and case["hold"] is None
    assert (
        w.vote(admin, case, proof=False).status_code == 403
    )  # the requester cannot approve their own A1
    approved = w.vote(w.o.owner, case)
    assert approved.status_code == 201 and approved.json()["state"] == "APPROVED_READY"
    assert w.run(lambda c: store.reviewers(c, status="ACTIVE")) == []  # approved is not applied
    assert w.execute(admin, case["case_id"]).json()["outcome"] == "EXECUTED"
    [active] = w.run(lambda c: store.reviewers(c, status="ACTIVE"))
    assert active.activated_via == "A1_APPROVAL" and active.activation_ref == case["case_id"]


# ------------------------------------------------------------------------- gates, executors, platform operations


def test_machine_checked_gates_refuse_and_inform(world: World) -> None:
    """Decision 11 section 02, additional gates: an incoming Owner must hold a passkey; a role grant shows its effective permissions."""
    w = world
    no_key = w.person(None, passkey=False)
    refused = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": no_key})
    assert refused.status_code == 403 and code(refused) == "INCOMING_OWNER_STRONG_AUTHENTICATOR"
    assert "INCOMING_OWNER_STRONG_AUTHENTICATOR" in {
        a.reason_code for a in w.audit() if a.outcome == "DENIED"
    }
    keyed = w.person(None)
    ok = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": keyed}).json()
    assert {g["code"]: g["ok"] for g in ok["gates"]} == {
        "INCOMING_OWNER_STRONG_AUTHENTICATOR": True,
        "AFFECTED_PARTIES_NOTIFIED": True,
    }
    grant = w.create(
        w.o.owner, "privileged-role-grant-or-elevation", "role:ADMIN", {"role": "ADMIN"}
    ).json()
    [impact] = [g for g in grant["gates"] if g["code"] == "EFFECTIVE_PERMISSIONS"]
    assert impact["ok"] and impact["blocking"] is False and "ADMIN could perform" in impact["note"]


def test_a_gate_that_stops_holding_blocks_the_run(world: World) -> None:
    """The gate is checked again at execution: an incoming Owner whose passkey is gone is not made an Owner by an old approval."""
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)

    async def drop_keys(c: Any) -> None:
        await c.execute(
            text("UPDATE authn.authenticator SET status = 'REVOKED' WHERE principal_id = :p"),
            {"p": uuid.UUID(target)},
        )

    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert w.vote(a1, case).status_code == 201 and w.vote(a2, case).status_code == 201
    try:
        w.run(drop_keys)
    except DBAPIError:
        pytest.skip("authenticators cannot be revoked by update here")
    done = w.execute(w.o.owner, case["case_id"]).json()
    assert (
        done["outcome"] == "FAILED" and done["reason_code"] == "INCOMING_OWNER_STRONG_AUTHENTICATOR"
    )
    assert target not in w.owner_roles()


def test_an_a1_invitation_and_a_trust_adjudication_request_have_executors(world: World) -> None:
    w = world
    admin = w.person("ADMIN", passkey=False)
    owner2 = w.person("OWNER")
    a1, a2 = w.person(), w.person()
    bad = w.create(admin, "invite-nonprivileged-team-member", "member", {"role": "ADMIN"})
    assert (
        bad.status_code == 422 and code(bad) == "APPROVAL_PAYLOAD_INVALID"
    )  # a privileged role is its own A2 action
    case = w.create(
        admin, "invite-nonprivileged-team-member", "member", {"role": "OPERATOR"}
    ).json()
    assert case["tier"] == "A1" and w.vote(admin, case, proof=False).status_code == 403
    assert w.vote(owner2, case).json()["state"] == "APPROVED_READY"
    done = w.execute(admin, case["case_id"]).json()
    assert (
        done["outcome"] == "EXECUTED"
        and done["result"]["role"] == "OPERATOR"
        and done["shown_once"]["enrollment_ticket"]
    )

    adj = w.create(w.o.owner, "manual-reversal-of-high-assurance-trust-decision", "decision:d1",
                   {"decision_ref": "verification/org-7", "grounds": "The register entry was corrected."}).json()  # fmt: skip
    assert w.vote(a1, adj).status_code == 201 and w.vote(a2, adj).status_code == 201
    out = w.execute(w.o.owner, adj["case_id"]).json()
    assert out["result"] == {
        "adjudication": "REQUESTED",
        "status_changed": False,
        "decision_ref": "verification/org-7",
    }

    async def announced(c: Any) -> int:
        return int(
            (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND event_type LIKE '%governance.adjudication.requested%'"
                    ),
                    {"t": uuid.UUID(w.o.tenant)},
                )
            ).scalar_one()
        )

    assert (
        w.run(announced) == 1
    )  # asked for, announced, and no status was changed by tenant votes alone
    unknown = w.create(
        w.o.owner,
        "powerful-agent-or-integration-authority",
        "agent",
        {"agent_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 422 and code(unknown) == "APPROVAL_PAYLOAD_INVALID"


def test_a_platform_operation_needs_a_consumed_approval_and_cannot_be_bypassed(
    world: World,
) -> None:
    """Decision 11 T09: a CLI, job or support tool is refused without two independent approvals, and one approval buys one operation."""
    w = world
    w.http.app.state.platform_tenant_id = (
        w.o.tenant
    )  # this organization plays the platform operations tenant
    p1, p2 = w.person("OWNER"), w.person("OWNER")
    action, target = "root-super-admin-or-global-break-glass-grant", "principal:op-2"
    payload = {"principal_id": "op-2", "roles": ["SECURITY"]}

    async def consume(p: dict[str, Any], *, who: str = "tool", now: datetime | None = None) -> str:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            try:
                got = await svc.consume_authorization(
                    c,
                    w.o.tenant,
                    action_id=action,
                    target_id=target,
                    payload=p,
                    consumer=who,
                    now=now,
                )
            except ApprovalError as e:
                return e.code
            return got.state.value

    assert (
        asyncio.run(consume(payload)) == "APPROVAL_AUTHORIZATION_MISSING"
    )  # no approval: the operation does not run
    case = w.create(w.o.owner, action, target, payload, "A second operator is needed").json()
    assert case["tier"] == "A2" and case["action_id"] == action
    assert w.vote(p1, case).status_code == 201
    assert (
        asyncio.run(consume(payload)) == "APPROVAL_AUTHORIZATION_MISSING"
    )  # one of two: still nothing
    assert w.vote(p2, case).json()["state"] == "APPROVED_READY"
    assert (
        asyncio.run(consume(payload)) == "APPROVAL_AUTHORIZATION_MISSING"
    )  # approved is not authorized until it is executed
    assert w.execute(w.o.owner, case["case_id"]).json()["result"] == {
        "authorization": "GRANTED",
        "single_use": True,
    }
    assert (
        asyncio.run(consume({**payload, "roles": ["SUPER_ADMIN"]}))
        == "APPROVAL_AUTHORIZATION_MISSING"
    )  # any other payload
    assert (
        asyncio.run(consume(payload, now=datetime.now(UTC) + timedelta(days=2)))
        == "APPROVAL_AUTHORIZATION_MISSING"
    )  # stale
    assert asyncio.run(consume(payload)) == "EXECUTED"  # exactly this operation, now
    assert (
        asyncio.run(consume(payload)) == "APPROVAL_AUTHORIZATION_MISSING"
    )  # used up: one approval, one operation
    kinds = {a.event_type for a in w.audit("governance.authorization.%")}
    assert kinds == {"governance.authorization.consumed", "governance.authorization.refused"}

    # a customer organization cannot approve a platform action at all
    w.http.app.state.platform_tenant_id = None
    assert code(w.create(w.o.owner, action, target, payload)) == "APPROVAL_PLATFORM_ACTION"
    # and customer actions cannot be used as platform authorizations
    assert asyncio.run(consume(payload)) == "APPROVAL_AUTHORIZATION_MISSING"
    with pytest.raises(ApprovalError):
        w.run(
            lambda c: svc.consume_authorization(
                c, w.o.tenant, action_id=OWNER_TRANSFER, target_id="t", payload={}, consumer="x"
            )
        )


def test_registering_another_operator_needs_a_platform_approval_in_a_deployed_environment(
    world: World,
) -> None:
    """The bootstrap command is the first real consumer: after the first operator, a deployed environment needs an approval."""
    from types import SimpleNamespace

    from zid_platform.entrypoints.bootstrap_operator import (
        BootstrapError,
        require_platform_authorization,
    )
    from zid_platform.modules.operator_access.application.service import register_operator
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    w = world
    w.http.app.state.platform_tenant_id = w.o.tenant
    first, second = w.person(None, passkey=False), w.person(None, passkey=False)
    p1, p2 = w.person("OWNER"), w.person("OWNER")
    deployed, local = (
        SimpleNamespace(environment="production"),
        SimpleNamespace(environment="local"),
    )
    roles = frozenset({next(iter(OperatorRole))})

    async def attempt(settings: Any, who: str) -> str:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            try:
                await require_platform_authorization(c, settings, w.o.tenant, who, roles)
            except BootstrapError:
                return "REFUSED"
            return "ALLOWED"

    async def register(who: str) -> None:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            await register_operator(
                c,
                tenant_id=w.o.tenant,
                principal_id=who,
                jurisdiction="UK",
                roles=roles,
                now=datetime.now(UTC),
            )

    assert (
        asyncio.run(attempt(deployed, first)) == "ALLOWED"
    )  # the bootstrap exception: nobody independent exists yet
    asyncio.run(register(first))
    assert asyncio.run(attempt(local, second)) == "ALLOWED"  # local and test are not gated
    assert (
        asyncio.run(attempt(deployed, second)) == "REFUSED"
    )  # a second operator needs two independent approvers
    payload = {"principal_id": second, "roles": sorted(r.value for r in roles)}
    case = w.create(
        w.o.owner, "root-super-admin-or-global-break-glass-grant", f"principal:{second}", payload
    ).json()
    assert w.vote(p1, case).status_code == 201 and w.vote(p2, case).status_code == 201
    assert w.execute(w.o.owner, case["case_id"]).json()["outcome"] == "EXECUTED"
    assert asyncio.run(attempt(deployed, second)) == "ALLOWED"
    assert asyncio.run(attempt(deployed, second)) == "REFUSED"  # the approval was used up
