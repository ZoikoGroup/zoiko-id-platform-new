"""Phase F controls wired into the running platform (PRIV-01/13/17, TEN-10, EVID-24).

- data-map discovery finds a subject's data in every registered module; erasure disposes it
  through each owning module (principal tombstone, revoked authenticators and sessions,
  revoked assertions, cleared presentation claims) and crypto-erases the evidence vault;
- the processing boundary refuses unregistered purposes in enforce mode, evidences them in audit;
- a cell refuses tenants whose residency home is another cell;
- the outbox ingest turns evidence-bearing events into audit records effectively once.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import CloudEvent
from zid_platform.main import create_app, register_privacy_stores
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.evidence.ports import ingest_event
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.principal.domain.principal import Principal, PrincipalStatus, PrincipalType
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.tenant.application import residency
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def runtime() -> Any:
    configure_runtime(development_runtime("uk-1"))
    register_privacy_stores()
    yield
    configure_runtime(None)
    privacy.configure_enforcement(None)


def test_erasure_reaches_every_registered_module(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(c, Principal(subject, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await c.execute(text("UPDATE core.principal SET display_label = 'Jane Example' WHERE principal_id = :p"),
                            {"p": uuid.UUID(subject)})
            now = datetime.now(UTC)
            await create_session(c, Session(uid(), tenant, subject, now, "native:webauthn", 2, 0, 1,
                                            SessionStatus.ACTIVE, now + timedelta(hours=1), now))
            found = await privacy.discover_subject(c, tenant, subject)
            assert found >= 2
            stores = {e.store for e in await privacy.store.subject_entries(c, subject)}
            assert {"core.principal", "authn.session"} <= stores
            req = await privacy.open_request(
                c, tenant, request_type="ERASURE", subject_principal_id=subject, requester_principal_id=subject,
                jurisdiction_pack_id="uk-gdpr", authn_level=2, auth_age=timedelta(minutes=1),
            )
            r = await privacy.fulfil_request(c, tenant, str(req.request_id), actor=None)
            assert r.status == "COMPLETED", r.fulfilment_items
            row = (await c.execute(text("SELECT status::text AS s, display_label FROM core.principal WHERE principal_id = :p"),
                                   {"p": uuid.UUID(subject)})).one()
            assert row.s == "ERASED" and row.display_label is None
            sessions = (await c.execute(text("SELECT status::text FROM authn.session WHERE principal_id = :p"),
                                        {"p": uuid.UUID(subject)})).scalars().all()
            assert sessions == ["REVOKED"]

    asyncio.run(run())


def test_processing_boundary_modes(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def run() -> None:
        privacy.configure_enforcement("audit")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            d = await privacy.enforce_processing(c, tenant, activity_code="IDENTITY_VERIFICATION", purpose="kyc onboarding",
                                                 data_category="D3", subject_principal_id=subject)
            assert not d.allowed and d.reason == "PRIVACY_ACTIVITY_NOT_ACTIVE"
            kinds = (await c.execute(text("SELECT event_type, outcome FROM evidence.audit_record"))).all()
            assert ("privacy.processing.unregistered", "SUCCESS") in [(k.event_type, k.outcome) for k in kinds]
        privacy.configure_enforcement("enforce")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(ZidError) as refused:
                await privacy.enforce_processing(c, tenant, activity_code="IDENTITY_VERIFICATION", purpose="kyc onboarding",
                                                 data_category="D3", subject_principal_id=subject)
            assert refused.value.code == "PRIVACY_PROCESSING_NOT_PERMITTED"
        assert privacy.purpose_code("kyc onboarding") == "KYC_ONBOARDING"

    asyncio.run(run())


def test_non_home_cell_refuses_the_tenant(rt_url: str) -> None:
    """TEN-10: a request for a tenant placed in another cell is refused (421)."""
    tenant, user = uid(), uid()
    web = f"web-{uid()[:8]}"
    ring = KeyRing(LocalSigner("issuer-route"))

    async def seed() -> str:
        from zid_platform.modules.session.domain.oauth import OAuthClient
        from zid_platform.modules.session.infrastructure.oauth_store import create_client

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(c, Principal(user, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await create_client(c, OAuthClient(web, tenant, "PUBLIC", "STANDARD", ("https://app.example/cb",),
                                               frozenset({"authorization_code"}), frozenset({"openid"}), (AUD,),
                                               "none", False, "ACTIVE", first_party=True))
            sid, now = uid(), datetime.now(UTC)
            await create_session(c, Session(sid, tenant, user, now, "native:webauthn", 2, 0, 1,
                                            SessionStatus.ACTIVE, now + timedelta(hours=1), now))
            await residency.set_residency(c, tenant, profile="SRP-1", home_cell_id="eu-1", recovery_cells=[],
                                          prohibited_cells=[], operator_juris=[], key_profile="PLATFORM_MANAGED",
                                          jurisdiction_pack_id="eu-gdpr", approval_ref="LEGAL-9", actor=user)
            return sid

    sid = asyncio.run(seed())
    now = datetime.now(UTC)
    tok = issue_access_token(ring, issuer=ISS, audience=AUD, subject=user, tenant_id=tenant, client_id=web,
                             now=now, session_id=sid, auth_time=now)
    for cell, expected in (("uk-1", 421), ("eu-1", 200)):
        app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
                                  environment="test", cell_id=cell), keyring=ring)
        with TestClient(app) as api:
            r = api.get("/v1/privacy/purposes", headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == expected, (cell, r.text)
            if expected == 421:
                assert r.json()["error"]["code"] == "CELL_NOT_AUTHORITATIVE"


def test_outbox_ingest_is_effectively_once(rt_url: str) -> None:
    tenant = uid()
    event = CloudEvent(
        id=uid(), type="com.zoiko.id.authority.grant.revoked.v1", source="zid-platform", subject="grant/x",
        time=datetime.now(UTC), tenantid=tenant, cellid="uk-1", aggregatetype="authority_grant",
        aggregateid=uid(), aggregateversion=2,
        data={"grant_id": uid(), "evidence_class": "E3", "revoked_by": uid(), "reason_commitment": "sha-256:" + "a" * 64},
        correlationid="cor-1",
    )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await ingest_event(c, event)
            again = await ingest_event(c, event)
            assert first is not None and again is None
            body = (await c.execute(text("SELECT body FROM evidence.audit_record WHERE audit_id = :a"),
                                    {"a": uuid.UUID(first)})).scalar_one()
            assert body["event_type"] == "authority.grant.revoked" and json.dumps(body).count("revoked_by") == 0

    asyncio.run(run())
