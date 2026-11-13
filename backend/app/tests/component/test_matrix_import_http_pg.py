"""The operator routes of the market matrix over HTTP, with the bulk row import (ZID-COM-WF-2026-11 WF-I01): all or nothing, every problem listed by row."""

from __future__ import annotations

import uuid

from conftest import CELL
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS
from operator_fixture import activate_grant, operators
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings

BASE = "/v1/commercial/market-matrices"


def row(family: str = "API", country: str = "ES", **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "family": family, "country": country, "seller_entity": "Zoiko Tech SL", "merchant_route": "stripe-eu", "currency": "EUR",
        "tax_class": "B2B-SERVICES", "data_region": "eu-central", "offer_ref": "offer:2026-11/api-es", "legal_basis_ref": "legal:memo-201",
        "release_evidence_ref": "release:G6-2026-11-20", "payment_configured": True, "tax_configured": True,
    }  # fmt: skip
    return {**base, **over}


def test_the_operator_matrix_routes_and_the_all_or_nothing_import(rt_url: str) -> None:
    maker, bob, carol = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    ops = operators(
        rt_url,
        {
            maker: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1",
            cell_uuid=CELL, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,),
        ),
        keyring=ops.ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as api:
        h = ops.bearer(maker)
        assert api.get(BASE, headers=h).status_code in (200, 403)
        activate_grant(
            api,
            ops,
            maker,
            (bob, carol),
            capability="commercial.catalog.manage",
            resource_scope="commercial:catalogue",
        )
        made = api.post(BASE, json={"note": "import test"}, headers=h)
        assert made.status_code == 201, made.text
        mid = made.json()["matrix_id"]

        # one bad row, one duplicate and one good row: nothing is written and every problem is listed by row
        bad = api.post(
            f"{BASE}/{mid}/rows:import", headers=h,
            json={"rows": [row(), row(seller_entity=" "), row("SIGN", "ES"), row("API", "ES", offer_ref="offer:2")]},
        )  # fmt: skip
        assert bad.status_code == 422, bad.text
        err = bad.json()["error"]
        assert err["code"] == "MARKET_MATRIX_IMPORT_REJECTED"
        issues = {d["field"]: d["issue"] for d in err["details"]}
        assert "SELLER_ENTITY_MISSING" in issues["row 2 API ES"]
        assert "PROVIDER_RIGHTS_REF_MISSING" in issues["row 3 SIGN ES"]
        assert "DUPLICATE_IN_IMPORT" in issues["row 4 API ES"]
        assert api.get(f"{BASE}/{mid}", headers=h).json()["rows"] == [], (
            "all or nothing: not even the good row was written"
        )

        ok = api.post(
            f"{BASE}/{mid}/rows:import",
            headers=h,
            json={
                "rows": [
                    row(),
                    row("API", "PT", offer_ref="offer:2026-11/api-pt"),
                    row("DOC", "ES"),
                ]
            },
        )
        assert ok.status_code == 200 and ok.json()["rows_written"] == 3, ok.text
        detail = api.get(f"{BASE}/{mid}", headers=h).json()
        assert [(r["family"], r["country"]) for r in detail["rows"]] == [
            ("API", "ES"),
            ("API", "PT"),
            ("DOC", "ES"),
        ]
        assert detail["incomplete_rows"] == [] and detail["missing_approvals"] == [
            "PRODUCT",
            "FINANCE",
            "LEGAL_PRIVACY",
            "SECURITY_QA",
        ]

        # a re-import replaces rows in place; an empty or oversized import is refused by the schema
        again = api.post(
            f"{BASE}/{mid}/rows:import",
            headers=h,
            json={"rows": [row(merchant_route="stripe-eu-2")]},
        )
        assert again.status_code == 200
        assert (
            next(
                r
                for r in api.get(f"{BASE}/{mid}", headers=h).json()["rows"]
                if r["country"] == "ES" and r["family"] == "API"
            )["merchant_route"]
            == "stripe-eu-2"
        )
        assert (
            api.post(f"{BASE}/{mid}/rows:import", headers=h, json={"rows": []}).status_code == 422
        )
        assert (
            api.post(
                f"{BASE}/{uuid.uuid4()}/rows:import", headers=h, json={"rows": [row()]}
            ).status_code
            == 404
        )
