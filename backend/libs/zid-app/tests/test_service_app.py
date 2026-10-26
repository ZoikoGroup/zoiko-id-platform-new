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
        raise ZidError(
            "AUTHORITY_STALE",
            "Fresh authority state is required for this action.",
            http_status=503,
            retryable=True,
            retry_after_ms=250,
        )

    @app.get("/budget")
    async def budget() -> dict[str, float | None]:
        from zid_app.resilience import budget_timeout, remaining

        left = remaining()
        return {"left": left, "timeout": budget_timeout(5.0) if left is None or left > 0 else 0.0}

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


def test_info_banner_is_minimal_outside_local_and_test() -> None:
    """The unauthenticated banner never lists modules, schemas, cell or environment in a deployed cell."""
    with make_client(environment="production") as client:
        data = client.get("/").json()["data"]
    assert set(data) == {"service", "plane", "version"}


def test_row_level_security_refusals_are_counted() -> None:
    from zid_app import _rls_refusal

    inner = RuntimeError('new row violates row-level security policy for table "tenant_domain"')
    outer = ValueError("database error")
    outer.__cause__ = inner
    assert _rls_refusal(outer)
    assert not _rls_refusal(ValueError("unique constraint"))


def test_sensitive_requests_never_execute_under_tls_early_data() -> None:
    """CRYP-18: a request the TLS terminator marks `Early-Data: 1` (0-RTT, replayable) is refused
    with 425 before its handler runs when it changes state or touches recovery/key/authz paths.

    Proves the application-side guard (RFC 8470); that the edge actually disables 0-RTT or sets
    the header is deployment configuration and is not exercised here."""
    ran: list[str] = []
    settings = ServiceSettings(service_name="test-unit", cell_id="test-1")
    app = create_service_app(SPEC, settings)

    @app.post("/things")
    async def create_thing() -> dict[str, str]:
        ran.append("POST /things")
        return {"ok": "yes"}

    @app.post("/authn/recovery")
    async def recovery() -> dict[str, str]:
        ran.append("POST /authn/recovery")
        return {"ok": "yes"}

    @app.get("/keys/export")
    async def key_admin() -> dict[str, str]:
        ran.append("GET /keys/export")
        return {"ok": "yes"}

    early = {"Early-Data": "1"}
    with TestClient(app) as client:
        for method, path in (
            ("POST", "/things"),
            ("POST", "/authn/recovery"),
            ("GET", "/keys/export"),
        ):
            r = client.request(method, path, headers=early)
            assert r.status_code == 425, (method, path)
            assert r.json()["error"]["code"] == "HTTP_425"
        assert ran == []  # none of the handlers ran
        # the same requests after the handshake (no early-data marker) execute normally
        assert client.post("/things").status_code == 200
        assert client.post("/authn/recovery").status_code == 200
        assert ran == ["POST /things", "POST /authn/recovery"]
        # a plain read of a non-sensitive resource may use early data
        assert client.get("/health/live", headers=early).status_code == 200


def test_the_callers_deadline_header_becomes_the_requests_budget_and_ends_with_it() -> None:
    """SVC-17: a downstream call made while serving the request gets at most what the caller said it had."""
    with make_client() as client:
        free = client.get("/budget").json()
        assert free == {"left": None, "timeout": 5.0}  # no caller deadline: the call's own cap
        limited = client.get("/budget", headers={"X-Zid-Deadline-Ms": "1500"}).json()
        assert 0 < limited["left"] <= 1.5 and limited["timeout"] <= limited["left"] + 0.001
        assert client.get("/budget").json() == {
            "left": None,
            "timeout": 5.0,
        }  # the budget did not leak across requests


def test_a_spent_deadline_is_a_504_in_the_canonical_envelope() -> None:
    """SVC-17: when the budget is gone an outbound call is refused and the caller gets 504, not a hang or a 500."""
    from zid_app.resilience import budget_timeout

    settings = ServiceSettings(service_name="test-unit", cell_id="test-1")
    app = create_service_app(SPEC, settings)

    @app.get("/slow")
    async def slow() -> None:
        import asyncio

        await asyncio.sleep(0.06)  # the work itself uses up the 50 ms the caller gave
        budget_timeout(5.0)

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/slow", headers={"X-Zid-Deadline-Ms": "50"})
    assert r.status_code == 504 and r.json()["error"]["code"] == "DEADLINE_EXCEEDED"
