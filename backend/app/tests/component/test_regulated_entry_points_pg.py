"""Every regulated entry point runs the regulatory checks (ZID-RCG-003; CR-067, CR-072, CR-074, CR-078), not only identity verification.

`enforce_regulated` (company verification, organization claims, v2 organizations, v2 credential cases) and `enforce_tenant` (a tenant's credential
issuance and enterprise activation) ask the country registry and then the pack, release, entitlement and kill-switch checks. A company signing up has
no tenant yet, so the platform records are read in a plain transaction. Account registration is not a regulated decision and keeps the country check only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import ctx, engine
from zid_errors import ZidError
from zid_platform import country_gate
from zid_platform.modules.verification.application import suspensions as svc
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.countries import APPROVALS, Capability
from zid_platform.modules.verification.infrastructure.country_registry import parse


def _registry(provider: str) -> Any:
    enabled = {
        "status": "ENABLED",
        "provider": provider,
        "effective_from": "2026-01-01",
        "approvals": {a: f"ref-{a}" for a in APPROVALS},
    }
    caps = {
        c: enabled
        for c in ("BUSINESS_VERIFICATION", "CREDENTIAL_ISSUANCE", "ENTERPRISE_USE", "REGISTRATION")
    }
    return parse(
        {"version": "t", "countries": {"IS": {"name": "IS", "wave": 1, "capabilities": caps}}}
    )


def _request(eng: Any, *, packs: str = "enforce") -> Any:
    state = SimpleNamespace(
        engine=eng,
        environment="production",
        regulatory_packs=packs,
        market_releases="off",
        provider_entitlements="off",
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> str:
    provider = f"prov-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(country_gate, "registry", lambda: _registry(provider))
    return provider


def _call(
    rt_url: str, capability: Capability, *, tenant: str | None, packs: str = "enforce"
) -> str | None:
    async def go() -> str | None:
        async with engine(rt_url) as eng:
            try:
                await country_gate.enforce_regulated(
                    _request(eng, packs=packs), "IS", capability, ctx(tenant) if tenant else None
                )
            except ZidError as e:
                return e.code
            return None

    return asyncio.run(go())


def test_company_verification_with_no_approved_pack_is_refused_with_or_without_a_tenant(
    rt_url: str, registry: str
) -> None:
    assert (
        _call(rt_url, Capability.BUSINESS_VERIFICATION, tenant=str(uuid.uuid4()))
        == "REGULATORY_POLICY_UNAVAILABLE"
    )
    assert (
        _call(rt_url, Capability.BUSINESS_VERIFICATION, tenant=None)
        == "REGULATORY_POLICY_UNAVAILABLE"
    )  # sign-up: no tenant yet
    assert (
        _call(rt_url, Capability.BUSINESS_VERIFICATION, tenant=None, packs="shadow") is None
    )  # shadow only reports


def test_account_registration_is_not_a_regulated_decision(rt_url: str, registry: str) -> None:
    assert _call(rt_url, Capability.REGISTRATION, tenant=None) is None


def test_the_kill_switch_stops_credential_issuance_and_company_verification_too(
    rt_url: str, registry: str
) -> None:
    now = datetime.now(UTC)
    s = sp.Suspension(
        suspension_id=f"entry-{uuid.uuid4().hex[:8]}",
        version=1,
        scope=sp.Scope(registry, service_id="CREDENTIAL_ISSUANCE", country="IS"),
        trigger=sp.Trigger.MANUAL,
        status=sp.Status.ACTIVE,
        reason="issuance paused while the issuer key is rotated",
        evidence_ref="INC-9",
        effective_at=now,
        recorded_by="ana",
        recorded_at=now,
    )

    async def apply() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await svc.apply_suspension(c, s, now=now)

    asyncio.run(apply())
    assert (
        _call(rt_url, Capability.CREDENTIAL_ISSUANCE, tenant=str(uuid.uuid4()), packs="off")
        == "PROVIDER_SCOPE_SUSPENDED"
    )
    assert (
        _call(rt_url, Capability.BUSINESS_VERIFICATION, tenant=None, packs="off") is None
    )  # another service of the same provider is untouched


def test_a_tenants_own_regulated_action_asks_the_regulatory_checks(
    rt_url: str, registry: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def declared(_conn: Any, _tenant: str) -> Any:
        return SimpleNamespace(register_namespace="IS-RSK")

    monkeypatch.setattr(country_gate, "declared_registration", declared)
    monkeypatch.setitem(country_gate.NAMESPACE_COUNTRY, "IS-RSK", "IS")

    async def go() -> str | None:
        async with engine(rt_url) as eng:
            try:
                await country_gate.enforce_tenant(
                    _request(eng), None, str(uuid.uuid4()), Capability.ENTERPRISE_USE
                )  # type: ignore[arg-type]
            except ZidError as e:
                return e.code
            return None

    assert asyncio.run(go()) == "REGULATORY_POLICY_UNAVAILABLE"
