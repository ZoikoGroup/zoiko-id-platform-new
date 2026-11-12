"""The kill switch on PostgreSQL (ZID-RCG-003 RCG-G08; P0 "cert withdrawal negative test"; T08; CR-078).

1. Through the operator API: a suspension of one exact scope stops new calls for it at the country gate, leaves the provider's other country and
   service alone, reports its impact, cannot be lifted by whoever applied it, and is lifted (rolled back) by another person; every step is audited.
2. It closes a launched market whose pinned supplier it covers, and takes off the claim resting on that supplier.
3. The table keeps the rules when the domain code is skipped.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from operator_fixture import ISS
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_entitlement_pg import _registry
from test_regulated_claims_pg import _approved, _snapshot
from test_regulatory_releases_api_pg import _app, _audit, _grant, _people, _seed_pack
from zid_errors import ZidError
from zid_platform.country_gate import check_packs
from zid_platform.modules.verification.application import market_releases as rel
from zid_platform.modules.verification.application import regulated_claims as claims_svc
from zid_platform.modules.verification.domain import market_release as md
from zid_platform.modules.verification.domain import regulated_claims as rc
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.attestation import Scope
from zid_platform.modules.verification.domain.countries import Capability
from zid_platform.modules.verification.domain.entitlement import Grant
from zid_platform.modules.verification.domain.entitlement import Scope as EntScope
from zid_platform.modules.verification.domain.entitlement import Status as EntStatus
from zid_platform.modules.verification.infrastructure import attestation_store, entitlement_store

CAP = Capability.INDIVIDUAL_VERIFICATION


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _gate(rt_url: str, provider: str, country: str) -> None:
    """The country gate with every switch off: only the kill switch can refuse."""

    async def go() -> None:
        async with engine(rt_url) as eng:
            await check_packs(
                eng,
                ctx(uuid.uuid4()),
                mode="off",
                environment="production",
                reg=_registry(provider, country),
                country=country,
                capability=CAP,
                now=datetime.now(UTC),
            )

    asyncio.run(go())


def test_the_kill_switch_stops_exactly_its_scope_and_is_rolled_back_by_another_person(
    rt_url: str, owner_url: str
) -> None:
    provider = f"prov-{uuid.uuid4().hex[:8]}"
    sid = f"kill-{uuid.uuid4().hex[:10]}"
    now = datetime.now(UTC)
    # an entitlement in the scope, so the impact has something to show
    _run(
        rt_url,
        lambda c: entitlement_store.record(
            c,
            Grant(
                grant_id=f"g-{uuid.uuid4().hex[:10]}",
                scope=EntScope(provider, "NO", CAP.value, "STANDARD"),
                status=EntStatus.GRANTED,
                effective_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=30),
                valid_until=None,
                contract_ref="CONTRACT-1",
                terms_ref="TERMS-1",
                recorded_by="vera",
                recorded_at=now - timedelta(days=1),
            ),
            now=now,
        ),
    )
    _gate(rt_url, provider, "NO")  # open before anything is suspended

    o, who = _people(rt_url)
    body = {
        "suspension_id": sid,
        "scope": {"provider": provider, "service_id": CAP.value, "country": "NO"},
        "trigger": "CERTIFICATION_WITHDRAWN",
        "reason": "the official register shows the certificate withdrawn today",
        "evidence_ref": "INC-2026-0042",
    }
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert (
            call("POST", "/v1/regulatory/suspensions", "author", json=body).status_code == 403
        )  # no capability yet
        _grant(api, o, who, "author", "regulatory.suspension.apply")
        _grant(api, o, who, "launcher", "regulatory.suspension.lift")

        preview = call(
            "GET",
            "/v1/regulatory/suspensions/impact",
            "author",
            params={"provider": provider, "country": "NO", "service_id": CAP.value},
        )
        assert preview.status_code == 200 and preview.json()["impact"]["entitlements"] == [
            f"{provider}/NO/{CAP.value}/STANDARD"
        ]

        too_broad = body | {"suspension_id": f"{sid}-wide", "scope": {"provider": provider}}
        r = call("POST", "/v1/regulatory/suspensions", "author", json=too_broad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "SUSPENSION_TOO_BROAD"

        r = call("POST", "/v1/regulatory/suspensions", "author", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "ACTIVE" and r.json()["impact"]["entitlements"] == [
            f"{provider}/NO/{CAP.value}/STANDARD"
        ]
        assert (
            call("POST", "/v1/regulatory/suspensions", "author", json=body).json()["error"]["code"]
            == "SUSPENSION_EXISTS"
        )

        # the gate refuses new calls in the scope, and only those
        with pytest.raises(ZidError) as e:
            _gate(rt_url, provider, "NO")
        assert (e.value.code, e.value.http_status) == ("PROVIDER_SCOPE_SUSPENDED", 422)
        _gate(rt_url, provider, "DK")  # the same provider in another country is untouched
        _gate(
            rt_url, f"{provider}-other", "NO"
        )  # another provider in the same country is untouched

        active = call(
            "GET", "/v1/regulatory/suspensions", "author", params={"active": "true"}
        ).json()["items"]
        assert sid in [i["current"]["suspension_id"] for i in active]

        lift_body = {
            "reason": "the register shows the certificate restored",
            "evidence_ref": "INC-2026-0043",
        }
        r = call("POST", f"/v1/regulatory/suspensions/{sid}:lift", "author", json=lift_body)
        assert (
            r.status_code == 403
        )  # whoever applied it holds no lift capability, and the domain would refuse them too
        r = call("POST", f"/v1/regulatory/suspensions/{sid}:lift", "launcher", json=lift_body)
        assert r.status_code == 200 and (r.json()["version"], r.json()["status"]) == (
            2,
            "LIFTED",
        ), r.text
        assert (
            call(
                "POST", f"/v1/regulatory/suspensions/{sid}:lift", "launcher", json=lift_body
            ).json()["error"]["code"]
            == "SUSPENSION_NOT_ACTIVE"
        )
        _gate(rt_url, provider, "NO")  # open again

        listed = call("GET", "/v1/regulatory/suspensions", "author").json()["items"]
        mine = next(i for i in listed if i["current"]["suspension_id"] == sid)
        assert [v["status"] for v in mine["versions"]] == [
            "ACTIVE",
            "LIFTED",
        ]  # history is kept, not rewritten

    records = _audit(owner_url, sid, f"{sid}:v%", f"{sid}-wide")
    events = [r["event_type"] for r in records]
    assert {
        "regulatory.suspension.applied",
        "regulatory.suspension.lifted",
        "regulatory.suspension.refused",
    } <= set(events)
    applied = next(r for r in records if r["event_type"] == "regulatory.suspension.applied")
    assert applied["body"]["details"]["impact_counts"]["entitlements"] == 1
    assert "withdrawn today" not in " ".join(
        str(r["body"]) for r in records
    )  # the reason text stays out of the audit trail

    # other systems are told on the outbox, in the same transaction as the change (ZID-RCG-003 09.2); a refusal announces nothing
    outbox = _outbox(owner_url, "provider_suspension", f"{sid}%")
    assert [(e["event_type"], e["aggregate_id"], e["lane"]) for e in outbox] == [
        ("com.zoiko.id.verification.provider.scope_suspended.v1", f"{sid}:v1", "priority"),
        ("com.zoiko.id.verification.provider.scope_restored.v1", f"{sid}:v2", "domain"),
    ]
    assert outbox[0]["payload"]["trigger"] == "CERTIFICATION_WITHDRAWN"
    assert "withdrawn today" not in str(outbox)


def _outbox(owner_url: str, aggregate_type: str, like: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, aggregate_id, lane, payload FROM events.outbox_event "
                        "WHERE aggregate_type = :t AND aggregate_id LIKE :i ORDER BY occurred_at, aggregate_version"
                    ),
                    {"t": aggregate_type, "i": like},
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


def test_suspending_a_pinned_supplier_closes_its_market_and_takes_its_claim_off(
    rt_url: str,
) -> None:
    product = f"P{uuid.uuid4().hex[:10].upper()}"
    market = md.MarketKey("GB", product, "STANDARD")
    supplier = Scope(f"PARTNER-{uuid.uuid4().hex[:8]}", "UK_DIATF", "GB", "RTR")
    rid = f"kill-rel-{uuid.uuid4().hex[:8]}"
    _seed_pack(rt_url, product)
    _run(rt_url, lambda c: attestation_store.record(c, _snapshot(supplier), now=datetime.now(UTC)))

    async def launch(c: Any) -> None:
        await rel.open_release(c, rid, market, author="maker", now=datetime.now(UTC))
        for g in md.GATES:
            await rel.attest_gate(
                c,
                rid,
                1,
                gate=g.gate_id,
                status=md.GateStatus.PASSED,
                evidence_ref=f"EVID-{g.gate_id}",
                role=g.owner_role,
                by=f"rec-{g.owner_role}",
                now=datetime.now(UTC),
            )
        frozen = await rel.freeze_release(c, rid, 1, suppliers=[supplier], now=datetime.now(UTC))
        for role, who in {
            "legal": "al",
            "security_grc": "gus",
            "qa": "qa-lead",
            "product_owner": "pat",
        }.items():
            await rel.approve_release(
                c,
                rid,
                1,
                role=role,
                approver=who,
                signed_hash=frozen.release_hash or "",
                now=datetime.now(UTC),
            )
        await rel.launch_release(c, rid, 1, now=datetime.now(UTC))

    _run(rt_url, launch)
    template = _approved(
        rc.ClaimTemplate(
            template_id=f"kill-claim-{uuid.uuid4().hex[:6]}",
            version=1,
            text="Right to Rent checks use a certified provider.",
            audiences=frozenset({"PUBLIC_WEBSITE"}),
            countries=("GB",),
            certifications=(supplier,),
            markets=(market,),
        )
    )

    def state() -> tuple[Any, Any]:
        async def go(c: Any) -> tuple[Any, Any]:
            now = datetime.now(UTC)
            return await rel.market_open(c, market, now=now), (
                await claims_svc.judge(c, [template], at=now)
            )[0][1]

        return _run(rt_url, go)

    market_state, claim = state()
    assert market_state.open and claim.publishable

    from zid_platform.modules.verification.application import suspensions as svc

    sid = f"kill-sup-{uuid.uuid4().hex[:8]}"
    suspension = sp.Suspension(
        suspension_id=sid,
        version=1,
        scope=sp.Scope(supplier.entity, service_id="RTR", country="GB"),
        trigger=sp.Trigger.CERTIFICATION_WITHDRAWN,
        status=sp.Status.ACTIVE,
        reason="the certificate was withdrawn by the scheme",
        evidence_ref="INC-7",
        effective_at=datetime.now(UTC),
        recorded_by="ana",
        recorded_at=datetime.now(UTC),
    )
    _, impact = _run(
        rt_url,
        lambda c: svc.apply_suspension(c, suspension, now=datetime.now(UTC), templates=[template]),
    )
    assert impact.markets == (f"{rid}:v1",) and impact.claims == (f"{template.template_id}:v1",)

    market_state, claim = state()
    assert (
        not market_state.open
        and market_state.reason.startswith("supplier_hold:")
        and f"suspended:{sid}" in market_state.reason
    )
    assert not claim.publishable and any(f"suspended:{sid}" in r for r in claim.reasons)

    _run(
        rt_url,
        lambda c: svc.lift_suspension(
            c,
            sid,
            by="ben",
            reason="the scheme restored the certificate",
            evidence_ref="INC-8",
            now=datetime.now(UTC),
        ),
    )
    market_state, claim = state()
    assert (
        market_state.open and claim.publishable
    )  # rolled back: the release and the template were never edited
    _run(
        rt_url, lambda c: rel.withdraw_release(c, rid, 1, now=datetime.now(UTC))
    )  # leave the shared database as we found it


def _raw(values: dict[str, Any]) -> Any:
    async def work(c: Any) -> None:
        base = {
            "id": f"raw-{uuid.uuid4().hex[:8]}",
            "v": 1,
            "p": "raw-provider",
            "svc": "RTR",
            "c": "GB",
            "t": "MANUAL",
            "st": "ACTIVE",
            "r": "a reason long enough",
            "e": "INC-1",
            "ea": datetime.now(UTC),
            "by": "ana",
        } | values
        await c.execute(
            text(
                "INSERT INTO verify.provider_suspension (suspension_id, version, provider, service_id, country, trigger, status, reason, "
                "evidence_ref, effective_at, recorded_by, recorded_at) VALUES (:id, :v, :p, :svc, :c, :t, :st, :r, :e, :ea, :by, now())"
            ),
            base,
        )

    return work


def test_the_table_keeps_the_rules_when_the_domain_is_skipped(rt_url: str, owner_url: str) -> None:
    def refused(url: str, work: Any, fragment: str) -> None:
        with pytest.raises(DBAPIError) as e:
            _run(url, work)
        assert fragment in str(e.value), str(e.value)

    sid = f"raw-{uuid.uuid4().hex[:8]}"
    _run(rt_url, _raw({"id": sid}))
    refused(
        rt_url, _raw({"id": sid, "v": 2, "st": "LIFTED"}), "a different person lifts"
    )  # same person
    refused(
        rt_url,
        _raw({"id": sid, "v": 2, "st": "LIFTED", "by": "ben", "svc": "IDV"}),
        "keeps the scope",
    )
    refused(rt_url, _raw({"id": sid, "v": 3, "st": "LIFTED", "by": "ben"}), "next version")
    refused(rt_url, _raw({"svc": None, "c": None}), "violates")  # provider-wide without a breach
    refused(rt_url, _raw({"ea": datetime.now(UTC) + timedelta(hours=1)}), "in the future")
    refused(rt_url, _raw({"st": "LIFTED"}), "version 1 and ACTIVE")

    def sql(statement: str) -> Any:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"i": sid})

        return work

    refused(
        rt_url,
        sql("UPDATE verify.provider_suspension SET status = 'LIFTED' WHERE suspension_id = :i"),
        "append-only",
    )
    refused(
        owner_url,
        sql("DELETE FROM verify.provider_suspension WHERE suspension_id = :i"),
        "append-only",
    )
    _run(
        rt_url, _raw({"id": sid, "v": 2, "st": "LIFTED", "by": "ben"})
    )  # a proper lift is accepted
    assert replace  # keep the import used by readers of this file
