"""Billable ledger rules (ZID-COM-001 §7, §26; CR-025). Pure domain, no database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import billable as b


def event(**kw: object) -> b.BillableEvent:
    base: dict[str, object] = {
        "event_id": "e1",
        "tenant_id": "t1",
        "deduplication_key": "auth:s1",
        "meter_id": "AUTH_EVENT",
        "quantity": Decimal(1),
        "occurred_at": datetime(2026, 10, 24, 12, tzinfo=UTC),
        "window_month": date(2026, 10, 1),
        "environment_id": "production",
        "region": "uk-1",
    }
    return b.BillableEvent(**{**base, **kw})  # type: ignore[arg-type]


def test_window_month_is_the_utc_month_and_rolls_over_the_year() -> None:
    assert b.window_month(datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)) == date(2026, 10, 1)
    assert b.window_month(datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)) == date(2026, 11, 1)
    assert b.next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert b.next_month(date(2026, 1, 1)) == date(2026, 2, 1)


def test_deduplication_keys_name_the_thing_charged_never_a_clock() -> None:
    assert b.key_auth("s1") == "auth:s1" == b.key_auth("s1")
    assert b.key_decision("d1") == "pdp:d1"
    assert b.key_m2m("j1") == "m2m:j1"
    assert b.key_vc_issued("c1") == "vci:c1" and b.key_vc_presented("p1") == "vcp:p1"
    keys = {b.key_auth("s1"), b.key_decision("s1"), b.key_m2m("s1"), b.key_vc_issued("s1")}
    assert len(keys) == 4  # the same id under two meters never collides


def test_the_mah_key_is_per_month_and_never_carries_the_principal() -> None:
    k = b.key_mah(date(2026, 10, 1), "principal-123")
    assert k == b.key_mah(date(2026, 10, 1), "principal-123")
    assert k != b.key_mah(date(2026, 11, 1), "principal-123")
    assert k != b.key_mah(date(2026, 10, 1), "principal-124")
    assert "principal-123" not in k and k.startswith("mah:202610:")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"meter_id": "LOGINS"}, "METER_UNKNOWN"),
        ({"quantity": Decimal(0)}, "QUANTITY_INVALID"),
        ({"quantity": Decimal(-1)}, "QUANTITY_INVALID"),
        ({"deduplication_key": "x"}, "DEDUPLICATION_KEY_INVALID"),
        ({"principal_class": "ROBOT"}, "PRINCIPAL_CLASS_INVALID"),
        ({"origin": "FAX"}, "ORIGIN_INVALID"),
        ({"transaction_type": "PAID"}, "TRANSACTION_TYPE_INVALID"),
        ({"billable_result": "MAYBE"}, "BILLABLE_RESULT_INVALID"),
        (
            {"finality": "INVOICED"},
            "FINALITY_INVALID",
        ),  # an event is never recorded already invoiced
        ({"origin": "PARTNER"}, "PARTNER_ORIGIN_MISMATCH"),
        ({"origin": "API", "partner_id": "p1"}, "PARTNER_ORIGIN_MISMATCH"),
        ({"market_policy": ""}, "CONTEXT_TEXT_INVALID"),
        ({"supplier_rate_version": "v" * 81}, "CONTEXT_TEXT_INVALID"),
    ],
)
def test_an_invalid_event_is_refused(change: dict[str, object], code: str) -> None:
    with pytest.raises(b.BillableError) as e:
        event(**change).validate()
    assert e.value.code == code
    event().validate()


def test_the_row_hash_covers_the_economic_facts_and_nothing_else() -> None:
    base = event()
    assert base.row_hash == event(event_id="other", late=True, recovered=True).row_hash
    for change in (
        {"tenant_id": "t2"},
        {"deduplication_key": "auth:s2"},
        {"meter_id": "VC_ISSUED"},
        {"quantity": Decimal(2)},
        {"occurred_at": datetime(2026, 10, 24, 12, 0, 1, tzinfo=UTC)},
        {"region": "eu-1"},
    ):
        assert event(**change).row_hash != base.row_hash, change
    assert event(quantity=Decimal("1.0")).row_hash == event(quantity=Decimal(1)).row_hash


def test_the_window_hash_total_is_order_independent_and_sensitive_to_every_row() -> None:
    hashes = [event(deduplication_key=f"auth:{i}").row_hash for i in range(50)]
    total = b.hash_total(hashes)
    assert total == b.hash_total(reversed(hashes)) == b.hash_total(sorted(hashes))
    assert total != b.hash_total(hashes[:-1])
    assert total != b.hash_total([*hashes, event(deduplication_key="auth:extra").row_hash])
    assert b.hash_total([]) == b.hash_total([])


def test_net_quantities_add_adjustments_and_never_go_below_zero() -> None:
    net = b.net_quantities(
        {"AUTH_EVENT": Decimal(100), "MAH": Decimal(5)},
        {"AUTH_EVENT": Decimal(-30), "MAH": Decimal(-9), "VC_ISSUED": Decimal(2)},
    )
    assert net == {"AUTH_EVENT": Decimal(70), "MAH": Decimal(0), "VC_ISSUED": Decimal(2)}


def test_the_usage_context_fields_default_to_not_stated_and_never_change_the_hash() -> None:
    plain = event()
    assert plain.legal_payer_id is None and plain.finality is None and plain.billable_result is None
    full = event(
        legal_payer_id="p1", origin="PARTNER", partner_id="partner-1", transaction_type="PROVIDER_EXECUTED",
        billable_result="CHARGEABLE", finality="PROVISIONAL", market_policy="DE:2026-11", supplier_rate_version="sr-7",
    )  # fmt: skip
    full.validate()
    assert full.row_hash == plain.row_hash, "frozen windows keep their hash totals"
