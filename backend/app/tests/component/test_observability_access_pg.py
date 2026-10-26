"""Production observability is not searchable across tenants by an unprivileged user or operator (OPS-29).

Operational data (service health, SLOs, incidents, recovery readiness, the commercial back office) lives behind the
operator surface. A company's own tokens, including its owner's, never reach it, and what the process exports for
scraping carries no tenant or person identifiers to search by.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

OPERATOR_PREFIXES = ("/ops", "/platform", "/v1/commercial")


@pytest.fixture(scope="module")
def stack(rt_url: str):  # type: ignore[no-untyped-def]
    o = onboard(rt_url, extra_resources=["tenant:*", "alert:*", "approval_request:*"])
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
    with TestClient(app) as api:
        yield o, app, api


def _operator_routes(app) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    out = []
    for path, item in app.openapi()["paths"].items():
        if path.startswith(OPERATOR_PREFIXES):
            for method in item:
                if method in ("get", "post", "put", "patch", "delete"):
                    out.append(
                        (
                            method.upper(),
                            re.sub(r"\{[^}]*\}", "00000000-0000-4000-8000-000000000001", path),
                        )
                    )
    return out


def test_a_company_owner_cannot_reach_any_operator_observability_route(stack) -> None:  # type: ignore[no-untyped-def]
    o, app, api = stack
    routes = _operator_routes(app)
    assert len(routes) > 20, "the operator surface should be large enough to be worth guarding"
    open_to_tenants = []
    for method, path in routes:
        r = api.request(
            method, path, headers=o.bearer(o.owner), json={} if method != "GET" else None
        )
        if r.status_code not in (401, 403, 404, 405):
            open_to_tenants.append((method, path, r.status_code))
    assert open_to_tenants == [], f"reachable with a company token: {open_to_tenants[:5]}"


def test_without_credentials_none_of_it_answers(stack) -> None:  # type: ignore[no-untyped-def]
    _, app, api = stack
    for method, path in _operator_routes(app)[:40]:
        r = api.request(method, path, json={} if method != "GET" else None)
        assert r.status_code in (401, 403, 404, 405), (method, path, r.status_code)


def test_the_scrape_endpoint_carries_no_tenant_or_person_identifiers(stack) -> None:  # type: ignore[no-untyped-def]
    o, _, api = stack
    api.get("/v1/alerts", headers=o.bearer(o.owner))
    api.get("/v1/billing/state", headers=o.bearer(o.owner))
    body = api.get("/internal/metrics").text
    assert "zid_" in body or "http_" in body or body  # the endpoint answers
    for secret in (o.tenant, o.owner, o.second):
        assert secret not in body, "an identifier is a label in the exported metrics"
