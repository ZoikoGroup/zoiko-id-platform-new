"""Acceptance rows AG2-T04 and AG2-T33 (ZID-AG-WF-002): an agent's id never changes when its name is edited, and a billing or alert outage never lifts a hard cap.

Both are properties that must hold whatever happens around them, so each test breaks the thing around the rule and shows the rule still holds.
Synthetic data only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from test_agent_governance_pg import REGION, Gov, _hash_of, svc
from test_daara_pg import Daara, _enforce  # noqa: F401 - the fixture is applied by name below
from zid_platform.modules.machine.infrastructure import machine_store


def ready(gv: Gov) -> None:
    """An agent that may spend on AI, with a company day cap of 20, the agent's own day cap of 0.50 and a per-call cap of 0.10."""
    gv.run(
        lambda c: svc.set_baseline(
            c, gv.tenant, gv.p("own"), allowed_tools=["docs.read"], allowed_regions=[REGION],
            ai_providers=["provider-a"], quotas=None, schedules_allowed=False,
        )
    )  # fmt: skip
    gv.publish(signers=("sec", "auth"))
    ai = gv.draft(ai_enabled=True)
    req = gv.request(ai["policy_id"])
    gv.approve(req, ["sec2", "priv", "fin"])
    assert gv.apply(req["request_id"]).ok
    company = gv.budget("COMPANY", "DAY", "20", signers=())
    chash = gv.run(lambda c: _hash_of(c, company["approval_request_id"]))
    for who in ("fin", "sec"):
        assert gv.decide(company["approval_request_id"], who, chash).ok
    gv.budget("AGENT", "DAY", "0.50", agent=True)
    gv.budget("CALL", "CALL", "0.10", agent=False)


def agent_ceiling(gv: Gov) -> dict[str, Any]:
    return next(c for c in gv.view()["ceilings"] if c["scope"] == "agent")


