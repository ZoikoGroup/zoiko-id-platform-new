"""Global coverage and deterministic provider routing against PostgreSQL (ZID-CP-WF09 v2.0; CR-171).

What these hold: a capability is approved only by a second person with its dates and legal determination; the resolver picks the same provider for the same snapshot, keeps one
decision per idempotency key and refuses a reused key with a different request; an unknown outcome is reconciled and never sent to another vendor; a safe failure is re-routed with
every gate re-checked and its lineage kept; releases need independent approvals of the exact dependency set and a rollback never changes a recorded decision; a public number is
refused when a cell lacks approved evidence; and every table is append-only and tenant-isolated. The market gate is replaced by an open market where the test says so, because
launching a real market release needs the seven evidence gates (covered by the market release tests).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from test_evidence_pg import fresh_runtime  # noqa: F401 - the fixture sets the evidence runtime, which every routing fact is written to
from sqlalchemy import text
from zid_db import tenant_transaction
from pydantic import ValidationError
from zid_platform.modules.evidence.ports import audit_timeline
from zid_platform.modules.verification.api import routing as api
from zid_platform.modules.verification.application import routing as svc
from zid_platform.modules.verification.domain import market_release as rm
from zid_platform.modules.verification.domain import routing as rt
from zid_platform.modules.verification.infrastructure import route_entitlement_store as ent_store
from zid_platform.modules.verification.infrastructure import routing_store as store
from zid_platform.modules.verification.infrastructure import workflow_store

FUTURE = datetime.now(UTC) + timedelta(days=200)
HOPS = [
    {"kind": "INGRESS", "party": "provider", "region": "uk", "data_class": "MEDIA"},
    {"kind": "STORAGE", "party": "provider", "region": "uk", "data_class": "MEDIA"},
    {"kind": "SUBPROCESSOR", "party": "cloud", "region": "uk", "data_class": "DERIVED"},
]
PRODUCT = ["idv"]  # capabilities are platform-wide, so each test serves its own product and never sees another test's providers


def uid() -> str:
    return str(uuid.uuid4())


def manifest(**over: Any) -> dict[str, Any]:
    base = {
        "product": PRODUCT[0], "purpose": "kyc", "countries": ["GB", "DE"], "specimens": {"GB": ["passport-2019", "licence-2020"], "DE": ["id-2021"]}, "channels": ["web", "ios"], "min_sdk_version": "2.0",
        "processing_regions": ["uk"], "subprocessor_regions": ["uk"], "assurance": 3, "health": "UP", "quota_remaining": 1000,
        "legal_data_flow_digest": rt.flow_digest(HOPS), "pricing": {"unit_cost_minor": 100, "review_cost_minor": 50, "currency": "EUR"}, "observed": {"valid_outcome_rate_bp": 9000, "p95_latency_ms": 800, "measured": True},
    }  # fmt: skip
    return {**base, **over}


def request(session: str = "vs_1", **over: Any) -> dict[str, Any]:
    base = {
        "session_id": session, "workflow_id": "wf_a", "workflow_version": "1", "product": PRODUCT[0], "purpose": "kyc", "issuer_country": "GB", "method": "DOCUMENT_FACE", "assurance_floor": 2, "channel": "web",
        "sdk_version": "2.1.0", "allowed_regions": ["uk"], "currency": "EUR",
    }  # fmt: skip
    return {**base, **over}


@pytest.fixture(autouse=True)
def own_product() -> None:
    PRODUCT[0] = "p" + uid()[:8].replace("-", "")


@pytest.fixture(autouse=True)
def market_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_market(conn: Any, market: Any, *, now: datetime, stale_after: Any = None) -> rm.MarketDecision:
        return rm.MarketDecision(True, "OPEN_FOR_TEST")

    monkeypatch.setattr(svc.market_releases, "market_open", open_market)


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


async def ensure_workflow(c: Any, tenant: str, approve: bool = True) -> None:
    if await workflow_store.load_version(c, "wf_a", "1") is None:
        await workflow_store.insert_version(c, tenant_id=tenant, workflow_id="wf_a", version="1", definition={"steps": []}, content_hash="0" * 64, author=uid())
        if approve:
            await workflow_store.approve_version(c, "wf_a", "1", uid(), datetime.now(UTC))


async def entitle(c: Any, tenant: str, pid: str, *, regions: tuple[str, ...] = ("uk",), assurance: int = 10) -> None:
    await ent_store.insert(c, tenant_id=tenant, created_by=uid(), provider_id=pid, service_code="document_face", countries=["GB", "DE"], purposes=["kyc"], regions=list(regions), valid_from=date.today() - timedelta(days=1), valid_until=None, assurance=assurance, cost_rank=0)


async def approved(c: Any, tenant: str, pid: str, *, flow: bool = True, entitled: bool = True, **over: Any) -> None:
    """A provider that passes every gate for this company: approved capability, a registered data-flow graph its legal determination covers, this company's entitlement and an approved workflow."""
    await ensure_workflow(c, tenant)
    if flow:
        await svc.record_data_flow(c, tenant, provider_id=pid, hops=HOPS, by="legal")
    await svc.propose_capability(
        c, provider_id=pid, method="DOCUMENT_FACE", manifest=manifest(**over), qualified_until=FUTURE, contract_until=FUTURE, legal_determination_ref="LD-1", legal_determination_valid_until=FUTURE, by="proposer"
    )  # fmt: skip
    await svc.decide_capability(c, tenant, provider_id=pid, method="DOCUMENT_FACE", action="APPROVE", by="approver", reason="qualified")
    if entitled:
        await entitle(c, tenant, pid)


