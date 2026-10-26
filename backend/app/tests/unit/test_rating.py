"""Rating (ZID-COM-001 §6, §7, §13; CR-026). Pure arithmetic, no database.

The first test is the proposal's own worked bill, line by line. The rest pin the rules: proration that never
doubles an allowance, rounding once per line, credits that never go negative, unpriced usage reported and
never guessed, and floats refused outright.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain.rating import (
    Credit,
    PlanTerms,
    PriceLine,
    RatingError,
    Segment,
    quantize,
    rate,
    sla_credit,
)

D = Decimal
START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 11, 1, tzinfo=UTC)
MID = datetime(2026, 10, 16, 12, tzinfo=UTC)  # exactly half of a 31-day month


def terms(code: str) -> PlanTerms:
    table = {
        "LAUNCH": ("299", 10_000, 150_000, 500_000, 50_000, 0),
        "BUSINESS": ("1499", 50_000, 1_000_000, 5_000_000, 250_000, 1),
        "SCALE": ("4999", 250_000, 5_000_000, 25_000_000, 1_000_000, 3),
    }
    fee, mah, auth, pdp, m2m, sso = table[code]
    return PlanTerms(
        code,
        D(fee),
        False,
        {"MAH": D(mah), "AUTH_EVENT": D(auth), "POLICY_DECISION": D(pdp), "M2M_GRANT": D(m2m)},
        sso,
    )


def prices(code: str) -> dict[str, PriceLine]:
    col = {"LAUNCH": 0, "BUSINESS": 1, "SCALE": 2}[code]
    rows = {
        "ZID-HUM": (("0.025", "0.015", "0.009"), 1),
        "ZID-AUTH": (("0.40", "0.25", "0.15"), 1000),
        "ZID-PDP": (("0.60", "0.45", "0.25"), 10_000),
        "ZID-M2M": (("0.50", "0.35", "0.20"), 1000),
        "ZID-FED": (("149", "149", "125"), 1),
        "ZID-SCIM": (("149", "149", "125"), 1),
        "ZID-VCI": (("0.08", "0.05", "0.03"), 1),
        "ZID-VCP": (("0.025", "0.015", "0.010"), 1),
    }
    out = {sku: PriceLine(D(r[col]), div) for sku, (r, div) in rows.items()}
    out["ZID-BASE"] = PriceLine(terms(code).platform_fee)
    return out


def seg(code: str, start: datetime = START, end: datetime = END) -> Segment:
    return Segment(terms(code), start, end, prices(code))


def test_the_proposal_worked_bill_is_2053_line_by_line() -> None:
    """ZID-COM-001 §13: a Business customer for one UTC month."""
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("BUSINESS")],
        usage=[
            {
                "MAH": D(68_000),
                "AUTH_EVENT": D(1_200_000),
                "POLICY_DECISION": D(6_500_000),
                "M2M_GRANT": D(300_000),
            }
        ],
        connections={"saml_sso": 2},
    )
    got = {(ln.kind, ln.meter): (ln.net, ln.formula) for ln in out.lines}
    assert got[("FEE", None)][0] == D("1499.00")
    assert got[("OVERAGE", "MAH")] == (D("270.00"), "18,000 x $0.015")
    assert got[("OVERAGE", "AUTH_EVENT")] == (D("50.00"), "200,000 / 1,000 x $0.25")
    assert got[("OVERAGE", "POLICY_DECISION")] == (D("67.50"), "1,500,000 / 10,000 x $0.45")
    assert got[("OVERAGE", "M2M_GRANT")] == (D("17.50"), "50,000 / 1,000 x $0.35")
    assert got[("CONNECTION", "saml_sso")] == (D("149.00"), "1 extra x $149")
    assert out.subtotal == D("2053.00") and out.credit_balance == 0 and not out.warnings
    assert len(out.lines) == 6


def test_usage_inside_the_allowance_bills_only_the_fee() -> None:
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("BUSINESS")],
        usage=[{"MAH": D(50_000), "AUTH_EVENT": D(999_999)}],
        connections={"saml_sso": 1},
    )
    assert [ln.kind for ln in out.lines] == ["FEE"] and out.subtotal == D("1499.00")


def test_a_mid_month_upgrade_prorates_the_fee_and_never_doubles_the_allowance() -> None:
    """Half a month on Launch then half on Business: each half carries half of its own allowance."""
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("LAUNCH", START, MID), seg("BUSINESS", MID, END)],
        usage=[{"MAH": D(9_000)}, {"MAH": D(26_000)}],
        connections={},
    )
    fees = [ln for ln in out.lines if ln.kind == "FEE"]
    assert [f.net for f in fees] == [D("149.50"), D("749.50")]  # 299/2 and 1499/2
    mah = [ln for ln in out.lines if ln.meter == "MAH"]
    # Launch half: 9,000 - 5,000 = 4,000 x 0.025 = 100; Business half: 26,000 - 25,000 = 1,000 x 0.015 = 15
    assert [(m.chargeable, m.net) for m in mah] == [(D(4000), D("100.00")), (D(1000), D("15.00"))]
    assert out.subtotal == D("1014.00")
    # the same 35,000 on Business all month would be inside one 50,000 allowance: an upgrade is not a free double
    whole = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("BUSINESS")],
        usage=[{"MAH": D(35_000)}],
        connections={},
    )
    assert [ln.kind for ln in whole.lines] == ["FEE"]


def test_proration_is_exact_to_the_second_and_deterministic() -> None:
    cut = datetime(2026, 10, 10, 7, 13, 41, tzinfo=UTC)
    args = {
        "currency": "USD",
        "period_start": START,
        "period_end": END,
        "segments": [seg("SCALE", START, cut), seg("BUSINESS", cut, END)],
        "usage": [{"AUTH_EVENT": D(4_000_000)}, {"AUTH_EVENT": D(900_000)}],
        "connections": {"saml_sso": 4},
    }
    one, two = rate(**args), rate(**args)  # type: ignore[arg-type]
    assert one == two
    fee = [ln.net for ln in one.lines if ln.kind == "FEE"]
    secs = (cut - START).total_seconds() / (END - START).total_seconds()
    assert fee[0] == quantize(
        D(4999) * D(str((cut - START).total_seconds())) / D(str((END - START).total_seconds())),
        "USD",
    )
    assert abs(float(fee[0]) - 4999 * secs) < 0.01
    # connections are counted at the cut-off, against the plan in force then (Business: 1 included)
    conn = next(ln for ln in one.lines if ln.kind == "CONNECTION")
    assert conn.chargeable == D(3) and conn.net == D("447.00")


def test_rounding_happens_once_per_line_half_even() -> None:
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("BUSINESS")],
        usage=[
            {"VC_PRESENTED": D(3)},
        ],
        connections={},
    )
    vcp = next(ln for ln in out.lines if ln.meter == "VC_PRESENTED")
    assert vcp.net == D("0.04")  # 3 x 0.015 = 0.045 -> 0.04 (half-even), never 0.05
    assert quantize(D("0.125"), "USD") == D("0.12") and quantize(D("0.135"), "USD") == D("0.14")
    assert sum(ln.net for ln in out.lines) == out.subtotal


def test_splitting_usage_across_meters_never_changes_a_meter_total() -> None:
    rng = random.Random(7)
    for _ in range(50):
        usage = {
            m: D(rng.randint(0, 3_000_000))
            for m in ("MAH", "AUTH_EVENT", "POLICY_DECISION", "M2M_GRANT")
        }
        out = rate(
            currency="USD",
            period_start=START,
            period_end=END,
            segments=[seg("BUSINESS")],
            usage=[usage],
            connections={},
        )
        assert out.subtotal >= D("1499.00")
        assert all(ln.net >= 0 for ln in out.lines)
        more = {k: v + 10_000 for k, v in usage.items()}
        bigger = rate(
            currency="USD",
            period_start=START,
            period_end=END,
            segments=[seg("BUSINESS")],
            usage=[more],
            connections={},
        )
        assert bigger.subtotal >= out.subtotal  # monotonic in usage


def test_credits_never_take_the_bill_below_zero() -> None:
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[seg("LAUNCH")],
        usage=[{}],
        connections={},
        credits=[
            Credit(D("100"), "PILOT_CREDIT"),
            Credit(D("500"), "SLA_CREDIT", reverses="line-1"),
        ],
    )
    credits = [ln for ln in out.lines if ln.kind == "CREDIT"]
    assert [c.net for c in credits] == [D("-100.00"), D("-199.00")]
    assert out.subtotal == D("0.00") and out.credit_balance == D("301.00")
    assert credits[1].reverses == "line-1"
    with pytest.raises(RatingError):
        rate(
            currency="USD",
            period_start=START,
            period_end=END,
            segments=[seg("LAUNCH")],
            usage=[{}],
            connections={},
            credits=[Credit(D("-5"), "BAD")],
        )


def test_usage_with_no_price_is_reported_never_guessed() -> None:
    developer = Segment(
        PlanTerms("DEVELOPER", D(0), False, {"MAH": D(2500), "AUTH_EVENT": D(25_000)}, 0),
        START,
        END,
        {"ZID-BASE": PriceLine(D(0))},
    )
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[developer],
        usage=[{"MAH": D(4000)}],
        connections={"saml_sso": 1},
    )
    assert out.subtotal == D("0.00")
    assert any("MAH" in w and "no price" in w for w in out.warnings)
    assert any("saml_sso" in w for w in out.warnings)


def test_a_negotiated_plan_is_not_rated_here() -> None:
    ent = Segment(
        PlanTerms("ENTERPRISE", D(12500), True, {"MAH": None}, None),
        START,
        END,
        {"ZID-BASE": PriceLine(D(12500))},
    )
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[ent],
        usage=[{"MAH": D(1_000_000)}],
        connections={"saml_sso": 9},
    )
    assert out.subtotal == D("12500.00") and "(floor)" in out.lines[0].formula
    assert any("negotiated" in w for w in out.warnings)


def test_a_minimum_commitment_is_an_explicit_true_up_line() -> None:
    p = prices("LAUNCH")
    p["ZID-BASE"] = PriceLine(D(299), 1, minimum_amount=D(1000))
    out = rate(
        currency="USD",
        period_start=START,
        period_end=END,
        segments=[Segment(terms("LAUNCH"), START, END, p)],
        usage=[{}],
        connections={},
    )
    top = next(ln for ln in out.lines if ln.kind == "MINIMUM")
    assert top.net == D("701.00") and out.subtotal == D("1000.00")


@pytest.mark.parametrize(
    "bad",
    [
        {"segments": []},
        {"segments": [seg("BUSINESS", START, MID)]},  # does not cover the period
        {
            "segments": [
                seg("LAUNCH", START, MID),
                seg("BUSINESS", datetime(2026, 10, 20, tzinfo=UTC), END),
            ]
        },
        {"usage": [{"MAH": 1.5}]},  # a float
        {"currency": "XXX"},
    ],
)
def test_invalid_inputs_are_refused(bad: dict[str, object]) -> None:
    args: dict[str, object] = {
        "currency": "USD",
        "period_start": START,
        "period_end": END,
        "segments": [seg("BUSINESS")],
        "usage": [{"MAH": D(1)}],
        "connections": {},
    }
    args.update(bad)
    if "segments" in bad and "usage" not in bad:
        args["usage"] = [{} for _ in bad["segments"]]  # type: ignore[union-attr]
    with pytest.raises(RatingError):
        rate(**args)  # type: ignore[arg-type]


def test_sla_credit_uses_the_signed_steps_and_the_cap() -> None:
    steps = [(D("0.0005"), D("0.05")), (D("0.002"), D("0.10")), (D("0.01"), D("0.20"))]
    assert sla_credit(D(1499), D("0.0001"), steps, D("0.20"), "USD") == D("0.00")
    assert sla_credit(D(1499), D("0.001"), steps, D("0.20"), "USD") == D("74.95")
    assert sla_credit(D(1499), D("0.05"), steps, D("0.20"), "USD") == D("299.80")
    assert sla_credit(D(1499), D("0.05"), [(D(0), D("0.5"))], D("0.20"), "USD") == D(
        "299.80"
    )  # capped
