"""A licensed screening provider's callback over HTTP (WF06 section 10; ENG-15; QA-037, QA-038), against PostgreSQL.

A correctly signed fresh event for this tenant is accepted once; a replay is recognised and changes nothing; a wrong signature, a stale clock, a wrong tenant, an unknown provider and an oversized body are
refused and recorded, and a refusal never uses up an event id."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.screening.domain import provider_controls as pc
from zid_platform.settings import Settings

SECRET = b"provider-signing-secret"


def _rows(owner_url: str, tenant: str) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT provider_id, event_id, accepted, outcome FROM verify.screening_provider_event WHERE tenant_id = CAST(:t AS uuid) ORDER BY received_at, row_id"
                    ),
                    {"t": tenant},
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(run())


def test_a_provider_callback_is_accepted_once_and_every_abuse_is_refused_and_recorded(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    tenant = o.tenant
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    app.state.screening_provider_secrets = {"vendor-a": SECRET}
    url = f"/v1/integrations/screening/{tenant}.vendor-a/events"

    def send(
        event_id: str,
        *,
        body: dict[str, Any] | None = None,
        at: datetime | None = None,
        secret: bytes = SECRET,
        sign_body: bytes | None = None,
        provider: str = "vendor-a",
        path: str = url,
        raw: bytes | None = None,
    ) -> Any:
        issued = at or datetime.now(UTC)
        doc = (
            body
            if body is not None
            else {
                "provider_id": provider,
                "tenant_id": tenant,
                "case_ref": str(uuid.uuid4()),
                "kind": "RESULT_UPDATED",
            }
        )
        payload = raw if raw is not None else json.dumps(doc).encode()
        signature = pc.sign(
            secret,
            doc.get("provider_id", provider),
            doc.get("tenant_id", tenant),
            event_id,
            issued,
            sign_body if sign_body is not None else payload,
        )
        headers = {
            "x-zoiko-event-id": event_id,
            "x-zoiko-timestamp": issued.isoformat(),
            "x-zoiko-signature": signature,
            "content-type": "application/json",
        }
        return http.post(path, content=payload, headers=headers)

    with TestClient(app, base_url=ISS) as http:
        ok = send("evt-0001")
        assert ok.status_code == 202 and ok.json()["outcome"] == "ACCEPTED", ok.text
        replay = send("evt-0001")
        assert (
            replay.status_code == 200 and replay.json()["outcome"] == "DUPLICATE"
        )  # QA-038: recognised, answered, nothing changes

        assert (
            send("evt-0002", secret=b"another-key").status_code == 401
        )  # QA-037: a wrong signature
        assert (
            send("evt-0003", sign_body=b"{}").json()["error"] == "SIGNATURE_INVALID"
        )  # signed over different bytes
        assert (
            send("evt-0004", at=datetime.now(UTC) - timedelta(minutes=30)).status_code == 400
        )  # a stale clock
        assert (
            send("evt-0005", at=datetime.now(UTC) + timedelta(minutes=30)).json()["error"]
            == "TIMESTAMP_OUTSIDE_WINDOW"
        )
        other = {"provider_id": "vendor-a", "tenant_id": str(uuid.uuid4()), "case_ref": "x"}
        wrong = send("evt-0006", body=other)
        assert (
            wrong.status_code == 401 and wrong.json()["error"] == "TENANT_MISMATCH"
        )  # a body naming another tenant
        named = {"provider_id": "vendor-b", "tenant_id": tenant}
        assert (
            send("evt-0007", body=named).json()["error"] == "PROVIDER_NOT_RECOGNISED"
        )  # a body naming another provider
        unknown = send(
            "evt-0008",
            path=f"/v1/integrations/screening/{tenant}.vendor-z/events",
            provider="vendor-z",
        )
        assert (
            unknown.status_code == 401 and unknown.json()["error"] == "PROVIDER_NOT_RECOGNISED"
        )  # no secret on record
        assert send("evt-0009", raw=b"not json").json()["error"] == "BODY_INVALID"
        assert send("evt-0010", raw=b"x" * (70 * 1024)).status_code == 413
        assert (
            http.post(
                "/v1/integrations/screening/not-a-uuid.vendor-a/events", content=b"{}"
            ).status_code
            == 401
        )

        # a refusal never used up an id: the real event with an id an attacker tried is still accepted
        assert send("evt-0002").status_code == 202

    rows = _rows(owner_url, tenant)
    accepted = [r for r in rows if r["accepted"]]
    assert sorted(r["event_id"] for r in accepted) == ["evt-0001", "evt-0002"]
    refused = {r["outcome"] for r in rows if not r["accepted"]}
    assert {
        "SIGNATURE_INVALID",
        "TIMESTAMP_OUTSIDE_WINDOW",
        "TENANT_MISMATCH",
        "PROVIDER_NOT_RECOGNISED",
        "BODY_INVALID",
        "BODY_TOO_LARGE",
    } <= refused
