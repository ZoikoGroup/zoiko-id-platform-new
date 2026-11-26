"""The developer MCP server, its registry and the agent gateway's protected-resource metadata over real HTTP (ZID-CP-WF10 v2.0, WF-10.11; CR-173)."""

from __future__ import annotations

import json
import uuid

from test_verify_api_sessions_pg import World


def rpc(w: World, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    r = w.http.post("/v1/verify-api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()  # type: ignore[no-any-return]


def test_the_developer_mcp_answers_documentation_questions_without_a_sign_in_and_acts_for_no_company(rt_url: str) -> None:
    w = World(rt_url)
    try:
        assert rpc(w, "initialize")["result"]["protocolVersion"]  # type: ignore[index]
        tools = [t["name"] for t in rpc(w, "tools/list")["result"]["tools"]]  # type: ignore[index]
        assert tools == ["lookup_error", "list_api_versions", "list_sdks", "list_test_personas", "get_test_plan"]
        err = json.loads(rpc(w, "tools/call", {"name": "lookup_error", "arguments": {"code": "idempotency_conflict"}})["result"]["content"][0]["text"])  # type: ignore[index]
        assert err["code"] == "IDEMPOTENCY_CONFLICT" and err["http_status"] == 409 and err["retryable"] is False
        vers = json.loads(rpc(w, "tools/call", {"name": "list_api_versions", "arguments": {}})["result"]["content"][0]["text"])  # type: ignore[index]
        assert vers["releases"][0]["version"] == "1.0"
        sdks = json.loads(rpc(w, "tools/call", {"name": "list_sdks", "arguments": {}})["result"]["content"][0]["text"])  # type: ignore[index]
        assert sdks["ga_count"] == 0
        plan = json.loads(rpc(w, "tools/call", {"name": "get_test_plan", "arguments": {"persona": "duplicate_webhook", "seed": "3"}})["result"]["content"][0]["text"])  # type: ignore[index]
        assert len(plan["deliveries"]) == 6 and plan["marker"] == "TEST / SYNTHETIC"
        # nothing that acts for a company can be called, and nothing is created
        refused = rpc(w, "tools/call", {"name": "create_verification_session", "arguments": {"customer_reference": "x"}})
        assert refused["error"]["message"] == "That tool is not available."  # type: ignore[index]
        assert w.http.post("/v1/verify-api/mcp", content=b"{not json", headers={"content-type": "application/json"}).json()["error"]["code"] == -32700
        assert w.http.post("/v1/verify-api/mcp", content=b"[1,2]", headers={"content-type": "application/json"}).json()["error"]["code"] == -32600
        assert w.http.post("/v1/verify-api/mcp", content=b"x" * 9000).status_code == 413
        assert w.http.post("/v1/verify-api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
        reg = w.http.get("/v1/verify-api/mcp/registry").json()
        assert [d["name"] for d in reg["disabled"]][:1] == ["create_verification_session"] and len(reg["enabled"]) == 5
    finally:
        w.close()


def test_the_agent_gateway_publishes_its_protected_resource_metadata_and_the_catalog_routes_are_public(rt_url: str) -> None:
    w = World(rt_url)
    try:
        r = w.http.get("/.well-known/oauth-protected-resource/v1/machine/mcp")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
        doc = r.json()
        assert doc["resource"].endswith("/v1/machine/mcp") and doc["bearer_methods_supported"] == ["header"] and isinstance(doc["authorization_servers"], list)
        assert "A token alone grants nothing" in doc["notes"]
        # the agent gateway itself still refuses an unauthenticated call
        assert w.http.post("/v1/machine/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code in (401, 403)
        # the public catalog routes
        assert {e["code"] for e in w.http.get("/v1/verify-api/errors").json()["errors"]} >= {"RATE_LIMITED", "AUTH_SCOPE_DENIED"}
        assert w.http.get("/v1/verify-api/sdks").json()["statement"] == "No package is generally available."
        assert w.http.get("/v1/verify-api/credential-policy").json()["access_token_lifetime_seconds"] == 900
        v = w.http.get("/v1/verify-api/versions").json()["releases"][0]
        snap = w.http.get(f"/v1/verify-api/versions/{v['version']}/openapi.json")
        assert snap.status_code == 200 and snap.headers["etag"] == '"' + v["digest"] + '"' and "paths" in snap.json()
        assert w.http.get("/v1/verify-api/versions/9.9/openapi.json").status_code == 404
        assert w.http.get("/v1/verify-api/fixtures/clear_pass/plan?seed=4").json()["persona"] == "clear_pass"
        assert w.http.get("/v1/verify-api/fixtures/ghost/plan").status_code == 404 and w.http.get("/v1/verify-api/fixtures/clear_pass/plan?seed=bad seed!").status_code == 422
        body = {"observations": [{"persona": "clear_pass", "seed": "1", "plan_digest": "sha-256:" + "0" * 64, "final_outcome_seen": "VERIFIED"}]}
        rep = w.http.post("/v1/verify-api/fixtures/report", json=body).json()
        assert rep["passed"] is False and rep["is_certification"] is False
        _ = uuid
    finally:
        w.close()