def test_a_capability_needs_a_second_person_and_dated_evidence_and_the_database_agrees(rt_url: str, owner_url: str) -> None:
    pid = "cap-" + uid()[:8]
    tenant = uid()

    async def go(c: Any) -> None:
        with pytest.raises(svc.RoutingError) as e:
            await svc.propose_capability(c, provider_id=pid, method="DOCUMENT_FACE", manifest={"product": "idv"}, qualified_until=None, contract_until=None, legal_determination_ref=None, legal_determination_valid_until=None, by="p")
        assert e.value.code == "CAPABILITY_MANIFEST_INVALID"
        await svc.propose_capability(c, provider_id=pid, method="DOCUMENT_FACE", manifest=manifest(), qualified_until=FUTURE, contract_until=None, legal_determination_ref=None, legal_determination_valid_until=None, by="p")
        for who, code in (("p", "SECOND_PERSON_REQUIRED"), ("q", "CAPABILITY_EVIDENCE_INCOMPLETE")):
            with pytest.raises(svc.RoutingError) as e:
                await svc.decide_capability(c, tenant, provider_id=pid, method="DOCUMENT_FACE", action="APPROVE", by=who, reason="ok")
            assert e.value.code == code

    run(rt_url, tenant, go)

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:  # the database refuses an APPROVED row with no second person, and any edit
            await c.execute(
                text("""INSERT INTO verify.route_capability (provider_id, method, version, state, manifest, manifest_digest, qualified_until, contract_until, legal_determination_ref, legal_determination_valid_until, proposed_by, approved_by)
                        VALUES (:p, 'DOCUMENT', 1, 'APPROVED', '{}', :d, now(), now(), 'x', now(), 'same', 'same')"""),
                {"p": pid + "x", "d": "sha-256:" + "0" * 64},
            )

    with pytest.raises(Exception, match="route_capability_approved_complete"):
        asyncio.run(tamper())

    async def edit() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE verify.route_capability SET state = 'APPROVED' WHERE provider_id = :p"), {"p": pid})

    with pytest.raises(Exception, match="append-only"):
        asyncio.run(edit())


