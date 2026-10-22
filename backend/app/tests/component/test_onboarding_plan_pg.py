"""Onboarding plan, shadow, pilot and rollback end to end (G-W4; OVERLAY-001 §17-20; OVL-19..22).

CONFIGURING -> INTEGRATING needs owner, residency and a verified domain; -> SHADOW needs an approved
plan and an active application. In SHADOW every AuthZEN answer is EVALUATE_ONLY and recorded next to
the incumbent's outcome; one critical false allow blocks the pilot. PILOT enforces only inside an
approved stage scope; ACTIVE needs a rollback to have been executed and recovered from during the
pilot. Plans are immutable once approved.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, approval, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.onboarding.infrastructure import store as plan_store
from zid_platform.modules.tenant.application import domains, residency
from zid_platform.settings import Settings

PEP = "pep-payroll"


def test_plan_shadow_pilot_rollback_activate(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)
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

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    def evaluate(api: TestClient, incumbent: str, action: str = "resource.write") -> dict[str, Any]:
        r = api.post(
            "/access/v1/evaluation",
            json={
                "subject": {"type": "principal", "id": o.second},
                "action": {"name": action},
                "resource": {"type": "document", "id": "d1"},
                "context": {"purpose": "payroll", "incumbent_decision": incumbent},
            },
            headers=o.bearer(o.owner, client=PEP),
        )
        assert r.status_code == 200, r.text
        return dict(r.json())

    with TestClient(app) as api:
        base = "/v1/onboarding/plans"
        r = api.post(
            base,
            json={"target_mode": "O1", "exit_criteria": {"max_false_allows": 3}},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 422  # criteria can only tighten the platform floor
        r = api.post(
            base,
            json={
                "target_mode": "O1",
                "scope": {"max_risk": 1},
                "exit_criteria": {"min_observations": 3},
            },
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        plan = r.json()["plan_id"]

        def approved(action: str, resource: str, change: str) -> dict[str, str]:
            return {
                "approval_request_id": approval(
                    api, o, owner_url, action=action, resource=resource, change=change
                )
            }

        r = api.post(f"{base}/{plan}:start-integration", headers=o.bearer(o.owner))
        assert r.status_code == 409 and code(r) == "ONBOARDING_PLAN_NOT_APPROVED"
        r = api.post(
            f"{base}/{plan}:approve",
            json=approved("tenant.onboarding.manage", f"onboarding_plan:{plan}", "APPROVE"),
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.text

        r = api.post(f"{base}/{plan}:start-integration", headers=o.bearer(o.owner))
        assert r.status_code == 409 and code(r) == "PILOT_GATE_FAILED"  # no residency, no domain

        async def configure() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                await residency.set_residency(
                    c,
                    o.tenant,
                    profile="SRP-0",
                    home_cell_id="uk-1",
                    recovery_cells=[],
                    prohibited_cells=[],
                    operator_juris=[],
                    key_profile="PLATFORM_MANAGED",
                    jurisdiction_pack_id="uk-gdpr",
                    approval_ref="TEST-1",
                    actor=o.owner,
                )
                d = await domains.start(
                    c,
                    o.tenant,
                    domain=f"t{o.tenant[:8]}.example.com",
                    method=domains.Method.DNS_TXT,
                    actor=o.owner,
                )

                async def txt(_: str) -> list[str]:
                    return [f"zoiko-domain-verification={d.challenge_token}"]

                async def https(_: str) -> str | None:
                    return None

                await domains.verify(
                    c, d.domain_id, checks=domains.DomainChecks(txt, https), actor=o.owner
                )

        asyncio.run(configure())
        r = api.post(f"{base}/{plan}:start-integration", headers=o.bearer(o.owner))
        assert r.status_code == 200 and r.json()["tenant_status"] == "INTEGRATING", r.text

        r = api.post(f"{base}/{plan}:start-shadow", headers=o.bearer(o.owner))
        assert r.status_code == 409  # no active application yet
        r = api.post(
            "/v1/integration/applications",
            json={
                "name": "Payroll",
                "environment": "PROD",
                "protocols": ["AUTHZEN"],
                "pep_client_id": PEP,
                "pep_obligations": ["STEP_UP", "HUMAN_APPROVAL"],
                "pep_context_fields": ["purpose"],
            },
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        app_id = r.json()["application_id"]
        for status in ("TESTING", "ACTIVE"):
            api.post(
                f"/v1/integration/applications/{app_id}:transition",
                json={"status": status},
                headers=o.bearer(o.owner),
            )
        r = api.post(f"{base}/{plan}:start-shadow", headers=o.bearer(o.owner))
        assert r.status_code == 200 and r.json()["tenant_status"] == "SHADOW", r.text

        # --- shadow: evaluate only, record next to the incumbent ---------------------------------
        for _ in range(3):
            out = evaluate(api, "DENY")
            assert out["context"]["enforcement"] == "EVALUATE_ONLY"
        gates = {
            g["gate_id"]: g["status"]
            for g in api.post(f"{base}/{plan}:validate", headers=o.bearer(o.owner)).json()["gates"]
        }
        assert gates["shadow.observations"] == "PASS" and gates["shadow.false_allows"] == "PASS"

        async def false_allow(add: bool) -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                if add:
                    await c.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": o.tenant}
                    )
                    await plan_store.record_observation(
                        c,
                        o.tenant,
                        {
                            "plan": plan,
                            "decision": "d-x",
                            "app": app_id,
                            "action": "payment.release",
                            "risk": 3,
                            "outcome": "ALLOW",
                            "allow": True,
                            "incumbent": "DENY",
                            "mismatch": "FALSE_ALLOW",
                            "complete": True,
                            "enforcement": "EVALUATE_ONLY",
                            "subject": "c",
                            "latency": 1.0,
                        },
                    )
                else:
                    await c.execute(
                        text("DELETE FROM platform.shadow_observation WHERE decision_id = 'd-x'")
                    )

        async def steady_latency() -> None:
            # The latency gate is unit-tested (test_overlay_rules); here wall-clock timings of a
            # handful of decisions on a dev machine must not decide the false-allow scenario.
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE platform.shadow_observation SET latency_ms = 5 WHERE plan_id = :p"
                    ),
                    {"p": uuid.UUID(plan)},
                )

        asyncio.run(steady_latency())
        asyncio.run(false_allow(True))
        pilot = lambda: api.post(
            f"{base}/{plan}:start-pilot",
            json=approved("tenant.onboarding.activate", f"onboarding_plan:{plan}", "START_PILOT"),
            headers=o.bearer(o.owner),
        )
        r = pilot()
        assert r.status_code == 409 and code(r) == "SHADOW_EXIT_CRITERIA_FAILED"  # OVL-20
        asyncio.run(false_allow(False))
        r = pilot()
        assert r.status_code == 200 and r.json()["tenant_status"] == "PILOT", r.text

        # --- pilot: enforcement only inside an approved scope ------------------------------------
        assert evaluate(api, "DENY")["context"]["enforcement"] == "EVALUATE_ONLY"
        r = api.post(
            f"{base}/{plan}/stages",
            json={
                "name": "Payroll writes",
                "scope": {"applications": [app_id], "actions": ["resource.write"], "max_risk": 1},
            },
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        stage = r.json()["stage_id"]
        r = api.post(
            f"/v1/onboarding/stages/{stage}:activate",
            json=approved("tenant.onboarding.activate", f"onboarding_stage:{stage}", "ACTIVATE"),
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
        assert evaluate(api, "DENY")["context"]["enforcement"] == "ENFORCE"  # OVL-21
        assert (
            evaluate(api, "DENY", action="invoice.approve")["context"]["enforcement"]
            == "EVALUATE_ONLY"
        )

        activate = lambda: api.post(
            f"{base}/{plan}:activate",
            json=approved("tenant.onboarding.activate", f"onboarding_plan:{plan}", "ACTIVATE"),
            headers=o.bearer(o.owner),
        )
        r = activate()
        assert r.status_code == 409 and code(r) == "PILOT_GATE_FAILED"
        details = {d["field"]: d["issue"] for d in r.json()["error"]["details"]}
        assert details == {"pilot.rollback_tested": "ROLLBACK_PLAN_UNTESTED"}

        # --- a rehearsed rollback, then production --------------------------------------------
        r = api.post(
            f"{base}/{plan}:rollback", json={"reason_code": "REHEARSAL"}, headers=o.bearer(o.owner)
        )
        assert r.status_code == 200 and r.json()["tenant_status"] == "SHADOW"  # OVL-22
        stages = api.get(f"{base}/{plan}/stages", headers=o.bearer(o.owner)).json()["items"]
        assert stages[0]["status"] == "ROLLED_BACK"
        assert pilot().json()["tenant_status"] == "PILOT"
        r = activate()
        assert r.status_code == 200 and r.json()["tenant_status"] == "ACTIVE", r.text
        assert evaluate(api, "DENY")["context"]["enforcement"] == "ENFORCE"

    async def immutable() -> None:
        async with engine(rt_url) as eng:
            with pytest.raises(DBAPIError):
                async with tenant_transaction(eng, ctx(o.tenant)) as c:
                    await c.execute(text("UPDATE platform.onboarding_plan SET scope = '{}'"))
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                rows = (
                    await c.execute(text("SELECT count(*) FROM platform.shadow_observation"))
                ).scalar_one()
                assert rows >= 6

    asyncio.run(immutable())
