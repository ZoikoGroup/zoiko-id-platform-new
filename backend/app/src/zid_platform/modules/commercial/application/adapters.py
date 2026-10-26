"""Tax and payment ports (ZID-COM-001 §11, §30; CR-027).

The platform never computes tax and never holds card data. Tax comes from a `TaxPort` (a tax engine the
merchant of record chooses); payment goes through a `PaymentPort` (a processor that returns a token and a
status only). The processor is Stripe (`StripePayments`) and the tax engine is Stripe Tax
(`StripeTax`), both behind these ports; a cell with no Stripe key configured falls back to:

- `NoTaxConfigured` answers "not configured", which blocks issuing unless an operator explicitly records a
  zero-tax override for that invoice;
- `RecordingPayments` is a local/test processor that records attempts and can be told to decline. A deployed
  cell with no processor configured refuses to collect (`PAYMENT_NOT_CONFIGURED`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

log = logging.getLogger("zid.commercial.adapters")


@dataclass(frozen=True, slots=True)
class TaxableLine:
    seq: int
    kind: str
    sku: str | None
    net: Decimal


@dataclass(frozen=True, slots=True)
class TaxLine:
    code: str
    amount: Decimal


class TaxPort(Protocol):
    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        """Tax per invoice line (by seq), or None when tax cannot be determined for this invoice.

        `country` is the buyer's ISO country from its commercial account; without it no engine can place
        the supply, so the answer is None (the invoice then needs an explicit zero-tax override)."""
        ...


class NoTaxConfigured:
    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        return None


@dataclass(frozen=True, slots=True)
class PaymentResult:
    status: str  # CAPTURED | FAILED
    provider_ref: str | None = None
    failure_code: str | None = None


class PaymentPort(Protocol):
    async def capture(
        self,
        *,
        invoice_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_ref: str | None,
    ) -> PaymentResult: ...


@dataclass
class RecordingPayments:
    """Local/test processor: captures unless told to decline the next attempts. Never sees card data."""

    decline_next: int = 0
    captured: list[tuple[str, Decimal, str]] = field(default_factory=list)

    async def capture(
        self,
        *,
        invoice_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_ref: str | None = None,
    ) -> PaymentResult:
        if self.decline_next > 0:
            self.decline_next -= 1
            return PaymentResult("FAILED", failure_code="CARD_DECLINED")
        self.captured.append((invoice_id, amount, idempotency_key))
        return PaymentResult("CAPTURED", provider_ref=f"rec_{len(self.captured):06d}")


# ------------------------------------------------------------------------------- Stripe

STRIPE_API = "https://api.stripe.com"
# ISO 4217 currencies Stripe treats as having no minor unit; the three-decimal ones are listed too. Every other
# currency handled here has two decimals.
_ZERO_DECIMAL = frozenset(
    [
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    ]
)
_THREE_DECIMAL = frozenset(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"])


def _exponent(currency: str) -> int:
    c = currency.upper()
    return 0 if c in _ZERO_DECIMAL else 3 if c in _THREE_DECIMAL else 2


def to_minor_units(amount: Decimal, currency: str) -> int:
    """A decimal amount as Stripe's integer minor units. Refuses an amount with more precision than the
    currency has, so a rounding difference can never silently change what is charged."""
    scaled = amount * (Decimal(10) ** _exponent(currency))
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more precision than {currency.upper()} allows")
    return int(scaled)


def from_minor_units(minor: int, currency: str) -> Decimal:
    exp = _exponent(currency)
    return (Decimal(minor) / (Decimal(10) ** exp)).quantize(Decimal(1).scaleb(-exp))


def _client(
    api_key: str, base_url: str, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        auth=httpx.BasicAuth(api_key, ""),
        headers={"Stripe-Version": "2025-09-30.clover"},
        timeout=httpx.Timeout(15.0, connect=5.0),
        transport=transport,
    )


class StripePayments:
    """Collects an invoice's outstanding amount from the buyer's saved payment method with an off-session
    PaymentIntent. The platform sends an amount, a currency, an invoice reference and the Stripe customer id
    only; it never sees a card number. The idempotency key is Stripe's `Idempotency-Key`, so a retry after a
    timeout can never charge twice. A processor outage is a FAILED attempt with `PROVIDER_UNAVAILABLE`, not
    an exception, so the dunning policy stays in charge of what happens next."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = STRIPE_API,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = _client(api_key, base_url, transport)

    async def capture(
        self,
        *,
        invoice_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_ref: str | None = None,
    ) -> PaymentResult:
        if not customer_ref:
            return PaymentResult("FAILED", failure_code="NO_PAYMENT_METHOD")
        try:
            minor = to_minor_units(amount, currency)
        except ValueError:
            return PaymentResult("FAILED", failure_code="AMOUNT_INVALID")
        form = {
            "amount": str(minor),
            "currency": currency.lower(),
            "customer": customer_ref,
            "confirm": "true",
            "off_session": "true",
            "metadata[invoice_id]": invoice_id,
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
        }
        try:
            res = await self._client.post(
                "/v1/payment_intents", data=form, headers={"Idempotency-Key": idempotency_key}
            )
        except httpx.HTTPError:
            log.warning("stripe payment_intents unreachable")
            return PaymentResult("FAILED", failure_code="PROVIDER_UNAVAILABLE")
        body: dict[str, Any] = res.json() if res.content else {}
        if res.status_code >= 500 or res.status_code == 429:
            return PaymentResult("FAILED", failure_code="PROVIDER_UNAVAILABLE")
        if res.is_success and body.get("status") == "succeeded":
            return PaymentResult("CAPTURED", provider_ref=str(body["id"]))
        if res.is_success:  # requires_action, processing and so on: not captured now
            return PaymentResult(
                "FAILED",
                provider_ref=str(body["id"]) if body.get("id") else None,
                failure_code=f"PAYMENT_{str(body.get('status', 'UNKNOWN')).upper()}"[:60],
            )
        err = body.get("error", {})
        pi = err.get("payment_intent")
        code = str(err.get("decline_code") or err.get("code") or "PROVIDER_ERROR").upper()
        return PaymentResult(
            "FAILED",
            provider_ref=str(pi["id"]) if isinstance(pi, dict) and pi.get("id") else None,
            failure_code=code[:60],
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class StripeTax:
    """Asks Stripe Tax to calculate tax for the invoice's lines (tax is never computed here). Without the
    buyer's country, or when Stripe cannot place the supply, the answer is None: the invoice cannot be issued
    with tax unless an operator explicitly records a zero-tax override. Amounts are exclusive of tax."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = STRIPE_API,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = _client(api_key, base_url, transport)

    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        if not country:
            return None
        positive = [ln for ln in lines if ln.net > 0]
        if not positive:
            return {}
        form: dict[str, str] = {
            "currency": currency.lower(),
            "customer_details[address][country]": country,
            "customer_details[address_source]": "billing",
        }
        for i, ln in enumerate(positive):
            form[f"line_items[{i}][amount]"] = str(to_minor_units(ln.net, currency))
            form[f"line_items[{i}][reference]"] = str(ln.seq)
            form[f"line_items[{i}][tax_behavior]"] = "exclusive"
        try:
            res = await self._client.post("/v1/tax/calculations", data=form)
        except httpx.HTTPError:
            log.warning("stripe tax unreachable")
            return None
        if not res.is_success:
            log.warning("stripe tax refused: %s", res.status_code)
            return None
        out: dict[int, TaxLine] = {}
        for item in res.json().get("line_items", {}).get("data", []):
            breakdown = item.get("tax_breakdown") or []
            first = breakdown[0].get("tax_rate_details", {}) if breakdown else {}
            kind = str(first.get("tax_type") or first.get("display_name") or "TAX")
            pct = str(first.get("percentage_decimal") or "0")
            code = f"{kind}_{pct}".upper().replace(" ", "_").replace("-", "_")[:40]
            out[int(item["reference"])] = TaxLine(
                code, from_minor_units(int(item["amount_tax"]), currency)
            )
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
