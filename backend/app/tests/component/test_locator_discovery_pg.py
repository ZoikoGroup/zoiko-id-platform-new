"""Global locator, signed discovery and epoch fencing (G-W6; DEC-G3; TENANCY-001 §2, §9, §15;
TEN-09, TEN-10, TEN-23, RES-06, RES-07).

A cell publishes its tenants' global-minimum records, signed with its pinned key; the global plane
refuses personal data, records from a cell that is not the tenant's home, unsigned or tampered
records, and anything that would move a tenant to an older epoch. Discovery answers with a signed
{cell, endpoint, placement_epoch, exp}; the cell refuses a request routed with a stale epoch
(CELL_ROUTING_STALE) and points at the current cell. A global-plane outage changes nothing in cells.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from conftest import RT_ROLE, ctx, engine
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.migrations import upgrade
from zid_platform.modules.tenant.application import directory
from zid_platform.settings import Settings

ENDPOINTS = {"uk-1": "https://uk-1.api.id.zoiko.test", "eu-1": "https://eu-1.api.id.zoiko.test"}


def pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def test_publish_discover_fence_and_route(rt_url: str, owner_url: str) -> None:
    async def global_schema() -> None:  # the disposable test database plays the global plane too
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("CREATE SCHEMA IF NOT EXISTS global_directory"))
            await c.execute(text(f"GRANT USAGE ON SCHEMA global_directory TO {RT_ROLE}"))

    asyncio.run(global_schema())
    upgrade(owner_url, ["locator"])

    async def grants() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA global_directory TO {RT_ROLE}"
                )
            )

    asyncio.run(grants())
    uk = LocalSigner.from_pem("cell-uk-1", pem())
    eu = LocalSigner.from_pem("cell-eu-1", pem())
    global_app = create_app(
        Settings(
            role="global",
            database_url=rt_url,
            environment="test",
            cell_id="global",
            cell_endpoints=ENDPOINTS,
            locator_cell_keys={"uk-1": [uk.public_jwk()], "eu-1": [eu.public_jwk()]},
            dev_signing_keys={"discovery": pem()},
        )
    )
    o = onboard(rt_url)
    cell_app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_endpoints=ENDPOINTS,
        ),
        keyring=o.ring,
    )

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    with TestClient(global_app) as g, TestClient(cell_app) as cell:

        async def send(token: str) -> tuple[int, str]:
            r = g.put("/internal/v1/directory/records", json={"record_jws": token})
            return r.status_code, "" if r.status_code == 200 else code(r)

        async def publish(ring: KeyRing, cell_id: str = "uk-1") -> dict[str, str]:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await directory.publish_pass(
                    c, cell_id=cell_id, ring=ring, send=send, now=datetime.now(UTC)
                )

        out = asyncio.run(publish(KeyRing(uk)))
        assert out[o.tenant] == "PUBLISHED"
        assert o.tenant not in asyncio.run(publish(KeyRing(uk)))  # unchanged: nothing re-sent

        async def slug() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                return str(
                    (await c.execute(text("SELECT tenant_slug FROM platform.tenant"))).scalar_one()
                )

        alias = asyncio.run(slug())
        d = g.get("/v1/discovery", params={"workspace": alias})
        assert d.status_code == 200, d.text
        found = d.json()
        assert (found["tid"], found["cell"], found["placement_epoch"]) == (o.tenant, "uk-1", 1)
        assert found["cell_endpoint"] == ENDPOINTS["uk-1"] and found["exp"] > found["iat"]
        keys = g.get("/.well-known/zoiko-discovery-keys").json()["keys"]
        _, claims = verify_jws(found["discovery"], keys=keys, typ="zid-discovery+jwt")
        assert claims["tid"] == o.tenant and claims["cell"] == "uk-1"
        assert g.get("/v1/discovery", params={"workspace": "no-such-company"}).status_code == 404

        def put(signer: LocalSigner, record: dict[str, Any], iss: str) -> Any:
            token = KeyRing(signer).sign(
                {"iss": iss, "iat": 1, "record": record}, typ="zid-directory+jwt"
            )
            return g.put("/internal/v1/directory/records", json={"record_jws": token})

        base = {
            "tenant_id": o.tenant,
            "tenant_status": "PROVISIONING",
            "home_cell_id": "uk-1",
            "placement_epoch": 1,
            "record_revision": 5,
        }
        r = put(uk, {**base, "admin_email": "jane@acme.example"}, "uk-1")
        assert r.status_code == 422 and code(r) == "GLOBAL_DATA_PROHIBITED"  # TEN-09 / TEN-23
        r = put(eu, base, "eu-1")
        assert r.status_code == 403 and code(r) == "CELL_NOT_AUTHORITATIVE"  # not the home cell
        r = put(eu, base, "uk-1")
        assert r.status_code == 401  # eu-1's key cannot speak for uk-1
        r = put(LocalSigner("rogue"), base, "uk-1")
        assert r.status_code == 401 and code(r) == "PUBLICATION_SIGNATURE_INVALID"

        # a migration cutover to eu-1 at epoch 2; the old cell can never move it back (RES-07)
        r = put(
            eu,
            {**base, "home_cell_id": "eu-1", "placement_epoch": 2, "tenant_status": "ACTIVE"},
            "eu-1",
        )
        assert r.status_code == 200, r.text
        r = put(uk, {**base, "record_revision": 9}, "uk-1")
        assert r.status_code == 409 and code(r) == "MIGRATION_EPOCH_CONFLICT"
        assert g.get("/v1/discovery", params={"tenant_id": o.tenant}).json()["cell"] == "eu-1"

        # --- the cell fences stale routing (DEC-G3) -----------------------------------------
        h = o.bearer(o.owner)
        assert (
            cell.get("/v1/tenant", headers={**h, "X-Zoiko-Placement-Epoch": "1"}).status_code == 200
        )
        r = cell.get("/v1/tenant", headers={**h, "X-Zoiko-Placement-Epoch": "0"})
        assert r.status_code == 421 and code(r) == "CELL_ROUTING_STALE"
        assert (
            r.headers["location"] == ENDPOINTS["uk-1"]
            and r.headers["x-zoiko-placement-epoch"] == "1"
        )

    # --- a global-plane outage never affects the cell (RES-06) ------------------------------------
    async def down(_: str) -> tuple[int, str]:
        return 0, "LOCATOR_UNREACHABLE"

    async def publish_while_down() -> dict[str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": o.tenant})
            await c.execute(text("DELETE FROM platform.directory_publication"))
            return await directory.publish_pass(
                c, cell_id="uk-1", ring=KeyRing(uk), send=down, now=datetime.now(UTC)
            )

    assert asyncio.run(publish_while_down())[o.tenant] == "LOCATOR_UNREACHABLE"
    with TestClient(cell_app) as cell:
        assert cell.get("/v1/tenant", headers=o.bearer(o.owner)).status_code == 200
