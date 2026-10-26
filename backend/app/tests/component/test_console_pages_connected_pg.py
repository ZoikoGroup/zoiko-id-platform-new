"""Every read the company console makes answers for the company's own owner (no page is 'not connected').

A company that has just registered has no signed order, no invoice and no usage yet. Each page must still load with
an honest empty or default answer, never a 403 for a missing permission, a 404 for a missing account or a 500.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.modules.onboarding.domain.gates import TENANT_ADMIN_ACTIONS
from zid_platform.settings import Settings

RESOURCES = [
    "billable:*",
    "tenant:*",
    "alert:*",
    "settings:*",
    "usage:*",
    "contact:*",
    "webhook_endpoint:*",
    "approval_request:*",
]

# What the Company, Account and developer pages request on load, in the order the console shows them.
READS = [
    "/v1/billing/state",
    "/v1/billing/orders",
    "/v1/billing/preview",
    "/v1/budgets",
    "/v1/invoices",
    "/v1/usage/billable",
    "/v1/usage",
    "/v1/usage/limits",
    "/v1/settings",
    "/v1/settings/cryptography",
    "/v1/settings/notification-kinds",
    "/v1/alerts",
    "/v1/alerts/summary",
    "/v1/tenant/contacts",
    "/v1/webhooks/endpoints",
    "/v1/webhooks/event-types",
    "/v1/webhooks/deliveries",
]


@pytest.fixture(scope="module")
def console(rt_url: str):  # type: ignore[no-untyped-def]
    o = onboard(rt_url, extra_resources=RESOURCES)
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
        yield o, api


def test_the_owner_grant_is_what_these_pages_need() -> None:
    assert {
        "commercial.read",
        "configuration.read",
        "notification.alert.read",
        "webhook.endpoint.read",
    } <= set(TENANT_ADMIN_ACTIONS)


@pytest.mark.parametrize("path", READS)
def test_the_owner_can_load_each_console_read(console, path: str) -> None:  # type: ignore[no-untyped-def]
    o, api = console
    r = api.get(path, headers=o.bearer(o.owner))
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"


def test_the_subscription_page_has_a_plan_before_any_order(console) -> None:  # type: ignore[no-untyped-def]
    o, api = console
    r = api.get(f"/v1/tenants/{o.tenant}/entitlements", headers=o.bearer(o.owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] and body["source"] == "DEFAULT"  # the default plan, honestly labelled
    assert body["limits"] and body["capabilities"]
