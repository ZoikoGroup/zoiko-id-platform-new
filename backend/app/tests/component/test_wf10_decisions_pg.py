"""The two decisions that were left to the owners, made switchable and measurable (ZID-CP-WF10 v2.0; CR-173).

1. Containment: every refusal of a revoked credential's token is timed from the revocation, the policy page shows the MEASURED figure beside the RATIFIED objective, and it never shows a number as a promise
   unless the owners set it (ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS).
2. The sandbox suite gate: off, shadow (the default, counting what it would have refused) or enforce (ZID_VERIFY_GO_LIVE_SUITE_GATE), so the owners can look at the evidence and then flip one switch.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from test_verify_api_go_live_pg import GOOD, Company, code, uid
from test_verify_api_sessions_pg import World
from test_wf10_certification_pg import perfect
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import certification_service, observe, service


def count() -> int:
    s = observe.REVOCATION_TO_REFUSAL.series.get(observe.REVOCATION_TO_REFUSAL._key({}))
    return int(s[-1]) if s else 0


def test_a_refused_token_of_a_revoked_credential_is_timed_and_the_policy_shows_measured_beside_ratified(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(rt_url)
    try:
        async def seed() -> dict[str, Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                made = await service.create_application(t, w.tenant, uid(), name="Contain", environment="SANDBOX", scopes=None, origins=None)
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                return {"app": made, "cred": await service.issue_credential(t, w.tenant, uid(), made["application_id"])}

        made = asyncio.run(seed())
        r = w.http.post("/v1/verify-api/oauth2/token", json={"client_id": made["cred"]["client_id"], "client_secret": made["cred"]["client_secret"]})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert w.http.get("/v1/verify-api/whoami", headers=h).status_code == 200

        monkeypatch.delenv("ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS", raising=False)
        before = count()
        pol = w.http.get("/v1/verify-api/credential-policy").json()["revocation_objective"]
        assert pol["status"] == "NOT_RATIFIED" and pol["ratified_seconds"] is None and "No numeric objective has been ratified" in pol["statement"]

        async def revoke() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                await service.revoke_credential(t, made["app"]["application_id"], made["cred"]["credential_id"])

        asyncio.run(revoke())
        assert w.http.get("/v1/verify-api/whoami", headers=h).status_code == 401  # refused on the very next call
        assert w.http.get("/v1/verify-api/whoami", headers=h).status_code == 401
        assert count() == before + 2  # each refusal was timed from the revocation

        monkeypatch.setenv("ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS", "5")
        met = w.http.get("/v1/verify-api/credential-policy").json()["revocation_objective"]
        assert met["ratified_seconds"] == 5.0 and met["status"] == "MET" and met["measured_p99_seconds"] <= 5.0 and met["measured_refusals"] >= 2 and "measured 99th percentile" in met["statement"]
        monkeypatch.setenv("ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS", "0.001")
        missed = w.http.get("/v1/verify-api/credential-policy").json()["revocation_objective"]
        assert missed["status"] == "NOT_MET" and "but the measured 99th percentile" in missed["statement"]  # a number the measurements do not meet is shown, not hidden
        monkeypatch.setenv("ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS", "soon")
        assert w.http.get("/v1/verify-api/credential-policy").json()["revocation_objective"]["status"] == "NOT_RATIFIED"  # a bad setting never becomes a promise
    finally:
        w.close()


def record_pass(co: Company, server: dict[str, str]) -> None:
    app_id = co.http.get("/v1/verify-api/whoami", headers=server).json()["application_id"]

    async def go() -> None:
        async with co.tx() as c:
            r = await certification_service.record_run(c, co.o.tenant, app_id, str(uuid.uuid4()), perfect())
            assert r["passed"] is True

    asyncio.run(go())


def would_block() -> float:
    return float(observe.GO_LIVE.values.get(("suite_would_block",), 0))


def test_the_suite_gate_counts_in_shadow_refuses_when_enforced_and_lets_a_passing_company_through(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # shadow is the default: the request goes ahead and the platform counts what an enforced gate would have refused
    monkeypatch.delenv("ZID_VERIFY_GO_LIVE_SUITE_GATE", raising=False)
    co = Company(rt_url)
    try:
        co.ready()
        assert co.http.get("/v1/verify-api/go-live", headers=co.h).json()["suite_gate"] == "shadow"
        before = would_block()
        ok = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD)
        assert ok.status_code == 201, ok.text
        assert would_block() == before + 1
    finally:
        co.close()

    monkeypatch.setenv("ZID_VERIFY_GO_LIVE_SUITE_GATE", "off")
    off = Company(rt_url)
    try:
        off.ready()
        before = would_block()
        assert off.http.post("/v1/verify-api/go-live-requests", headers=off.h, json=GOOD).status_code == 201 and would_block() == before  # off counts nothing
    finally:
        off.close()

    monkeypatch.setenv("ZID_VERIFY_GO_LIVE_SUITE_GATE", "enforce")
    hard = Company(rt_url)
    try:
        server = hard.ready()
        assert hard.http.get("/v1/verify-api/go-live", headers=hard.h).json()["suite_gate"] == "enforce"
        refused = hard.http.post("/v1/verify-api/go-live-requests", headers=hard.h, json=GOOD)
        assert refused.status_code == 409 and code(refused) == "GO_LIVE_SUITE_NOT_PASSED" and "Run the sandbox test scenarios first" in refused.json()["error"]["message"]
        assert hard.http.get("/v1/verify-api/go-live", headers=hard.h).json()["request"] is None  # nothing was requested
        record_pass(hard, server)
        passed = hard.http.post("/v1/verify-api/go-live-requests", headers=hard.h, json=GOOD)
        assert passed.status_code == 201, passed.text  # a company that passed the scenarios goes through
    finally:
        hard.close()

    monkeypatch.setenv("ZID_VERIFY_GO_LIVE_SUITE_GATE", "banana")
    monkeypatch.setenv("ZID_VERIFY_GO_LIVE_SUITE_GATE", "banana")
    from zid_platform.modules.verify_api.application import go_live_service

    assert go_live_service.suite_gate_mode() == "shadow"  # an unreadable setting falls back to the safe default, never to enforce or off
