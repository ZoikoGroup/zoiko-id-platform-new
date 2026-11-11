"""The policy pack gate through the real HTTP routes (ZID-RCG-003 T01, T02): POST /verifications and the provider callback check.

A provider is never asked when the gate refuses, so the service call behind the gate is replaced by a stub that records whether it was reached.
The country registry is replaced by one with enabled countries, because none is enabled in the shipped configuration.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import _draft
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
from zid_platform.modules.verification.application import policy_packs as svc
from zid_platform.modules.verification.application import service as verification_service
from zid_platform.modules.verification.domain.countries import APPROVALS
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure.country_registry import parse
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CAP = "INDIVIDUAL_VERIFICATION"


def uid() -> str:
    return str(uuid.uuid4())


def _registry() -> Any:
    enabled = {
        "status": "ENABLED",
        "provider": "simulator",
        "effective_from": "2026-01-01",
        "approvals": {a: f"ref-{a}" for a in APPROVALS},
    }
    sandbox = {"status": "SANDBOX", "provider": "simulator"}

    def caps(entry: dict[str, Any]) -> dict[str, Any]:
        return {"capabilities": {CAP: entry}}

    return parse(
        {
            "version": "t",
            "countries": {
                "GB": {"name": "GB", "wave": 1, **caps(enabled)},
                "US": {"name": "US", "wave": 1, **caps(enabled)},
                "CA": {"name": "CA", "wave": 1, **caps(enabled)},
                "AU": {"name": "AU", "wave": 1, **caps(enabled)},
                "NZ": {"name": "NZ", "wave": 1, **caps(sandbox)},
            },
        }
    )


class World:
    def __init__(
        self, http: TestClient, app: Any, headers: dict[str, str], reached: list[str], rt_url: str
    ) -> None:
        self.http, self.app, self.headers, self.reached, self.rt_url = (
            http,
            app,
            headers,
            reached,
            rt_url,
        )

    def start(self, country: str, mode: str) -> Any:
        self.app.state.regulatory_packs = mode
        body = {
            "profile_id": uid(),
            "subject_principal_id": uid(),
            "purpose": "onboarding",
            "jurisdiction": country,
        }
        return self.http.post("/verifications", headers=self.headers, json=body)

    def publish_pack(self, country: str, **key: Any) -> Any:
        market: dict[str, Any] = {"subdivision": None, "profession": None} | key
        pack = _draft(country=country, product=CAP, purpose=PACK_PURPOSE, **market)

        async def go() -> None:
            async with engine(self.rt_url) as eng, eng.begin() as c:
                await _publish(c, pack)

        asyncio.run(go())
        return pack

    def retire(self, pack: Any) -> None:
        async def go() -> None:
            async with engine(self.rt_url) as eng, eng.begin() as c:
                await svc.retire_pack(c, pack.pack_id, 1, now=datetime(2026, 11, 10, tzinfo=UTC))

        asyncio.run(go())


@pytest.fixture(scope="module")
def world(rt_url: str) -> Iterator[World]:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))

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
    reached: list[str] = []

    async def stub_start(*args: Any, **kw: Any) -> Any:
        reached.append("provider side reached")
        raise VerificationError(
            "STUBBED_START"
        )  # ends the request after the gate, so no provider, profile or session is needed

    mp = pytest.MonkeyPatch()
    mp.setattr(country_gate, "registry", _registry)
    mp.setattr(verification_service, "start", stub_start)
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
            yield World(http, app, {"Authorization": f"Bearer {token}"}, reached, rt_url)
    finally:
        mp.undo()


def _code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_t01_an_enabled_country_without_an_approved_pack_is_refused_before_any_provider_is_asked(
    world: World,
) -> None:
    before = len(world.reached)
    r = world.start("GB", "enforce")
    assert (r.status_code, _code(r)) == (422, "REGULATORY_POLICY_UNAVAILABLE")
    assert len(world.reached) == before  # the provider side was never reached


def test_an_approved_country_pack_opens_the_route_and_retiring_it_closes_it_again(
    world: World,
) -> None:
    pack = world.publish_pack("AU")
    before = len(world.reached)
    r = world.start("AU", "enforce")
    assert (r.status_code, _code(r)) == (
        409,
        "STUBBED_START",
    )  # past the gate, stopped by the stub behind it
    assert len(world.reached) == before + 1
    world.retire(pack)
    r = world.start("AU", "enforce")
    assert (r.status_code, _code(r)) == (422, "REGULATORY_POLICY_UNAVAILABLE")
    assert len(world.reached) == before + 1


def test_shadow_goes_on_and_logs_what_would_have_been_refused(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    before = len(world.reached)
    with caplog.at_level(logging.WARNING, logger="zid.regulatory"):
        r = world.start("US", "shadow")
    assert (r.status_code, _code(r)) == (409, "STUBBED_START")
    assert len(world.reached) == before + 1
    assert any("would refuse (shadow)" in rec.getMessage() for rec in caplog.records)


def test_the_development_sandbox_needs_no_pack_and_a_closed_country_is_still_refused_first(
    world: World,
) -> None:
    before = len(world.reached)
    assert (world.start("NZ", "enforce").status_code, len(world.reached)) == (409, before + 1)
    r = world.start(
        "ZZ", "enforce"
    )  # not in the registry: the existing country gate answers, not the pack gate
    assert (r.status_code, _code(r)) == (422, "COUNTRY_CAPABILITY_UNAVAILABLE")
    assert len(world.reached) == before + 1


def test_t02_a_country_with_only_a_profession_pack_needs_a_review(world: World) -> None:
    world.publish_pack("CA", profession="nurse")
    r = world.start("CA", "enforce")
    assert (r.status_code, _code(r)) == (422, "REGULATORY_REVIEW_REQUIRED")


# ---- the provider callback: a result is held, never applied, when the pack is gone ----------------------------------------


def _stub_request(eng: Any, mode: str) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(engine=eng, environment="production", regulatory_packs=mode)
        )
    )


def _callback(rt_url: str, country: str, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(country_gate, "registry", _registry)

    async def go() -> None:
        async with engine(rt_url) as eng:
            await verification_api._callback_check(_stub_request(eng, mode), uid(), country)

    asyncio.run(go())


def test_a_callback_for_a_market_with_no_approved_pack_is_held_so_the_provider_retries(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(VerificationError) as e:
        _callback(rt_url, "GB", "enforce", monkeypatch)
    assert e.value.code == "REGULATORY_POLICY_UNAVAILABLE"
    assert (
        verification_api._STATUS["REGULATORY_POLICY_UNAVAILABLE"] == 503
    )  # the provider retries, as for a closed country
    assert verification_api._STATUS["REGULATORY_REVIEW_REQUIRED"] == 503


def test_a_callback_goes_on_in_shadow_and_with_an_approved_pack_and_a_closed_country_is_still_held(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _callback(rt_url, "GB", "shadow", monkeypatch)  # only logged
    _draft_country = _draft(
        country="GB", product=CAP, purpose=PACK_PURPOSE, subdivision=None, profession=None
    )

    async def publish() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _publish(c, _draft_country)

    asyncio.run(publish())
    _callback(rt_url, "GB", "enforce", monkeypatch)  # an approved pack governs: no error
    with pytest.raises(VerificationError) as e:
        _callback(rt_url, "ZZ", "enforce", monkeypatch)
    assert e.value.code == "COUNTRY_CAPABILITY_UNAVAILABLE"
