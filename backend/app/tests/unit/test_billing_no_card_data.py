"""No card number and no amount in what a browser may send (ZID-COM-CL-002 P07 and P08; ZID-UX-COM-008 UI-11).

A card is entered only on the processor's hosted page, so no request the platform accepts has a field for one; an invoice is paid for the amount the server
holds, so no payment request has a field for an amount or a currency. The check reads the OpenAPI document of the running cell, every route in it, so a new route cannot add either quietly."""

from __future__ import annotations

import re
from typing import Any

from zid_platform.main import create_app
from zid_platform.settings import Settings

from zid_platform.modules.commercial.api.commercial import OnlinePayCompleteBody, PayBody, PaymentCompleteBody

CARD_FIELD = re.compile(r"(^|_)(pan|cvc|cvv|cvn|card_?number|card_?no|card_?num|primary_account|exp_?month|exp_?year|expiry|security_code)($|_)", re.IGNORECASE)
MONEY_FIELD = re.compile(r"(^|_)(amount|total|price|currency|outstanding|balance)($|_)", re.IGNORECASE)


def _request_fields(doc: dict[str, Any]) -> dict[str, set[str]]:
    """Every property of every request body schema of the contract, by 'METHOD path'."""
    schemas = doc.get("components", {}).get("schemas", {})
    out: dict[str, set[str]] = {}

    def props(root: Any) -> set[str]:
        """Property names reachable from a schema, following references once each."""
        found: set[str] = set()
        todo, seen = [root], set()
        while todo:
            node = todo.pop()
            if isinstance(node, list):
                todo.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    todo.append(schemas.get(name, {}))
                continue
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    found |= set(value)
                    todo.extend(value.values())
                elif key in ("items", "allOf", "anyOf", "oneOf", "additionalProperties"):
                    todo.append(value)
        return found

    for path, item in doc.get("paths", {}).items():
        for method, op in item.items():
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                out[f"{method.upper()} {path}"] = props(body)
    return out


def _cell_requests() -> dict[str, set[str]]:
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://x:y@localhost:1/z", token_issuer="http://i", token_audience="http://a", public_base_url="http://a",
            environment="test", cell_id="uk-1", role="cell",
        )
    )  # fmt: skip
    return _request_fields(app.openapi())


def test_no_request_the_cell_accepts_has_a_field_for_a_card() -> None:
    requests = _cell_requests()
    assert len(requests) > 150, "the test is not looking at the cell's routes"
    offenders = {route: sorted(f for f in fields if CARD_FIELD.search(f)) for route, fields in requests.items() if any(CARD_FIELD.search(f) for f in fields)}
    assert not offenders, f"a request accepts something that looks like card data: {offenders}"


def test_a_payment_request_has_no_field_for_an_amount_a_currency_or_a_card() -> None:
    """A payment names the invoice and a one-click key (or the processor's session); what is owed comes from the server."""
    assert set(PayBody.model_fields) == {"idempotency_key"}
    assert set(OnlinePayCompleteBody.model_fields) == {"session_id"}
    assert set(PaymentCompleteBody.model_fields) == {"session_id"}
    for model in (PayBody, OnlinePayCompleteBody, PaymentCompleteBody):
        assert model.model_config.get("extra") == "forbid", model.__name__  # an extra amount or card field is refused, not ignored
        assert not [f for f in model.model_fields if CARD_FIELD.search(f) or MONEY_FIELD.search(f)]


def test_the_routes_that_collect_money_take_no_money() -> None:
    # the customer's own payment routes; an operator's credit note after a refund is another thing and names its amount
    paying = {route: f for route, f in _cell_requests().items() if re.search(r"/v1/invoices/.+/pay|/v1/billing/payment", route)}
    assert len(paying) >= 3, sorted(paying)
    for route, names in paying.items():
        assert not [n for n in names if MONEY_FIELD.search(n) or CARD_FIELD.search(n)], (route, names)
