"""Official-register connectors (CR-087) through the operator API and the worker pass, against the sandbox simulator."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from operator_fixture import ISS
from sqlalchemy import text
from test_regulatory_releases_api_pg import _app, _grant, _people
from zid_platform.modules.verification.application import register_refresh as rr


def test_a_refresh_asks_the_register_preserves_its_answer_and_the_worker_keeps_it_fresh(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    entity = f"sim-{uuid.uuid4().hex[:8]}"
    scope = {"entity": entity, "scheme": "UK_DIATF", "country": "GB", "service_code": "RTR"}
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who["recorder"]), **kw)

        assert call("GET", "/v1/regulatory/register-sources").status_code == 403
        _grant(api, o, who, "recorder", "regulatory.attestation.record")

        sources = {
            s["source_id"]: s
            for s in call("GET", "/v1/regulatory/register-sources").json()["items"]
        }
        assert (
            sources["simulator"]["usable"] is True
        )  # a test environment: the sandbox stand-in may be used

        r = call(
            "POST", "/v1/regulatory/attestations:refresh", json=scope | {"source_id": "simulator"}
        )
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["listed"] is True and got["outcome"] == "CERTIFIED", got

        # a provider the register does not list: nothing recorded, so it is not certified
        missing = scope | {"entity": f"acme-{uuid.uuid4().hex[:6]}", "source_id": "simulator"}
        r = call("POST", "/v1/regulatory/attestations:refresh", json=missing)
        assert (
            r.status_code == 201 and r.json()["listed"] is False and r.json()["outcome"] == "HOLD"
        )

        r = call(
            "POST", "/v1/regulatory/attestations:refresh", json=scope | {"source_id": "nowhere"}
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "SOURCE_UNKNOWN"

    async def checks() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            body = (
                await c.execute(
                    text("SELECT body FROM verify.register_response WHERE proof_hash = :h"),
                    {"h": got["proof_hash"]},
                )
            ).scalar_one()
            assert (
                hashlib.sha256(bytes(body)).hexdigest() == got["proof_hash"]
            )  # the proof points at the exact bytes

            # the worker asks again for a connector-recorded scope once its answer is old enough (here: at once)
            counts = await rr.refresh_due(
                c, environment="test", now=datetime.now(UTC), refresh_after=timedelta(0), limit=500
            )
            assert counts["refreshed"] >= 1
            n = (
                await c.execute(
                    text("SELECT count(*) FROM verify.certification_attestation WHERE entity = :e"),
                    {"e": entity},
                )
            ).scalar_one()
            assert n == 2

    asyncio.run(checks())
