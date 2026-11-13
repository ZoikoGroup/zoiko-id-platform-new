"""Purchase-order rules (ZID-COM-WF-2026-11 CW-11): invoiced only against a PO that fits, nothing changes for a company with none."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import purchase_order as po

ON = date(2026, 11, 13)


def make(number: str, **over: object) -> po.PO:
    base: dict[str, object] = {
        "po_id": number, "number": number, "currency": "EUR", "value": Decimal(1000), "drawn": Decimal(0),
        "valid_from": date(2026, 1, 1), "valid_until": date(2026, 12, 31), "status": "ACTIVE",
    }  # fmt: skip
    return po.PO(**{**base, **over})  # type: ignore[arg-type]


def test_a_company_with_no_purchase_order_is_not_covered_by_the_rule() -> None:
    assert po.choose([], currency="EUR", amount=Decimal(50), on=ON) is None


def test_the_po_that_expires_soonest_is_used_first() -> None:
    later, sooner = (
        make("B", valid_until=date(2027, 3, 1)),
        make("A", valid_until=date(2026, 12, 1)),
    )
    chosen = po.choose([later, sooner], currency="EUR", amount=Decimal(100), on=ON)
    assert chosen is not None and chosen.number == "A"


@pytest.mark.parametrize(
    ("pos", "code"),
    [
        ([make("A", status="CLOSED")], "PO_REQUIRED"),
        ([make("A", currency="USD")], "PO_REQUIRED"),
        ([make("A", valid_until=date(2026, 11, 12))], "PO_REQUIRED"),
        ([make("A", valid_from=date(2026, 11, 14))], "PO_REQUIRED"),
        ([make("A", drawn=Decimal(950))], "PO_INSUFFICIENT"),
    ],
)
def test_an_invoice_with_no_fitting_po_is_refused_with_the_reason(
    pos: list[po.PO], code: str
) -> None:
    with pytest.raises(po.PurchaseOrderError) as e:
        po.choose(pos, currency="EUR", amount=Decimal(100), on=ON)
    assert e.value.code == code


def test_the_boundaries_are_inclusive_and_an_exact_fit_is_allowed() -> None:
    exact = make("A", drawn=Decimal(900), valid_from=ON, valid_until=ON)
    assert po.choose([exact], currency="EUR", amount=Decimal(100), on=ON) is not None
    with pytest.raises(po.PurchaseOrderError):
        po.choose([exact], currency="EUR", amount=Decimal("100.000001"), on=ON)


def test_a_smaller_po_is_skipped_for_one_that_has_room() -> None:
    small, big = make("A", drawn=Decimal(990), valid_until=date(2026, 12, 1)), make("B")
    chosen = po.choose([small, big], currency="EUR", amount=Decimal(100), on=ON)
    assert chosen is not None and chosen.number == "B"
