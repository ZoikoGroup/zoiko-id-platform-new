"""Stronger Verify API credentials (CR-158), against the real database and the real token endpoint: private-key JWT credentials and an IP allowlist.

A company can register only a PUBLIC key; its server proves itself with a signed, single-use, short-lived assertion, so Zoiko iD holds nothing that could leak. An application can also be limited to the networks its
server calls from, at the token endpoint and on every later call.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from test_commercial_pg import settings
from test_verify_api_pg import issue, make_app, refused, uid, who
from zid_crypto import LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.application import observe, service
from zid_platform.modules.verify_api.domain import credential_options as opts
from zid_platform.modules.verify_api.infrastructure import store

AUD = ["https://id.example.test/v1/verify-api/oauth2/token"]


async def issue_key(
    eng: Any, tenant: str, application_id: str, jwk: dict[str, Any]
) -> dict[str, Any]:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        return await service.issue_credential(t, tenant, uid(), application_id, public_jwk=jwk)


def assertion(
    key: LocalSigner, client_id: str, *, now: datetime, jti: str | None = None, **over: Any
) -> str:
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": AUD[0],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=2)).timestamp()),
        "jti": jti or uuid.uuid4().hex,
    }
    return str(sign_jws(key, {**claims, **over}, typ="JWT"))


async def exchange_key(
    eng: Any,
    token: str,
    now: datetime,
    *,
    scope: str | None = None,
    ip: str | None = None,
    cid: str | None = None,
) -> dict[str, Any]:
    async with eng.begin() as c, store.auth_scope(c):
        return await service.exchange_assertion(
            c, token, AUD, scope, now, client_id=cid, caller_ip=ip
        )


async def set_allowlist(eng: Any, tenant: str, application_id: str, entries: list[str]) -> Any:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        return await service.update_settings(t, application_id, {"ip_allowlist": entries})


def test_a_private_key_credential_holds_only_a_public_key_and_its_assertion_works_once(
    rt_url: str, owner_url: str
) -> None:
    tenant, key = uid(), LocalSigner("partner-key-1")
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(
                eng, tenant, "Key app", scopes=["verifications:create", "decisions:read"]
            )
            cred = await issue_key(eng, tenant, app["application_id"], key.public_jwk())
            assert cred["auth_method"] == "PRIVATE_KEY_JWT" and cred["key_id"] == "partner-key-1"
            assert "client_secret" not in cred  # there is no secret to show, so none can be leaked

            # what is stored is the public key and no secret material at all
            async with engine(owner_url) as oeng, oeng.begin() as oc:
                stored = (
                    await oc.execute(
                        text(
                            "SELECT secret_hash, secret_hint, public_jwk, auth_method FROM verify.api_credential WHERE client_id = :c"
                        ),
                        {"c": cred["client_id"]},
                    )
                ).one()
            assert stored[0] is None and stored[1] is None and stored[3] == "PRIVATE_KEY_JWT"
            assert "d" not in stored[2] and stored[2]["kid"] == "partner-key-1"

            # the company can see the credential, but there is no secret hint and the key is only named
            async with tenant_transaction(eng, ctx(tenant)) as t:
                detail = await service.application_detail(t, app["application_id"])
            seen = detail["credentials"][0]
            assert (
                seen["auth_method"] == "PRIVATE_KEY_JWT"
                and seen["key_id"] == "partner-key-1"
                and seen["secret_hint"] is None
            )

            # a signed assertion gives a token that says exactly what the application holds
            token = assertion(key, cred["client_id"], now=now, jti="once-only-0001")
            tok = await exchange_key(eng, token, now)
            assert tok["token_type"] == "Bearer" and tok["environment"] == "sandbox"
            caller = await who(eng, tok["access_token"])
            assert caller.application_id == app["application_id"]
            assert set(caller.scopes) == {"verifications:create", "decisions:read"}

            # the same assertion a second time is a replay: a copy captured in transit is useless
            assert await refused(exchange_key(eng, token, now)) == "AUTH_INVALID"
            # a new assertion works, and a narrower scope can be asked for
            second = await exchange_key(
                eng, assertion(key, cred["client_id"], now=now), now, scope="decisions:read"
            )
            assert second["scope"] == "decisions:read"

    asyncio.run(run())


def test_an_assertion_that_is_forged_misdirected_expired_or_for_the_wrong_kind_of_credential_is_refused(
    rt_url: str, owner_url: str
) -> None:
    tenant, key, other = uid(), LocalSigner("partner-key-1"), LocalSigner("attacker-key")
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            cred = await issue_key(eng, tenant, app["application_id"], key.public_jwk())
            cid = cred["client_id"]
            secret_cred = await issue(eng, tenant, app["application_id"])

            for label, bad in {
                "signed by another key": assertion(other, cid, now=now),
                "wrong audience": assertion(
                    key, cid, now=now, aud="https://elsewhere.example/token"
                ),
                "for another client": assertion(key, cid, now=now, sub="zpc_00000000000000000000"),
                "already expired": assertion(
                    key, cid, now=now, exp=int((now - timedelta(minutes=1)).timestamp())
                ),
                "lives too long": assertion(
                    key, cid, now=now, exp=int((now + timedelta(hours=2)).timestamp())
                ),
                "no id": assertion(key, cid, now=now, jti="x"),
                "not a token": "abc.def.ghi",
                "empty": "",
            }.items():
                assert await refused(exchange_key(eng, bad, now, cid=cid)) == "AUTH_INVALID", label

            # an unsigned token (alg none) is never accepted
            import base64
            import json

            def b64(d: dict[str, Any]) -> str:
                return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

            unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'iss': cid, 'sub': cid, 'aud': AUD[0], 'exp': int((now + timedelta(minutes=1)).timestamp()), 'jti': 'unsigned-001'})}."
            assert await refused(exchange_key(eng, unsigned, now, cid=cid)) == "AUTH_INVALID"

            # an assertion naming a secret credential is refused, and a key credential cannot be used with a secret
            assert (
                await refused(
                    exchange_key(eng, assertion(key, secret_cred["client_id"], now=now), now)
                )
                == "AUTH_INVALID"
            )
            async with eng.begin() as c, store.auth_scope(c):
                assert (
                    await refused(service.exchange(c, cid, "zps_anything", None, now))
                ) == "AUTH_INVALID"

            # once the credential is revoked even a good assertion is refused
            async with tenant_transaction(eng, ctx(tenant)) as t:
                await service.revoke_credential(t, app["application_id"], cred["credential_id"])
            assert (
                await refused(exchange_key(eng, assertion(key, cid, now=now), now))
                == "AUTH_INVALID"
            )

    asyncio.run(run())


def test_a_key_credential_is_rotated_to_a_new_key_and_a_secret_one_stays_a_secret(
    rt_url: str, owner_url: str
) -> None:
    tenant, k1, k2 = uid(), LocalSigner("key-one"), LocalSigner("key-two")
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            first = await issue_key(eng, tenant, app["application_id"], k1.public_jwk())
            secret = await issue(eng, tenant, app["application_id"])

            async def rotate(cred: dict[str, Any], jwk: dict[str, Any] | None) -> dict[str, Any]:
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    return await service.rotate_credential(
                        t,
                        tenant,
                        uid(),
                        app["application_id"],
                        cred["credential_id"],
                        public_jwk=jwk,
                    )

            async def code_of(cred: dict[str, Any], jwk: dict[str, Any] | None) -> str:
                return await refused(rotate(cred, jwk))

            assert (
                await code_of(first, None) == "VALIDATION_FAILED"
            )  # a key credential needs its new key
            assert (
                await code_of(secret, k2.public_jwk()) == "VALIDATION_FAILED"
            )  # a secret is replaced by a secret
            fresh = await rotate(first, k2.public_jwk())
            assert (
                fresh["auth_method"] == "PRIVATE_KEY_JWT"
                and fresh["key_id"] == "key-two"
                and "client_secret" not in fresh
            )
            # both keys work during the overlap; each only for its own client id
            await exchange_key(eng, assertion(k1, first["client_id"], now=now), now)
            await exchange_key(eng, assertion(k2, fresh["client_id"], now=now), now)
            assert (
                await refused(exchange_key(eng, assertion(k2, first["client_id"], now=now), now))
                == "AUTH_INVALID"
            )
            moved = await rotate(secret, None)
            assert moved["auth_method"] == "SECRET" and moved["client_secret"].startswith("zps_")

    asyncio.run(run())


def test_an_ip_allowlist_limits_the_token_endpoint_and_every_later_call(
    rt_url: str, owner_url: str
) -> None:
    tenant, key = uid(), LocalSigner("partner-key-1")
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await make_app(eng, tenant)
            secret = await issue(eng, tenant, app["application_id"])
            keyed = await issue_key(eng, tenant, app["application_id"], key.public_jwk())

            async def secret_token(ip: str | None) -> dict[str, Any]:
                async with eng.begin() as c, store.auth_scope(c):
                    return await service.exchange(
                        c, secret["client_id"], secret["client_secret"], None, now, caller_ip=ip
                    )

            # with no allowlist every address works
            assert (await secret_token("198.51.100.4"))["access_token"]

            changed = await set_allowlist(
                eng, tenant, app["application_id"], ["203.0.113.0/24", "203.0.113.0/24"]
            )
            assert changed["ip_allowlist"] == ["203.0.113.0/24"]
            assert (await secret_token("203.0.113.9"))["access_token"]
            assert await refused(secret_token("198.51.100.4")) == "IP_NOT_ALLOWED"
            assert (
                await refused(secret_token(None)) == "IP_NOT_ALLOWED"
            )  # an unknown address never passes
            # the same rule for a private-key credential, and only after the assertion itself proved good
            good = assertion(key, keyed["client_id"], now=now)
            assert (
                await refused(exchange_key(eng, good, now, ip="198.51.100.4")) == "IP_NOT_ALLOWED"
            )
            tok = await exchange_key(
                eng, assertion(key, keyed["client_id"], now=now), now, ip="203.0.113.9"
            )

            # a token already issued is useless from outside the allowlist, and works again from inside it
            async def caller(ip: str | None) -> Any:
                async with eng.begin() as c, store.auth_scope(c):
                    return await service.authenticate(c, tok["access_token"], now, caller_ip=ip)

            assert (await caller("203.0.113.77")).application_id == app["application_id"]
            assert await refused(caller("198.51.100.4")) == "IP_NOT_ALLOWED"
            # narrowing it later takes effect on that very token
            await set_allowlist(eng, tenant, app["application_id"], ["203.0.113.5"])
            assert await refused(caller("203.0.113.77")) == "IP_NOT_ALLOWED"
            # clearing it lifts the restriction
            await set_allowlist(eng, tenant, app["application_id"], [])
            assert (await caller("198.51.100.4")).application_id == app["application_id"]

            for bad in (["0.0.0.0/0"], ["nonsense"]):
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    assert (
                        await refused(
                            service.update_settings(t, app["application_id"], {"ip_allowlist": bad})
                        )
                        == "VALIDATION_FAILED"
                    )

    asyncio.run(run())
    assert opts.MAX_ALLOWLIST == 20


def test_the_http_token_endpoint_takes_a_signed_assertion_and_the_allowlist_blocks_an_unknown_address(
    rt_url: str, owner_url: str
) -> None:
    tenant, key = uid(), LocalSigner("http-key")
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)
    now = datetime.now(UTC)

    async def seed() -> tuple[dict[str, Any], dict[str, Any]]:
        async with engine(rt_url) as eng:
            made = await make_app(eng, tenant, "Http key app", scopes=["decisions:read"])
            return made, await issue_key(eng, tenant, made["application_id"], key.public_jwk())

    made, cred = asyncio.run(seed())
    url = "/v1/verify-api/oauth2/token"
    kind = opts.ASSERTION_TYPE
    audience = f"{ISS}{url}"

    def seen(method: str, result: str) -> float:
        return observe.AUTH.value(method=method, result=result)

    issued0, denied0, ip0 = (
        seen("private_key_jwt", "issued"),
        seen("private_key_jwt", "denied"),
        seen("private_key_jwt", "ip_denied"),
    )
    with TestClient(app, base_url=ISS) as http:

        def post(token: str, **extra: str) -> Any:
            return http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": kind,
                    "client_assertion": token,
                    **extra,
                },
            )

        good = str(
            sign_jws(
                key,
                {
                    "iss": cred["client_id"],
                    "sub": cred["client_id"],
                    "aud": audience,
                    "exp": int((now + timedelta(minutes=2)).timestamp()),
                    "jti": uuid.uuid4().hex,
                },
                typ="JWT",
            )
        )
        ok = post(good)
        assert ok.status_code == 200, ok.text
        assert ok.headers["cache-control"] == "no-store" and ok.json()["scope"] == "decisions:read"
        me = http.get(
            "/v1/verify-api/whoami",
            headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
        )
        assert me.status_code == 200 and me.json()["scopes"] == ["decisions:read"]

        # the same assertion again is a replay, and the answer says nothing about why
        again = post(good)
        assert again.status_code == 401 and again.json()["error"]["code"] == "AUTH_INVALID"
        # a wrong type or a missing assertion is a plain bad request / refusal
        assert (
            http.post(
                url, data={"client_assertion_type": "urn:wrong", "client_assertion": good}
            ).status_code
            == 400
        )
        assert post("garbage").status_code == 401
        # an assertion that names a different audience is refused
        wrong = str(
            sign_jws(
                key,
                {
                    "iss": cred["client_id"],
                    "sub": cred["client_id"],
                    "aud": "https://elsewhere.example/token",
                    "exp": int((now + timedelta(minutes=2)).timestamp()),
                    "jti": uuid.uuid4().hex,
                },
                typ="JWT",
            )
        )
        assert post(wrong).status_code == 401

        # an allowlist the test client's address cannot satisfy blocks the exchange with a clear, non-secret reason
        async def restrict() -> None:
            async with engine(rt_url) as eng:
                await set_allowlist(eng, tenant, made["application_id"], ["203.0.113.5"])

        asyncio.run(restrict())
        fresh = str(
            sign_jws(
                key,
                {
                    "iss": cred["client_id"],
                    "sub": cred["client_id"],
                    "aud": audience,
                    "exp": int((now + timedelta(minutes=2)).timestamp()),
                    "jti": uuid.uuid4().hex,
                },
                typ="JWT",
            )
        )
        blocked = post(fresh)
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "IP_NOT_ALLOWED"
        # the service reports each ending by method and result, with nothing about who
        assert seen("private_key_jwt", "issued") == issued0 + 1
        assert (
            seen("private_key_jwt", "denied") >= denied0 + 3
        )  # the replay, the garbage and the wrong audience
        assert seen("private_key_jwt", "ip_denied") == ip0 + 1
        # and the earlier token stops working from this address too
        assert (
            http.get(
                "/v1/verify-api/whoami",
                headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
            ).status_code
            == 403
        )
