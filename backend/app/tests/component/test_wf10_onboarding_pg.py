"""The first-verification checklist and the partner webhook confirmation, against PostgreSQL (ZID-CP-WF10 v2.0, WF-10.01; CR-173).

What these hold: each step is backed by something that really happened; a confirmation must name an event the platform really delivered to this company; one event is confirmed
once; another company event cannot be confirmed; the confirmation is append-only; and the checklist is not ready until the partner has confirmed.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import onboarding_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


async def deliver(owner_url: str, tenant: str, *, status: str = "DELIVERED") -> str:
    """A webhook the platform really sent to one of the company endpoints (written as the platform would, with its own role)."""
    endpoint, event = uuid.uuid4(), uuid.uuid4()
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO events.webhook_endpoint (endpoint_id, tenant_id, url, event_types, status, signing_key_id, created_by) VALUES (:e, :t, 'https://hooks.example.com/zoiko', ARRAY['verification.api.session_decided'], 'ACTIVE', 'k1', :u)"
            ),
            {"e": endpoint, "t": uuid.UUID(tenant), "u": uuid.uuid4()},
        )
        await c.execute(
            text(
                "INSERT INTO events.webhook_delivery (delivery_id, tenant_id, endpoint_id, event_id, event_type, payload, payload_digest, status, delivered_at) VALUES (:d, :t, :e, :ev, 'verification.api.session_decided', '{}', 'x', :s, now())"
            ),
            {"d": uuid.uuid4(), "t": uuid.UUID(tenant), "e": endpoint, "ev": event, "s": status},
        )
    return str(event)


def test_each_step_is_backed_by_something_that_happened_and_only_a_confirmed_delivered_webhook_makes_it_ready(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        app_id = w.http.get("/v1/verify-api/whoami", headers=h).json()["application_id"]
        assert w.create(h).status_code == 201

        first = run(rt_url, w.tenant, lambda c: svc.checklist(c, app_id))
        steps = {s["step"]: s["done"] for s in first["steps"]}
        assert (
            steps["CREDENTIAL"]
            and steps["TOKEN"]
            and steps["SESSION"]
            and not steps["VERIFIED_WEBHOOK"]
        )
        assert (
            first["ready"] is False
            and first["seconds_to_first_verified_webhook"] is None
            and first["environment"] == "sandbox"
        )

        url = "/v1/verify-api/onboarding/webhook-confirmations"
        # a webhook the platform did not deliver cannot be confirmed
        bad = w.http.post(url, headers=h, json={"event_id": str(uuid.uuid4())})
        assert bad.status_code == 404 and bad.json()["error"]["code"] == "NOT_FOUND"
        assert w.http.post(url, headers=h, json={"event_id": "not-an-event-id"}).status_code == 404
        assert (
            w.http.post(
                url, headers=h, json={"event_id": str(uuid.uuid4()), "extra": 1}
            ).status_code
            == 422
        )

        # a delivery that failed is not a delivery
        failed_event = asyncio.run(deliver(owner_url, w.tenant, status="FAILED"))
        assert w.http.post(url, headers=h, json={"event_id": failed_event}).status_code == 404

        real = asyncio.run(deliver(owner_url, w.tenant))
        ok = w.http.post(url, headers=h, json={"event_id": real})
        assert ok.status_code == 201, ok.text
        done = ok.json()
        assert done["complete"] is True and done["ready"] is True and done["next_step"] is None
        assert (
            done["seconds_to_first_verified_webhook"] is not None
            and done["seconds_to_first_verified_webhook"] >= 0
        )
        assert next(s for s in done["steps"] if s["step"] == "VERIFIED_WEBHOOK")["evidence"] == real

        again = w.http.post(url, headers=h, json={"event_id": real})
        assert (
            again.status_code == 422 and "already confirmed" in again.json()["error"]["message"]
        )  # one event is confirmed once

        # another company event cannot be confirmed by this one
        foreign = asyncio.run(deliver(owner_url, uid()))
        assert w.http.post(url, headers=h, json={"event_id": foreign}).status_code == 404

        async def edit() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE verify.api_webhook_confirmation SET confirmed_at = now() WHERE application_id = :a"
                    ),
                    {"a": uuid.UUID(app_id)},
                )

        with pytest.raises(Exception, match="append-only"):
            asyncio.run(edit())
    finally:
        w.close()


def test_a_token_without_the_webhook_scope_cannot_confirm_and_an_unknown_application_has_no_checklist(
    rt_url: str,
) -> None:
    w = World(rt_url)
    try:
        narrow = w.server(scopes=["verifications:create", "verifications:read"])
        r = w.http.post(
            "/v1/verify-api/onboarding/webhook-confirmations",
            headers=narrow,
            json={"event_id": str(uuid.uuid4())},
        )
        assert r.status_code in (400, 403), r.text

        async def missing(c: Any) -> None:
            with pytest.raises(PartnerError):
                await svc.checklist(c, str(uuid.uuid4()))

        run(rt_url, w.tenant, missing)
    finally:
        w.close()


def test_the_cohort_counts_unfinished_installs_and_takes_the_median_of_finished_ones_only(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        assert w.create(h).status_code == 201
        before = run(rt_url, w.tenant, svc.cohort)
        assert (
            before["installs"] == 1
            and before["complete"] == 0
            and before["incomplete"] == 1
            and before["median_seconds"] is None
        )  # an unfinished install has no time, not zero

        real = asyncio.run(deliver(owner_url, w.tenant))
        assert (
            w.http.post(
                "/v1/verify-api/onboarding/webhook-confirmations",
                headers=h,
                json={"event_id": real},
            ).status_code
            == 201
        )
        after = run(rt_url, w.tenant, svc.cohort)
        assert (
            after["installs"] == 1
            and after["complete"] == 1
            and after["incomplete"] == 0
            and after["median_seconds"] >= 0
        )

        other = World(rt_url)  # another company's installs are not in this company's cohort
        try:
            other.server()
            assert run(rt_url, w.tenant, svc.cohort)["installs"] == 1
        finally:
            other.close()
    finally:
        w.close()
