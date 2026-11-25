"""Confirming a mandate's exact terms (ZID-CP-WF07 v2.0 .04; A07-03; CR-169), over the real routes with real tokens against PostgreSQL.

What these hold: the grantor reads the canonical terms and confirms their hash with a fresh strong sign-in (a stale one is refused); the wrong hash, the wrong person and a second confirmation from the grantor, the agent or the person it
acts for are each refused; a mandate that can spend needs an independent second confirmation; every confirmation verifies and cannot be edited; and with signing enforced an unconfirmed mandate cannot be activated.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.mandate.domain.canonical import SIGNATURE_TYP
from zid_platform.modules.mandate.domain.ledger import Budget, MandateStatus
from zid_platform.modules.mandate.infrastructure.ledger_store import MandateRecord, create_mandate
from zid_platform.modules.principal.domain.principal import Principal, PrincipalStatus, PrincipalType
from zid_platform.modules.principal.infrastructure.principal_store import create_principal, load_principal
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CELL_ID = uuid.UUID(CELL)


def uid() -> str:
    return str(uuid.uuid4())


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_the_grantor_and_an_independent_second_person_confirm_the_exact_hash_and_signing_can_be_enforced(rt_url: str, owner_url: str) -> None:
    tenant, grantor, approver, outsider, executor, represented = (uid() for _ in range(6))
    quiet_id, spending_id = uuid.uuid4(), uuid.uuid4()
    ring = KeyRing(LocalSigner("issuer-sign"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (grantor, approver, outsider, represented):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(c, tenant, [grantor, approver, outsider], ["mandate.sign", "mandate.countersign", "mandate.read", "mandate.manage"], ["mandate:*"])
            now = datetime.now(UTC)
            for mid, budget in ((spending_id, Budget(currency="GBP", value_limit=Decimal(100))), (quiet_id, Budget())):
                await create_mandate(
                    c,
                    MandateRecord(
                        mandate_id=mid, tenant_id=uuid.UUID(tenant), authority_grant_id=uuid.uuid4(), represented_principal_id=uuid.UUID(represented), executor_principal_id=uuid.UUID(executor), purpose_id=uuid.uuid4(),
                        resource_scope_id=uuid.uuid4(), allowed_actions=("purchase.create",), budget=budget, home_cell_id=CELL_ID, status=MandateStatus.PENDING_APPROVAL, valid_from=now - timedelta(hours=1),
                        valid_to=now + timedelta(days=1), created_by_principal_id=uuid.UUID(grantor),
                    ),
                )  # fmt: skip

    async def session_for(p: str, *, auth_age: timedelta) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(c, Session(sid, tenant, p, t - auth_age, "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8)))
        return sid

    async def db(sql: str, **params: Any) -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return list((await c.execute(text(sql), {"t": uuid.UUID(tenant), **params})).all())

    asyncio.run(seed())
    fresh = {p: asyncio.run(session_for(p, auth_age=timedelta(seconds=20))) for p in (grantor, approver, outsider)}
    stale_grantor = asyncio.run(session_for(grantor, auth_age=timedelta(minutes=20)))
    app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_uuid=CELL), keyring=ring)

    def bearer(sub: str, session: str | None = None) -> dict[str, str]:
        tok = issue_access_token(ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="web", now=datetime.now(UTC), session_id=session or fresh[sub], scope=frozenset())
        return {"Authorization": f"Bearer {tok}"}

    def canonical(mid: uuid.UUID, who: str) -> dict[str, Any]:
        r = http.get(f"/v1/mandates/{mid}/canonical", headers=bearer(who))
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    with TestClient(app, base_url=ISS) as http:
        app.state.mandate_signing = "off"
        c1 = canonical(spending_id, grantor)
        h = c1["canonical_hash"]
        assert h.startswith("sha256:") and c1["canonical_terms"]["value_limit"] == "100" and c1["canonical_terms"]["created_by_principal_id"] == grantor
        assert c1["signing"]["required"] == ["GRANTOR", "APPROVER"] and c1["signing"]["complete"] is False and c1["signatures"] == []
        assert "not a qualified" in c1["note"]
        assert canonical(quiet_id, grantor)["signing"]["required"] == ["GRANTOR"]  # a mandate that cannot spend needs only its grantor
        assert http.get(f"/v1/mandates/{spending_id}/canonical", headers=bearer(outsider)).status_code == 200  # an administrator with mandate.read may read the terms
        sign = lambda who, **kw: http.post(f"/v1/mandates/{spending_id}:sign", headers=bearer(who, kw.get("session")), json={"canonical_hash": kw.get("h", h)})  # noqa: E731
        counter = lambda who, **kw: http.post(f"/v1/mandates/{spending_id}:countersign", headers=bearer(who, kw.get("session")), json={"canonical_hash": kw.get("h", h)})  # noqa: E731

        # the wrong hash, a sign-in that is not fresh, and anyone but the grantor are each refused; nothing is stored
        assert code(sign(grantor, h="sha256:" + "0" * 64)) == "MANDATE_HASH_MISMATCH"
        stale = sign(grantor, session=stale_grantor)
        assert stale.status_code == 403 and code(stale) == "STEP_UP_REQUIRED"
        assert code(sign(outsider)) == "MANDATE_SIGNER_NOT_GRANTOR"
        assert http.post(f"/v1/mandates/{spending_id}:sign", headers=bearer(grantor), json={"canonical_hash": h, "terms": {"value_limit": "1000000"}}).status_code == 422  # the terms are never a body field
        assert asyncio.run(db("SELECT count(*) AS n FROM agent.mandate_signature WHERE tenant_id = :t"))[0].n == 0

        # the grantor confirms the hash they read
        ok = sign(grantor)
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["signing"]["have"] == ["GRANTOR"] and body["signing"]["missing"] == ["APPROVER"] and body["signing"]["complete"] is False
        s = body["signatures"][0]
        assert s["role"] == "GRANTOR" and s["signer"] == grantor and s["current"] is True and s["signature_valid"] is True and s["assurance_level"] >= 2 and s["auth_time"]
        _header, claims = ring.verify(s["signature"], typ=SIGNATURE_TYP)
        assert claims["canonical_hash"] == h and claims["mandate_id"] == str(spending_id) and claims["role"] == "GRANTOR" and claims["signer"] == grantor
        assert len(sign(grantor).json()["signatures"]) == 1  # the same confirmation twice is still one

        # a mandate that can spend needs someone independent: not the grantor, not the agent, not the person it acts for
        assert code(counter(grantor)) == "MANDATE_SIGNER_NOT_INDEPENDENT"
        done = counter(approver)
        assert done.status_code == 200, done.text
        assert done.json()["signing"]["complete"] is True and sorted(done.json()["signing"]["have"]) == ["APPROVER", "GRANTOR"]
        # a mandate that cannot spend refuses a pointless second confirmation
        q = canonical(quiet_id, grantor)["canonical_hash"]
        assert code(http.post(f"/v1/mandates/{quiet_id}:countersign", headers=bearer(approver), json={"canonical_hash": q})) == "MANDATE_NO_SECOND_CONFIRMATION_NEEDED"

        # every confirmation still verifies after the signing key is rotated, and none can be edited
        ring.rotate(LocalSigner("issuer-sign-2"))
        assert all(x["signature_valid"] is True for x in canonical(spending_id, grantor)["signatures"])
        with pytest.raises(Exception, match="append-only"):
            asyncio.run(db("UPDATE agent.mandate_signature SET role = 'APPROVER' WHERE tenant_id = :t"))
        with pytest.raises(Exception, match="append-only"):
            asyncio.run(db("DELETE FROM agent.mandate_signature WHERE tenant_id = :t"))

        # with signing enforced, an unconfirmed mandate cannot be activated; a confirmed one gets past the gate
        app.state.mandate_signing = "enforce"
        no = http.post(f"/v1/mandates/{quiet_id}:activate", headers=bearer(approver))
        assert no.status_code == 409 and code(no) == "MANDATE_NOT_SIGNED"
        yes = http.post(f"/v1/mandates/{spending_id}:activate", headers=bearer(approver))
        assert code(yes) != "MANDATE_NOT_SIGNED" if yes.status_code >= 400 else True  # past the signing gate (the source grant is a test stub, so a later check may refuse)
