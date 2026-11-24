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


def test_a_plan_fee_quote_is_not_held_to_the_regional_release_gate_but_every_other_quote_is() -> (
    None
):
    """CR-147: a plan's platform fee is priced the same in every region, so a self-serve plan quote turns the gate off; the gate is on unless asked off."""
    assert validate(quote(region="eu-9"), CAT, BANDS).reasons == ("REGION_NOT_RELEASED",)
    v = validate(quote(region="global", region_gated=False), CAT, BANDS)
    assert v.status == "VALIDATED" and v.reasons == ()
    # turning the gate off changes nothing else: the other hard rules still reject
    v = validate(quote(region="global", region_gated=False, liability_capped=False), CAT, BANDS)
    assert v.reasons == ("UNCAPPED_LIABILITY",)


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
        (
            (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("1")),),
            "DISCOUNT_INVALID",
        ),  # a 100% coupon is not a discount (CL03-T12)
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
        ("0.05001", {"CCO"}),  # 5.001% needs the CCO (CL03-T01)
        ("0.12", {"CCO"}),  # 12% is still the CCO's
        ("0.1201", {"CFO", "FOUNDER"}),
        ("0.12001", {"CFO", "FOUNDER"}),  # 12.001% needs the CFO and the Founder (CL03-T01)
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


def test_a_fractional_discount_is_never_rounded_down_into_a_lower_band() -> None:
    """CL03-T02: the band is chosen on the exact fraction, so no rounding can move a discount to an authority tier that approves less."""
    for pct, roles in (
        ("0.0500001", {"CCO"}),
        ("0.05000000001", {"CCO"}),
        ("0.1200001", {"CFO", "FOUNDER"}),
        ("0.0499999", {"AE"}),
    ):
        assert approvers_for(D(pct), BANDS) == roles, pct
    q = quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.0500001")))
    v = validate(q, CAT, BANDS)
    assert v.status == "NEEDS_APPROVAL" and v.approvers_required == {"CCO"}


def test_stacked_promotions_are_judged_on_their_combined_economics() -> None:
    """CL03-T03: two 5% promotions are an effective 9.75%, which is the CCO's band, though each alone fits the account executive's."""
    from zid_platform.modules.commercial.domain.orders import effective_discount

    both = QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.05"), (D("0.05"),))
    assert effective_discount(both) == D("0.0975")
    assert effective_discount(
        QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D(0), (D("0.05"), D("0.05")))
    ) == D("0.0975")
    assert effective_discount(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))) == D(0)
    for alone in (
        QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.05")),
        QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D(0), (D("0.05"),)),
    ):
        assert validate(quote(alone), CAT, BANDS).approvers_required == {"AE"}
    v = validate(quote(both), CAT, BANDS)
    assert v.status == "NEEDS_APPROVAL" and v.approvers_required == {"CCO"}
    assert v.lines[0]["effective_discount_pct"] == "0.0975" and v.lines[0]["promotions"] == ["0.05"]
    # enough small promotions cross the top band as well
    many = QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.06"), (D("0.05"), D("0.05")))
    assert validate(quote(many), CAT, BANDS).approvers_required == {"CFO", "FOUNDER"}


def test_a_promotion_cannot_be_hidden_on_a_pass_through_line_or_be_a_free_ride() -> None:
    bad = (
        (
            QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)),
            QuoteLine(2, "ZID-IDV", "BUSINESS", D(9), D(0), (D("0.05"),)),
        ),
        (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D(0), (D("1"),)),),
        (QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D(0), (D("-0.1"),)),),
    )
    codes = [validate(quote(*lines), CAT, BANDS).reasons for lines in bad]
    assert (
        "PASSTHROUGH_DISCOUNT" in codes[0]
        and "DISCOUNT_INVALID" in codes[1]
        and "DISCOUNT_INVALID" in codes[2]
    )


def test_an_approval_binds_the_promotions_too() -> None:
    """The digest an approval signs changes when a promotion is added, so a quote cannot be approved without one and sold with it."""
    plain = digest({"lines": [[1, "ZID-BASE", "BUSINESS", "1", "0.05", []]]})
    stacked = digest({"lines": [[1, "ZID-BASE", "BUSINESS", "1", "0.05", ["0.05"]]]})
    assert plain != stacked


def test_a_floor_reason_adds_its_own_approver_that_the_percentage_approvers_cannot_replace() -> (
    None
):
    """CL03-T11: a quote below the price floor needs the CFO's risk acceptance on top of the discount band, so the people who approve the percentage
    cannot release it alone, and one person cannot fill both; a hard limit blocks the quote whoever signs."""
    # an 8% discount on a price whose floor is higher than the discounted price
    cat = replace(
        CAT, floors={("ZID-BASE", "uk-1"): D(1400)}
    )  # 8% off 1,499 is 1,379: below the floor
    v = validate(quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.08"))), cat, BANDS)
    assert (
        v.status == "NEEDS_APPROVAL"
        and "BELOW_FLOOR" in v.reasons
        and v.approvers_required == {"CCO", "CFO"}
    )
    assert missing_approvals(v.approvers_required, {"CCO": "cora"}, creator="alice") == {"CFO"}
    assert missing_approvals(
        v.approvers_required, {"CCO": "cora", "CFO": "cora"}, creator="alice"
    ) == {"CFO"}  # one person, one role
    assert (
        missing_approvals(v.approvers_required, {"CCO": "cora", "CFO": "carol"}, creator="alice")
        == frozenset()
    )
    hard = validate(
        quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.08")), liability_capped=False),
        CAT,
        BANDS,
    )
    assert hard.status == "REJECTED"  # no approval set can release it


def test_glb_35_a_price_book_in_one_currency_never_prices_an_account_billed_in_another() -> None:
    """GLB-35: a quote built from a GBP book (the UK illustrative price) must not activate for an account billed in USD."""
    gbp_book = quote(currency="GBP", account_currency="USD")
    v = validate(gbp_book, CAT, BANDS)
    assert "PRICE_BOOK_CURRENCY_MISMATCH" in v.reasons and v.status == "REJECTED"
    same = validate(quote(currency="GBP", account_currency="GBP"), CAT, BANDS)
    assert "PRICE_BOOK_CURRENCY_MISMATCH" not in same.reasons
    # a caller that does not state the account currency is not accused of a mismatch (existing callers keep working)
    assert "PRICE_BOOK_CURRENCY_MISMATCH" not in validate(quote(), CAT, BANDS).reasons


def test_a_credential_check_line_is_refused_while_no_price_book_line_exists_for_it() -> None:
    """CR-070: the two credential check meters are definitions only. A SKU that is not in the catalogue is refused, never priced at zero."""
    v = validate(
        quote(
            QuoteLine(1, "ZID-BASE", "BUSINESS", D(1)),
            QuoteLine(2, "ZID-CRED-CHECK", "BUSINESS", D(5)),
        ),
        CAT,
        BANDS,
    )
    assert "SKU_UNKNOWN" in v.reasons and v.status == "REJECTED"
