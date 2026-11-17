"""The country privacy overlay through POST /verifications (ZID-RCG-003 RCG-G09; T31, T32; CR-079).

The overlay is checked after the country and pack gates and before any provider is asked. The service behind the gates is a stub that records
whether it was reached, as in test_policy_gate_http_pg. Real profiles are stored: one that uses biometrics in GB and one that does not, so a refused
biometric route can name a real alternate. Consent is read through the privacy module's own check, which denies by default.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from pack_test_support import captured
from test_policy_gate_http_pg import AUD, ISS, _registry
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
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.settings import Settings


def uid() -> str:
    return str(uuid.uuid4())


def _overlay(**over: Any) -> po.Overlay:
    o = po.Overlay(
        country="GB",
        version=1,
        home_regions=frozenset({"UK"}),
        transfer_regions={"EU": "UK_ADEQUACY_REGULATIONS"},
        provider_regions={"simulator": "UK"},
        biometrics=po.Biometrics.PROHIBITED,
    )
    o = replace(o, **over)
    digest = po.overlay_hash(o)
    return replace(o, approvals={r: po.Approval(f"REF-{r}", digest) for r in po.APPROVER_ROLES})


class World:
    def __init__(
        self,
        http: TestClient,
        app: Any,
        headers: dict[str, str],
        reached: list[str],
        profiles: dict[str, str],
    ) -> None:
        self.http, self.app, self.headers, self.reached, self.profiles = (
            http,
            app,
            headers,
            reached,
            profiles,
        )
        self.overlays: dict[str, po.Overlay] = {}

    def start(self, profile: str, mode: str = "enforce", country: str = "GB") -> Any:
        self.app.state.regulatory_packs = "off"  # this test is about the privacy overlay only
        self.app.state.market_releases = "off"
        self.app.state.provider_entitlements = "off"
        self.app.state.privacy_overlay = mode
        body = {
            "profile_id": self.profiles[profile],
            "subject_principal_id": uid(),
            "purpose": "onboarding",
            "jurisdiction": country,
        }
        return self.http.post("/verifications", headers=self.headers, json=body)


async def _profile(c: Any, tenant: str, name: str, biometric: list[str]) -> str:
    pid, _ = await store.create_profile(
        c,
        tenant_id=tenant,
        name=name,
        level="IAL-Z1",
        requirements={
            "required_claims": ["legal_name"],
            "accepted_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"]
            if biometric
            else ["GOVERNMENT_ID"],
            "jurisdictions": ["GB", "US"],
            "biometric_jurisdictions": biometric,
        },
    )
    await store.activate_profile(c, await store.load_profile(c, pid), datetime.now(UTC))
    return pid


@pytest.fixture(scope="module")
def world(rt_url: str) -> Iterator[World]:
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
            profiles["biometric"] = await _profile(c, tenant, f"bio-{uid()[:6]}", ["GB"])
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
    reached: list[str] = []

    async def stub_start(*args: Any, **kw: Any) -> Any:
        reached.append("provider side reached")
        raise VerificationError("STUBBED_START")

    w_holder: dict[str, World] = {}
    mp = pytest.MonkeyPatch()
    mp.setattr(country_gate, "registry", _registry)
    mp.setattr(verification_api, "registry", _registry)
    mp.setattr(verification_api, "privacy_overlays", lambda: w_holder["w"].overlays)
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
            w_holder["w"] = World(
                http, app, {"Authorization": f"Bearer {token}"}, reached, profiles
            )
            yield w_holder["w"]
    finally:
        mp.undo()


def _code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_no_overlay_refuses_in_enforce_and_only_reports_in_shadow(world: World) -> None:
    world.overlays = {}
    before = len(world.reached)
    r = world.start("document")
    assert (r.status_code, _code(r)) == (422, "PRIVACY_OVERLAY_UNAVAILABLE") and len(
        world.reached
    ) == before
    with captured("zid.regulatory") as logs:
        r = world.start("document", mode="shadow")
    assert _code(r) == "STUBBED_START" and len(world.reached) == before + 1
    assert any(
        "privacy overlay check would refuse (shadow)" in m and "PRIVACY_OVERLAY_MISSING" in m
        for m in logs
    )


def test_a_home_region_route_without_biometrics_goes_on_to_the_provider(world: World) -> None:
    world.overlays = {"GB": _overlay()}
    before = len(world.reached)
    r = world.start("document")
    assert _code(r) == "STUBBED_START" and len(world.reached) == before + 1


def test_t32_a_provider_processing_outside_the_lawful_regions_is_never_asked(world: World) -> None:
    world.overlays = {"GB": _overlay(provider_regions={"simulator": "US"})}
    before = len(world.reached)
    r = world.start("document")
    assert (r.status_code, _code(r)) == (422, "CROSS_BORDER_ROUTE_DENIED") and len(
        world.reached
    ) == before
    world.overlays = {"GB": _overlay(provider_regions={"simulator": "EU"})}  # a lawful transfer
    assert _code(world.start("document")) == "STUBBED_START"


def test_t31_a_biometric_route_the_country_prohibits_is_refused_and_names_the_compliant_alternate(
    world: World,
) -> None:
    world.overlays = {"GB": _overlay(biometrics=po.Biometrics.PROHIBITED)}
    before = len(world.reached)
    r = world.start("biometric")
    assert (r.status_code, _code(r)) == (422, "BIOMETRIC_ROUTE_NOT_PERMITTED") and len(
        world.reached
    ) == before
    alternates = [
        d["issue"] for d in r.json()["error"]["details"] if d["field"] == "alternate_profile_id"
    ]
    assert alternates == [world.profiles["document"]]
    assert _code(world.start("document")) == "STUBBED_START"  # the alternate itself goes through


def test_where_consent_is_required_a_person_without_it_is_refused(
    world: World, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    world.overlays = {"GB": _overlay(biometrics=po.Biometrics.CONSENT_REQUIRED)}
    r = world.start("biometric")
    assert (
        _code(r) == "BIOMETRIC_ROUTE_NOT_PERMITTED"
    )  # the privacy module denies by default: no recorded consent
    world.overlays = {"GB": _overlay(biometrics=po.Biometrics.ALLOWED)}
    # CR-110: the country allows biometrics, but the provider's face match and liveness are not approved for this tenant (no method state, DPIA,
    # retention or evaluation): still refused, with the compliant alternate named
    assert _code(world.start("biometric")) == "BIOMETRIC_ROUTE_NOT_PERMITTED"
    from zid_platform.modules.verification.application import biometric_eligibility as bel
    from zid_platform.modules.verification.domain import biometric_methods as bmd

    async def all_open(conn, **kw):  # type: ignore[no-untyped-def]
        answers = tuple(
            bel.MethodAnswer(m, True, bmd.State.PRODUCTION_ELIGIBLE, (), False) for m in bmd.Method
        )
        return bel.Eligibility(kw["country"], kw["purpose"], None, answers, "0" * 64)

    monkeypatch.setattr(bel, "eligibility", all_open)
    assert _code(world.start("biometric")) == "STUBBED_START"


def test_an_overlay_edited_after_approval_counts_for_nothing(world: World) -> None:
    approved = _overlay()
    world.overlays = {
        "GB": replace(
            approved, provider_regions={"simulator": "US"}, transfer_regions={"US": "SCCS"}
        )
    }  # widened, not re-signed
    r = world.start("document")
    assert _code(r) == "PRIVACY_OVERLAY_UNAVAILABLE"


def test_a_country_that_is_not_enabled_is_left_to_the_country_gate(world: World) -> None:
    world.overlays = {}
    r = world.start(
        "document", country="NZ"
    )  # SANDBOX in this registry: not ENABLED, so no overlay is read
    assert _code(r) != "PRIVACY_OVERLAY_UNAVAILABLE"
