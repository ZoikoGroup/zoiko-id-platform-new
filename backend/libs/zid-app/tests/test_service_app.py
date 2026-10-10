"""Service-shell behavior every deployable unit inherits."""

from fastapi.testclient import TestClient
from zid_app import ServiceSettings, ServiceSpec, create_service_app
from zid_errors import ZidError

SPEC = ServiceSpec("test-unit", "runtime", ["alpha"], ["core"])


def make_client(**overrides: object) -> TestClient:
    settings = ServiceSettings(service_name="test-unit", cell_id="test-1", **overrides)  # type: ignore[arg-type]
    app = create_service_app(SPEC, settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail that must not leak")

    @app.get("/denied")
    async def denied() -> None:
        raise ZidError("AUTHORITY_STALE", "Fresh authority state is required for this action.",
                       http_status=503, retryable=True, retry_after_ms=250)

    return TestClient(app, raise_server_exceptions=False)


def test_live_returns_success_envelope_and_ids() -> None:
    with make_client() as client:
        r = client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"] == {"status": "live"}
    assert body["request_id"].startswith("req_") and r.headers["X-Request-Id"] == body["request_id"]
    assert body["meta"]["service"] == "test-unit" and body["meta"]["region"] == "test-1"


def test_correlation_id_is_propagated() -> None:
    with make_client() as client:
        r = client.get("/health/live", headers={"X-Correlation-Id": "cor_abc"})
    assert r.json()["correlation_id"] == "cor_abc" and r.headers["X-Correlation-Id"] == "cor_abc"


def test_ready_fails_closed_without_database() -> None:
    with make_client() as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    data = r.json()["data"]
    assert data["status"] == "not_ready" and data["checks"][0]["ok"] is False


def test_unknown_route_uses_error_envelope() -> None:
    with make_client() as client:
        r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["success"] is False and r.json()["error"]["code"] == "NOT_FOUND"


def test_internal_error_does_not_leak_details() -> None:
    with make_client() as client:
        r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text


def test_domain_error_is_retryable_with_retry_after() -> None:
    with make_client() as client:
        r = client.get("/denied")
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    err = r.json()["error"]
    assert err["code"] == "AUTHORITY_STALE" and err["retryable"] is True


def test_info_reports_modules_and_schemas() -> None:
    with make_client() as client:
        data = client.get("/").json()["data"]
    assert data["modules"] == ["alpha"] and data["owned_schemas"] == ["core"]