def test_the_resolver_is_deterministic_idempotent_and_never_picks_a_provider_that_fails_a_gate(rt_url: str) -> None:
    tenant = uid()
    a, b, c_ = ("a-" + uid()[:6], "b-" + uid()[:6], "c-" + uid()[:6])

    async def go(c: Any) -> None:
        await approved(c, tenant, a, pricing={"unit_cost_minor": 300, "review_cost_minor": 50, "currency": "EUR"})
        await approved(c, tenant, b, pricing={"unit_cost_minor": 100, "review_cost_minor": 50, "currency": "EUR"})
        await approved(c, tenant, c_, pricing={"unit_cost_minor": 1, "review_cost_minor": 0, "currency": "EUR"}, processing_regions=["us"])  # cheapest, but its data path is not allowed
        key = "key-" + uid()
        first = await svc.resolve_and_record(c, tenant, idempotency_key=key, body=request())
        assert first["status"] == "ROUTE_SELECTED" and first["selected"] == b and first["replayed"] is False
        assert all(set(x) == {"provider_id", "p95_latency_ms", "rank"} for x in first["ranked"])  # the vendor cost the order came from is not in what a company sees (WF10 10-03)
        assert "pricing" not in str(svc.capability_view(await store.latest_capability(c, a, "DOCUMENT_FACE"))) and "unit_cost" not in str(first)
        assert [x["provider_id"] for x in first["ranked"]] == [b, a] and any(x["provider_id"] == c_ and "PROCESSING_REGION_NOT_ALLOWED" in x["reasons"] for x in first["rejected"])
        again = await svc.resolve_and_record(c, tenant, idempotency_key=key, body=request())
        assert again["replayed"] is True and again["decision_id"] == first["decision_id"] and again["decision_digest"] == first["decision_digest"]
        with pytest.raises(svc.RoutingError) as e:
            await svc.resolve_and_record(c, tenant, idempotency_key=key, body=request(issuer_country="DE"))
        assert e.value.code == "IDEMPOTENCY_KEY_REUSED"
        # a request nobody can serve is NO_ROUTE with its reasons, recorded like any other decision
        nope = await svc.resolve_and_record(c, tenant, idempotency_key="key-" + uid(), body=request(session="vs_2", chip_required=True))
        assert nope["status"] == "NO_ROUTE" and "CHIP_REQUIRED_BUT_METHOD_IS_WEAKER" in nope["no_route_reasons"]
        unapproved = await svc.resolve_and_record(c, tenant, idempotency_key="key-" + uid(), body=request(session="vs_3", workflow_id="wf_draft"))
        assert unapproved["status"] == "NO_ROUTE" and "WORKFLOW_NOT_APPROVED" in unapproved["no_route_reasons"]
        row = await store.load_decision(c, first["decision_id"])
        assert row.selected == b and row.algorithm_version == rt.ALGORITHM_VERSION and row.route_version == "unreleased"

    run(rt_url, tenant, go)


