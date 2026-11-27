"""The Command Center over HTTP on PostgreSQL (ZID-CPS-WF-02; Q01, Q02, Q04, Q29, Q36, Q39, Q43, Q44, Q47).

Facts arrive the way the ingest consumer writes them; the API is read as the tenant's Owner, as an Administrator with no analytics grant,
and as another tenant."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import give_tenant_role
from conftest import ctx, engine
from onboarding_fixture import onboard
from test_v2_privacy_pg import api_for, runtime  # noqa: F401  (runtime is autouse)
from zid_context import uuid7
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.modules.verify_analytics.ports import ingest

P = "/v1/verify-analytics"
NOW = datetime.now(UTC)


def event(
    tenant: str, name: str, data: dict[str, Any], at: datetime, event_id: str | None = None
) -> CloudEvent:
    return CloudEvent(id=event_id or str(uuid7()), type=f"com.zoiko.id.{name}.v1", source="test", subject="s", time=at, tenantid=tenant, cellid="uk-1",
                      aggregatetype="t", aggregateid="t", aggregateversion=1, data=data, correlationid="cor")  # fmt: skip


def journey(tenant: str, ref: str, *, approved: bool) -> list[CloudEvent]:
    t0 = NOW - timedelta(days=2)
    base = {"session_ref": ref, "environment": "test", "market": "GB"}
    evs = [
        event(
            tenant,
            "verification.session.created",
            {
                **base,
                "expires_at": (t0 + timedelta(hours=1)).isoformat(),
                "full_name": "never kept",
            },
            t0,
        ),
        event(
            tenant,
            "verification.session.capture_started",
            {**base, "step": "DOCUMENT"},
            t0 + timedelta(minutes=2),
        ),
        event(
            tenant,
            "verification.session.capture_attempt_completed",
            {**base, "attempt_no": 1, "result": "ACCEPTED"},
            t0 + timedelta(minutes=3),
        ),
        event(tenant, "verification.session.submitted", base, t0 + timedelta(minutes=4)),
    ]
    if approved:
        evs.append(event(tenant, "verification.session.finalized", {**base, "state": "FINAL_APPROVED", "outcome": "APPROVED", "decision_version": 3,
                                                                     "decided_by": "AUTOMATED"}, t0 + timedelta(minutes=5)))  # fmt: skip
    return evs


def load(rt_url: str, tenant: str, events: list[CloudEvent]) -> list[bool]:
    async def go() -> list[bool]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return [await ingest(c, e) for e in events]

    return asyncio.run(go())


def test_facts_land_once_without_personal_fields_and_the_owner_reads_governed_figures(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    evs = [e for i in range(4) for e in journey(o.tenant, f"vs_{uuid.uuid4()}", approved=i < 3)]
    assert all(load(rt_url, o.tenant, evs))
    assert not any(load(rt_url, o.tenant, evs[:3]))  # Q04: a replay adds nothing
    with api_for(rt_url, o, verify_analytics_min_group=2) as api:
        owner = o.bearer(o.owner)
        ov = api.get(
            f"{P}/overview",
            headers=owner,
            params={"from": (NOW - timedelta(days=6)).date().isoformat()},
        )
        assert ov.status_code == 200, ov.text
        tiles = {t["metric_id"]: t for t in ov.json()["tiles"]}
        assert tiles["M01"]["value"] == 4 and tiles["M05"]["numerator"] == 3
        assert (
            ov.json()["scope"]["data_label"] == "SANDBOX"
        )  # a test deployment never shows production figures (Q11)
        q = api.post(
            f"{P}/query",
            headers=owner,
            json={"metric_ids": ["M03", "M06"], "dimension": "market", "snapshot": True},
        )
        assert q.status_code == 200, q.text
        body = q.json()
        assert (
            body["breakdowns"]["M03"][0]["market"] == "GB"
            and body["query_fingerprint"]
            and body["snapshot_id"]
        )
        assert "never kept" not in q.text  # only allow-listed fields were stored
        again = api.get(f"{P}/snapshots/{body['snapshot_id']}", headers=owner).json()
        assert (
            again["results"] == body["results"]
        )  # Q43: a rerun of the snapshot gives the same totals
        assert (
            api.post(f"{P}/query", headers=owner, json={"metric_ids": ["M13"]}).status_code == 403
        )  # cost lives behind billing access


def test_small_groups_are_withheld(rt_url: str) -> None:
    o = onboard(rt_url)
    load(rt_url, o.tenant, journey(o.tenant, f"vs_{uuid.uuid4()}", approved=True))
    with api_for(rt_url, o, verify_analytics_min_group=20) as api:
        tiles = {
            t["metric_id"]: t
            for t in api.get(f"{P}/overview", headers=o.bearer(o.owner)).json()["tiles"]
        }
        assert (
            tiles["M01"]["suppressed"] is True and tiles["M01"]["value"] is None
        )  # Q36: one session is never shown


def test_an_administrator_without_a_grant_is_refused_until_the_owner_gives_one_and_tenants_never_mix(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    other = onboard(rt_url)
    load(rt_url, other.tenant, journey(other.tenant, f"vs_{uuid.uuid4()}", approved=True))
    with api_for(rt_url, o, verify_analytics_min_group=1) as api:
        admin = o.bearer(o.second)
        refused = api.get(f"{P}/overview", headers=admin)
        assert (
            refused.status_code == 403
            and refused.json()["error"]["code"] == "ANALYTICS_PERMISSION_REQUIRED"
        )
        self_grant = api.post(
            f"{P}/grants",
            headers=admin,
            json={"grantee_principal_id": o.second, "bundle": "AUDITOR"},
        )
        assert self_grant.status_code == 403  # only an Owner gives analytics access
        given = api.post(
            f"{P}/grants",
            headers=o.bearer(o.owner),
            json={"grantee_principal_id": o.second, "bundle": "OPERATIONS_LEAD"},
        )
        assert given.status_code == 201, given.text
        ov = api.get(f"{P}/overview", headers=admin)
        assert ov.status_code == 200
        assert {t["metric_id"]: t for t in ov.json()["tiles"]}["M01"][
            "value"
        ] == 0  # Q01/Q02: the other tenant's session is invisible
        assert (
            api.get(f"{P}/grants", headers=admin).status_code == 403
        )  # a bundle never carries delegation
        assert (
            api.get(f"{P}/access/me", headers=admin).json()["finance"] is False
        )  # Q29: analytics access is not billing access


def test_an_export_downloads_once_and_a_correction_needs_a_second_person(rt_url: str) -> None:
    o = onboard(rt_url)
    evs = journey(o.tenant, f"vs_{uuid.uuid4()}", approved=True)
    load(rt_url, o.tenant, evs)
    with api_for(rt_url, o, verify_analytics_min_group=1) as api:
        owner = o.bearer(o.owner)
        snap = api.post(
            f"{P}/query", headers=owner, json={"metric_ids": ["M01", "M04"], "snapshot": True}
        ).json()["snapshot_id"]
        made = api.post(
            f"{P}/exports",
            headers=owner,
            json={"snapshot_id": snap, "format": "csv", "purpose": "monthly review"},
        )
        assert made.status_code == 202, made.text
        eid, token = made.json()["export_id"], made.json()["download_token"]
        bad = api.get(f"{P}/exports/{eid}/download", headers={**owner, "X-Download-Token": "wrong"})
        assert bad.status_code == 410
        got = api.get(f"{P}/exports/{eid}/download", headers={**owner, "X-Download-Token": token})
        assert got.status_code == 200 and b"definition_version" in got.content
        assert (
            api.get(
                f"{P}/exports/{eid}/download", headers={**owner, "X-Download-Token": token}
            ).status_code
            == 410
        )  # Q39: once only

        fact = evs[-1].id
        req = api.post(
            f"{P}/corrections",
            headers=owner,
            json={"event_id": fact, "reason": "test decision recorded twice by mistake"},
        )
        assert req.status_code == 201, req.text
        cid = req.json()["correction_id"]
        assert (
            api.post(f"{P}/corrections/{cid}:approve", headers=owner).status_code == 403
        )  # the same person cannot approve

        async def make_member() -> None:  # analytics access goes to an active member only
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                await give_tenant_role(c, o.tenant, o.second, role="MEMBER")

        asyncio.run(make_member())
        given = api.post(
            f"{P}/grants",
            headers=owner,
            json={"grantee_principal_id": o.second, "bundle": "DATA_STEWARD"},
        )
        assert given.status_code in (200, 201), given.text
        assert (
            api.post(f"{P}/corrections/{cid}:approve", headers=o.bearer(o.second)).status_code
            == 200
        )
        after = api.post(f"{P}/query", headers=owner, json={"metric_ids": ["M04"]}).json()
        assert after["results"][0]["numerator"] == 0  # the excluded decision no longer counts
        assert (
            api.get(f"{P}/snapshots/{snap}", headers=owner).json()["results"][1]["numerator"] == 1
        )  # Q44: the frozen figure never changes
