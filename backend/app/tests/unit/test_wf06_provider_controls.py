"""WF06 licensed provider controls (CR-113): contract, region and budget gates, circuit breaker, signed callbacks (QA-037, QA-038, QA-041, QA-045 to QA-047)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.screening.domain import provider_controls as pc

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SECRET = b"provider-secret"


def provider(**kw):
    base = {
        "provider_id": "vendor-a",
        "state": pc.ProviderState.APPROVED,
        "classes": frozenset({"PEP_RCA", "ADVERSE_MEDIA"}),
        "markets": frozenset({"GB", "DE"}),
        "regions": frozenset({"eu-west"}),
        "contract_version": "c1",
        "price_version": "p1",
        "finance_authorised_by": "finance-1",
        "unit_cost": Decimal("0.40"),
        "monthly_budget": Decimal(100),
        "monthly_quota": 1000,
    }
    base.update(kw)
    return pc.Provider(**base)


def gate(p=None, **kw):
    args = {
        "klass": "PEP_RCA",
        "market": "GB",
        "region": "eu-west",
        "tenant_regions": ["eu-west"],
        "usage": pc.Usage(),
        "circuit": pc.Circuit(),
        "now": NOW,
    }
    args.update(kw)
    return pc.may_call(provider() if p is None else p, **args)


def test_an_approved_contracted_call_in_a_signed_region_is_allowed() -> None:
    assert gate() == pc.Gate(True, ())


@pytest.mark.parametrize(
    "p,kw,reason,status",
    [
        (None, {}, "PROVIDER_NOT_REGISTERED", 422),
        (provider(state=pc.ProviderState.DISABLED), {}, "PROVIDER_DISABLED", 422),  # QA-046
        (provider(state=pc.ProviderState.SANDBOX), {}, "PROVIDER_SANDBOX_ONLY", 422),
        (provider(finance_authorised_by=None), {}, "FINANCE_AUTHORISATION_MISSING", 422),
        (provider(price_version=None), {}, "FINANCE_AUTHORISATION_MISSING", 422),
        (
            provider(),
            {"klass": "SANCTIONS"},
            "CLASS_NOT_CONTRACTED",
            422,
        ),  # QA-046: the contract disallows the feed
        (provider(), {"market": "US"}, "MARKET_NOT_CONTRACTED", 422),
        (
            provider(),
            {"region": "us-east", "tenant_regions": ["us-east"]},
            "REGION_NOT_APPROVED",
            422,
        ),
        (
            provider(),
            {"tenant_regions": ["uk-south"]},
            "REGION_NOT_APPROVED",
            422,
        ),  # signed with the vendor, not for this tenant
        (provider(monthly_quota=5), {"usage": pc.Usage(Decimal(1), 5)}, "QUOTA_EXCEEDED", 429),
        (
            provider(monthly_budget=Decimal(10)),
            {"usage": pc.Usage(Decimal("9.80"), 3)},
            "BUDGET_EXHAUSTED",
            429,
        ),  # QA-047: before the charge
    ],
)
def test_each_gate_refuses_with_its_own_reason_and_status(p, kw, reason, status) -> None:
    g = (
        gate(p if p is not None else None, **kw)
        if p is not None
        else pc.may_call(
            None,
            klass="PEP_RCA",
            market="GB",
            region="eu-west",
            tenant_regions=["eu-west"],
            usage=pc.Usage(),
            circuit=pc.Circuit(),
            now=NOW,
        )
    )
    assert g.allowed is False and g.reasons == (reason,) and g.http_status == status


def test_a_sandbox_provider_may_be_used_outside_production() -> None:
    assert gate(provider(state=pc.ProviderState.SANDBOX), production=False).allowed


def test_the_last_call_inside_the_budget_is_allowed_and_the_next_is_not() -> None:
    p = provider(monthly_budget=Decimal(10))
    assert gate(p, usage=pc.Usage(Decimal("9.60"), 1)).allowed
    assert not gate(p, usage=pc.Usage(Decimal("9.61"), 1)).allowed


def test_there_is_never_a_failover_region() -> None:
    p = provider(regions=frozenset({"eu-west", "us-east"}))
    assert pc.next_region("eu-west", p, ["eu-west", "us-east"]) is None  # QA-041


def test_repeated_failures_open_the_circuit_and_it_closes_after_the_cooldown() -> None:
    policy = pc.CircuitPolicy(
        threshold=3, window=timedelta(minutes=5), cooldown=timedelta(minutes=2)
    )
    c = pc.Circuit()
    for i in range(2):
        c = pc.record_failure(c, policy, NOW + timedelta(seconds=i))
        assert not pc.circuit_open(c, policy, NOW + timedelta(seconds=i))
    c = pc.record_failure(c, policy, NOW + timedelta(seconds=2))
    assert pc.circuit_open(c, policy, NOW + timedelta(seconds=3))
    refused = gate(circuit=c, now=NOW + timedelta(seconds=3))
    assert (
        refused.reasons == ("CIRCUIT_OPEN",) and refused.http_status == 503
    )  # QA-045: unavailable, never clear
    assert not pc.circuit_open(c, policy, NOW + timedelta(minutes=3))
    assert pc.record_success(c) == pc.Circuit()


def test_failures_older_than_the_window_do_not_count() -> None:
    policy = pc.CircuitPolicy(threshold=3, window=timedelta(minutes=1))
    c = pc.record_failure(pc.Circuit(), policy, NOW)
    c = pc.record_failure(c, policy, NOW + timedelta(seconds=10))
    c = pc.record_failure(c, policy, NOW + timedelta(minutes=5))
    assert c.opened_at is None and len(c.failures) == 1


# ---- callbacks ---------------------------------------------------------------------------------------------------------


def event(**kw):
    base = {
        "provider_id": "vendor-a",
        "tenant_id": "t1",
        "event_id": "e1",
        "case_ref": "scr_1",
        "issued_at": NOW,
        "body": b'{"hits":0}',
    }
    base.update(kw)
    return pc.Event(**base)


def signed(e: pc.Event) -> str:
    return pc.sign(SECRET, e.provider_id, e.tenant_id, e.event_id, e.issued_at, e.body)


def verify(e, sig=None, **kw):
    args = {
        "secret": SECRET,
        "expected_provider": "vendor-a",
        "expected_tenant": "t1",
        "seen": set(),
        "now": NOW,
    }
    args.update(kw)
    return pc.verify_event(e, signed(e) if sig is None else sig, **args)


def refused(e, sig=None, **kw) -> str:
    with pytest.raises(pc.EventError) as ex:
        verify(e, sig, **kw)
    return ex.value.code


def test_a_correctly_signed_fresh_event_for_this_tenant_is_accepted() -> None:
    assert verify(event()) == "e1"


def test_a_wrong_signature_a_changed_body_or_a_wrong_secret_is_refused() -> None:
    e = event()
    assert refused(e, "0" * 64) == "SIGNATURE_INVALID"  # QA-037
    assert refused(event(body=b'{"hits":1}'), signed(e)) == "SIGNATURE_INVALID"
    assert refused(e, secret=b"another") == "SIGNATURE_INVALID"


def test_a_stale_or_future_timestamp_is_refused() -> None:
    old = event(issued_at=NOW - timedelta(minutes=11))
    assert refused(old) == "TIMESTAMP_OUTSIDE_WINDOW"
    assert refused(event(issued_at=NOW + timedelta(minutes=11))) == "TIMESTAMP_OUTSIDE_WINDOW"
    assert verify(event(issued_at=NOW - timedelta(minutes=9))) == "e1"


def test_an_event_for_another_tenant_or_provider_is_refused_before_anything_else() -> None:
    assert refused(event(tenant_id="t2")) == "TENANT_MISMATCH"
    assert refused(event(provider_id="vendor-b")) == "PROVIDER_NOT_RECOGNISED"


def test_a_replayed_event_is_refused_so_nothing_changes_and_nothing_is_billed() -> None:
    assert refused(event(), seen={"e1"}) == "DUPLICATE_EVENT"  # QA-038
    key = pc.call_key("t1", "scr_1", "vendor-a", "PEP_RCA")
    first = pc.charge_for(key, provider(), {}, outage=False)
    again = pc.charge_for(key, provider(), {key: first.cost}, outage=False)
    assert first.billable and first.cost == Decimal("0.40")
    assert not again.billable and again.cost == 0


def test_an_outage_is_never_charged_and_the_call_key_is_stable_per_case_provider_and_class() -> (
    None
):
    k = pc.call_key("t1", "scr_1", "vendor-a", "PEP_RCA")
    assert pc.charge_for(k, provider(), {}, outage=True) == pc.Charge(k, Decimal(0), False)
    assert k == pc.call_key("t1", "scr_1", "vendor-a", "PEP_RCA")
    assert (
        len(
            {
                k,
                pc.call_key("t2", "scr_1", "vendor-a", "PEP_RCA"),
                pc.call_key("t1", "scr_2", "vendor-a", "PEP_RCA"),
                pc.call_key("t1", "scr_1", "vendor-b", "PEP_RCA"),
                pc.call_key("t1", "scr_1", "vendor-a", "ADVERSE_MEDIA"),
            }
        )
        == 5
    )
