"""A verification records the regulatory basis it started under (ZID-RCG-003 section 09.1 RegulatoryDecision; CR-082).

Through the real POST /verifications: the governing pack (id, version, hash), the switch modes, the provider and the privacy route are written on the
session, once. The service behind the gates is replaced by one that only creates the session, so no provider is involved. The table refuses a later edit.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_policy_gate_http_pg import AUD, ISS, _registry
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import _draft
from test_privacy_overlay_http_pg import _overlay, _profile
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform import country_gate
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.api import verification as verification_api
from zid_platform.modules.verification.application import service as verification_service
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE, pack_hash
from zid_platform.modules.verification.domain.verification import SessionStatus
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.settings import Settings


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def world(rt_url: str) -> Iterator[dict[str, Any]]:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    profiles: dict[str, str] = {}

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            from zid_platform.modules.principal.infrastructure.principal_store import (
                create_principal,
            )

            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c, tenant, [admin], ["verification.profile.manage"], ["verification_profile:*"]
            )
            profiles["document"] = await _profile(c, tenant, f"doc-{uid()[:6]}", [])

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )

    async def create_only(
        engine_: Any,
        *,
        ctx: Any,
        profile_id: str,
        subject_principal_id: str,
        purpose: str,
        jurisdiction: str,
        now: datetime,
        **_: Any,
    ) -> Any:
        s = store.VSession(
            session_id=uid(),
            tenant_id=ctx.tenant_id,
            profile_id=profile_id,
            subject_principal_id=subject_principal_id,
            purpose=purpose,
            jurisdiction=jurisdiction,
            status=SessionStatus.CREATED,
            adapter_id=None,
            provider_transaction_ref=None,
            attempted_adapters=(),
            outcome_code=None,
            pending_result=None,
            row_version=1,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        async with tenant_transaction(engine_, ctx) as conn:
            await store.create_session(conn, s)
        return verification_service.Started(s.session_id, "CREATED", None, None)

    mp = pytest.MonkeyPatch()
    mp.setattr(country_gate, "registry", _registry)
    mp.setattr(verification_api, "registry", _registry)
    mp.setattr(
        verification_api,
        "privacy_overlays",
        lambda: {"AU": _overlay(country="AU", biometrics=po.Biometrics.ALLOWED)},
    )
    mp.setattr(verification_service, "start", create_only)
    token = issue_access_token(
        ring,
        issuer=ISS,
        audience=AUD,
        subject=admin,
        tenant_id=tenant,
        client_id="onboarding-svc",
        now=datetime.now(UTC),
        scope=frozenset({"verification:execute", "verification:read"}),
    )
    try:
        with TestClient(app, base_url=ISS) as http:
            yield {
                "http": http,
                "app": app,
                "headers": {"Authorization": f"Bearer {token}"},
                "tenant": tenant,
                "profiles": profiles,
            }
    finally:
        mp.undo()


def _basis(rt_url: str, tenant: str, session_id: str) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await store.regulatory_basis(c, session_id)

    return asyncio.run(go())


def test_a_start_records_the_pack_the_modes_the_provider_and_the_privacy_route(
    rt_url: str, world: dict[str, Any]
) -> None:
    pack = _draft(
        country="AU",
        product="INDIVIDUAL_VERIFICATION",
        purpose=PACK_PURPOSE,
        subdivision=None,
        profession=None,
    )

    async def publish() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _publish(c, pack)

    asyncio.run(publish())
    app = world["app"]
    (
        app.state.regulatory_packs,
        app.state.market_releases,
        app.state.provider_entitlements,
        app.state.privacy_overlay,
    ) = (
        "enforce",
        "shadow",
        "shadow",
        "enforce",
    )
    body = {
        "profile_id": world["profiles"]["document"],
        "subject_principal_id": uid(),
        "purpose": "onboarding",
        "jurisdiction": "AU",
    }
    r = world["http"].post("/verifications", headers=world["headers"], json=body)
    assert r.status_code == 201, r.text
    basis = _basis(rt_url, world["tenant"], r.json()["verification_id"])
    assert basis["pack"] == {"pack_id": pack.pack_id, "version": 1, "pack_hash": pack_hash(pack)}
    assert basis["modes"] == {"pack": "enforce", "release": "shadow", "entitlement": "shadow"}
    assert (basis["country"], basis["capability"], basis["provider"]) == (
        "AU",
        "INDIVIDUAL_VERIFICATION",
        "simulator",
    )
    assert (
        basis["market_outcome"] == "no_release" and basis["entitlement_outcome"] == "no_entitlement"
    )  # shadow: recorded, not refused
    assert basis["suspensions_checked"] is True
    assert basis["privacy"] == {
        "mode": "enforce",
        "allowed": True,
        "reasons": [],
        "region": "UK",
        "transfer_mechanism": None,
    }


def test_the_basis_is_written_once_and_the_table_refuses_a_later_edit(
    rt_url: str, world: dict[str, Any]
) -> None:
    app = world["app"]
    app.state.regulatory_packs, app.state.privacy_overlay = "shadow", "shadow"
    body = {
        "profile_id": world["profiles"]["document"],
        "subject_principal_id": uid(),
        "purpose": "onboarding",
        "jurisdiction": "AU",
    }
    sid = (
        world["http"]
        .post("/verifications", headers=world["headers"], json=body)
        .json()["verification_id"]
    )
    first = _basis(rt_url, world["tenant"], sid)
    assert first is not None

    async def again() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(world["tenant"])) as c:
            await store.set_regulatory_basis(
                c, sid, {"pack": None}
            )  # a second write changes nothing

    asyncio.run(again())
    assert _basis(rt_url, world["tenant"], sid) == first

    async def edit() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(world["tenant"])) as c:
            await c.execute(
                text(
                    "UPDATE verify.verification_session SET regulatory_basis = '{}'::jsonb WHERE session_id = :s"
                ),
                {"s": uuid.UUID(sid)},
            )

    with pytest.raises(DBAPIError) as e:
        asyncio.run(edit())
    assert "written once" in str(e.value)


def test_a_country_where_no_regulatory_check_applies_records_nothing(
    rt_url: str, world: dict[str, Any]
) -> None:
    body = {
        "profile_id": world["profiles"]["document"],
        "subject_principal_id": uid(),
        "purpose": "onboarding",
        "jurisdiction": "NZ",
    }  # SANDBOX
    r = world["http"].post("/verifications", headers=world["headers"], json=body)
    if r.status_code == 201:  # the sandbox is open in the test environment
        assert _basis(rt_url, world["tenant"], r.json()["verification_id"]) is None
