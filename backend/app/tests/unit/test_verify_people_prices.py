"""The Verify People price table from the pricing page (version 5.0): it loads, the estimate reproduces the page calculator, the spend floor applies, and a custom plan is never guessed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from zid_platform.modules.verify_api.domain import people_prices as pp
from zid_platform.modules.verify_api.domain.model import PartnerError

FILE = Path(__file__).resolve().parents[3] / "config" / "developer" / "verify-people-prices.yaml"
TABLE = pp.parse(yaml.safe_load(FILE.read_text(encoding="utf-8")))


def test_the_table_carries_the_three_published_plans_and_marks_itself_proposed() -> None:
    assert TABLE.status == "PROPOSED" and TABLE.currency == "USD" and TABLE.version == "5.0-page"
    assert {
        c: (str(p.charge), str(p.minimum_monthly)) for c, p in TABLE.plans.items() if not p.custom
    } == {"essentials": ("0.95", "49"), "business": ("1.29", "149"), "advanced": ("1.79", "299")}
    assert TABLE.plans["enterprise"].custom


def test_the_estimate_reproduces_the_page_calculator() -> None:
    e = pp.estimate(TABLE, "business", 600)
    assert e["total"] == "774.00" and e["usage"] == "774.00" and e["minimum_applied"] is False


def test_the_monthly_minimum_is_a_floor_not_an_addition() -> None:
    low = pp.estimate(TABLE, "essentials", 10)
    assert low["total"] == "49" and low["minimum_applied"] is True
    assert pp.estimate(TABLE, "essentials", 100)["total"] == "95.00"
    assert pp.estimate(TABLE, "advanced", 0)["total"] == "299"
    assert pp.estimate(TABLE, "advanced", 10000)["total"] == "17900.00"


def test_an_estimate_names_its_table_and_says_it_is_not_an_invoice() -> None:
    e = pp.estimate(TABLE, "business", 1)
    assert (
        e["table_version"] == "5.0-page"
        and e["table_status"] == "PROPOSED"
        and "not an invoice" in e["note"]
    )


@pytest.mark.parametrize(
    ("plan", "n", "code"),
    [
        ("enterprise", 100, "PRICE_ON_REQUEST"),
        ("gold", 1, "PLAN_UNKNOWN"),
        ("business", -1, "VOLUME_INVALID"),
        ("business", 2_000_000_000, "VOLUME_INVALID"),
    ],
)
def test_a_custom_plan_an_unknown_plan_and_an_impossible_volume_are_refused(
    plan: str, n: int, code: str
) -> None:
    with pytest.raises(PartnerError) as e:
        pp.estimate(TABLE, plan, n)
    assert e.value.code == code


def test_a_table_with_a_free_charge_or_a_non_floor_minimum_is_refused() -> None:
    with pytest.raises(ValueError):
        pp.parse(
            {
                "version": "x",
                "status": "P",
                "currency": "USD",
                "minimum_model": "FLOOR",
                "plans": {"a": {"name": "A", "charge": "0", "minimum_monthly": "1"}},
            }
        )
    with pytest.raises(ValueError):
        pp.parse(
            {
                "version": "x",
                "status": "P",
                "currency": "USD",
                "minimum_model": "PLATFORM_FEE",
                "plans": {},
            }
        )


def test_the_public_route_serves_the_table_and_an_estimate_and_refuses_what_it_should() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zid_errors import ZidError
    from zid_platform.modules.verify_api.api import sessions as api

    app = FastAPI()
    app.include_router(api.router)

    @app.exception_handler(ZidError)
    async def _h(_r, e):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": {"code": e.code}}, status_code=e.http_status)

    c = TestClient(app)
    base = c.get("/v1/verify-api/pricing/people").json()
    assert (
        base["status"] == "PROPOSED"
        and [p["code"] for p in base["plans"]]
        == ["essentials", "business", "advanced", "enterprise"]
        and "estimate" not in base
    )
    assert (
        next(p for p in base["plans"] if p["code"] == "enterprise")["rate_per_verification"] is None
    )
    assert (
        c.get("/v1/verify-api/pricing/people?plan=business&verifications=600").json()["estimate"][
            "total"
        ]
        == "774.00"
    )
    assert c.get("/v1/verify-api/pricing/people?plan=enterprise&verifications=5").status_code == 422
    assert c.get("/v1/verify-api/pricing/people?plan=gold&verifications=5").status_code == 404