def test_an_unknown_outcome_is_reconciled_and_never_sent_to_another_vendor_while_a_safe_failure_is_rerouted_with_lineage(rt_url: str) -> None:
    tenant = uid()
    a, b = ("a-" + uid()[:6], "b-" + uid()[:6])

    async def go(c: Any) -> None:
        await approved(c, tenant, a, pricing={"unit_cost_minor": 100, "review_cost_minor": 50, "currency": "EUR"})
        await approved(c, tenant, b, pricing={"unit_cost_minor": 130, "review_cost_minor": 50, "currency": "EUR"})
        dec = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("vs_u"))
        assert dec["selected"] == a
        key = "att-" + uid()
        att = await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key=key, body=request("vs_u"))
        assert att["state"] == "ATTEMPT_RESERVED" and [h["to"] for h in att["history"]] == ["ELIGIBLE", "ATTEMPT_RESERVED"]
        same = await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key=key, body=request("vs_u"))
        assert same["attempt_id"] == att["attempt_id"] and len(same["history"]) == 2  # a repeat never makes a second attempt
        with pytest.raises(svc.RoutingError) as e:
            await svc.advance_attempt(c, tenant, att["attempt_id"], "DISPATCHED", actor="SYSTEM")
        assert e.value.code == "EXTERNAL_OPERATION_ID_REQUIRED"
        sent = await svc.advance_attempt(c, tenant, att["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-123")
        assert sent["state"] == "DISPATCHED" and sent["external_operation_id"] == "op-123" and sent["may_ask_another_provider"] is False
        unknown = await svc.record_failure(c, tenant, att["attempt_id"], transport_timeout=True)
        assert unknown["state"] == "OUTCOME_UNKNOWN" and unknown["failure_class"] == "OUTCOME_UNKNOWN" and unknown["may_ask_another_provider"] is False
        with pytest.raises(svc.RoutingError) as e:
            await svc.reroute(c, tenant, failed_attempt_id=att["attempt_id"], idempotency_key="rr-" + uid(), body=request("vs_u"))
        assert e.value.code == "REROUTE_REFUSED_OUTCOME_NOT_KNOWN"
        with pytest.raises(svc.RoutingError) as e:
            await svc.advance_attempt(c, tenant, att["attempt_id"], "PENDING", actor="OPERATOR")  # an unknown outcome does not quietly become pending
        assert e.value.code == "ATTEMPT_TRANSITION_FORBIDDEN"
        rec = await svc.advance_attempt(c, tenant, att["attempt_id"], "RECONCILING", actor="OPERATOR", reason="asking the provider")
        assert rec["state"] == "RECONCILING"

        # a failure before anything was sent can go to another provider; every gate is checked again and the lineage is kept
        d2 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("vs_s"))
        a2 = await svc.reserve_attempt(c, tenant, decision_id=d2["decision_id"], idempotency_key="att-" + uid(), body=request("vs_s"))
        failed = await svc.record_failure(c, tenant, a2["attempt_id"])
        assert failed["state"] == "FAILED_BEFORE_DISPATCH" and failed["may_ask_another_provider"] is True and failed["may_retry_same_provider"] is True
        moved = await svc.reroute(c, tenant, failed_attempt_id=a2["attempt_id"], idempotency_key="rr-" + uid(), body=request("vs_s"))
        assert moved["selected"] == b and moved["lineage"]["failed_provider"] == a and moved["lineage"]["previous_decision"] == d2["decision_id"]
        # a provider result is evidence, not a decision: nobody but the owning verifier finalises
        d3 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("vs_f"))
        a3 = await svc.reserve_attempt(c, tenant, decision_id=d3["decision_id"], idempotency_key="att-" + uid(), body=request("vs_f"))
        await svc.advance_attempt(c, tenant, a3["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-9")
        await svc.advance_attempt(c, tenant, a3["attempt_id"], "RESULT_RECEIVED", actor="PROVIDER")
        await svc.advance_attempt(c, tenant, a3["attempt_id"], "NORMALIZED", actor="SYSTEM")
        with pytest.raises(svc.RoutingError) as e:
            await svc.advance_attempt(c, tenant, a3["attempt_id"], "FINALIZED", actor="OPERATOR")
        assert e.value.code == "ONLY_THE_OWNING_VERIFIER_FINALISES" and e.value.status == 403
        done = await svc.advance_attempt(c, tenant, a3["attempt_id"], "FINALIZED", actor="VERIFIER")
        assert done["state"] == "FINALIZED" and done["terminal"] is True

    run(rt_url, tenant, go)


def test_a_provider_that_stops_being_lawful_before_the_attempt_cancels_it_and_no_vendor_call_is_possible(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        dec = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("vs_c"))
        assert dec["selected"] == a
        await svc.decide_capability(c, tenant, provider_id=a, method="DOCUMENT_FACE", action="SUSPEND", by="security", reason="breach")  # the provider is suspended after the route was chosen
        att = await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key="att-" + uid(), body=request("vs_c"))
        assert att["state"] == "CANCELLED" and "PROVIDER_SUSPENDED" in att["history"][-1]["reason"]
        with pytest.raises(svc.RoutingError):
            await svc.advance_attempt(c, tenant, att["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-x")
        after = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("vs_c2"))
        assert after["status"] == "NO_ROUTE" and "PROVIDER_SUSPENDED" in after["no_route_reasons"]

    run(rt_url, tenant, go)


