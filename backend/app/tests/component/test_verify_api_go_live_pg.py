"""Opening production for a Verify API partner (CR-153), against the real database and the real HTTP endpoints.

The company asks through its signed-in administrator; its sandbox work is checked automatically; an operator takes it into review and a different operator approves; approval opens PRODUCTION applications
and leaves a record with a digest; an emergency revoke stops them at once. Operator steps run through the service under the operator scope (the operator gate itself is tested with the operator plane).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from conftest import ctx, engine
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, Onboarded, onboard
from sqlalchemy import text
from test_commercial_pg import settings
from test_webhooks_pg import Receiver, public_dns
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.application import go_live_service as svc
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.domain import go_live as g
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure.go_live_store import operator_scope
from zid_platform.modules.webhook.infrastructure.delivery import register, verify

ORIGIN = "https://console.example.com"
ACTIONS = ("verify_api.application.manage", "verify_api.application.read")
GOOD = {
    "markets": ["GB", "US"],
    "methods": ["document_authenticity", "face_match"],
    "purpose_code": "customer_onboarding",
    "volume_band": "1k_10k",
    "use_case": "Verify new customers of our savings app.",
    "terms_version": g.TERMS_VERSION,
    "dpa_version": g.DPA_VERSION,
    "accept_terms": True,
    "accept_dpa": True,
}
ALL = {k: True for k in g.ATTESTATIONS}


def uid() -> str:
    return str(uuid.uuid4())


def operator_tx(rt_url: str) -> Any:
    """A transaction as the operator plane: its own tenant context and the operator scope."""
    return _OperatorTx(rt_url)


class _OperatorTx:
    def __init__(self, rt_url: str) -> None:
        self.rt_url = rt_url

    async def __aenter__(self) -> Any:
        self.eng = engine(self.rt_url)
        eng = await self.eng.__aenter__()
        self.tx = tenant_transaction(eng, ctx(uid()))
        conn = await self.tx.__aenter__()
        self.scope = operator_scope(conn)
        await self.scope.__aenter__()
        return conn

    async def __aexit__(self, *exc: object) -> None:
        await self.scope.__aexit__(*exc)
        await self.tx.__aexit__(*exc)
        await self.eng.__aexit__(*exc)


class Company:
    """An onboarded company with a signed-in owner, a sandbox application and a TestClient for the whole app."""

    def __init__(self, rt_url: str) -> None:
        self.rt_url = rt_url
        self.o: Onboarded = onboard(
            rt_url, extra_actions=ACTIONS, extra_resources=("verify_api_application:*",)
        )
        self.http = TestClient(
            create_app(settings(rt_url, verify_hosted_origin=ORIGIN), keyring=self.o.ring),
            base_url=ISS,
        )
        self.http.__enter__()
        self.h = self.o.bearer(self.o.owner)

    def close(self) -> None:
        self.http.__exit__(None, None, None)

    def tx(self) -> Any:
        return _Tx(self.rt_url, self.o.tenant)

    def sandbox_server(self) -> dict[str, str]:
        async def seed() -> dict[str, Any]:
            async with self.tx() as c:
                a = await service.create_application(
                    c,
                    self.o.tenant,
                    uid(),
                    name="Checkout",
                    environment="SANDBOX",
                    scopes=None,
                    origins=None,
                )
                return {
                    "a": a,
                    "c": await service.issue_credential(
                        c, self.o.tenant, uid(), a["application_id"]
                    ),
                }

        made = asyncio.run(seed())
        r = self.http.post(
            "/v1/verify-api/oauth2/token",
            json={"client_id": made["c"]["client_id"], "client_secret": made["c"]["client_secret"]},
        )
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def run_sandbox(self, server: dict[str, str], outcomes: tuple[str, ...]) -> None:
        for name in outcomes:
            r = self.http.post(
                f"/v1/verify-api/sandbox/fixtures/{name}/sessions",
                headers={**server, "Idempotency-Key": uid()},
                json={},
            )
            assert r.status_code == 201, r.text

    def add_webhook(self) -> None:
        key = Ed25519PrivateKey.generate()
        client = httpx.AsyncClient(transport=httpx.MockTransport(Receiver([]).handler))

        async def go() -> None:
            async with self.tx() as c:
                ep = await register(
                    c, tenant_id=self.o.tenant, url="https://hooks.example.com/verify",
                    event_types=["com.zoiko.id.verification.api.session_decided.v1"], key_id="zid-whk-2026-10", created_by=uid(), resolve=public_dns,
                )  # fmt: skip
                assert await verify(c, ep, client=client, resolve=public_dns, sign=key.sign)

        asyncio.run(go())

    def ready(self) -> dict[str, str]:
        server = self.sandbox_server()
        self.run_sandbox(server, ("approved", "declined"))
        self.add_webhook()
        return server


class _Tx:
    def __init__(self, rt_url: str, tenant: str) -> None:
        self.rt_url, self.tenant = rt_url, tenant

    async def __aenter__(self) -> Any:
        self.eng = engine(self.rt_url)
        eng = await self.eng.__aenter__()
        self.tx = tenant_transaction(eng, ctx(self.tenant))
        return await self.tx.__aenter__()

    async def __aexit__(self, *exc: object) -> None:
        await self.tx.__aexit__(*exc)
        await self.eng.__aexit__(*exc)


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_a_company_cannot_ask_until_its_sandbox_is_certified_then_asks_once(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        s0 = co.http.get("/v1/verify-api/go-live", headers=co.h).json()
        assert (
            [c["met"] for c in s0["certification"]] == [False, False, False]
            and s0["request"] is None
            and s0["grant"] is None
        )
        assert s0["terms_version"] == g.TERMS_VERSION and {
            x["id"] for x in s0["options"]["volume_bands"]
        } == set(g.VOLUME_BANDS)
        refused = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD)
        assert (
            refused.status_code == 409
            and code(refused) == "GO_LIVE_NOT_READY"
            and "test verification" in refused.json()["error"]["message"]
        )

        server = co.sandbox_server()
        co.run_sandbox(server, ("approved",))
        assert [
            c["met"]
            for c in co.http.get("/v1/verify-api/go-live", headers=co.h).json()["certification"]
        ] == [True, False, False]
        co.run_sandbox(server, ("declined", "review"))
        co.add_webhook()
        assert all(
            c["met"]
            for c in co.http.get("/v1/verify-api/go-live", headers=co.h).json()["certification"]
        )

        # terms and the request itself are checked before anything is stored
        assert (
            code(
                co.http.post(
                    "/v1/verify-api/go-live-requests",
                    headers=co.h,
                    json={**GOOD, "accept_terms": False},
                )
            )
            == "TERMS_NOT_ACCEPTED"
        )
        assert (
            co.http.post(
                "/v1/verify-api/go-live-requests", headers=co.h, json={**GOOD, "markets": ["usa"]}
            ).status_code
            == 422
        )
        assert (
            co.http.post(
                "/v1/verify-api/go-live-requests", headers=co.h, json={**GOOD, "bogus": 1}
            ).status_code
            == 422
        )
        assert co.http.get("/v1/verify-api/go-live", headers=co.h).json()["request"] is None

        made = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD)
        assert made.status_code == 201, made.text
        req = made.json()
        assert (
            req["status"] == "SUBMITTED"
            and req["markets"] == ["GB", "US"]
            and all(c["met"] for c in req["certification"])
        )
        assert (
            code(co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD))
            == "GO_LIVE_REQUEST_OPEN"
        )
        assert (
            co.http.get("/v1/verify-api/go-live", headers=co.h).json()["request"]["request_id"]
            == req["request_id"]
        )

        # taking it back lets the company send a new one; a closed one cannot be taken back again
        w = co.http.post(
            f"/v1/verify-api/go-live-requests/{req['request_id']}:withdraw", headers=co.h
        )
        assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
        assert (
            code(
                co.http.post(
                    f"/v1/verify-api/go-live-requests/{req['request_id']}:withdraw", headers=co.h
                )
            )
            == "GO_LIVE_STATE_INVALID"
        )
        assert (
            co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).status_code
            == 201
        )
        assert (
            code(
                co.http.post(
                    f"/v1/verify-api/go-live-requests/{uuid.uuid4()}:withdraw", headers=co.h
                )
            )
            == "NOT_FOUND"
        )
        # nothing without sign-in
        assert co.http.get("/v1/verify-api/go-live").status_code == 401
        assert co.http.post("/v1/verify-api/go-live-requests", json=GOOD).status_code == 401
    finally:
        co.close()


def test_an_operator_reviews_a_different_operator_approves_and_production_opens_with_a_record(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        co.ready()
        req = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).json()
        rid, op1, op2 = req["request_id"], uid(), uid()
        # production cannot be created yet
        no = co.http.post(
            "/v1/verify-api/applications",
            headers=co.h,
            json={"name": "Live app", "environment": "PRODUCTION"},
        )
        assert no.status_code == 403 and code(no) == "PRODUCTION_NOT_APPROVED"

        async def operate() -> None:
            async with operator_tx(rt_url) as c:
                items = await svc.operator_open(c)
                mine = next(i for i in items if i["request_id"] == rid)
                assert (
                    mine["tenant_id"] == co.o.tenant
                    and mine["status"] == "SUBMITTED"
                    and mine["attestations"] == list(g.ATTESTATIONS)
                )
            # approval is not possible without a review, by the reviewer, or with an unconfirmed item
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.approve(c, rid, op1, ALL, None)
                assert e.value.code == "GO_LIVE_STATE_INVALID"
            async with operator_tx(rt_url) as c:
                assert (await svc.start_review(c, rid, op1))["status"] == "IN_REVIEW"
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.approve(c, rid, op1, ALL, None)
                assert e.value.code == "GO_LIVE_SELF_APPROVAL"
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.approve(c, rid, op2, {**ALL, "billing_ready": False}, None)
                assert e.value.code == "ATTESTATION_MISSING"
            async with operator_tx(rt_url) as c:
                done = await svc.approve(c, rid, op2, ALL, "Checked against the signed order form.")
                assert done["status"] == "APPROVED"

        asyncio.run(operate())

        # the company sees the grant: what is allowed, and the digest of that record
        st = co.http.get("/v1/verify-api/go-live", headers=co.h).json()
        assert st["request"]["status"] == "APPROVED" and st["grant"]["status"] == "ACTIVE"
        assert st["grant"]["markets"] == ["GB", "US"] and len(st["grant"]["manifest_digest"]) == 64

        # the stored record recomputes to its digest and names both people
        async def stored() -> Any:
            async with engine(owner_url) as eng, eng.begin() as conn:
                return (
                    await conn.execute(
                        text(
                            "SELECT manifest, manifest_digest FROM verify.api_production_grant WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(co.o.tenant)},
                    )
                ).one()

        manifest, digest = asyncio.run(stored())
        assert (
            g.digest(json.loads(json.dumps(manifest))).hex()
            == bytes(digest).hex()
            == st["grant"]["manifest_digest"]
        )
        assert (
            manifest["reviewed_by"] == op1
            and manifest["approved_by"] == op2
            and manifest["environment"] == "PRODUCTION"
        )
        assert all(manifest["confirmed"].values()) and all(
            manifest["sandbox_certification"].values()
        )

        # now a production application can be created, and it can sign in; serving live verifications is a later gate
        live = co.http.post(
            "/v1/verify-api/applications",
            headers=co.h,
            json={"name": "Live app", "environment": "PRODUCTION"},
        )
        assert live.status_code == 201 and live.json()["environment"] == "PRODUCTION", live.text
        cred = co.http.post(
            f"/v1/verify-api/applications/{live.json()['application_id']}/credentials", headers=co.h
        ).json()
        tok = co.http.post(
            "/v1/verify-api/oauth2/token",
            json={"client_id": cred["client_id"], "client_secret": cred["client_secret"]},
        )
        assert tok.status_code == 200
        bearer = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        assert (
            co.http.get("/v1/verify-api/whoami", headers=bearer).json()["environment"]
            == "production"
        )
        refused = co.http.post(
            "/v1/verify-api/verification-sessions", headers={**bearer, "Idempotency-Key": uid()},
            json={"customer_reference": "c-1", "country": "US", "purpose_code": "customer_onboarding"},
        )  # fmt: skip
        assert refused.status_code == 403 and code(refused) == "PRODUCT_NOT_ENTITLED"
        # a second request while a grant stands is refused
        assert (
            code(co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD))
            == "GO_LIVE_ALREADY_APPROVED"
        )

        # emergency stop: the grant ends, the production application is suspended and its token stops at once
        async def stop() -> dict[str, Any]:
            async with operator_tx(rt_url) as c:
                return await svc.revoke(
                    c, co.o.tenant, op2, "Suspected misuse reported by support."
                )

        stopped = asyncio.run(stop())
        assert stopped["status"] == "REVOKED" and stopped["applications_stopped"] == 1
        assert co.http.get("/v1/verify-api/whoami", headers=bearer).status_code == 401
        assert (
            code(
                co.http.post(
                    "/v1/verify-api/applications",
                    headers=co.h,
                    json={"name": "Again", "environment": "PRODUCTION"},
                )
            )
            == "PRODUCTION_NOT_APPROVED"
        )
        assert co.http.get("/v1/verify-api/go-live", headers=co.h).json()["grant"] is None

        async def again() -> None:
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.revoke(c, co.o.tenant, op2, "Again.")
                assert e.value.code == "NOT_FOUND"
                with pytest.raises(m.PartnerError) as e2:
                    await svc.revoke(c, co.o.tenant, op2, "")
                assert e2.value.code == "NOTE_REQUIRED"

        asyncio.run(again())
    finally:
        co.close()


def test_a_declined_request_tells_the_company_why_and_another_company_sees_none_of_it(
    rt_url: str, owner_url: str
) -> None:
    co, other = Company(rt_url), Company(rt_url)
    try:
        co.ready()
        rid = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).json()[
            "request_id"
        ]
        op = uid()

        async def decide() -> None:
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.decline(c, rid, op, "")
                assert e.value.code == "NOTE_REQUIRED"
            async with operator_tx(rt_url) as c:
                d = await svc.decline(
                    c, rid, op, "The markets are not covered by our approved provider yet."
                )
                assert d["status"] == "DECLINED"
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.start_review(c, rid, op)
                assert e.value.code == "GO_LIVE_STATE_INVALID"  # a closed request never reopens
                assert await svc.operator_open(c) == [] or all(
                    i["request_id"] != rid for i in await svc.operator_open(c)
                )

        asyncio.run(decide())
        st = co.http.get("/v1/verify-api/go-live", headers=co.h).json()["request"]
        assert st["status"] == "DECLINED" and "approved provider" in st["note"]
        # the company may ask again after a decline
        assert (
            co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).status_code
            == 201
        )

        # the other company has no request, no grant, cannot withdraw this one and cannot create production
        seen = other.http.get("/v1/verify-api/go-live", headers=other.h).json()
        assert seen["request"] is None and seen["grant"] is None
        assert (
            code(
                other.http.post(f"/v1/verify-api/go-live-requests/{rid}:withdraw", headers=other.h)
            )
            == "NOT_FOUND"
        )
        assert (
            code(
                other.http.post(
                    "/v1/verify-api/applications",
                    headers=other.h,
                    json={"name": "Sneaky", "environment": "PRODUCTION"},
                )
            )
            == "PRODUCTION_NOT_APPROVED"
        )
    finally:
        co.close()
        other.close()


def test_a_request_whose_sandbox_checks_lapse_before_approval_is_not_approved(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        co.ready()
        rid = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).json()[
            "request_id"
        ]
        op1, op2 = uid(), uid()

        async def run() -> None:
            async with operator_tx(rt_url) as c:
                await svc.start_review(c, rid, op1)
            # the company's only endpoint is retired after it asked
            async with engine(owner_url) as eng, eng.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE events.webhook_endpoint SET status = 'RETIRED' WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(co.o.tenant)},
                )
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.approve(c, rid, op2, ALL, None)
                assert e.value.code == "GO_LIVE_NOT_READY"

        asyncio.run(run())
        assert co.http.get("/v1/verify-api/go-live", headers=co.h).json()["grant"] is None
    finally:
        co.close()


def test_the_operator_routes_refuse_anyone_who_is_not_an_operator(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        for method, path in (
            ("get", "/operator/verify-api/go-live-requests"),
            ("post", f"/operator/verify-api/go-live-requests/{uuid.uuid4()}:approve"),
            ("post", f"/operator/verify-api/grants/{uuid.uuid4()}:revoke"),
        ):
            anon = getattr(co.http, method)(path)
            assert anon.status_code in (401, 403), (path, anon.status_code)
            owner = getattr(co.http, method)(
                path, headers=co.h
            )  # a company owner is not an operator
            assert owner.status_code in (401, 403), (path, owner.status_code)
    finally:
        co.close()
