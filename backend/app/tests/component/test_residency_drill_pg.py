"""Residency drill (ZID-EDR-002 section 6): wrong-cell tokens, rogue webhooks, stale placement epochs and disallowed
support access, injected together against one company, all refused the same way with no side effects, and the one
refusal that is evidence (support access, a restricted decision) is audited.

The wrong-cell and stale-epoch refusals write nothing for the company on purpose: the refusing cell is not its home,
and a company's records are never written outside its region.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_crypto import LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.tenant.domain.residency import ResidencyError
from zid_platform.settings import Settings

ENDPOINTS = {"uk-1": "https://uk-1.api.id.zoiko.test", "eu-1": "https://eu-1.api.id.zoiko.test"}


def _pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def test_wrong_cell_rogue_webhook_stale_epoch_and_foreign_support_access_are_all_refused(
    rt_url: str,
) -> None:
    o = onboard(rt_url)  # a company homed on uk-1, epoch 1

    def cell(cell_id: str) -> Any:
        return create_app(
            Settings(
                database_url=rt_url,
                token_issuer=ISS,
                token_audience=AUD,
                public_base_url=AUD,
                environment="test",
                cell_id=cell_id,
                cell_endpoints=ENDPOINTS,
            ),
            keyring=o.ring,
        )

    async def place_and_count() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await residency.set_residency(
                c, o.tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[],
                prohibited_cells=[], operator_juris=[], key_profile="PLATFORM_MANAGED",
                jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=o.owner,
            )  # fmt: skip
            return await audit_count(c)

    async def audit_count(c: Any, event_type: str | None = None, outcome: str | None = None) -> int:
        sql = "SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t"
        params: dict[str, Any] = {"t": o.tenant}
        if event_type:
            sql += " AND event_type = :e"
            params["e"] = event_type
        if outcome:
            sql += " AND outcome = :o"
            params["o"] = outcome
        return int((await c.execute(text(sql), params)).scalar_one())

    async def counts() -> tuple[int, int, int]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            return (
                await audit_count(c),
                await audit_count(c, "cross_cell_transfer.denied", "DENIED"),
                await audit_count(c, "cross_cell_transfer.authorized"),
            )

    asyncio.run(place_and_count())
    total_before, denied_before, authorized_before = asyncio.run(counts())

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    forged = sign_jws(
        LocalSigner("rogue-provider"),
        {"iss": "beta", "iat": 1, "event_id": str(uuid.uuid4()), "transaction_ref": "x"},
        typ="verification-result+jwt",
    ).encode()
    bearer = o.bearer(o.owner)

    with TestClient(cell("uk-1")) as home, TestClient(cell("eu-1")) as elsewhere:
        assert home.get("/v1/tenant", headers=bearer).status_code == 200  # the right cell serves it

        # 1. a valid token for this company presented at the wrong cell
        r = elsewhere.get("/v1/tenant", headers=bearer)
        assert r.status_code == 421 and code(r) == "CELL_NOT_AUTHORITATIVE"
        assert r.headers["location"] == ENDPOINTS["uk-1"]  # pointed home, nothing served

        # 2. a stale placement epoch at the right cell
        r = home.get("/v1/tenant", headers={**bearer, "X-Zoiko-Placement-Epoch": "0"})
        assert r.status_code == 421 and code(r) == "CELL_ROUTING_STALE"
        assert r.headers["x-zoiko-placement-epoch"] == "1"

        # 3. rogue webhooks: a result signed by an unknown provider, and one addressed to another company
        for cell_client in (home, elsewhere):
            for tenant in (o.tenant, str(uuid.uuid4())):
                r = cell_client.post(
                    f"/verification-provider-callbacks/{tenant}.beta", content=forged
                )
                assert r.status_code == 401, (tenant, r.status_code)

    # 4. support staff reading the company's data from outside its region: refused, and the refusals are evidence
    async def foreign_support() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for where in ("US", "DE"):
                with pytest.raises(ResidencyError, match="REMOTE_ACCESS_TRANSFER_REQUIRED"):
                    await residency.authorize_remote_access(c, o.tenant, where)

    asyncio.run(foreign_support())

    total_after, denied_after, authorized_after = asyncio.run(counts())
    assert (
        denied_after - denied_before == 2
    )  # one audited denial per foreign jurisdiction, E3-class
    assert authorized_after == authorized_before  # nothing was ever authorized
    # the three request-level refusals added no tenant record: every new record is a support-access denial
    assert total_after - total_before == denied_after - denied_before