def test_a_preflight_shows_who_loses_coverage_and_touches_nothing_and_a_release_needs_independent_approval_of_the_exact_dependencies(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        before = (await c.execute(text("SELECT count(*) FROM verify.route_capability"))).scalar_one()
        scen = [{**request("p1"), "required": True}, {**request("p2", channel="ios"), "required": False}]
        change = [{"provider_id": a, "method": "DOCUMENT_FACE", "manifest": manifest(channels=["ios"])}]  # the provider drops web
        pf = await svc.preflight(c, tenant, scenarios=scen, proposed=change)
        assert pf["stored"] is False and pf["provider_calls"] == 0 and pf["passes"] is False and pf["blocking"] == 1
        assert [r["change"] for r in pf["rows"]] == ["LOSES_COVERAGE", "SAME"] and pf["cohorts"]["GB/web"]["LOSES_COVERAGE"] == 1
        assert (await c.execute(text("SELECT count(*) FROM verify.route_capability"))).scalar_one() == before  # nothing was written
        ok = await svc.preflight(c, tenant, scenarios=scen, proposed=[])
        assert ok["passes"] is True

        rel = await svc.create_release(c, maker="maker", risk="HIGH", scope={"region": "uk"})
        with pytest.raises(svc.RoutingError) as e:
            await svc.approve_release(c, rel["release_id"], approver="p", role="PRODUCT")
        assert e.value.code == "RELEASE_NOT_READY_FOR_APPROVAL"
        blocked = await svc.record_preflight(c, rel["release_id"], result=pf, by="maker")
        assert blocked["state"] == "DRAFT"  # a required cohort loses coverage: the release does not advance
        passed = await svc.record_preflight(c, rel["release_id"], result=ok, by="maker")
        assert passed["state"] == "PREFLIGHT_PASSED"
        with pytest.raises(svc.RoutingError) as e:
            await svc.approve_release(c, rel["release_id"], approver="maker", role="PRODUCT")
        assert e.value.code == "MAKER_CANNOT_APPROVE_OWN_RELEASE"
        for who, role in (("p", "PRODUCT"), ("s", "SECURITY"), ("v", "PRIVACY")):
            await svc.approve_release(c, rel["release_id"], approver=who, role=role)
        with pytest.raises(svc.RoutingError) as e:
            await svc.publish_release(c, rel["release_id"], by="p")
        assert e.value.code == "RELEASE_CANNOT_GO_LIVE" and "MISSING_APPROVAL:LEGAL" in str(e.value)
        await svc.approve_release(c, rel["release_id"], approver="l", role="LEGAL")
        # a provider changes after the approvals: they no longer apply
        await svc.decide_capability(c, tenant, provider_id=a, method="DOCUMENT_FACE", action="SUSPEND", by="sec", reason="drift")
        with pytest.raises(svc.RoutingError) as e:
            await svc.publish_release(c, rel["release_id"], by="p")
        assert "DEPENDENCIES_CHANGED_SINCE_PREFLIGHT" in str(e.value) or "APPROVAL_FOR_A_DIFFERENT_DEPENDENCY_SET" in str(e.value)

    run(rt_url, tenant, go)


def test_a_published_release_pins_new_decisions_and_a_rollback_never_changes_a_recorded_one(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        before = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("v1"))
        assert before["route_version"] == "unreleased"
        rel = await svc.create_release(c, maker="maker", risk="LOW", scope={})
        await svc.record_preflight(c, rel["release_id"], result=await svc.preflight(c, tenant, scenarios=[request("p")], proposed=[]), by="maker")
        await svc.approve_release(c, rel["release_id"], approver="p", role="PRODUCT")
        await svc.approve_release(c, rel["release_id"], approver="s", role="SECURITY")
        with pytest.raises(svc.RoutingError) as e:
            await svc.publish_release(c, rel["release_id"], by="maker")
        assert e.value.code == "MAKER_CANNOT_PUBLISH_OWN_RELEASE"
        live = await svc.publish_release(c, rel["release_id"], by="p")
        assert live["state"] == "LIVE"
        during = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("v2"))
        assert during["route_version"].startswith("release:") and during["selected"] == a
        await svc.rollback_release(c, rel["release_id"], by="sec", reason="canary showed drift")
        after = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("v3"))
        assert after["route_version"] == "unreleased"
        kept = await store.load_decision(c, during["decision_id"])
        assert kept.route_version == during["route_version"] and kept.selected == a and kept.decision_digest == during["decision_digest"]  # the recorded decision is what it was

    run(rt_url, tenant, go)