def test_a_billing_or_alert_outage_never_lifts_a_hard_cap(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG2-T33: the cap is enforced where the money is reserved. Alerts are advice; an outage of the thing that raises them changes nothing."""
    gv = Gov(rt_url, owner_url)
    ready(gv)
    assert gv.reserve("0.10", key="before-0001").ok

    async def down(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("the alert and billing services are unreachable")

    # every path that tracks, raises or lists an alert now fails
    monkeypatch.setattr(svc, "_with_alert_state", down)
    with pytest.raises(ConnectionError):
        gv.run(lambda c: svc.overview(c, gv.tenant, gv.p("own")))

    won = [gv.reserve("0.10", key=f"during-{i:04d}") for i in range(1, 8)]
    assert sum(1 for r in won if r.ok) == 4, [
        r.codes for r in won
    ]  # 0.50 in 0.10 steps: one before, four more, nothing beyond
    assert {c for r in won if not r.ok for c in r.codes} == {"BUDGET_EXCEEDED"}
    cap = agent_ceiling(gv)
    assert Decimal(cap["reserved"]) == Decimal("0.50") and Decimal(cap["available"]) == 0
    assert Decimal(cap["reserved"]) + Decimal(cap["committed"]) <= Decimal(cap["hard_cap"])

    # the freeze is not an alert either: a stop still stops, with the alert path down
    assert gv.reserve("0.10", key="after-0001").codes == ("BUDGET_EXCEEDED",)


@pytest.mark.usefixtures("_enforce")
def test_an_agents_id_never_changes_when_its_display_name_is_edited(
    rt_url: str, owner_url: str
) -> None:
    """AG2-T04: the display label can be edited as often as wanted; the agent keeps one id, and no route renames the agent itself."""
    dd = Daara(rt_url, owner_url)
    before = dd.run(lambda c: machine_store.load_agent(c, dd.agent))
    assert before is not None and before.agent_principal_id == dd.agent

    first = dd.setup(label="Access Readiness: Finance")
    second = dd.setup(label="Access Readiness: Finance, renamed")
    third = dd.setup(label="Renamed again")
    assert {first["agent_id"], second["agent_id"], third["agent_id"]} == {dd.agent}
    assert third["setup"]["label"] == "Renamed again"

    after = dd.run(lambda c: machine_store.load_agent(c, dd.agent))
    assert after is not None
    assert after.agent_principal_id == dd.agent  # the id is the identity; the label is only a label
    assert (
        after.agent_name == before.agent_name
    )  # the registered name is not what the setup label edits

    # the principal the agent acts as is the same row throughout
    assert (
        dd.w.owner(
            "SELECT count(*) FROM agent.agent_profile WHERE agent_principal_id = :a AND tenant_id = :t",
            a=__import__("uuid").UUID(dd.agent),
        )[0][0]
        == 1
    )

    # and the API offers no way to change an agent's name or id: the agent resource is read-only apart from its lifecycle actions
    paths = dd.w.app.openapi()["paths"]
    verbs = set(paths["/v1/machine/agents/{agent_id}"])
    assert verbs == {"get"}, verbs
    renamed = [
        p
        for p in paths
        if p.startswith("/v1/machine/agents/{agent_id}") and p.endswith(("rename", "name"))
    ]
    assert renamed == []


def test_daara_is_retired_by_default_its_routes_answer_404_and_the_alert_routes_stay(
    rt_url: str, owner_url: str
) -> None:
    """ADR adr-two-implementations-of-wf-001, option 1: the DAARA agent's routes answer 404 unless ZID_DAARA_ENABLED is set; nothing is deleted."""
    from conftest import CELL
    from fastapi.testclient import TestClient
    from gateway_fixture import AUD, ISS
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    dd = Daara(rt_url, owner_url)
    head = dd.w.bearer(dd.w.human, scope=frozenset({"admin"}))

    def app(**over: Any) -> Any:
        return create_app(
            Settings(
                database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
                environment="test", cell_uuid=CELL, **over,
            ),
            keyring=dd.w.ring,
        )  # fmt: skip

    daara_routes = [
        ("GET", "/v1/machine/daara/template"),
        ("GET", f"/v1/machine/agents/{dd.agent}/daara"),
        ("PUT", f"/v1/machine/agents/{dd.agent}/daara"),
        ("POST", f"/v1/machine/agents/{dd.agent}/daara:validate"),
        ("POST", f"/v1/machine/agents/{dd.agent}/daara/dry-run"),
        ("POST", f"/v1/machine/agents/{dd.agent}/daara:request-publish"),
        ("POST", f"/v1/machine/agents/{dd.agent}/daara/runs"),
        ("GET", f"/v1/machine/agents/{dd.agent}/daara/runs"),
        ("GET", "/v1/machine/agent-review"),
        ("GET", "/v1/machine/agent-review/00000000-0000-0000-0000-000000000000"),
        ("POST", "/v1/machine/agent-review/00000000-0000-0000-0000-000000000000:dismiss"),
    ]
    with TestClient(app(), base_url=ISS) as http:  # the default: retired
        for method, path in daara_routes:
            r = http.request(method, path, headers=head, json={} if method != "GET" else None)
            assert r.status_code == 404, (method, path, r.status_code)
            assert "replaced by the Access Readiness agent" in r.text, (method, path)
        # the governance alert routes live in the same file but are not DAARA's: they are still served (this one answers about the alert, not about DAARA)
        alert = http.post(
            "/v1/machine/governance/alerts/nothing:acknowledge", headers=head, json={}
        )
        assert "replaced by the Access Readiness agent" not in alert.text

    with TestClient(app(daara_enabled=True), base_url=ISS) as http:  # on again, unchanged
        assert http.get("/v1/machine/daara/template", headers=head).status_code in (200, 403)
        assert (
            "replaced by the Access Readiness agent"
            not in http.get("/v1/machine/daara/template", headers=head).text
        )
