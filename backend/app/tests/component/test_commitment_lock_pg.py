"""The customer's approval tiers have no authority until the policy is signed (ZID-UX-COM-008 UI-13, ZID-COM-CL-004 section 07).

In a deployed environment a customer's signature on a commitment counts only while the Founder and the CFO have signed the policy file whose thresholds
placed it in its tier. Until then the request can be opened and read, but nobody can approve or reject it, and the refusal is a coded answer, not a crash."""

from __future__ import annotations

import asyncio

import pytest
from conftest import ctx, engine
from test_commitment_pg import book, owners, refused, signed_order, uid
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import commitment_service as commitments


def test_a_commitment_cannot_be_decided_in_a_deployed_environment_on_an_unsigned_policy(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, owner, member = uid(), uid(), uid()
    owners(monkeypatch, owner)

    async def run() -> None:
        b = await book(rt_url, owner_url)
        order = await signed_order(rt_url, b, tenant)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req = await commitments.request(
                c, tenant_id=tenant, order_id=order, requested_by=member
            )
            digest = req["snapshot_hash"]

            def decide(env: str, decision: str = "APPROVE") -> object:
                return commitments.decide(
                    c, tenant_id=tenant, request_id=req["request_id"], approver=owner, decision=decision, presented_hash=digest,
                    reason="no" if decision == "REJECT" else None, step_up_at=None, environment=env,
                )  # fmt: skip

            for env in ("production", "staging", "pilot"):
                await refused(decide(env), "POLICY_NOT_SIGNED")
                await refused(decide(env, "REJECT"), "POLICY_NOT_SIGNED")
            assert (await commitments.get(c, req["request_id"]))[
                "state"
            ] == "PENDING_APPROVAL"  # nothing was recorded

            # the sandbox environments skip the lock, which is how the rest of the suite runs
            assert (await decide("test"))["state"] == "APPROVED"  # type: ignore[index]

    asyncio.run(run())


def test_a_commitment_approval_and_a_delegation_need_a_strong_proof_where_it_is_enforced(
    rt_url: str, owner_url: str
) -> None:
    """CL04-C17: every high-risk customer approval carries the hash of what was read and, where step-up is enforced, a fresh proof bound to exactly it;
    a refused proof records nothing."""
    from fastapi.testclient import TestClient
    from onboarding_fixture import AUD, ISS, onboard
    from test_role_matrix_pg import _seed_role
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    order = asyncio.run(signed_order(rt_url, asyncio.run(book(rt_url, owner_url)), o.tenant))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    app.state.step_up_enforcement = "enforce"
    with TestClient(app) as api:
        own = o.bearer(o.owner)
        path = "/v1/billing/commitment-requests"
        req = api.post(path, json={"order_id": order}, headers=own).json()
        for proof in (None, "not-a-proof"):
            headers = {**own, **({"x-step-up-proof": proof} if proof else {})}
            for verb in ("approve", "reject"):
                r = api.post(
                    f"{path}/{req['request_id']}:{verb}",
                    json={"snapshot_hash": req["snapshot_hash"], "reason": "no"},
                    headers=headers,
                )
                assert r.status_code == 403 and r.json()["error"]["code"].startswith("STEP_UP"), (
                    r.text
                )
        assert (
            api.get(f"{path}/{req['request_id']}", headers=own).json()["state"]
            == "PENDING_APPROVAL"
        )
        assert api.get(f"{path}/{req['request_id']}", headers=own).json()["approvals"] == []

        body = {"principal_id": o.second, "kind": "PROCUREMENT_APPROVER", "cap_usd": "20000.00"}
        r = api.post("/v1/billing/commitment-delegates", json=body, headers=own)
        assert r.status_code == 403 and r.json()["error"]["code"].startswith("STEP_UP"), r.text
        assert api.get("/v1/billing/commitment-delegates", headers=own).json()["items"] == []
