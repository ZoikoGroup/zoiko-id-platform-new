"""Quote validation and discount authority (ZID-COM-001 §12, §27, §31; CR-028). Pure rules.

Each rejection reason is triggered by a quote that breaks only that one rule.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain.orders import (
    Band,
    Catalogue,
    OrderError,
    Quote,
    QuoteLine,
    approvers_for,
    digest,
    missing_approvals,
    validate,
)

D = Decimal
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
BANDS = [
    Band(D("0.05"), frozenset({"AE"})),
    Band(D("0.12"), frozenset({"CCO"})),
    Band(None, frozenset({"CFO", "FOUNDER"})),
]
CAT = Catalogue(
    released_skus=frozenset({"ZID-BASE", "ZID-FED", "ZID-SCIM", "ZID-HUM", "ZID-SUP"}),
    sku_consumption={
        "ZID-BASE": "FLAT",
        "ZID-FED": "FLAT",
        "ZID-SCIM": "FLAT",
        "ZID-HUM": "METERED",
        "ZID-IDV": "PASSTHROUGH",
        "ZID-SUP": "CONTRACT",
        "ZID-DPL": "CONTRACT",
    },
    prices={
        ("ZID-BASE", "BUSINESS"): D(1499),
        ("ZID-FED", "BUSINESS"): D(149),
        ("ZID-SCIM", "BUSINESS"): D(149),
        ("ZID-HUM", "BUSINESS"): D("0.015"),
    },
    released_regions=frozenset({"uk-1"}),
    floors={("ZID-FED", "uk-1"): D(100)},
)


def quote(*lines: QuoteLine, **kw: object) -> Quote:
    base = Quote(
        lines=lines
        or (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)), QuoteLine(2, "ZID-FED", "BUSINESS", D(2))),
        region="uk-1",
        currency="USD",
        price_book_status="APPROVED",
        price_book_effective_from=NOW - timedelta(days=1),
        quoted_at=NOW,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_a_clean_quote_validates_and_prices_every_line() -> None:
    v = validate(quote(), CAT, BANDS)
    assert v.status == "VALIDATED" and v.reasons == () and not v.approvers_required
    assert [ln["net_unit_price"] for ln in v.lines] == ["1499", "149"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"price_book_status": "DRAFT"}, "PRICE_BOOK_NOT_APPROVED"),
        ({"price_book_effective_from": NOW + timedelta(days=1)}, "PRICE_BOOK_NOT_EFFECTIVE"),
        ({"region": "eu-9"}, "REGION_NOT_RELEASED"),
        ({"residency_commitment": "us-2"}, "RESIDENCY_OUTSIDE_RELEASE"),
        ({"liability_capped": False}, "UNCAPPED_LIABILITY"),
        ({"prepaid": True, "prepaid_discount_pct": D("0.11")}, "PREPAID_DISCOUNT_CEILING"),
    ],
)
def test_each_hard_rule_rejects_on_its_own(change: dict[str, object], code: str) -> None:
    v = validate(quote(**change), CAT, BANDS)
    assert v.status == "REJECTED" and v.reasons == (code,), v.reasons


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        ((QuoteLine(1, "ZID-FED", "BUSINESS", D(1)),), "PLAN_LINE_MISSING"),
        (
            (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)), QuoteLine(2, "ZID-FED", "SCALE", D(1))),
            "ADDON_WITHOUT_PARENT",
        ),
        (
            (
                QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)),
                QuoteLine(2, "ZID-NOPE", "BUSINESS", D(1)),
            ),
            "SKU_UNKNOWN",
        ),
        (
            (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)), QuoteLine(2, "ZID-DPL", "BUSINESS", D(1))),
            "SKU_NOT_RELEASED",
        ),
        (
            (
                QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)),
                QuoteLine(2, "ZID-IDV", "BUSINESS", D(9), D("0.05")),
            ),
            "PASSTHROUGH_DISCOUNT",
        ),
        ((QuoteLine(1, "ZID-BASE", "LAUNCH", D(1)),), "PRICE_MISSING"),
        ((QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("1.2")),), "DISCOUNT_INVALID"),
    ],
)
def test_each_line_rule_reports_its_own_reason(lines: tuple[QuoteLine, ...], code: str) -> None:
    v = validate(quote(*lines), CAT, BANDS)
    assert code in v.reasons and v.status == "REJECTED", v.reasons


def test_pass_through_without_a_confirmed_vendor_cost_needs_approval_not_a_promise() -> None:
    q = quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)), QuoteLine(2, "ZID-IDV", "BUSINESS", D(9)))
    cat = replace(CAT, released_skus=CAT.released_skus | {"ZID-IDV"})
    v = validate(q, cat, BANDS)
    assert v.status == "NEEDS_APPROVAL" and v.reasons == ("VENDOR_COST_UNCONFIRMED",)
    confirmed = replace(cat, confirmed_vendor_skus=frozenset({"ZID-IDV"}))
    assert validate(q, confirmed, BANDS).status == "VALIDATED"


def test_below_the_floor_needs_the_cfo() -> None:
    q = quote(
        QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)),
        QuoteLine(2, "ZID-FED", "BUSINESS", D(1), D("0.40")),
    )
    v = validate(q, CAT, BANDS)
    assert (
        "BELOW_FLOOR" in v.reasons
        and "CFO" in v.approvers_required
        and v.status == "NEEDS_APPROVAL"
    )


@pytest.mark.parametrize(
    ("pct", "roles"),
    [
        ("0", set()),
        ("0.01", {"AE"}),
        ("0.05", {"AE"}),  # 5% is still the account executive's
        ("0.0501", {"CCO"}),
        ("0.12", {"CCO"}),  # 12% is still the CCO's
        ("0.1201", {"CFO", "FOUNDER"}),
        ("0.5", {"CFO", "FOUNDER"}),
    ],
)
def test_discount_bands_have_exact_boundaries(pct: str, roles: set[str]) -> None:
    assert approvers_for(D(pct), BANDS) == roles


def test_a_discount_makes_the_quote_need_its_band() -> None:
    q = quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.08")))
    v = validate(q, CAT, BANDS)
    assert v.status == "NEEDS_APPROVAL" and v.approvers_required == {"CCO"}
    prepaid = validate(quote(prepaid=True, prepaid_discount_pct=D("0.10")), CAT, BANDS)
    assert prepaid.status == "NEEDS_APPROVAL" and prepaid.approvers_required == {
        "CFO"
    }  # 10% is allowed, by the CFO


def test_an_incomplete_policy_never_lets_a_discount_through() -> None:
    with pytest.raises(OrderError):
        approvers_for(D("0.5"), [Band(D("0.05"), frozenset({"AE"}))])


def test_approvals_bind_the_digest_and_people_not_roles() -> None:
    a = digest({"lines": [["ZID-BASE", "BUSINESS", "1", "0.08"]]})
    assert a == digest({"lines": [["ZID-BASE", "BUSINESS", "1", "0.08"]]})
    assert a != digest({"lines": [["ZID-BASE", "BUSINESS", "1", "0.09"]]})
    need = frozenset({"CFO", "FOUNDER"})
    assert (
        missing_approvals(need, {"CFO": "carol", "FOUNDER": "dave"}, creator="alice") == frozenset()
    )
    assert missing_approvals(need, {"CFO": "alice", "FOUNDER": "dave"}, creator="alice") == {
        "CFO"
    }  # no self-approval
    assert (
        len(missing_approvals(need, {"CFO": "carol", "FOUNDER": "carol"}, creator="alice")) == 1
    )  # one person, one role