def test_coverage_is_counted_from_cells_and_a_public_number_is_refused_when_a_cell_lacks_evidence(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        cov = await svc.coverage(c)
        enabled = cov["tiers"]["ENABLED"]
        assert enabled["countries"] >= 2 and enabled["specimens"] >= 3 and cov["method"] is None  # GB with two specimens, DE with one, deduplicated across providers
        claim = await svc.public_claim(c)
        assert claim["countries"] >= 2 and "no certification is implied" in claim["caveat"]
        # a launched cell whose evidence has gone: the number is refused, never quietly lowered
        cells = await svc.cells(c, datetime.now(UTC))
        bad = [*cells, rt.Cell("x/y/FR/z", "FR", "z", "DOCUMENT_FACE", "ENABLED", None, True, FUTURE)]
        assert rt.cell_status(bad[-1], datetime.now(UTC)) == "UNKNOWN"
        with pytest.raises(ValueError, match="COUNTED_CELL_LACKS_APPROVED_EVIDENCE"):
            rt.public_claim(bad, datetime.now(UTC))

    run(rt_url, tenant, go)


def test_decisions_and_attempts_are_tenant_isolated_and_vendor_invoices_are_matched_not_adjusted(rt_url: str, owner_url: str) -> None:
    t1, t2 = uid(), uid()
    a = "a-" + uid()[:6]
    out: dict[str, str] = {}

    async def one(c: Any) -> None:
        await approved(c, t1, a)
        dec = await svc.resolve_and_record(c, t1, idempotency_key="k-" + uid(), body=request("vs_t"))
        att = await svc.reserve_attempt(c, t1, decision_id=dec["decision_id"], idempotency_key="att-" + uid(), body=request("vs_t"))
        await svc.advance_attempt(c, t1, att["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-iso")
        out.update(decision=dec["decision_id"], attempt=att["attempt_id"])
        rec = await svc.reconciliation(c, [rt.VendorLine(a, "op-iso", 100, "EUR"), rt.VendorLine(a, "op-ghost", 100, "EUR")])
        assert rec["matched"] == 1 and rec["held"] == [{"operation": "op-ghost", "reason": "VENDOR_LINE_WITHOUT_ATTEMPT"}] and rec["adjusts_anything"] is False

    run(rt_url, t1, one)

    async def other(c: Any) -> None:
        assert await store.load_decision(c, out["decision"]) is None and await store.events_of(c, out["attempt"]) == []

    run(rt_url, t2, other)

    async def edit() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE verify.route_attempt_event SET to_state = 'FINALIZED' WHERE attempt_id = :a"), {"a": uuid.UUID(out["attempt"])})

    with pytest.raises(Exception, match="append-only"):
        asyncio.run(edit())


# ------------------------------------------------------------------------------------------------------------------------- CR-172


async def seed_attempts(c: Any, tenant: str, provider: str, n: int, final_state: str, *, age_seconds: int = 0) -> None:
    """Attempts this platform really sent to a provider and how each ended, written straight into the append-only table with the age given (the measurement the breaker reads)."""
    for _ in range(n):
        attempt, dec = uuid.uuid4(), uuid.uuid4()
        for seq, (frm, to) in enumerate([(None, "ELIGIBLE"), ("ELIGIBLE", "ATTEMPT_RESERVED"), ("ATTEMPT_RESERVED", "DISPATCHED"), ("DISPATCHED", final_state)], start=1):
            await c.execute(
                text("""INSERT INTO verify.route_attempt_event (event_id, tenant_id, attempt_id, seq, decision_id, session_id, provider_id, idempotency_key, external_operation_id, from_state, to_state, actor, created_at)
                        VALUES (:e, :t, :a, :s, :d, :se, :p, :k, 'op', :f, :to, 'SYSTEM', now() - make_interval(secs => :age))"""),
                {"e": uuid.uuid4(), "t": uuid.UUID(tenant), "a": attempt, "s": seq, "d": dec, "se": "seed-" + str(attempt), "p": provider, "k": "seed-key-" + str(attempt), "f": frm, "to": to, "age": age_seconds},
            )


def test_the_workflow_and_entitlements_are_looked_up_and_a_caller_cannot_assert_them(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        ok = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("t1"))
        assert ok["status"] == "ROUTE_SELECTED" and ok["selected"] == a
        # a workflow that is not approved (or does not exist) cannot be routed, whatever the caller says
        missing = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("t2", workflow_id="wf_unknown"))
        assert missing["status"] == "NO_ROUTE" and "WORKFLOW_NOT_APPROVED" in missing["no_route_reasons"]
        # an entitlement that does not cover the data path is named, in the entitlement guard's own words
        other = "b-" + uid()[:6]
        await approved(c, tenant, other, entitled=False)
        await entitle(c, tenant, other, regions=("eu",))
        d = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("t3"))
        why = next(x for x in d["rejected"] if x["provider_id"] == other)["reasons"]
        assert "ENTITLEMENT:REGION_NOT_PERMITTED" in why and d["selected"] == a
        none = "c-" + uid()[:6]
        await approved(c, tenant, none, entitled=False)
        d2 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("t4"))
        assert "ENTITLEMENT:NO_ENTITLEMENT" in next(x for x in d2["rejected"] if x["provider_id"] == none)["reasons"]

    run(rt_url, tenant, go)

    # a company with no entitlement at all is told that, not routed on its own say-so
    t2, p2 = uid(), "n-" + uid()[:6]

    async def bare(c: Any) -> None:
        await approved(c, t2, p2, entitled=False)
        d = await svc.resolve_and_record(c, t2, idempotency_key="k-" + uid(), body=request("t5"))
        assert d["status"] == "NO_ROUTE" and "METHOD_NOT_ENTITLED_FOR_TENANT" in d["no_route_reasons"]

    run(rt_url, t2, bare)
    for field, value in (("workflow_approved", True), ("entitled_methods", ["DOCUMENT_FACE"])):  # the request no longer carries them: the model refuses them
        with pytest.raises(ValidationError):
            api.ResolveBody(**{**request("x"), field: value})


