"""Direct operator mode: what the capability check gives up, and what it never does."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from zid_platform.modules.operator_access.application import service
from zid_platform.modules.operator_access.domain.model import CAPABILITIES


@pytest.fixture(autouse=True)
def governed_after() -> Any:
    yield
    service.configure_direct_mode(False)  # never leak the switch into other tests


RECORDED: list[tuple[Any, ...]] = []


def check(name: str, tenant: str | None = None) -> service.CapabilityCheck:
    return asyncio.run(
        service.check_capability(
            object(),  # type: ignore[arg-type]
            operator_principal_id="op-1",
            capability_name=name,
            tenant_id=tenant,
            resource="commercial:catalogue",
            now=datetime.now(UTC),
        )
    )


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    async def profile(conn: Any, principal: str) -> Any:
        return SimpleNamespace(active=True, tenant_id="operator-tenant")

    async def secure(conn: Any, tenant: str, principal: str) -> bool:
        return True

    async def no_grants(conn: Any, principal: str, capability: str, now: datetime) -> list[Any]:
        return []

    async def record(*args: Any) -> None:
        RECORDED.append(args)

    RECORDED.clear()
    monkeypatch.setattr(service.store, "load_profile", profile)
    monkeypatch.setattr(service, "_security_ok", secure)
    monkeypatch.setattr(service.store, "active_grants_for", no_grants)
    monkeypatch.setattr(service, "_record_direct_use", record)


def test_governed_mode_still_needs_a_grant(operator: None) -> None:
    out = check("commercial.catalog.manage")
    assert not out.allowed and out.reason == "NO_ACTIVE_GRANT"


def test_direct_mode_gives_a_registered_operator_every_non_customer_data_capability(
    operator: None,
) -> None:
    service.configure_direct_mode(True)
    assert service.direct_mode()
    non_customer = [n for n, c in CAPABILITIES.items() if not c.customer_data]
    assert "commercial.catalog.manage" in non_customer
    assert all(check(n).allowed for n in non_customer)
    assert len(RECORDED) == len(non_customer)  # every use is recorded: nobody else approves it


def test_direct_mode_never_opens_what_touches_customer_data(operator: None) -> None:
    service.configure_direct_mode(True)
    customer_data = [n for n, c in CAPABILITIES.items() if c.customer_data]
    assert customer_data  # the catalogue has some
    assert all(
        not check(n, tenant="t-1").allowed for n in customer_data
    )  # the customer's lockbox stays in force
    assert RECORDED == []


def test_direct_mode_still_needs_a_registered_active_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nobody(conn: Any, principal: str) -> None:
        return None

    monkeypatch.setattr(service.store, "load_profile", nobody)
    service.configure_direct_mode(True)
    out = check("commercial.catalog.manage")
    assert not out.allowed and out.reason == "OPERATOR_PROFILE_MISSING"


def test_a_deployed_cell_refuses_direct_mode_without_the_explicit_acknowledgement() -> None:
    ack = service.DIRECT_MODE_ACK
    for environment in ("production", "sandbox", "preprod"):
        with pytest.raises(RuntimeError, match="ZID_OPERATOR_DIRECT_ACK"):
            service.require_direct_mode_ack("direct", environment, None)
        with pytest.raises(RuntimeError, match="ZID_OPERATOR_DIRECT_ACK"):
            service.require_direct_mode_ack("direct", environment, "yes")
        service.require_direct_mode_ack(
            "direct", environment, ack
        )  # said in so many words: allowed
        service.require_direct_mode_ack("governed", environment, None)  # governed needs nothing
    for environment in ("local", "test", "dev"):
        service.require_direct_mode_ack("direct", environment, None)
    assert ack == "accept-no-dual-control"
