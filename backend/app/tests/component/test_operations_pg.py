"""Operational control over the API (G-W8; ZID-SRE-001 §5, §16-17, §23-24, §26, §29; OPS-02/06/18/
23/26/28; SRE-25/32).

Any active operator declares an incident at once and the timeline records every change; resolving
needs validation, closing a SEV1 needs its PIR with independently verified action items. A freeze
blocks ordinary change but never fixes; lifting it needs an approved grant. A PRR with a failed gate
is not production-ready. Requests are measured per journey and exported for Prometheus.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, activate_grant, operators, uid
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operations.domain.incidents import PRR_GATES
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_incidents_freeze_prr_and_slis(rt_url: str, owner_url: str) -> None:
    alice, bob, carol, mallory = uid(), uid(), uid(), uid()
    o = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.REQUESTER},
        },
        unregistered=(mallory,),
    )
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            operator_tenant_id=o.tenant,
            operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )
    with TestClient(app) as api:
        # --- catalogue and journey SLIs -------------------------------------------------------
        r = api.get("/ops/services", headers=o.bearer(alice))
        assert r.status_code == 200 and {"authorization", "mandate"} <= {
            s["service_id"] for s in r.json()["services"]
        }
        for _ in range(3):
            api.post(
                "/access/v1/evaluation", json={}
            )  # 4xx: caller-caused, a good availability event
        r = api.get("/ops/services/authorization/slos", headers=o.bearer(alice))
        avail = next(s for s in r.json()["slos"] if s["sli"] == "availability")
        assert (
            avail["events"] >= 3
            and avail["good_ratio"] == 1.0
            and avail["budget_state"] == "NORMAL"
        )
        r = api.get(
            "/health/live",
            headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
        )
        assert r.headers["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")  # OPS-06
        metrics = api.get("/internal/metrics").text
        assert (
            'zid_sli_events_total{journey="authorization",sli="availability",outcome="good"}'
            in metrics
        )
        assert "tenant" not in metrics.split("zid_sli_events_total", 1)[1].split("\n", 1)[0]
        assert api.get("/health/startup").status_code == 200

        # --- declaring needs only an active operator ------------------------------------------
        body = {
            "title": "Mandate reservations failing in uk-1",
            "severity": "SEV2",
            "services": ["mandate"],
            "cells": ["uk-1"],
        }
        r = api.post("/ops/incidents", json=body, headers=o.bearer(mallory))
        assert r.status_code == 403 and code(r) == "OPERATOR_PROFILE_MISSING"
        r = api.post("/ops/incidents", json={**body, "services": ["nope"]}, headers=o.bearer(alice))
        assert r.status_code == 404
        r = api.post("/ops/incidents", json=body, headers=o.bearer(alice))
        assert r.status_code == 201, r.text
        inc = r.json()["incident_id"]
        assert (
            r.json()["roles"] == {"INCIDENT_COMMANDER": alice} and r.json()["status"] == "DECLARED"
        )

        r = api.patch(
            f"/ops/incidents/{inc}",
            headers=o.bearer(carol),
            json={
                "severity": "SEV1",
                "status": "MITIGATING",
                "roles": {"SCRIBE": carol},
                "reason": "cohort of tenants affected",
            },
        )
        assert r.status_code == 200 and r.json()["severity"] == "SEV1", r.text
        r = api.patch(
            f"/ops/incidents/{inc}",
            json={"roles": {"JESTER": carol}, "reason": "role"},
            headers=o.bearer(carol),
        )
        assert r.status_code == 422
        r = api.post(
            f"/ops/incidents/{inc}/actions",
            headers=o.bearer(carol),
            json={
                "kind": "DECISION",
                "note": "roll back release 2026.10.22-1",
                "data": {"change": "CHG-7"},
            },
        )
        assert r.status_code == 201

        # --- the freeze: protective, then governed release ------------------------------------
        r = api.post(
            "/ops/services/mandate:freeze-changes",
            json={"reason_code": "INCIDENT", "incident_id": inc},
            headers=o.bearer(carol),
        )
        assert r.status_code == 201, r.text
        r = api.post(
            "/ops/services/mandate:freeze-changes",
            json={"reason_code": "INCIDENT"},
            headers=o.bearer(alice),
        )
        assert r.status_code == 409 and code(r) == "CHANGE_ALREADY_FROZEN"
        r = api.get("/ops/services/mandate/change-policy", headers=o.bearer(alice))
        assert r.json()["allowed"] is False and r.json()["reason"] == "CHANGE_FROZEN"  # OPS-23
        r = api.get("/ops/services/mandate/change-policy?change_class=FIX", headers=o.bearer(alice))
        assert r.json()["allowed"] is True and r.json()["requires"] == "incident_commander"
        h = api.get("/ops/services/mandate/health", headers=o.bearer(alice)).json()
        assert h["state"] == "DEGRADED" and h["frozen"] and inc in h["open_incidents"]
        r = api.post("/ops/services/mandate:unfreeze-changes", headers=o.bearer(alice))
        assert r.status_code == 403 and code(r) == "OPERATOR_CAPABILITY_REQUIRED"

        # --- resolve, PIR and close -----------------------------------------------------------
        r = api.post(
            f"/ops/incidents/{inc}:resolve", json={"validation": "ok"}, headers=o.bearer(alice)
        )
        assert r.status_code == 422
        r = api.post(
            f"/ops/incidents/{inc}:resolve",
            headers=o.bearer(alice),
            json={"validation": "reservations succeed; synthetic mandate probe green for 30 min"},
        )
        assert r.status_code == 200 and r.json()["status"] == "RESOLVED", r.text
        r = api.post(f"/ops/incidents/{inc}:close", json={}, headers=o.bearer(alice))
        assert r.status_code == 409 and code(r) == "PIR_REQUIRED"
        r = api.post(
            f"/ops/incidents/{inc}/action-items",
            headers=o.bearer(alice),
            json={
                "title": "Canary gate on reservation errors",
                "owner": carol,
                "due": "2026-11-15",
            },
        )
        item = r.json()["item_id"]
        r = api.post(
            f"/ops/incidents/{inc}:close", json={"pir_ref": "PIR-2026-17"}, headers=o.bearer(alice)
        )
        assert r.status_code == 409 and code(r) == "PIR_ACTION_OPEN"
        r = api.post(f"/ops/incidents/{inc}/action-items/{item}:verify", headers=o.bearer(carol))
        assert r.status_code == 403 and code(r) == "PIR_VERIFICATION_NOT_INDEPENDENT"
        assert (
            api.post(
                f"/ops/incidents/{inc}/action-items/{item}:verify", headers=o.bearer(alice)
            ).status_code
            == 200
        )
        r = api.post(
            f"/ops/incidents/{inc}:close", json={"pir_ref": "PIR-2026-17"}, headers=o.bearer(alice)
        )
        assert r.status_code == 200 and r.json()["status"] == "CLOSED", r.text
        r = api.get(f"/ops/incidents/{inc}", headers=o.bearer(alice)).json()
        assert [e["kind"] for e in r["timeline"]] == [
            "DECLARED",
            "SEVERITY",
            "STATUS",
            "ROLE",
            "DECISION",
            "RESOLVED",
            "CLOSED",
        ]
        assert r["action_items"][0]["verified_by"] == alice

        activate_grant(
            api,
            o,
            alice,
            (bob,),
            capability="platform.change.manage",
            resource_scope="service:mandate",
        )
        r = api.post("/ops/services/mandate:unfreeze-changes", headers=o.bearer(alice))
        assert r.status_code == 200 and r.json()["frozen"] is False, r.text
        assert (
            api.get("/ops/services/mandate/change-policy", headers=o.bearer(alice)).json()[
                "allowed"
            ]
            is True
        )

        # --- PRR -------------------------------------------------------------------------------
        gates = [{"gate": g, "status": "PASS", "evidence_ref": f"EV-{g}"} for g in PRR_GATES]
        gates[6] = {"gate": "ha_dr", "status": "FAIL"}
        r = api.post(
            "/ops/readiness-reviews",
            headers=o.bearer(carol),
            json={"service_id": "mandate", "release_ref": "2026.10.22-2", "gates": gates},
        )
        assert r.status_code == 201 and r.json()["production_ready"] is False, r.text
        assert r.json()["blocking"] == [{"gate": "ha_dr", "reason": "FAILED"}]
        gates[6] = {
            "gate": "ha_dr",
            "status": "ACCEPTED",
            "risk_owner": bob,
            "risk_expires": str(date(2099, 1, 1)),
            "compensating_control": "manual failover runbook RB-12 rehearsed weekly",
        }
        api.post(
            "/ops/readiness-reviews",
            headers=o.bearer(carol),
            json={"service_id": "mandate", "release_ref": "2026.10.22-3", "gates": gates},
        )
        r = api.get("/ops/services/mandate/readiness", headers=o.bearer(alice))
        assert r.json()["production_ready"] is True and r.json()["release_ref"] == "2026.10.22-3"

        assert api.get("/ops/cells/uk-1/health", headers=o.bearer(alice)).json()["state"] in (
            "UNKNOWN",
            "HEALTHY",
        )

    async def events() -> set[str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT DISTINCT event_type FROM events.outbox_event WHERE tenant_id = :t"
                    ),
                    {"t": o.tenant},
                )
            ).all()
            return {r.event_type for r in rows}

    got = asyncio.run(events())
    for name in (
        "ops.incident.declared",
        "ops.incident.severity_changed",
        "ops.incident.resolved",
        "ops.incident.closed",
        "ops.change_freeze.applied",
        "ops.change_freeze.lifted",
        "ops.readiness_review.recorded",
    ):
        assert f"com.zoiko.id.{name}.v1" in got, name