def test_a_changed_data_flow_stops_routing_until_the_legal_determination_covers_it_and_the_change_is_audited(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]
    nograph = "g-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        await approved(c, tenant, nograph, flow=False)
        d0 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("f0"))
        assert d0["selected"] == a and "NO_DATA_FLOW_GRAPH" in next(x for x in d0["rejected"] if x["provider_id"] == nograph)["reasons"]
        with pytest.raises(svc.RoutingError) as e:
            await svc.record_data_flow(c, tenant, provider_id=a, hops=HOPS[:1], by="legal")
        assert e.value.code == "DATA_FLOW_INVALID"
        same = await svc.record_data_flow(c, tenant, provider_id=a, hops=list(reversed(HOPS)), by="legal")
        assert same["changed"] is False  # the same graph in another order is not a change

        # a new subprocessor abroad: the provider drops out at once
        abroad = [*HOPS, {"kind": "ANALYTICS", "party": "stats", "region": "us", "data_class": "DERIVED"}]
        ch = await svc.record_data_flow(c, tenant, provider_id=a, hops=abroad, by="vendor-ops")
        assert ch["changed"] is True and ch["drift"]["new_regions"] == ["us"]
        d1 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("f1"))
        assert d1["status"] == "NO_ROUTE" and "DATA_FLOW_REGION_NOT_ALLOWED:ANALYTICS" in d1["no_route_reasons"]
        assert any(x["event_type"] == "provider.data_flow.changed" and x["evidence_class"] == "E3" for x in await audit_timeline(c, "provider_data_flow", a))

        # back to an allowed region, but not the graph the determination was made about: still blocked, until legal looks again
        quiet = [*HOPS, {"kind": "TELEMETRY", "party": "metrics", "region": "uk", "data_class": "METADATA"}]
        await svc.record_data_flow(c, tenant, provider_id=a, hops=quiet, by="vendor-ops")
        d2 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("f2"))
        assert d2["status"] == "NO_ROUTE" and "DATA_FLOW_CHANGED_SINCE_DETERMINATION" in d2["no_route_reasons"]
        # legal proposes a new capability version naming the new graph's digest; a second person approves it
        await svc.propose_capability(c, provider_id=a, method="DOCUMENT_FACE", manifest=manifest(legal_data_flow_digest=rt.flow_digest(quiet)), qualified_until=FUTURE, contract_until=FUTURE, legal_determination_ref="LD-2", legal_determination_valid_until=FUTURE, by="legal")
        pending = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("f3"))
        assert pending["status"] == "NO_ROUTE" and "PROVIDER_NOT_APPROVED" in pending["no_route_reasons"]  # a new version is not eligible until it is approved
        await svc.decide_capability(c, tenant, provider_id=a, method="DOCUMENT_FACE", action="APPROVE", by="approver-2", reason="covers the new graph")
        d4 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("f4"))
        assert d4["selected"] == a

    run(rt_url, tenant, go)


