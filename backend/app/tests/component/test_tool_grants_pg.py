"""WP-11 (ZID-GAP-001): a human approval bound to one call, and a grant that is spent once, at the tool's own server.

An irreversible tool runs only after a person approves this exact call (action, resource, arguments digest): the approved
path works, and changing one byte of the arguments makes the approval stop matching. A grant is then redeemed by the tool's
server: once; never by another server; never for other arguments; never with a tampered signature; never after the sponsor
has left in the meantime; and two parallel redemptions cannot both win.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from gateway_fixture import ISS, READ, World, uid
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_evidence import verify_bundle
from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalPolicy,
    CompositeMode,
    Verdict,
)
from zid_platform.modules.authority.infrastructure.approval_store import (
    create_request,
    record_decision,
    tx_hash,
)
from zid_platform.modules.evidence.application import replay
from zid_platform.modules.evidence.application.runtime import current_runtime
from zid_platform.modules.evidence.ports import worker_pass
from zid_platform.modules.machine.domain.tool_gateway import args_digest, call_context
from zid_platform.modules.machine.infrastructure import grant_store

CALL = "/v1/machine/tool-calls:authorize"
REDEEM = "/v1/machine/tool-grants:redeem"
ARGS = args_digest({"doc": "contract-1", "amount": "100.00"})


def body(tool_id: str = "docs.read", digest: str = ARGS, **over: Any) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "resource_type": "agent",
        "resource_id": "any",
        "arguments_digest": digest,
        **over,
    }


def codes(r: Any) -> list[str]:
    return [d["issue"] for d in r.json()["error"]["details"] if d.get("field") is None]


def approve(w: World, digest: str) -> str:
    """A person approves exactly this call: the approval is bound to `action|resource|arguments digest`."""
    request_id = uid()
    context = call_context(READ, "agent:any", digest)

    async def go() -> None:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            await create_request(
                c,
                tenant_id=w.tenant,
                request_id=request_id,
                requester_id=w.child,
                subject_ref="tool:docs.sign",
                transaction_context=context,
                policy=ApprovalPolicy(CompositeMode.SINGLE, ("approver",)),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await record_decision(
                c,
                tenant_id=w.tenant,
                request_id=request_id,
                decision=ApprovalDecision(
                    approver_principal_id=w.approver,
                    approver_roles=frozenset({"approver"}),
                    authority_grant_id=w.approver_grant,
                    authority_valid=True,
                    verdict=Verdict.APPROVE,
                    transaction_context_hash=tx_hash(context).hex(),
                    decided_at=datetime.now(UTC),
                    session_id="",
                ),
                transaction_context=context,
            )

    w.run(go())
    return request_id


def test_a_human_approval_is_bound_to_the_exact_call(rt_url: str, owner_url: str) -> None:
    w = World(rt_url, owner_url).start()
    with TestClient(w.app, base_url=ISS) as http:
        agent = w.bearer(w.child)
        refused = http.post(CALL, headers=agent, json=body("docs.sign"))
        assert refused.status_code == 403 and "HUMAN_APPROVAL_REQUIRED" in codes(refused)

        approval = approve(w, ARGS)
        ok = http.post(CALL, headers=agent, json=body("docs.sign", approval_request_id=approval))
        assert ok.status_code == 200, (
            ok.text
        )  # the approved path: this exact call, approved by a person

        # one byte of the arguments changes and the approval no longer matches: the payload that was approved is not this one
        other = args_digest({"doc": "contract-1", "amount": "100.01"})
        moved = http.post(
            CALL, headers=agent, json=body("docs.sign", digest=other, approval_request_id=approval)
        )
        assert moved.status_code == 403 and "HUMAN_APPROVAL_REQUIRED" in codes(moved)

        # a person cannot approve their own request: the requester voting is not a second pair of eyes
        # (covered by the approval service's own tests); here the agent's own word is never an approval
        assert (
            http.post(
                CALL, headers=agent, json=body("docs.sign", approval_request_id=uid())
            ).status_code
            == 403
        )


def test_a_grant_is_spent_once_at_the_tools_own_server(rt_url: str, owner_url: str) -> None:
    w = World(rt_url, owner_url).start()
    with TestClient(w.app, base_url=ISS) as http:
        agent, server, other = w.bearer(w.child), w.bearer(w.server), w.bearer(w.other_server)

        def grant() -> str:
            r = http.post(CALL, headers=agent, json=body())
            assert r.status_code == 200, r.text
            return str(r.json()["grant"])

        def redeem(token: str, headers: dict[str, str], digest: str = ARGS) -> Any:
            return http.post(
                REDEEM, headers=headers, json={"grant": token, "arguments_digest": digest}
            )

        # --- the server it was made for spends it, once, for the arguments it was made for
        g1 = grant()
        done = redeem(g1, server)
        assert done.status_code == 200, done.text
        assert (
            done.json()["agent_chain"] == [w.child, w.root]
            and done.json()["tool_id"] == "docs.read"
        )
        again = redeem(g1, server)
        assert again.status_code == 409 and again.json()["error"]["code"] == "TOOL_GRANT_USED"

        # --- not another server, not other arguments, not a tampered grant, not a person who found it
        g2 = grant()
        stolen = redeem(g2, other)
        assert stolen.status_code == 403 and stolen.json()["error"]["code"] == "TOOL_GRANT_AUDIENCE"
        wrong = redeem(g2, server, args_digest({"doc": "contract-2"}))
        assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "TOOL_GRANT_ARGUMENTS"
        head, payload, sig = g2.split(".")
        forged = redeem(f"{head}.{payload}.{'A' * len(sig)}", server)
        assert forged.status_code == 403 and forged.json()["error"]["code"] == "TOOL_GRANT_INVALID"
        person = redeem(g2, w.bearer(w.human, scope=frozenset()))
        assert person.status_code == 403  # a person is not the grant's audience
        assert redeem(g2, server).status_code == 200  # none of those refusals spent it

        # --- the sponsor leaves between signing and redeeming: the call stops at the moment of execution
        g3 = grant()
        w.disable_sponsor()
        late = redeem(g3, server)
        assert late.status_code == 403 and late.json()["error"]["code"] == "SPONSOR_NOT_ACTIVE"

    rows = w.owner(
        "SELECT event_type, outcome FROM evidence.audit_record WHERE tenant_id = :t AND event_type LIKE 'tool_call.%'"
    )
    seen = {(r.event_type, r.outcome) for r in rows}
    assert ("tool_call.redeemed", "SUCCESS") in seen
    assert ("tool_call.refused", "DENIED") in seen  # the stopped call is on the trail
    used = w.owner(
        "SELECT count(*) AS n FROM agent.tool_grant WHERE tenant_id = :t AND consumed_at IS NOT NULL"
    )
    assert used[0].n == 2  # g1 and g2; g3 was stopped before it was spent


def test_two_parallel_redemptions_cannot_both_win(rt_url: str, owner_url: str) -> None:
    w = World(rt_url, owner_url).start()
    grant_id, now = str(uuid.uuid4()), datetime.now(UTC)

    async def go() -> list[Any]:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            await grant_store.save_grant(
                c, w.tenant, grant_id=grant_id, agent_id=w.child, tool_id="docs.read", audience=w.server,
                resource="agent:any", args_sha256=ARGS, decision_id="d1", issued_at=now,
                expires_at=now + timedelta(minutes=2),
            )  # fmt: skip

        async def spend(by: str) -> Any:
            async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
                return await grant_store.consume(c, grant_id, by=by, now=now)

        return list(await asyncio.gather(*(spend(w.server) for _ in range(8))))

    results = w.run(go())
    assert (
        sum(r is not None for r in results) == 1
    )  # exactly one of eight parallel attempts spends it


def test_a_gateway_decision_verifies_offline_without_zoiko(rt_url: str, owner_url: str) -> None:
    """ZID-GAP-001 WP-19: the receipt of an allowed tool call is sealed in the ledger, exported, and an outside verifier that holds only the
    published evidence keys accepts it; the same bundle with one field changed is refused."""
    w = World(rt_url, owner_url).start()
    with TestClient(w.app, base_url=ISS) as http:
        allowed = http.post(CALL, headers=w.bearer(w.child), json=body())
        assert allowed.status_code == 200, allowed.text
        decision_id = allowed.json()["decision_id"]

    async def run() -> dict[str, Any]:
        async with (
            engine(owner_url) as eng,
            eng.begin() as c,
        ):  # the worker role sequences and seals
            await worker_pass(c, force_seal=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            receipt_id = str(
                (
                    await c.execute(
                        text(
                            "SELECT receipt_id FROM evidence.decision_receipt WHERE decision_id = :d"
                        ),
                        {"d": uuid.UUID(decision_id)},
                    )
                ).scalar_one()
            )
            return await replay.export_package(
                c, w.tenant, receipt_id, mode="CUSTOMER_VERIFY", purpose="self-verification",
                case_ref="gw-1", custodian_principal_id=w.sponsor,
            )  # fmt: skip

    package = w.run(run())
    pinned = (
        current_runtime().trusted_keys()
    )  # what an outsider would take from the published keys endpoint
    report = verify_bundle(package["bundle"], trusted_keys=pinned)
    assert report.ok, (
        report.to_dict()
    )  # integrity, chain position, Merkle path and checkpoint signature, offline

    tampered = {**package["bundle"], "record": {**package["bundle"]["record"], "outcome": "DENY"}}
    bad = verify_bundle(tampered, trusted_keys=pinned)
    assert not bad.ok and "record_hash" in {
        c.name for c in bad.checks if not c.ok
    }  # a changed record is caught
