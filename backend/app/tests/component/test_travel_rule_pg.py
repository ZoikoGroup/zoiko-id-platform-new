"""POST /v2/travel-rule/validate over HTTP against PostgreSQL (DTV-33, CR-097): behind the product line gate, stateless, findings by code and path."""

from __future__ import annotations

import sys
from pathlib import Path

from test_v2_facade_pg import world
from zid_platform.modules.verification.domain.product_lines import parse as parse_lines
from zid_platform.modules.verification.domain.travel_rule import parse_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_travel_rule import BENEF, GOOD, ORIGIN

URL = "/v2/travel-rule/validate"


def lines(state: str) -> object:
    entry = {
        "dtv": "DTV-33",
        "state": state,
        "countries": ["GB"],
        "legal_basis_ref": "TEST-LEGAL",
        "partner_contract_ref": "TEST-PARTNER",
    }
    return parse_lines({"version": "t", "lines": {"travel_rule": entry}})


def test_the_shipped_line_is_design_so_the_route_refuses_until_the_owners_switch_it_on(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        assert http.post(URL, json={"jurisdiction": "GB", "ivms101": GOOD}).status_code == 401
        out = http.post(URL, headers=mine, json={"jurisdiction": "GB", "ivms101": GOOD})
        assert out.status_code == 409 and out.json()["code"] == "service_unavailable", out.text
        assert "design" in out.text.lower()


def test_a_staging_line_returns_findings_counterparty_state_and_threshold_and_stores_nothing(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        http.app.state.product_lines = lines("STAGING")
        http.app.state.vasp_registry = parse_registry(
            {
                "version": "t-1",
                "vasps": [
                    {
                        "lei": ORIGIN,
                        "legal_name": "Origin VASP",
                        "jurisdictions": ["GB"],
                        "status": "ACTIVE",
                        "source_ref": "register:1",
                    }
                ],
                "thresholds": {
                    "GB": {"amount": "1000", "currency": "GBP", "legal_basis_ref": "LEG-9"}
                },
            }
        )
        ok = http.post(URL, headers=mine, json={"jurisdiction": "GB", "ivms101": GOOD})
        assert ok.status_code == 200, ok.text
        out = ok.json()
        assert out["well_formed"] is True and out["findings"] == []
        assert (
            out["originating_vasp"] == "KNOWN"
            and out["beneficiary_vasp"] == "UNVERIFIED_COUNTERPARTY"
        )
        assert out["threshold"] == "AT_OR_ABOVE_THRESHOLD" and out["registry_version"] == "t-1"
        assert BENEF not in ok.text and "Lee" not in ok.text  # nothing about the people is echoed

        broken = {**GOOD, "transfer": {"amount": 2500, "asset": "GBP"}}
        bad = http.post(URL, headers=mine, json={"jurisdiction": "GB", "ivms101": broken}).json()
        assert bad["well_formed"] is False and bad["findings"] == [
            {"code": "AMOUNT_INVALID", "path": "transfer.amount"}
        ]

        # a country the line does not list is unavailable, and a malformed request is refused
        assert (
            http.post(URL, headers=mine, json={"jurisdiction": "FR", "ivms101": GOOD}).status_code
            == 409
        )
        assert (
            http.post(URL, headers=mine, json={"jurisdiction": "gb", "ivms101": GOOD}).status_code
            == 422
        )
        assert (
            http.post(
                URL, headers=mine, json={"jurisdiction": "GB", "ivms101": GOOD, "extra": 1}
            ).status_code
            == 422
        )
