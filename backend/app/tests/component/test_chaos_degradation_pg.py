"""Chaos: what the platform does when a dependency disappears under a running app (ZID-GAP-001 WP-24; attack catalog rows 5 and 15).

The rule is the plan's own: high-risk enforcement fails closed, and a failure is never an approval. With the database taken away from a running cell the
decision API, a protected admin route and the agent tool gateway must each refuse with a retryable 503 (or an auth refusal), never 200 and never a
grant, and they must recover on their own the moment the database is back, with nothing left half-done.
"""

from __future__ import annotations

import uuid
from typing import Any

from conftest import CELL
from fastapi.testclient import TestClient
from gateway_fixture import World
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy.ext.asyncio import create_async_engine
from zid_platform.main import create_app
from zid_platform.settings import Settings

EVAL = "/access/v1/evaluation"
CALL = "/v1/machine/tool-calls:authorize"
DEAD = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none"  # nothing listens on port 1


def evaluation() -> dict[str, Any]:
    return {
        "subject": {"type": "principal", "id": str(uuid.uuid4())},
        "action": {"name": "application.access"},
        "resource": {"type": "application", "id": "demo"},
    }


def test_the_decision_api_refuses_while_the_database_is_gone_and_recovers(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL,
        ),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS, raise_server_exceptions=False) as http:
        headers = o.bearer(o.owner)
        assert http.post(EVAL, headers=headers, json=evaluation()).status_code == 200

        healthy = app.state.engine
        app.state.engine = create_async_engine(DEAD)  # the database drops away under the running cell
        try:
            for _ in range(3):  # repeated, so a half-broken pool cannot let one through
                down = http.post(EVAL, headers=headers, json=evaluation())
                assert down.status_code in (401, 503), down.text  # a refusal, never an answer
                assert down.json().get("decision") is not True  # and never an allow
                if down.status_code == 503:
                    assert down.json()["error"]["retryable"] is True
        finally:
            app.state.engine = healthy

        back = http.post(EVAL, headers=headers, json=evaluation())
        assert back.status_code == 200, back.text  # recovers by itself, no restart


def test_the_tool_gateway_signs_no_grant_when_the_decision_point_fails(rt_url: str, owner_url: str, monkeypatch: Any) -> None:
    w = World(rt_url, owner_url).start()
    body = {
        "tool_id": "docs.read", "resource_type": "agent", "resource_id": "any",
        "arguments_digest": "sha256:" + "d" * 64,
    }  # fmt: skip
    with TestClient(w.app, base_url=ISS, raise_server_exceptions=False) as http:
        agent = w.bearer(w.child)
        assert http.post(CALL, headers=agent, json=body).status_code == 200

        async def broken(*_a: Any, **_k: Any) -> Any:
            raise ConnectionError("the policy decision point is unreachable")

        monkeypatch.setattr("zid_platform.modules.machine.api.tool_calls.evaluate_access", broken)
        failed = http.post(CALL, headers=agent, json=body)
        assert failed.status_code == 503 and failed.json()["error"]["retryable"] is True
        assert "grant" not in failed.text  # a failed decision is never an allow

    granted = w.owner("SELECT count(*) AS n FROM agent.tool_grant WHERE tenant_id = :t")[0].n
    assert granted == 1  # only the first call, made while the decision point was up, ever got a grant
