"""The Verify API's applications, credentials and tokens (CR-149), against the real database and the real HTTP endpoints.

A company creates an application and a credential; its server exchanges the client id and secret for a short-lived token and calls the API with it. The secret is shown once and kept only as a hash.
A token ends at once when its credential is revoked or its application is suspended, a rotated credential works for a bounded overlap, and one company can never see or use another's.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from test_commercial_pg import settings
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import store


def uid() -> str:
    return str(uuid.uuid4())


async def make_app(eng: Any, tenant: str, name: str = "Checkout", **kw: Any) -> dict[str, Any]:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        return await service.create_application(
            t,
            tenant,
            uid(),
            name=name,
            environment=kw.get("environment", "SANDBOX"),
            scopes=kw.get("scopes"),
            origins=kw.get("origins"),
        )


async def issue(
    eng: Any, tenant: str, application_id: str, now: datetime | None = None
) -> dict[str, Any]:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        return await service.issue_credential(t, tenant, uid(), application_id, now)


async def exchange(
    eng: Any, client_id: str, secret: str, scope: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    async with eng.begin() as c, store.auth_scope(c):
        return await service.exchange(c, client_id, secret, scope, now)


async def who(eng: Any, token: str, now: datetime | None = None) -> service.VerifyApiCaller:
    async with eng.begin() as c, store.auth_scope(c):
        return await service.authenticate(c, token, now)


async def refused(coro: Any) -> str:
    with pytest.raises(m.PartnerError) as e:
        await coro
    return e.value.code


def test_a_company_gets_a_credential_whose_secret_is_shown_once_and_its_server_exchanges_it_for_a_token(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant, origins=["https://app.example.com"])
            assert (
                app["environment"] == "SANDBOX"
                and app["status"] == "ACTIVE"
                and app["redirect_origins"] == ["https://app.example.com"]
            )
            assert set(app["scopes"]) == set(m.DEFAULT_SANDBOX_SCOPES)
            cred = await issue(eng, tenant, app["application_id"])
            assert cred["client_id"].startswith("zpc_") and cred["client_secret"].startswith("zps_")

            # what is stored is the hash of the secret, never the secret; and the company can read the credential again, but never its secret
            async with engine(owner_url) as oeng, oeng.begin() as oc:
                stored = (
                    await oc.execute(
                        text(
                            "SELECT secret_hash, secret_hint FROM verify.api_credential WHERE client_id = :c"
                        ),
                        {"c": cred["client_id"]},
                    )
                ).one()
            assert bytes(stored[0]) == m.hash_secret(cred["client_secret"]) and cred[
                "client_secret"
            ].encode() not in bytes(stored[0])
            assert stored[1] == cred["client_secret"][-4:]
            async with tenant_transaction(eng, ctx(tenant)) as t:
                detail = await service.application_detail(t, app["application_id"])
            assert [c["client_id"] for c in detail["credentials"]] == [cred["client_id"]]
            assert "client_secret" not in str(detail) and cred["client_secret"] not in str(detail)

            # the server exchanges the pair for a short-lived token, and the token says exactly what it is
            tok = await exchange(eng, cred["client_id"], cred["client_secret"])
            assert (
                tok["token_type"] == "Bearer"
                and tok["expires_in"] == 900
                and tok["access_token"].startswith("zpt_")
                and tok["environment"] == "sandbox"
            )
            caller = await who(eng, tok["access_token"])
            assert (caller.tenant_id, caller.application_id, caller.environment) == (
                tenant,
                app["application_id"],
                "SANDBOX",
            )
            assert set(caller.scopes) == set(m.DEFAULT_SANDBOX_SCOPES)
            async with (
                engine(owner_url) as oeng,
                oeng.begin() as oc,
            ):  # the token too is kept only as a hash, and the credential records it was used
                row = (
                    await oc.execute(
                        text("SELECT count(*) FROM verify.api_access_token WHERE token_hash = :h"),
                        {"h": m.hash_secret(tok["access_token"])},
                    )
                ).scalar_one()
                used = (
                    await oc.execute(
                        text("SELECT last_used_at FROM verify.api_credential WHERE client_id = :c"),
                        {"c": cred["client_id"]},
                    )
                ).scalar_one()
            assert row == 1 and used is not None

    asyncio.run(run())


def test_every_failure_gets_the_same_answer_and_a_token_cannot_carry_more_than_the_application_holds(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant, scopes=["verifications:create", "decisions:read"])
            cred = await issue(eng, tenant, app["application_id"])
            cid, sec = cred["client_id"], cred["client_secret"]

            # wrong secret, unknown client, empty secret, a token passed as a secret: one code, one message
            seen = set()
            for client, secret in (
                (cid, sec + "x"),
                ("zpc_" + "0" * 20, sec),
                (cid, ""),
                (cid, "zpt_" + "a" * 43),
            ):
                with pytest.raises(m.PartnerError) as e:
                    await exchange(eng, client, secret)
                seen.add((e.value.code, e.value.detail))
            assert seen == {("AUTH_INVALID", service.AUTH_FAILED)}

            # the token carries a subset on request, never more than the application holds
            narrow = await exchange(eng, cid, sec, scope="decisions:read")
            assert narrow["scope"] == "decisions:read" and (
                await who(eng, narrow["access_token"])
            ).scopes == ("decisions:read",)
            assert (await exchange(eng, cid, sec))["scope"] == "verifications:create decisions:read"
            assert await refused(exchange(eng, cid, sec, scope="usage:read")) == "INVALID_SCOPE"
            assert (
                await refused(exchange(eng, cid, sec, scope="not-a-scope")) == "VALIDATION_FAILED"
            )
            caller = await who(eng, narrow["access_token"])
            with pytest.raises(m.PartnerError) as e2:
                caller.require("verifications:create")
            assert e2.value.code == "TENANT_SCOPE_DENIED"

            # a token that was never issued, or is of the wrong shape, is not a caller
            for bad in ("", "zpt_" + "z" * 43, "Bearer", cid, sec):
                assert await refused(who(eng, bad)) == "AUTH_INVALID"

    asyncio.run(run())


def test_production_cannot_be_created_names_are_unique_and_the_number_of_credentials_is_bounded(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as t:
                with pytest.raises(m.PartnerError) as e:
                    await service.create_application(
                        t,
                        tenant,
                        uid(),
                        name="Live app",
                        environment="PRODUCTION",
                        scopes=None,
                        origins=None,
                    )
            assert e.value.code == "PRODUCTION_NOT_APPROVED"
            app = await make_app(eng, tenant, "Checkout")
            assert (
                await refused(make_app(eng, tenant, "checkout")) == "APPLICATION_NAME_TAKEN"
            )  # the name is unique ignoring case
            for _ in range(m.MAX_LIVE_CREDENTIALS_PER_APPLICATION):
                await issue(eng, tenant, app["application_id"])
            assert await refused(issue(eng, tenant, app["application_id"])) == "LIMIT_REACHED"
            # a retired application frees its name
            async with tenant_transaction(eng, ctx(tenant)) as t:
                await service.set_status(t, app["application_id"], "RETIRED")
            again = await make_app(eng, tenant, "Checkout")
            assert again["application_id"] != app["application_id"]

    asyncio.run(run())


def test_a_rotated_credential_keeps_the_old_one_working_for_a_bounded_overlap_and_no_longer(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            old = await issue(eng, tenant, app["application_id"])
            now = datetime.now(UTC)
            async with tenant_transaction(eng, ctx(tenant)) as t:
                new = await service.rotate_credential(
                    t,
                    tenant,
                    uid(),
                    app["application_id"],
                    old["credential_id"],
                    overlap=timedelta(hours=2),
                    now=now,
                )
            assert (
                new["replaces"] == old["credential_id"]
                and new["client_secret"] != old["client_secret"]
            )
            # both work during the overlap, so a team can deploy the new secret without downtime
            assert (
                await exchange(
                    eng, old["client_id"], old["client_secret"], now=now + timedelta(hours=1)
                )
            )["access_token"]
            assert (
                await exchange(
                    eng, new["client_id"], new["client_secret"], now=now + timedelta(hours=1)
                )
            )["access_token"]
            # after it, only the new one does
            assert (
                await refused(
                    exchange(
                        eng, old["client_id"], old["client_secret"], now=now + timedelta(hours=3)
                    )
                )
                == "AUTH_INVALID"
            )
            assert (
                await exchange(
                    eng, new["client_id"], new["client_secret"], now=now + timedelta(hours=3)
                )
            )["access_token"]
            async with tenant_transaction(eng, ctx(tenant)) as t:
                states = {
                    c["credential_id"]: c["status"]
                    for c in (
                        await service.application_detail(
                            t, app["application_id"], now + timedelta(hours=3)
                        )
                    )["credentials"]
                }
                # only an active credential is rotated, and the overlap is bounded
                assert (
                    await refused(
                        service.rotate_credential(
                            t, tenant, uid(), app["application_id"], old["credential_id"]
                        )
                    )
                    == "CREDENTIAL_STATE_INVALID"
                )
                assert (
                    await refused(
                        service.rotate_credential(
                            t,
                            tenant,
                            uid(),
                            app["application_id"],
                            new["credential_id"],
                            overlap=timedelta(days=30),
                        )
                    )
                    == "VALIDATION_FAILED"
                )
            assert (
                states[old["credential_id"]] == "EXPIRED"
                and states[new["credential_id"]] == "ACTIVE"
            )

    asyncio.run(run())


def test_revoking_a_credential_or_suspending_an_application_ends_its_tokens_at_once(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            a, b = (
                await issue(eng, tenant, app["application_id"]),
                await issue(eng, tenant, app["application_id"]),
            )
            ta = (await exchange(eng, a["client_id"], a["client_secret"]))["access_token"]
            tb = (await exchange(eng, b["client_id"], b["client_secret"]))["access_token"]
            assert (await who(eng, ta)).credential_id == a["credential_id"]

            async with tenant_transaction(eng, ctx(tenant)) as t:
                revoked = await service.revoke_credential(
                    t, app["application_id"], a["credential_id"]
                )
            assert revoked["status"] == "REVOKED" and revoked["revoked_at"]
            assert (
                await refused(who(eng, ta)) == "AUTH_INVALID"
            )  # its token stops now, not in fifteen minutes
            assert (
                await refused(exchange(eng, a["client_id"], a["client_secret"])) == "AUTH_INVALID"
            )
            assert (await who(eng, tb)).credential_id == b[
                "credential_id"
            ]  # the other credential is untouched

            # suspending the application stops every token and every exchange; resuming lets a new exchange work again
            async with tenant_transaction(eng, ctx(tenant)) as t:
                await service.set_status(t, app["application_id"], "SUSPENDED")
            assert await refused(who(eng, tb)) == "AUTH_INVALID"
            assert (
                await refused(exchange(eng, b["client_id"], b["client_secret"])) == "AUTH_INVALID"
            )
            assert (
                await refused(issue(eng, tenant, app["application_id"]))
                == "APPLICATION_STATE_INVALID"
            )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                await service.set_status(t, app["application_id"], "ACTIVE")
            assert await refused(who(eng, tb)) == "AUTH_INVALID"  # the old token does not come back
            assert (await exchange(eng, b["client_id"], b["client_secret"]))["access_token"]

            # retiring ends the application for good, and every credential with it
            async with tenant_transaction(eng, ctx(tenant)) as t:
                done = await service.set_status(t, app["application_id"], "RETIRED")
                assert all(c["status"] == "REVOKED" for c in done["credentials"])
                assert (
                    await refused(service.set_status(t, app["application_id"], "ACTIVE"))
                    == "APPLICATION_STATE_INVALID"
                )
            assert (
                await refused(exchange(eng, b["client_id"], b["client_secret"])) == "AUTH_INVALID"
            )

    asyncio.run(run())


def test_a_credential_past_its_own_expiry_does_not_exchange_and_a_token_past_its_time_is_not_a_caller(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            cred = await issue(eng, tenant, app["application_id"])
            now = datetime.now(UTC)
            tok = (await exchange(eng, cred["client_id"], cred["client_secret"], now=now))[
                "access_token"
            ]
            assert (await who(eng, tok, now + timedelta(minutes=14))).environment == "SANDBOX"
            assert (
                await refused(who(eng, tok, now + timedelta(minutes=16))) == "AUTH_INVALID"
            )  # fifteen minutes
            assert (
                await refused(
                    exchange(
                        eng,
                        cred["client_id"],
                        cred["client_secret"],
                        now=now + m.CREDENTIAL_TTL + timedelta(days=1),
                    )
                )
                == "AUTH_INVALID"
            )

    asyncio.run(run())


def test_one_company_never_sees_or_changes_another_companys_applications_or_credentials(
    rt_url: str, owner_url: str
) -> None:
    a, b = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            app_a = await make_app(eng, a, "A app")
            cred_a = await issue(eng, a, app_a["application_id"])
            await make_app(eng, b, "B app")
            async with tenant_transaction(eng, ctx(b)) as t:
                assert [x["name"] for x in await service.list_applications(t)] == ["B app"]
                assert (
                    await refused(service.application_detail(t, app_a["application_id"]))
                    == "NOT_FOUND"
                )
                assert (
                    await refused(service.set_status(t, app_a["application_id"], "SUSPENDED"))
                    == "NOT_FOUND"
                )
                assert (
                    await refused(service.issue_credential(t, b, uid(), app_a["application_id"]))
                    == "NOT_FOUND"
                )
                assert (
                    await refused(
                        service.revoke_credential(
                            t, app_a["application_id"], cred_a["credential_id"]
                        )
                    )
                    == "NOT_FOUND"
                )
                assert (
                    await refused(
                        service.rotate_credential(
                            t, b, uid(), app_a["application_id"], cred_a["credential_id"]
                        )
                    )
                    == "NOT_FOUND"
                )
            # without the auth scope, no company's credentials or tokens can be read at all, whoever asks
            async with eng.begin() as c:
                assert (
                    await c.execute(text("SELECT count(*) FROM verify.api_credential"))
                ).scalar_one() == 0
                assert (
                    await c.execute(text("SELECT count(*) FROM verify.api_access_token"))
                ).scalar_one() == 0
            # and A's credential still works for A
            assert (
                await who(
                    eng,
                    (await exchange(eng, cred_a["client_id"], cred_a["client_secret"]))[
                        "access_token"
                    ],
                )
            ).tenant_id == a

    asyncio.run(run())


def test_the_http_token_endpoint_accepts_json_form_and_basic_never_caches_and_limits_guessing(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)

    async def seed() -> dict[str, Any]:
        async with engine(rt_url) as eng:
            made = await make_app(
                eng, tenant, "Http app", scopes=["verifications:create", "decisions:read"]
            )
            return await issue(eng, tenant, made["application_id"])

    cred = asyncio.run(seed())
    cid, sec = cred["client_id"], cred["client_secret"]
    with TestClient(app, base_url=ISS) as http:
        url = "/v1/verify-api/oauth2/token"
        ok = http.post(
            url, json={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}
        )
        assert ok.status_code == 200, ok.text
        assert ok.headers["cache-control"] == "no-store" and ok.headers["pragma"] == "no-cache"
        token = ok.json()["access_token"]
        assert (
            http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": sec,
                    "scope": "decisions:read",
                },
            ).json()["scope"]
            == "decisions:read"
        )
        basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        assert (
            http.post(url, headers={"Authorization": f"Basic {basic}"}).status_code == 200
        )  # client_credentials is the default grant

        # the token says what it is, and names the company only by an opaque reference
        me = http.get("/v1/verify-api/whoami", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["environment"] == "sandbox"
        assert (
            me.json()["tenant_reference"].startswith("tnt_")
            and tenant not in me.text
            and sec not in me.text
        )
        assert set(me.json()["scopes"]) == {"verifications:create", "decisions:read"}
        assert http.get("/v1/verify-api/whoami").status_code == 401
        assert (
            http.get("/v1/verify-api/whoami", headers={"Authorization": "Bearer zpt_nope"}).json()[
                "error"
            ]["code"]
            == "AUTH_INVALID"
        )
        assert (
            http.get(
                "/v1/verify-api/whoami", headers={"Authorization": f"Basic {basic}"}
            ).status_code
            == 401
        )  # a secret is not a token

        # failures: 401 with one code; an unsupported grant is a plain 400; a request that is too large is refused
        bad = http.post(url, json={"client_id": cid, "client_secret": "zps_wrong"})
        assert (
            bad.status_code == 401
            and bad.json()["error"]["code"] == "AUTH_INVALID"
            and "no-store" in bad.headers.get("cache-control", "no-store")
        )
        assert (
            http.post(
                url, json={"grant_type": "password", "client_id": cid, "client_secret": sec}
            ).status_code
            == 400
        )
        assert (
            http.post(
                url, json={"client_id": cid, "client_secret": sec, "scope": "usage:read"}
            ).json()["error"]["code"]
            == "INVALID_SCOPE"
        )
        assert (
            http.post(
                url, content=b"x" * 5000, headers={"content-type": "application/json"}
            ).status_code
            == 413
        )

        # guessing the secret is limited: after ten wrong ones for one client id, even the right one waits
        guess = "zpc_" + "9" * 20
        codes = [
            http.post(url, json={"client_id": guess, "client_secret": f"zps_{i}"}).status_code
            for i in range(12)
        ]
        assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
        assert (
            http.post(url, json={"client_id": cid, "client_secret": sec}).status_code == 200
        )  # another client is not slowed


def test_the_console_routes_need_a_signed_in_administrator(rt_url: str, owner_url: str) -> None:
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)
    with TestClient(app, base_url=ISS) as http:
        for method, path in (
            ("get", "/v1/verify-api/applications"),
            ("post", "/v1/verify-api/applications"),
            ("get", f"/v1/verify-api/applications/{uid()}"),
            ("post", f"/v1/verify-api/applications/{uid()}/credentials"),
            ("post", f"/v1/verify-api/applications/{uid()}:suspend"),
        ):
            r = getattr(http, method)(
                path, **({"json": {"name": "Nope app"}} if method == "post" else {})
            )
            assert r.status_code == 401, (path, r.status_code)


def test_a_signed_in_owner_creates_an_application_and_a_credential_through_the_console_routes(
    rt_url: str, owner_url: str
) -> None:
    """The routes go through the PDP: the owner holds every catalogue action, and the secret appears once, in the answer to creating the credential and nowhere after."""
    o = onboard(
        rt_url,
        extra_actions=("verify_api.application.manage", "verify_api.application.read"),
        extra_resources=("verify_api_application:*",),
    )
    app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(app, base_url=ISS) as http:
        h = o.bearer(o.owner)
        created = http.post(
            "/v1/verify-api/applications",
            headers=h,
            json={"name": "Console app", "redirect_origins": ["https://app.example.com"]},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["application_id"]
        assert (
            http.post(
                "/v1/verify-api/applications", headers=h, json={"name": "Console app"}
            ).json()["error"]["code"]
            == "APPLICATION_NAME_TAKEN"
        )
        assert (
            http.post(
                "/v1/verify-api/applications",
                headers=h,
                json={"name": "Live app", "environment": "PRODUCTION"},
            ).json()["error"]["code"]
            == "PRODUCTION_NOT_APPROVED"
        )
        assert (
            http.post(
                "/v1/verify-api/applications", headers=h, json={"name": "Bad", "unexpected": 1}
            ).status_code
            == 422
        )

        issued = http.post(f"/v1/verify-api/applications/{app_id}/credentials", headers=h)
        assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
        secret = issued.json()["client_secret"]
        read = http.get(f"/v1/verify-api/applications/{app_id}", headers=h)
        assert (
            read.status_code == 200
            and secret not in read.text
            and read.json()["credentials"][0]["client_id"] == issued.json()["client_id"]
        )
        assert [
            a["name"] for a in http.get("/v1/verify-api/applications", headers=h).json()["items"]
        ] == ["Console app"]

        # the server side works with what the console issued
        tok = http.post(
            "/v1/verify-api/oauth2/token",
            json={"client_id": issued.json()["client_id"], "client_secret": secret},
        )
        assert tok.status_code == 200
        bearer = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        assert http.get("/v1/verify-api/whoami", headers=bearer).status_code == 200

        # rotating, then revoking from the console, ends access at once
        cid = issued.json()["credential_id"]
        rotated = http.post(
            f"/v1/verify-api/applications/{app_id}/credentials/{cid}:rotate",
            headers=h,
            json={"overlap_hours": 1},
        )
        assert (
            rotated.status_code == 201
            and rotated.json()["client_secret"] != secret
            and rotated.headers["cache-control"] == "no-store"
        )
        revoked = http.post(
            f"/v1/verify-api/applications/{app_id}/credentials/{rotated.json()['credential_id']}:revoke",
            headers=h,
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
        assert (
            http.post(f"/v1/verify-api/applications/{app_id}:suspend", headers=h).json()["status"]
            == "SUSPENDED"
        )
        assert (
            http.get("/v1/verify-api/whoami", headers=bearer).status_code == 401
        )  # the token issued before is gone
        assert http.get(f"/v1/verify-api/applications/{uuid.uuid4()}", headers=h).status_code == 404

        # another company's owner sees none of it
        other = onboard(
            rt_url,
            extra_actions=("verify_api.application.manage", "verify_api.application.read"),
            extra_resources=("verify_api_application:*",),
        )
        other.ring = o.ring  # the second company's tokens are signed by its own fixture ring: the app trusts the first company's
        oh = other.bearer(other.owner)
        assert http.get("/v1/verify-api/applications", headers=oh).json()["items"] == []
        assert http.get(f"/v1/verify-api/applications/{app_id}", headers=oh).status_code == 404
