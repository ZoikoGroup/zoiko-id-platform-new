"""The API-only agent client (tools/scripts/agents_api_check.py) against the real app and PostgreSQL with RLS.

The client uses nothing but HTTP routes. Here its transport is swapped for the app's own ASGI test client so no port or Docker
container is needed for the app; everything else (routes, step-up, authority checks, row-level security) is the real thing.
Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from access_readiness_fixture import World
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from gateway_fixture import AUD, ISS
from test_daara_pg import Daara, _enforce  # noqa: F401 - the fixture is applied by name below
from zid_db import tenant_transaction

CLIENT = Path(__file__).resolve().parents[4] / "tools" / "scripts" / "agents_api_check.py"


def load_client() -> Any:
    spec = importlib.util.spec_from_file_location("agents_api_check", CLIENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # a dataclass with postponed annotations looks its own module up
    spec.loader.exec_module(mod)
    return mod


def transport(http: TestClient, tokens: dict[str, dict[str, str]]) -> Any:
    def call(
        method: str,
        path: str,
        who: str = "admin",
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        r = http.request(method, path, headers={**tokens[who], **(headers or {})}, json=body)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    return call


def test_access_readiness_agent_works_end_to_end_over_the_api_only(
    rt_url: str, owner_url: str
) -> None:
    client = load_client()
    w = World(rt_url, owner_url).start()
    tokens = {
        "admin": w.bearer(w.admin),
        "approver": w.bearer(w.approver),
        "reviewer": w.bearer(w.reviewer),
    }
    report = client.Report()
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        client.check_access_readiness(transport(http, tokens), w.daara, report)
    failed = [(n, d) for n, ok, d in report.steps if not ok]
    assert not failed, failed
    assert len(report.steps) >= 11  # every step ran: nothing was skipped by an early return


def test_the_client_says_so_when_the_feature_is_off(rt_url: str, owner_url: str) -> None:
    client = load_client()
    w = World(rt_url, owner_url).start()
    tokens = {
        "admin": w.bearer(w.admin),
        "approver": w.bearer(w.approver),
        "reviewer": w.bearer(w.reviewer),
    }
    report = client.Report()
    with TestClient(w.app(), base_url="https://id.zoiko.test") as http:  # default OFF
        client.check_access_readiness(transport(http, tokens), w.daara, report)
    assert report.ok is False
    assert report.steps[0][0].startswith("feature is on") and "404" in report.steps[0][2]


@pytest.mark.usefixtures("_enforce")
def test_daara_agent_works_over_the_api_only(rt_url: str, owner_url: str) -> None:
    client = load_client()
    dd = Daara(rt_url, owner_url)
    dd.seed()
    dd.go_live()

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(dd.tenant)) as c:
            await make_admin(
                c, dd.tenant, dd.w.human,
                ["agent.daara.read", "agent.daara.manage", "agent.daara.run", "agent.review.read", "agent.review.decide", "agent.audit.read"],
                ["agent:*"], release=False,
            )  # fmt: skip

    dd.w.run(grant())
    tokens = {
        "admin": dd.w.bearer(dd.w.human, scope=frozenset({"admin"})),
        "reviewer": dd.w.bearer(dd.w.human, scope=frozenset({"admin"})),
    }
    report = client.Report()
    with TestClient(dd.w.app, base_url=ISS) as http:
        client.check_daara(transport(http, tokens), dd.agent, report)
    failed = [(n, d) for n, ok, d in report.steps if not ok]
    assert not failed, failed
    assert any(n == "start a governed run" for n, _, _ in report.steps)


ACTIONS = [
    "machine.agent.read",
    "machine.agent.manage",
    "machine.agent.activate",
    "machine.blueprint.manage",
    "machine.build.register",
    "machine.build.manage",
    "machine.tool.manage",
    "authority.grant.read",
]


def test_an_api_agent_goes_from_nothing_to_signed_in_over_the_api_only(
    rt_url: str, owner_url: str
) -> None:
    """No container image, no attestor, no database access by the client: register, approve (two people), bind, activate, sign in."""
    from gateway_fixture import World as GatewayWorld

    client = load_client()
    w = GatewayWorld(rt_url, owner_url).start()

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            await make_admin(
                c,
                w.tenant,
                [w.human, w.approver],
                ACTIONS,
                ["agent:*", "build:*", "blueprint:*", "tool:*", "authority_grant:*"],
                release=False,
                delegable=True,
            )  # the fixture already released the tenant policy

    w.run(grant())
    tokens = {
        "admin": w.bearer(w.human, scope=frozenset({"admin"})),
        "approver": w.bearer(w.approver, scope=frozenset({"admin"})),
        "anonymous": {},
    }

    report = client.Report()
    with TestClient(w.app, base_url=ISS) as http:
        agent_id = client.check_api_agent_lifecycle(
            transport(http, tokens),
            report,
            operator=w.operator,
            sponsor=w.sponsor,
            publisher=w.publisher,
            organization=w.org,
            issuer=ISS,
            tenant_id=w.tenant,
            tool_server=w.server,
            api_base=AUD,
            grantor=w.human,
        )
    failed = [(n, d) for n, ok, d in report.steps if not ok]
    assert not failed, failed
    assert any(
        n.startswith("the agent's governed tool call is granted") for n, _, _ in report.steps
    )
    assert agent_id and len(report.steps) == 22
    status = w.owner(
        "SELECT status FROM agent.agent_profile WHERE tenant_id = :t AND agent_principal_id = :a",
        a=__import__("uuid").UUID(agent_id),
    )[0][0]
    assert str(status) == "ACTIVE"
    # nothing container-shaped was involved: no workload binding exists for this tenant
    assert w.owner("SELECT count(*) FROM agent.workload_binding WHERE tenant_id = :t")[0][0] == 0


def test_the_cli_works_over_real_http_against_a_running_server(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command an operator runs: a real socket, the standard-library transport, bearer tokens from the environment, exit code 0."""
    import socket
    import threading
    import time

    import uvicorn
    from gateway_fixture import World as GatewayWorld

    client = load_client()
    w = GatewayWorld(rt_url, owner_url).start()

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            await make_admin(
                c, w.tenant, [w.human, w.approver], ACTIONS,
                ["agent:*", "build:*", "blueprint:*", "tool:*", "authority_grant:*"], release=False, delegable=True,
            )  # fmt: skip

    w.run(grant())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(w.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started, "the test server did not start"
        monkeypatch.setenv(
            "ZID_TOKEN_ADMIN",
            w.bearer(w.human, scope=frozenset({"admin"}))["Authorization"].split()[1],
        )
        monkeypatch.setenv(
            "ZID_TOKEN_APPROVER",
            w.bearer(w.approver, scope=frozenset({"admin"}))["Authorization"].split()[1],
        )
        code = client.main(
            [
                "--base", f"http://127.0.0.1:{port}", "--create-api-agent",
                "--operator", w.operator, "--sponsor", w.sponsor, "--publisher", w.publisher,
                "--organization", w.org, "--issuer", ISS, "--api-base", AUD,
                "--tenant", w.tenant, "--tool-server", w.server, "--grantor", w.human,
            ]
        )  # fmt: skip
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    out = capsys.readouterr().out
    # the person delegates the agent its authority over the API, so the tool call is granted
    assert code == 0, out
    assert "FAIL" not in out and "steps passed" in out
    assert "the agent's governed tool call is granted" in out


@pytest.mark.usefixtures("_enforce")
def test_daara_is_released_and_run_over_the_api_only(rt_url: str, owner_url: str) -> None:
    """From an unpublished agent: request, the Security and Authority people sign the exact hash, apply, then a governed run."""
    from test_agent_governance_pg import REGION

    client = load_client()
    dd = Daara(rt_url, owner_url)
    dd.seed()
    dd.setup()
    dd.baseline()
    sec, auth = dd.p("sec"), dd.p("auth")
    dd.w.sessions[sec] = dd.w.run(dd.w._session(sec))
    dd.w.sessions[auth] = dd.w.run(dd.w._session(auth))

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(dd.tenant)) as c:
            await make_admin(
                c, dd.tenant, dd.w.human,
                ["agent.daara.read", "agent.daara.manage", "agent.daara.run", "agent.review.read",
                 "agent.approval.apply", "agent.governance.read"],
                ["agent:*"], release=False,
            )  # fmt: skip
            await make_admin(
                c, dd.tenant, [sec, auth], ["agent.approval.sign", "agent.governance.read"],
                ["agent:*"], release=False,
            )  # fmt: skip

    dd.w.run(grant())
    scope = frozenset({"admin"})
    tokens = {
        "admin": dd.w.bearer(dd.w.human, scope=scope),
        "reviewer": dd.w.bearer(dd.w.human, scope=scope),
        "security": dd.w.bearer(sec, scope=scope),
        "authority": dd.w.bearer(auth, scope=scope),
    }
    report = client.Report()
    with TestClient(dd.w.app, base_url=ISS) as http:
        client.check_daara(transport(http, tokens), dd.agent, report, release=True, region=REGION)
    failed = [(n, d) for n, ok, d in report.steps if not ok]
    assert not failed, failed
    names = [n for n, _, _ in report.steps]
    assert "start a governed run" in names, names  # it was released, so it ran
    assert any(n.startswith("the Security person signs") for n in names)
    assert any(n.startswith("the Authority person signs") for n in names)