def test_every_decision_and_attempt_change_leaves_an_event_and_an_audit_record_and_the_trace_can_show_them(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        dec = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("e1"))
        trace = await svc.decision_evidence(c, dec["decision_id"])
        assert [(x["event_type"], x["outcome"], x["evidence_class"]) for x in trace] == [("route.selected", "SUCCESS", "E3")]
        await svc.resolve_and_record(c, tenant, idempotency_key="k-same-key", body=request("e2"))
        await svc.resolve_and_record(c, tenant, idempotency_key="k-same-key", body=request("e2"))  # a replay writes nothing more
        replay = await store.decision_by_key(c, "k-same-key")
        assert len(await svc.decision_evidence(c, str(replay.decision_id))) == 1
        none = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("e3", workflow_id="wf_missing"))
        assert [x["event_type"] for x in await svc.decision_evidence(c, none["decision_id"])] == ["route.no_route"]
        att = await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key="att-" + uid(), body=request("e1"))
        await svc.advance_attempt(c, tenant, att["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-e")
        await svc.record_failure(c, tenant, att["attempt_id"], transport_timeout=True)
        events = await audit_timeline(c, "route_attempt", att["attempt_id"])
        assert [(x["event_type"], x["evidence_class"]) for x in events] == [("route.attempt.reserved", "E2"), ("route.attempt.advanced", "E2"), ("route.attempt.unknown", "E3")]
        assert events[-1]["outcome"] == "PARTIAL" and events[-1]["reason_code"] == "UNKNOWN"
        assert any(x["event_type"] == "provider.capability.changed" for x in await audit_timeline(c, "provider_capability", f"{a}/DOCUMENT_FACE"))

    run(rt_url, tenant, go)


def test_a_breaker_opens_on_the_platforms_own_measurements_and_a_quality_quarantine_is_independent_of_uptime(rt_url: str) -> None:
    tenant = uid()
    flaky, steady, poor = ("f-" + uid()[:6], "s-" + uid()[:6], "q-" + uid()[:6])

    async def go(c: Any) -> None:
        await approved(c, tenant, flaky, pricing={"unit_cost_minor": 10, "review_cost_minor": 0, "currency": "EUR"})
        await approved(c, tenant, steady, pricing={"unit_cost_minor": 500, "review_cost_minor": 0, "currency": "EUR"})
        assert (await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("b0")))["selected"] == flaky  # cheapest, until it misbehaves
        await seed_attempts(c, tenant, flaky, 25, "OUTCOME_UNKNOWN")  # twenty-five dispatches that ended unknown, just now
        d = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("b1"))
        assert d["selected"] == steady and "BREAKER_OPEN" in next(x for x in d["rejected"] if x["provider_id"] == flaky)["reasons"]

        # quality: plenty of old results, most of them poor, so the method is quarantined although nothing is down right now
        await approved(c, tenant, poor, pricing={"unit_cost_minor": 1, "review_cost_minor": 0, "currency": "EUR"})
        await seed_attempts(c, tenant, poor, 30, "EVIDENCE_REJECTED", age_seconds=2 * 86400)
        await seed_attempts(c, tenant, poor, 30, "NORMALIZED", age_seconds=2 * 86400)
        d2 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("b2"))
        why = next(x for x in d2["rejected"] if x["provider_id"] == poor)["reasons"]
        assert "QUALITY_QUARANTINED" in why and "BREAKER_OPEN" not in why and d2["selected"] == steady

    run(rt_url, tenant, go)


def test_a_provider_with_a_per_minute_cap_refuses_the_burst_and_the_attempt_says_why(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a, max_attempts_per_minute=2)
        dec = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("th"))
        views = [await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key="att-" + uid(), body=request("th")) for _ in range(3)]
        assert [v["state"] for v in views] == ["ATTEMPT_RESERVED", "ATTEMPT_RESERVED", "CANCELLED"]
        assert "PROVIDER_THROTTLED" in views[2]["history"][-1]["reason"]

    run(rt_url, tenant, go)


def test_the_applicant_is_told_something_safe_at_every_step_and_never_that_they_failed(rt_url: str) -> None:
    tenant = uid()
    a = "a-" + uid()[:6]

    async def go(c: Any) -> None:
        await approved(c, tenant, a)
        dec = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("ap"))
        att = await svc.reserve_attempt(c, tenant, decision_id=dec["decision_id"], idempotency_key="att-" + uid(), body=request("ap"))
        assert att["applicant"]["code"] == "IN_PROGRESS" and att["applicant"]["next"] == "WAIT"
        await svc.advance_attempt(c, tenant, att["attempt_id"], "DISPATCHED", actor="SYSTEM", external_operation_id="op-ap")
        unknown = await svc.record_failure(c, tenant, att["attempt_id"], transport_timeout=True)
        assert unknown["applicant"]["code"] == "STILL_CHECKING" and "should not start again" in unknown["applicant"]["body"] and a not in str(unknown["applicant"])
        d2 = await svc.resolve_and_record(c, tenant, idempotency_key="k-" + uid(), body=request("ap2"))
        a2 = await svc.reserve_attempt(c, tenant, decision_id=d2["decision_id"], idempotency_key="att-" + uid(), body=request("ap2"))
        failed = await svc.record_failure(c, tenant, a2["attempt_id"])
        assert failed["applicant"]["code"] == "PLEASE_TRY_AGAIN" and failed["applicant"]["keeps_evidence"] is True and failed["applicant"]["counts_against_applicant"] is False

    run(rt_url, tenant, go)
