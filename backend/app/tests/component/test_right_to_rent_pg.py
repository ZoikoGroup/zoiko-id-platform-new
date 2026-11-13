"""England Right to Rent from the platform's own records (ZID-RCG-003 section 10.2; T06, T27, T28, T29; CR-084): the provider's standing comes from
official-register attestations, its entitlement and the kill switch, never from a flag someone sets."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from conftest import engine
from zid_platform.modules.verification.application import right_to_rent as svc
from zid_platform.modules.verification.application import suspensions
from zid_platform.modules.verification.domain import right_to_rent as rtr
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.attestation import Scope, Snapshot
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.domain.entitlement import Grant, Status
from zid_platform.modules.verification.domain.entitlement import Scope as EntScope
from zid_platform.modules.verification.infrastructure import attestation_store
from zid_platform.modules.verification.infrastructure import (
    contract_entitlement_store as entitlement_store,
)

NOW = datetime.now(UTC)


def _snap(
    provider: str,
    scheme: str,
    service: str = "RTR",
    status: CertStatus = CertStatus.ACTIVE,
    ago: timedelta = timedelta(hours=2),
) -> Snapshot:
    return Snapshot(
        snapshot_id=f"s-{uuid.uuid4().hex[:12]}",
        scope=Scope(provider, scheme, "GB", service),
        scheme_version="4.0",
        certificate_ref="C-1",
        component_only=False,
        status=status,
        valid_from=date(2025, 1, 1),
        valid_until=None,
        register_url="https://www.access-dvs-register.service.gov.uk/entry/1",
        register_entry_id="E-1",
        cab="CAB-1",
        fetched_at=NOW - ago,
        proof_hash="9" * 64,
        evidence_locator="vault:dvs/x",
        recorded_by="ops",
        recorded_at=NOW - ago,
    )


async def _good_provider(c: Any, provider: str, *, certified_for: str = "RTR") -> None:
    await attestation_store.record(
        c, _snap(provider, svc.CERTIFICATION_SCHEME, certified_for), now=NOW
    )
    await attestation_store.record(c, _snap(provider, svc.REGISTER_SCHEME), now=NOW)
    await attestation_store.record(c, _snap(provider, svc.SUPPLEMENTARY_SCHEME), now=NOW)
    await entitlement_store.record(
        c,
        Grant(
            grant_id=f"g-{uuid.uuid4().hex[:10]}",
            scope=EntScope(provider, "GB", svc.CAPABILITY, "STANDARD"),
            status=Status.GRANTED,
            effective_at=NOW - timedelta(days=1),
            valid_from=NOW - timedelta(days=30),
            valid_until=None,
            contract_ref="CONTRACT-RTR-1",
            terms_ref="TERMS-RTR-1",
            recorded_by="vera",
            recorded_at=NOW - timedelta(days=1),
        ),
        now=NOW,
    )


def _decide(
    rt_url: str,
    provider: str,
    *,
    subdivision: str = "GB-ENG",
    route: rtr.Route = rtr.Route.DIGITAL_IDSP,
) -> rtr.Decision:
    async def go() -> rtr.Decision:
        async with engine(rt_url) as eng, eng.begin() as c:
            return await svc.decide_for(
                c,
                property_country="GB",
                property_subdivision=subdivision,
                route=route,
                provider=provider,
                at=NOW,
            )

    return asyncio.run(go())


def _run(rt_url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def test_a_provider_with_every_record_in_place_may_produce_evidence_and_t27_scotland_needs_none(
    rt_url: str,
) -> None:
    provider = f"idsp-{uuid.uuid4().hex[:8]}"
    _run(rt_url, lambda c: _good_provider(c, provider))
    d = _decide(rt_url, provider)
    assert (d.outcome, d.zoiko_evidence) == (rtr.Outcome.ROUTE_AVAILABLE, True), d.reasons
    scot = _decide(rt_url, provider, subdivision="GB-SCT")
    assert (scot.outcome, scot.zoiko_evidence) == (rtr.Outcome.NOT_APPLICABLE, False)


def test_t28_certified_only_for_identity_verification_is_blocked_for_right_to_rent(
    rt_url: str,
) -> None:
    provider = f"idsp-{uuid.uuid4().hex[:8]}"
    _run(rt_url, lambda c: _good_provider(c, provider, certified_for="IDV"))
    d = _decide(rt_url, provider)
    assert d.outcome is rtr.Outcome.BLOCKED and "PROVIDER_NOT_CERTIFIED_FOR_RTR" in d.reasons


def test_t06_a_withdrawn_supplementary_code_blocks_new_invocations(rt_url: str) -> None:
    provider = f"idsp-{uuid.uuid4().hex[:8]}"
    _run(rt_url, lambda c: _good_provider(c, provider))
    _run(
        rt_url,
        lambda c: attestation_store.record(
            c,
            _snap(
                provider,
                svc.SUPPLEMENTARY_SCHEME,
                status=CertStatus.EXPIRED,
                ago=timedelta(minutes=5),
            ),
            now=NOW,
        ),
    )
    d = _decide(rt_url, provider)
    assert d.outcome is rtr.Outcome.BLOCKED and d.reasons == ("RTR_SUPPLEMENTARY_CODE_EXPIRED",)


def test_a_suspension_of_the_providers_rtr_service_blocks_it(rt_url: str) -> None:
    provider = f"idsp-{uuid.uuid4().hex[:8]}"
    _run(rt_url, lambda c: _good_provider(c, provider))
    s = sp.Suspension(
        suspension_id=f"rtr-{uuid.uuid4().hex[:8]}",
        version=1,
        scope=sp.Scope(provider, service_id="RTR", country="GB"),
        trigger=sp.Trigger.CERTIFICATION_WITHDRAWN,
        status=sp.Status.ACTIVE,
        reason="the register withdrew the RTR listing",
        evidence_ref="INC-11",
        effective_at=NOW,
        recorded_by="ana",
        recorded_at=NOW,
    )
    _run(rt_url, lambda c: suspensions.apply_suspension(c, s, now=NOW))
    assert "PROVIDER_SCOPE_SUSPENDED" in _decide(rt_url, provider).reasons


def test_t29_the_share_code_route_is_the_home_office_service_whatever_the_provider_holds(
    rt_url: str,
) -> None:
    provider = f"idsp-{uuid.uuid4().hex[:8]}"
    _run(rt_url, lambda c: _good_provider(c, provider))
    d = _decide(rt_url, provider, route=rtr.Route.HOME_OFFICE_SHARE_CODE)
    assert (d.outcome, d.zoiko_evidence) == (rtr.Outcome.PRESCRIBED_ROUTE, False)
    assert "NO_SUBSTITUTED_BIOMETRIC_APPROVAL" in d.reasons
