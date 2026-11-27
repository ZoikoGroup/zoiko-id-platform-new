"""What becomes of an authorized tool call, and its signed receipts (ZID-CP-WF07 v2.0 .07, .10, .11; CR-169), against PostgreSQL with real tokens and the real gateway.

What these hold: a grant is also the start of an action, whose first receipt says "authorized, nothing run"; only the tool's own server can report on it, and only after it redeemed the grant; an UNKNOWN outcome is its own state
and never reads as a success; a repeat of a report changes nothing and a different report after a final outcome is refused; every revision's receipt verifies, still does after a key rotation, and cannot be edited; capacity held for an
action is committed after it ran, given back when it did not, and kept held while nobody knows; and the dry run of a stop changes nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, peek_header
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.ports import configure_runtime, current_runtime, verify_evidence
from zid_platform.modules.machine.application import tool_actions as svc
from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    ReviewStatus,
    SideEffect,
    Tool,
    ToolStatus,
)
from zid_platform.modules.machine.domain.tool_action import RECEIPT_TYP
from zid_platform.modules.machine.domain.tool_gateway import args_digest
from zid_platform.modules.machine.infrastructure.machine_store import (
    save_agent,
    save_blueprint,
    save_tool,
)
from zid_platform.modules.mandate.domain.ledger import Amounts, Budget, MandateStatus
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    MandateRecord,
    create_mandate,
    read_balance,
    reserve_capacity,
)
from zid_platform.modules.mandate.ports import capacity_check, settle_for_action
from zid_platform.modules.organization.infrastructure.root_store import create_organization
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
DIGEST = "sha256:" + "a" * 64
ARGS = args_digest({"invoice": "demo_492"})
READ = "machine.agent.read"
CELL_ID = uuid.UUID(CELL)


def uid() -> str:
    return str(uuid.uuid4())


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_a_grant_starts_an_action_only_its_server_reports_and_unknown_is_never_a_success(
    rt_url: str, owner_url: str
) -> None:
    tenant, sponsor, operator, publisher, server, other_server, org, human = (
        uid() for _ in range(8)
    )
    root, child = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-act"))

    def profile(agent_id: str, parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, tenant, f"bot-{agent_id[:4]}", "bp", "1", publisher, operator, sponsor, org, "", frozenset({"docs.read"}), 2 if parent is None else 0, AgentStatus.DRAFT, parent_agent_id=parent
        )  # fmt: skip

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (publisher, PrincipalType.HUMAN),
                (human, PrincipalType.HUMAN),
                (server, PrincipalType.SERVICE),
                (other_server, PrincipalType.SERVICE),
            ):
                await create_principal(
                    c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
            await save_blueprint(
                c,
                tenant,
                BlueprintRelease("bp", "1", DIGEST, DIGEST, publisher, ReviewStatus.APPROVED),
            )
            await save_tool(
                c,
                tenant,
                Tool(
                    "docs.read",
                    "1",
                    server,
                    READ,
                    1,
                    SideEffect.NONE,
                    False,
                    False,
                    ToolStatus.DRAFT,
                ),
            )
            await save_agent(c, profile(root))
            await save_agent(c, profile(child, root))
            for agent_id in (root, child):
                await create_principal(
                    c,
                    Principal(
                        agent_id,
                        tenant,
                        PrincipalType.AI_AGENT,
                        PrincipalStatus.ACTIVE,
                        "uk-1",
                        sponsor_principal_id=sponsor,
                    ),
                )
            await make_admin(c, tenant, [root, child, human], [READ], ["agent:*"])
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
            await c.execute(
                text("UPDATE agent.tool_capability SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )

    async def session_for(p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    p,
                    t - timedelta(seconds=30),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    t + timedelta(hours=8),
                ),
            )
        return sid

    async def db(sql: str, **params: Any) -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return list((await c.execute(text(sql), {"t": uuid.UUID(tenant), **params})).all())

    asyncio.run(seed())
    sessions = {p: asyncio.run(session_for(p)) for p in (root, child, human, server, other_server)}
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_uuid=CELL,
        ),
        keyring=ring,
    )

    def bearer(sub: str, *, scope: frozenset[str] = frozenset({"workload"})) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="agent-runtime",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=scope,
        )
        return {"Authorization": f"Bearer {tok}"}

    def counts() -> Any:
        return asyncio.run(
            db(
                "SELECT (SELECT count(*) FROM agent.tool_grant WHERE tenant_id = :t) AS grants, (SELECT count(*) FROM agent.tool_action WHERE tenant_id = :t) AS actions, "
                "(SELECT count(*) FROM agent.tool_action_receipt WHERE tenant_id = :t) AS receipts, (SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t) AS audit, "
                "(SELECT count(*) FROM evidence.decision_receipt WHERE tenant_id = :t) AS decisions"
            )
        )[0]

    with TestClient(app, base_url=ISS) as http:
        # a rehearsal of the real gateway: the answer is the gateway's, and nothing at all is left behind
        quiet = counts()
        call = {
            "tool_id": "docs.read",
            "resource_type": "agent",
            "resource_id": "any",
            "arguments_digest": ARGS,
        }
        rehearsal = http.post("/v1/machine/tool-calls:preflight", headers=bearer(child), json=call)
        assert rehearsal.status_code == 200, rehearsal.text
        r = rehearsal.json()
        assert (
            r["decision"] == "ALLOW"
            and r["reason_codes"] == []
            and r["simulated"] is True
            and r["agent_chain"] == [child, root]
        )
        assert (
            "grant" not in r
            and not any(r["side_effects"].values())
            and r["canonical_request_hash"].startswith("sha256:")
        )
        assert (
            counts() == quiet
        )  # no grant, action, receipt, audit record or decision receipt was kept
        assert (
            http.post("/v1/machine/tool-calls:preflight", headers=bearer(child), json=call).json()[
                "canonical_request_hash"
            ]
            == r["canonical_request_hash"]
        )
        assert (
            http.post(
                "/v1/machine/tool-calls:preflight",
                headers=bearer(child),
                json={**call, "arguments_digest": args_digest({"invoice": "other"})},
            ).json()["canonical_request_hash"]
            != r["canonical_request_hash"]
        )
        denied = http.post(
            "/v1/machine/tool-calls:preflight",
            headers=bearer(child),
            json={**call, "tool_id": "docs.ghost"},
        )
        assert (
            denied.status_code == 200
            and denied.json()["decision"] == "DENY"
            and "TOOL_NOT_REGISTERED" in denied.json()["reason_codes"]
        )
        assert counts() == quiet  # a refusal is rehearsed without a trace too
        assert (
            http.post(
                "/v1/machine/tool-calls:preflight",
                headers=bearer(child, scope=frozenset()),
                json=call,
            ).status_code
            == 403
        )
        assert (
            http.post(
                "/v1/machine/tool-calls:preflight",
                headers=bearer(child),
                json={**call, "agent_id": root},
            ).status_code
            == 422
        )  # the agent is never a body field
        assert counts() == quiet

        ok = http.post(
            "/v1/machine/tool-calls:authorize",
            headers=bearer(child),
            json={
                "tool_id": "docs.read",
                "resource_type": "agent",
                "resource_id": "any",
                "arguments_digest": ARGS,
            },
        )
        assert ok.status_code == 200, ok.text
        grant = ok.json()["grant"]
        after = counts()
        assert (after.grants, after.actions, after.receipts) == (
            quiet.grants + 1,
            quiet.actions + 1,
            quiet.receipts + 1,
        )  # the real call leaves exactly what the rehearsal did not
        row = asyncio.run(
            db(
                "SELECT action_id::text AS a, state, revision, agent_chain FROM agent.tool_action WHERE tenant_id = :t"
            )
        )
        assert (
            len(row) == 1
            and row[0].state == "AUTHORIZED"
            and list(row[0].agent_chain) == [child, root]
        )
        action_id = row[0].a
        first = asyncio.run(
            db(
                "SELECT revision, state, payload FROM agent.tool_action_receipt WHERE action_id = CAST(:a AS uuid) ORDER BY revision",
                a=action_id,
            )
        )
        assert [r.revision for r in first] == [1] and first[0].payload[
            "conclusion"
        ] == "AUTHORIZED_NOT_YET_RUN"
        assert (
            first[0].payload["outcomes"]["executed"] is False
            and first[0].payload["arguments_sha256"] == ARGS
            and "limits" in first[0].payload
        )

        url = f"/v1/machine/tool-actions/{action_id}:finalize"

        def report(who: str, outcome: str, **extra: Any) -> Any:
            return http.post(url, headers=bearer(who), json={"outcome": outcome, **extra})

        # nothing can be reported before the server redeemed the grant, and nobody else can report at all
        early = report(server, "EXECUTED")
        assert early.status_code == 409 and code(early) == "TOOL_ACTION_NOT_DISPATCHED"
        assert (
            report(other_server, "EXECUTED").status_code == 404
        )  # another server gets the answer for an action that does not exist
        assert report(child, "EXECUTED").status_code == 404  # and the agent cannot vouch for itself
        redeemed = http.post(
            "/v1/machine/tool-grants:redeem",
            headers=bearer(server),
            json={"grant": grant, "arguments_digest": ARGS},
        )
        assert redeemed.status_code == 200, redeemed.text
        assert (
            asyncio.run(
                db(
                    "SELECT state FROM agent.tool_action WHERE action_id = CAST(:a AS uuid)",
                    a=action_id,
                )
            )[0].state
            == "DISPATCHED"
        )
        assert code(report(server, "SUCCEEDED")) == "TOOL_ACTION_INVALID"
        assert (
            report(server, "DISPATCHED").status_code == 422
        )  # a server reports an outcome, not a state of its own choosing

        # a timeout is UNKNOWN: its own state, its own receipt, and never a success
        unknown = report(server, "UNKNOWN", downstream_ref="erp-call-77")
        assert unknown.status_code == 200, unknown.text
        u = unknown.json()
        assert (
            u["state"] == "UNKNOWN"
            and u["conclusion"] == "OUTCOME_UNKNOWN"
            and u["downstream_ref"] == "erp-call-77"
        )
        pay = u["latest_receipt"]["payload"]
        assert (
            pay["outcomes"]["unknown"] is True
            and pay["outcomes"]["executed"] is False
            and pay["conclusion"] == "OUTCOME_UNKNOWN"
        )
        revisions_after_unknown = asyncio.run(
            db(
                "SELECT count(*) AS n FROM agent.tool_action_receipt WHERE action_id = CAST(:a AS uuid)",
                a=action_id,
            )
        )[0].n
        again = report(server, "UNKNOWN")
        assert (
            again.status_code == 200 and again.json()["revision"] == u["revision"]
        )  # the same report twice changes nothing
        assert (
            asyncio.run(
                db(
                    "SELECT count(*) AS n FROM agent.tool_action_receipt WHERE action_id = CAST(:a AS uuid)",
                    a=action_id,
                )
            )[0].n
            == revisions_after_unknown
        )

        # the owner of the execution reconciles it: now it ran. A final outcome is not changed by a different report.
        done = report(server, "EXECUTED")
        assert (
            done.status_code == 200
            and done.json()["state"] == "EXECUTED"
            and done.json()["conclusion"] == "EXECUTED_AS_REPORTED"
        )
        final = report(server, "FAILED")
        assert final.status_code == 409 and code(final) == "TOOL_ACTION_STATE_INVALID"
        assert report(server, "EXECUTED").json()["revision"] == done.json()["revision"]

        # people can read it: every receipt, each one verified now
        listing = http.get(
            "/v1/machine/tool-actions", headers=bearer(human), params={"state": "EXECUTED"}
        )
        assert listing.status_code == 200 and [i["action_id"] for i in listing.json()["items"]] == [
            action_id
        ]
        receipts = http.get(f"/v1/machine/tool-actions/{action_id}/receipts", headers=bearer(human))
        assert receipts.status_code == 200, receipts.text
        items = receipts.json()["items"]
        assert [i["state"] for i in items] == ["AUTHORIZED", "UNKNOWN", "EXECUTED"] and all(
            i["signature_valid"] is True for i in items
        )
        assert [i["payload"]["conclusion"] for i in items] == [
            "AUTHORIZED_NOT_YET_RUN",
            "OUTCOME_UNKNOWN",
            "EXECUTED_AS_REPORTED",
        ]
        claims = verify_evidence(items[-1]["signature"], RECEIPT_TYP)
        assert (
            claims["receipt_for"] == action_id
            and claims["agent_chain"] == [child, root]
            and peek_header(items[-1]["signature"])["kid"] == items[-1]["key_id"]
        )
        # receipts are signed by the EVIDENCE key, not the key that signs access tokens: a token-key holder cannot forge one
        token_kids = {k["kid"] for k in ring.jwks()["keys"]}
        assert items[-1]["key_id"] not in token_kids
        import pytest as _pytest

        with _pytest.raises(Exception):  # noqa: B017 - the token keyring does not verify it
            ring.verify(items[-1]["signature"], typ=RECEIPT_TYP)
        assert "person" not in str(claims).lower() and ARGS in str(
            claims
        )  # identifiers and digests only
        # a receipt verifies after the signing key is rotated, and nobody can edit one
        rt = current_runtime()
        configure_runtime(
            replace(
                rt,
                signer=LocalSigner("evidence-key-rotated-1"),
                historic_keys=(*rt.historic_keys, rt.signer.public_jwk()),
            )
        )
        try:
            again_items = http.get(
                f"/v1/machine/tool-actions/{action_id}/receipts", headers=bearer(human)
            ).json()["items"]
            assert all(
                i["signature_valid"] is True for i in again_items
            )  # the old public key stays published, so earlier receipts still verify
        finally:
            configure_runtime(rt)
        with pytest.raises(Exception, match="append-only"):
            asyncio.run(
                db(
                    "UPDATE agent.tool_action_receipt SET state = 'EXECUTED' WHERE action_id = CAST(:a AS uuid)",
                    a=action_id,
                )
            )
        with pytest.raises(Exception, match="append-only"):
            asyncio.run(
                db(
                    "DELETE FROM agent.tool_action_receipt WHERE action_id = CAST(:a AS uuid)",
                    a=action_id,
                )
            )

        # a dry run of stopping the parent names the child and changes nothing
        before = asyncio.run(
            db(
                "SELECT agent_principal_id::text AS a, status::text AS s, security_revision FROM agent.agent_profile WHERE tenant_id = :t ORDER BY 1"
            )
        )
        impact = http.get(f"/v1/machine/agents/{root}/stop-impact", headers=bearer(human))
        assert impact.status_code == 200, impact.text
        i = impact.json()
        assert (
            i["dry_run"] is True
            and set(i["would_stop"]["agents"]) == {root, child}
            and i["would_stop"]["agent_count"] == 2
        )
        assert "cannot undo" in i["would_not_change"]["note"] and i["enforcement"]["not_enforced"]
        assert (
            asyncio.run(
                db(
                    "SELECT agent_principal_id::text AS a, status::text AS s, security_revision FROM agent.agent_profile WHERE tenant_id = :t ORDER BY 1"
                )
            )
            == before
        )
        assert (
            http.get(f"/v1/machine/agents/{uid()}/stop-impact", headers=bearer(human)).status_code
            == 404
        )
        # the audit trail names the report, and an unknown outcome is not a success there either
        trail = asyncio.run(
            db(
                "SELECT event_type, outcome FROM evidence.audit_record WHERE tenant_id = :t AND event_type LIKE 'tool_action.%' ORDER BY 1"
            )
        )
        assert {r.event_type for r in trail} == {
            "tool_action.unknown",
            "tool_action.executed",
        } and {r.outcome for r in trail} == {"SUCCESS"}


def test_capacity_held_for_an_action_is_committed_after_it_ran_given_back_when_it_did_not_and_kept_while_nobody_knows(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()
    t = uuid.UUID(tenant)
    server, agent = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-ledger"))

    def sign(claims: Any, typ: str) -> str:
        return ring.sign(claims, typ=typ)

    async def run() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 3) ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"t": t, "c": CELL_ID},
            )
        async with engine(rt_url) as eng:
            now = datetime.now(UTC)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                mid = uuid.uuid4()
                await create_mandate(
                    c,
                    MandateRecord(
                        mandate_id=mid, tenant_id=t, authority_grant_id=uuid.uuid4(), represented_principal_id=uuid.uuid4(), executor_principal_id=uuid.UUID(agent), purpose_id=uuid.uuid4(),
                        resource_scope_id=uuid.uuid4(), allowed_actions=("purchase.create",), budget=Budget(currency="GBP", value_limit=Decimal(100)), home_cell_id=CELL_ID, status=MandateStatus.ACTIVE,
                        valid_from=now - timedelta(hours=1), valid_to=now + timedelta(days=1), created_by_principal_id=uuid.uuid4(),
                    ),
                )  # fmt: skip
            ids: dict[str, tuple[str, str]] = {}
            for name, key in (("ran", "k1"), ("didnt", "k2"), ("unsure", "k3")):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    res = await reserve_capacity(
                        c,
                        tenant_id=t,
                        mandate_id=mid,
                        idempotency_key=key,
                        transaction_context=f"ctx-{key}",
                        amounts=Amounts(Decimal(20), 1, 0),
                        currency="GBP",
                        cell_id=CELL_ID,
                        writer_epoch=3,
                        now=now,
                    )
                    action = uid()
                    await svc.record_authorized(
                        c, tenant, action_id=action, agent_id=agent, tool_id="erp.pay", audience=server, resource="invoice:1", args_sha256=ARGS, decision_id="dec", chain_commitment="sha256:" + "b" * 64,
                        agent_chain=[agent], mandate_id=str(mid), reservation_id=str(res.reservation_id), now=now,
                    )  # fmt: skip
                    await svc.issue_first_receipt(c, tenant, action, sign=sign, issuer=ISS, now=now)
                    assert await svc.record_dispatched(c, action, now)
                    ids[name] = (action, str(res.reservation_id))

            async def report(name: str, outcome: str) -> dict[str, Any]:
                action, _rid = ids[name]
                async with tenant_transaction(eng, ctx(tenant)) as c:

                    async def settle(effect: str, reservation_id: str) -> tuple[str, str | None]:
                        return await settle_for_action(
                            c,
                            tenant_id=tenant,
                            reservation_id=reservation_id,
                            effect=effect,
                            cell_id=CELL_ID,
                            writer_epoch=3,
                            now=datetime.now(UTC),
                        )

                    return await svc.finalize(
                        c,
                        tenant,
                        server_id=server,
                        action_id=action,
                        outcome=outcome,
                        downstream_ref=None,
                        settle=settle,
                        sign=sign,
                        issuer=ISS,
                    )

            async def balance() -> Any:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await read_balance(c, mid)

            held = await balance()
            assert held.reserved.value == Decimal(60) and held.committed.value == Decimal(0)
            # asking whether another spend would fit reads the ledger and holds nothing
            async with tenant_transaction(eng, ctx(tenant)) as c:
                fits = await capacity_check(c, str(mid), value=("30", "GBP"), now=datetime.now(UTC))
                too_much = await capacity_check(
                    c, str(mid), value=("50", "GBP"), now=datetime.now(UTC)
                )
                wrong_currency = await capacity_check(
                    c, str(mid), value=("1", "EUR"), now=datetime.now(UTC)
                )
                missing = await capacity_check(
                    c, str(uuid.uuid4()), value=("1", "GBP"), now=datetime.now(UTC)
                )
            assert (
                fits["available"] is True
                and fits["reserved"] is False
                and fits["remaining"]["value"] == "40.000000000"
            )
            assert (
                too_much["available"] is False
                and too_much["reason"] == "MANDATE_VALUE_LIMIT"
                and wrong_currency["reason"] == "MANDATE_CURRENCY_MISMATCH"
                and missing["reason"] == "MANDATE_NOT_FOUND"
            )
            again = await balance()
            assert (
                again.reserved.value == Decimal(60) and again.ledger_version == held.ledger_version
            )  # nothing was reserved by asking
            # it ran: the capacity is spent
            r = await report("ran", "EXECUTED")
            assert (
                r["latest_receipt"]["payload"]["reservation_state"] == "COMMITTED"
                and r["latest_receipt"]["payload"]["outcomes"]["committed"] is True
            )
            b = await balance()
            assert b.committed.value == Decimal(20) and b.reserved.value == Decimal(40)
            # it did not: the capacity comes back
            r = await report("didnt", "FAILED")
            assert (
                r["latest_receipt"]["payload"]["reservation_state"] == "RELEASED"
                and r["latest_receipt"]["payload"]["outcomes"]["failed"] is True
            )
            b = await balance()
            assert b.committed.value == Decimal(20) and b.reserved.value == Decimal(20)
            # nobody knows: the capacity stays held, and the receipt does not say executed or committed
            r = await report("unsure", "UNKNOWN")
            p = r["latest_receipt"]["payload"]
            assert (
                p["reservation_state"] == "RESERVED"
                and p["outcomes"]["committed"] is False
                and p["outcomes"]["executed"] is False
                and p["conclusion"] == "OUTCOME_UNKNOWN"
            )
            b = await balance()
            assert b.reserved.value == Decimal(20) and b.committed.value == Decimal(
                20
            )  # nothing was freed because the answer is missing
            # once reconciled it ran, and only then is the held capacity spent
            r = await report("unsure", "EXECUTED")
            assert r["latest_receipt"]["payload"]["reservation_state"] == "COMMITTED"
            b = await balance()
            assert b.committed.value == Decimal(40) and b.reserved.value == Decimal(0)
            # whatever happened, committed plus reserved never passed the cap
            assert b.committed.value + b.reserved.value <= Decimal(100)

    asyncio.run(run())
