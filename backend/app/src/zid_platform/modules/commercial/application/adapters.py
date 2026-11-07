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

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
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
class RateTableTax:
    """Tax from a rate table Finance approved: the buyer's country picks a rate, applied to each line's net amount.

    Only an approved table answers; an unapproved or empty one answers None, so nothing is ever taxed from a table nobody signed off. A country the
    table does not list also answers None for the whole invoice, never a guess. Each line's code starts with TABLE_."""

    rates: dict[str, tuple[str, Decimal]]
    exempt: frozenset[str]
    approved: bool

    @classmethod
    def from_file(cls, path: Any) -> RateTableTax:
        import yaml

        try:
            doc = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
        except OSError:
            return cls({}, frozenset(), False)
        rates: dict[str, tuple[str, Decimal]] = {}
        for country, spec in (doc.get("rates") or {}).items():
            rate = Decimal(str(spec["rate"]))
            if not Decimal(0) <= rate < Decimal(1):
                raise ValueError(f"tax rate for {country} must be at least 0 and below 1")
            rates[str(country).upper()] = (str(spec["tax"]).upper().replace(" ", "_"), rate)
        approved = bool(doc.get("approved")) and bool(doc.get("approved_by")) and bool(doc.get("approved_on"))
        return cls(rates, frozenset(str(k) for k in doc.get("exempt") or []), approved)

    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        if not self.approved or not country or country.upper() not in self.rates:
            return None
        tax, rate = self.rates[country.upper()]
        code = f"TABLE_{tax}_{format(rate * 100, 'f').rstrip('0').rstrip('.')}"[:40]
        out: dict[int, TaxLine] = {}
        for ln in lines:
            taxed = ln.net > 0 and ln.kind not in self.exempt
            amount = (ln.net * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if taxed else Decimal("0.00")
            out[ln.seq] = TaxLine(code if taxed else f"TABLE_{tax}_0", amount)
        return out


@dataclass(frozen=True, slots=True)
class FallbackTax:
    """The first engine that can answer: Stripe Tax when it does, else the approved rate table, else None (still needs a zero-tax override)."""

    primary: TaxPort
    fallback: TaxPort

    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        got = await self.primary.lines_for(currency=currency, region=region, country=country, lines=lines)
        if got is not None:
            return got
        return await self.fallback.lines_for(currency=currency, region=region, country=country, lines=lines)


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
        timeout=httpx.Timeout(20.0, connect=10.0),
        transport=transport,
    )


# Pauses between attempts after a network failure, a 429 or a 5xx. A tuple, so a test can make it empty.
RETRY_PAUSES: tuple[float, ...] = (0.3, 1.0)


async def _send(client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> httpx.Response:
    """One Stripe request, retried when the network or Stripe is briefly unavailable. A POST is only ever repeated
    under an `Idempotency-Key` (the caller's, or one made here and held across the attempts), so a retry after a
    timeout cannot create or charge twice. The last failure is raised or returned for the caller to classify."""
    headers = dict(kw.pop("headers", None) or {})
    if method.upper() == "POST":
        headers.setdefault("Idempotency-Key", str(uuid.uuid4()))
    attempt = 0
    while True:
        try:
            res = await client.request(method, path, headers=headers, **kw)
        except httpx.HTTPError:
            if attempt >= len(RETRY_PAUSES):
                raise
        else:
            if res.status_code != 429 and res.status_code < 500:
                return res
            if attempt >= len(RETRY_PAUSES):
                return res
        await asyncio.sleep(RETRY_PAUSES[attempt])
        attempt += 1


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
        try:
            method = await self._default_payment_method(customer_ref)
        except httpx.HTTPError:
            log.warning("stripe customers unreachable")
            return PaymentResult("FAILED", failure_code="PROVIDER_UNAVAILABLE")
        if method is None:
            return PaymentResult("FAILED", failure_code="NO_PAYMENT_METHOD")
        if method == "UNAVAILABLE":
            return PaymentResult("FAILED", failure_code="PROVIDER_UNAVAILABLE")
        form = {
            "amount": str(minor),
            "currency": currency.lower(),
            "customer": customer_ref,
            "payment_method": method,
            "confirm": "true",
            "off_session": "true",
            "metadata[invoice_id]": invoice_id,
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
        }
        try:
            res = await _send(
                self._client,
                "POST",
                "/v1/payment_intents",
                data=form,
                headers={"Idempotency-Key": idempotency_key},
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

    async def _default_payment_method(self, customer_ref: str) -> str | None:
        """The customer's saved payment method: Stripe's off-session PaymentIntent needs it named (it does not
        pick `invoice_settings.default_payment_method` by itself; only the legacy `default_source` is implicit).
        None: the customer has nothing saved or does not exist. "UNAVAILABLE": Stripe could not answer."""
        res = await _send(self._client, "GET", f"/v1/customers/{customer_ref}")
        if res.status_code >= 500 or res.status_code == 429:
            return "UNAVAILABLE"
        if not res.is_success:
            return None
        body: dict[str, Any] = res.json() if res.content else {}
        settings = body.get("invoice_settings") or {}
        found = settings.get("default_payment_method") or body.get("default_source")
        if isinstance(found, dict):
            found = found.get("id")
        return str(found) if found else None

    async def aclose(self) -> None:
        await self._client.aclose()


class PaymentSetupError(Exception):
    """The processor could not complete a payment-method step. `code` is safe to show; nothing else is."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PaymentMethodSummary:
    """What a company may see of its saved card: never the number, only what identifies it."""

    brand: str
    last4: str
    exp_month: int
    exp_year: int


@dataclass(frozen=True, slots=True)
class OnlinePayment:
    """A payment the buyer made on Stripe's hosted page: what the platform records, never a card."""

    payment_intent: str
    invoice_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ActivationPayment:
    """The first month's platform fee paid on Stripe's hosted page for one order (CR-062)."""

    payment_intent: str
    order_id: str
    amount_minor: int
    currency: str


class StripeCustomers:
    """A company's saved payment method, collected on Stripe's hosted page (Checkout in setup mode): the card number
    never reaches the platform or the browser app. The platform stores only the Stripe customer id, sends the buyer to
    Stripe, and on return verifies the session server-side before making the saved card the customer's default, which
    is the card `StripePayments` charges off-session."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = STRIPE_API,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = _client(api_key, base_url, transport)

    async def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            res = await _send(self._client, method, path, **kw)
        except httpx.HTTPError as e:
            log.warning(
                "stripe %s unreachable", path.split("/")[2] if path.count("/") > 1 else path
            )
            raise PaymentSetupError("PROVIDER_UNAVAILABLE") from e
        if res.status_code >= 500 or res.status_code == 429:
            raise PaymentSetupError("PROVIDER_UNAVAILABLE")
        body: dict[str, Any] = res.json() if res.content else {}
        if not res.is_success:
            raise PaymentSetupError(
                str((body.get("error") or {}).get("code") or "PROVIDER_ERROR").upper()[:60]
            )
        return body

    async def create_customer(self, *, name: str, tenant_id: str, idempotency_key: str) -> str:
        body = await self._call(
            "POST",
            "/v1/customers",
            data={"name": name[:300], "metadata[tenant_id]": tenant_id},
            headers={"Idempotency-Key": idempotency_key},
        )
        return str(body["id"])

    async def start_setup(self, *, customer_ref: str, success_url: str, cancel_url: str) -> str:
        """The URL of Stripe's hosted page where the buyer enters a card to save. `success_url` carries the session id."""
        body = await self._call(
            "POST",
            "/v1/checkout/sessions",
            data={
                "mode": "setup",
                "customer": customer_ref,
                "payment_method_types[0]": "card",
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        return str(body["url"])

    async def complete_setup(self, *, session_id: str, customer_ref: str) -> PaymentMethodSummary:
        """Verify a returned session belongs to this customer and finished, then make its card the default."""
        if not session_id.startswith("cs_") or len(session_id) > 200:
            raise PaymentSetupError("SESSION_INVALID")
        session = await self._call(
            "GET", f"/v1/checkout/sessions/{session_id}", params={"expand[]": "setup_intent"}
        )
        intent = session.get("setup_intent")
        if (
            session.get("customer") != customer_ref
            or session.get("status") != "complete"
            or not isinstance(intent, dict)
            or intent.get("status") != "succeeded"
            or not intent.get("payment_method")
        ):
            raise PaymentSetupError("SETUP_NOT_COMPLETE")
        method = str(intent["payment_method"])
        await self._call(
            "POST",
            f"/v1/customers/{customer_ref}",
            data={"invoice_settings[default_payment_method]": method},
        )
        return await self._summary(method)

    async def start_payment(
        self,
        *,
        customer_ref: str,
        invoice_id: str,
        invoice_label: str,
        amount: Decimal,
        currency: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """The address of Stripe's hosted page where the buyer pays one invoice, with the bank's own confirmation
        step (3-D Secure) when it asks for one. The invoice is named in the payment's metadata so the webhook and the
        return both find it; the buyer's card is saved for nothing else."""
        body = await self._call(
            "POST",
            "/v1/checkout/sessions",
            data={
                "mode": "payment",
                "customer": customer_ref,
                "payment_method_types[0]": "card",
                "line_items[0][quantity]": "1",
                "line_items[0][price_data][currency]": currency.lower(),
                "line_items[0][price_data][unit_amount]": str(to_minor_units(amount, currency)),
                "line_items[0][price_data][product_data][name]": invoice_label[:200],
                "payment_intent_data[metadata][invoice_id]": invoice_id,
                "metadata[invoice_id]": invoice_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        return str(body["url"])

    async def start_activation_payment(
        self,
        *,
        customer_ref: str,
        order_id: str,
        label: str,
        amount: Decimal,
        currency: str,
        success_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Stripe's hosted page for the first month's platform fee of one order. Returns the page's address and the session's id, which is kept so a payment made
        while the browser was closed can still be found. The order is named in the payment's metadata."""
        body = await self._call(
            "POST",
            "/v1/checkout/sessions",
            data={
                "mode": "payment",
                "customer": customer_ref,
                "payment_method_types[0]": "card",
                "line_items[0][quantity]": "1",
                "line_items[0][price_data][currency]": currency.lower(),
                "line_items[0][price_data][unit_amount]": str(to_minor_units(amount, currency)),
                "line_items[0][price_data][product_data][name]": label[:200],
                "payment_intent_data[metadata][order_id]": order_id,
                "metadata[order_id]": order_id,
                "metadata[purpose]": "plan_activation",
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        return str(body["url"]), str(body["id"])

    async def complete_activation_payment(self, *, session_id: str, customer_ref: str) -> ActivationPayment:
        """Read the session back from Stripe: it must be this customer's, a plan-activation payment, paid, and name an order."""
        if not session_id.startswith("cs_") or len(session_id) > 200:
            raise PaymentSetupError("SESSION_INVALID")
        session = await self._call("GET", f"/v1/checkout/sessions/{session_id}")
        meta = session.get("metadata") or {}
        intent = session.get("payment_intent")
        if isinstance(intent, dict):
            intent = intent.get("id")
        if (
            session.get("mode") != "payment"
            or session.get("customer") != customer_ref
            or session.get("payment_status") != "paid"
            or meta.get("purpose") != "plan_activation"
            or not intent
            or not meta.get("order_id")
            or not isinstance(session.get("amount_total"), int)
        ):
            raise PaymentSetupError("PAYMENT_NOT_COMPLETE")
        return ActivationPayment(
            payment_intent=str(intent),
            order_id=str(meta["order_id"]),
            amount_minor=int(session["amount_total"]),
            currency=str(session.get("currency") or "").upper(),
        )

    async def complete_payment(self, *, session_id: str, customer_ref: str) -> OnlinePayment:
        """Verify a returned payment session is this customer's and was paid, and say what was paid."""
        if not session_id.startswith("cs_") or len(session_id) > 200:
            raise PaymentSetupError("SESSION_INVALID")
        session = await self._call("GET", f"/v1/checkout/sessions/{session_id}")
        meta = session.get("metadata") or {}
        intent = session.get("payment_intent")
        if isinstance(intent, dict):
            intent = intent.get("id")
        if (
            session.get("mode") != "payment"
            or session.get("customer") != customer_ref
            or session.get("payment_status") != "paid"
            or not intent
            or not meta.get("invoice_id")
            or not isinstance(session.get("amount_total"), int)
        ):
            raise PaymentSetupError("PAYMENT_NOT_COMPLETE")
        return OnlinePayment(
            payment_intent=str(intent),
            invoice_id=str(meta["invoice_id"]),
            amount_minor=int(session["amount_total"]),
            currency=str(session.get("currency") or "").upper(),
        )

    async def current_method(self, customer_ref: str) -> PaymentMethodSummary | None:
        customer = await self._call("GET", f"/v1/customers/{customer_ref}")
        method = (customer.get("invoice_settings") or {}).get("default_payment_method")
        return await self._summary(str(method)) if method else None

    async def _summary(self, method_id: str) -> PaymentMethodSummary:
        pm = await self._call("GET", f"/v1/payment_methods/{method_id}")
        card = pm.get("card") or {}
        return PaymentMethodSummary(
            brand=str(card.get("brand") or pm.get("type") or "card"),
            last4=str(card.get("last4") or ""),
            exp_month=int(card.get("exp_month") or 0),
            exp_year=int(card.get("exp_year") or 0),
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
            res = await _send(self._client, "POST", "/v1/tax/calculations", data=form)
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


# ------------------------------------------------------------------------------- refunds and disputes (ZID-UX-COM-009 F03)


@dataclass(frozen=True, slots=True)
class RefundResult:
    """What the processor said about one refund. `UNAVAILABLE` is not a failure: the request may or may not have been accepted, so the case is UNKNOWN and is
    resolved by asking the processor what it holds, never by sending again."""

    outcome: str  # SENT (the processor answered) | REFUSED (it said no) | UNAVAILABLE (no answer)
    refund_ref: str | None = None
    status: str | None = (
        None  # the processor's own word: pending, succeeded, failed, canceled, requires_action
    )
    amount_minor: int | None = None
    failure_code: str | None = None


class RefundPort(Protocol):
    async def create(
        self,
        *,
        payment_intent: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        request_ref: str,
    ) -> RefundResult: ...

    async def retrieve(self, refund_ref: str) -> RefundResult: ...

    async def find(self, *, payment_intent: str, request_ref: str) -> RefundResult | None: ...

    async def retrieve_dispute(self, dispute_ref: str) -> dict[str, Any] | None: ...

    async def payout_transactions(
        self, payout_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None: ...


@dataclass
class RecordingRefunds:
    """Local and test processor. Scripts what it answers (`next_create`, `next_retrieve`) and records what it was asked, so a test can prove a refund was sent once."""

    next_create: list[RefundResult] = field(default_factory=list)
    next_retrieve: list[RefundResult] = field(default_factory=list)
    held: dict[str, RefundResult] = field(
        default_factory=dict
    )  # by request_ref: what the processor holds
    disputes: dict[str, dict[str, Any]] = field(default_factory=dict)
    payouts: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = field(
        default_factory=dict
    )  # payout id -> (payout, its balance transactions)
    sent: list[tuple[str, int, str, str]] = field(
        default_factory=list
    )  # payment_intent, amount, idempotency key, request ref

    async def create(
        self,
        *,
        payment_intent: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        request_ref: str,
    ) -> RefundResult:
        self.sent.append((payment_intent, amount_minor, idempotency_key, request_ref))
        if idempotency_key in {
            k for _, _, k, _ in self.sent[:-1]
        }:  # the processor remembers a key: the same one never refunds twice
            return self.held.get(request_ref) or RefundResult(
                "SENT", f"re_{len(self.sent):06d}", "pending", amount_minor
            )
        result = (
            self.next_create.pop(0)
            if self.next_create
            else RefundResult("SENT", f"re_{len(self.sent):06d}", "pending", amount_minor)
        )
        if result.outcome == "SENT":
            self.held[request_ref] = result
        return result

    async def retrieve(self, refund_ref: str) -> RefundResult:
        if self.next_retrieve:
            return self.next_retrieve.pop(0)
        for r in self.held.values():
            if r.refund_ref == refund_ref:
                return r
        return RefundResult("UNAVAILABLE")

    async def find(self, *, payment_intent: str, request_ref: str) -> RefundResult | None:
        return self.held.get(request_ref)

    async def retrieve_dispute(self, dispute_ref: str) -> dict[str, Any] | None:
        return self.disputes.get(dispute_ref)

    async def payout_transactions(
        self, payout_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        return self.payouts.get(payout_id)


class StripeRefunds:
    """Refunds and the authoritative reads behind them (Stripe's Refunds and Disputes APIs; the Payments dispute workflow, never the Issuing one). A refund is a POST under an
    `Idempotency-Key` that is the case's own, so a retry after a timeout cannot refund twice; the case id travels as metadata so the processor can be asked what it holds
    for it. The API version and the merchant account are configuration and are verified against the approved ones before this is used with money (owner pack)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = STRIPE_API,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = _client(api_key, base_url, transport)

    @staticmethod
    def _result(res: httpx.Response) -> RefundResult:
        body: dict[str, Any] = res.json() if res.content else {}
        if res.status_code >= 500 or res.status_code == 429:
            return RefundResult("UNAVAILABLE")
        if res.is_success:
            amount = body.get("amount")
            return RefundResult(
                "SENT",
                str(body["id"]) if body.get("id") else None,
                str(body.get("status") or ""),
                int(amount) if isinstance(amount, int) else None,
                str(body["failure_reason"])[:60] if body.get("failure_reason") else None,
            )
        err = body.get("error", {})
        return RefundResult(
            "REFUSED",
            failure_code=str(err.get("code") or err.get("type") or "PROVIDER_REFUSED").upper()[:60],
        )

    async def create(
        self,
        *,
        payment_intent: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        request_ref: str,
    ) -> RefundResult:
        form = {
            "payment_intent": payment_intent,
            "amount": str(amount_minor),
            "metadata[zid_refund]": request_ref,
            "reason": "requested_by_customer",
        }
        try:
            res = await _send(
                self._client,
                "POST",
                "/v1/refunds",
                data=form,
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.HTTPError:
            log.warning("stripe refunds unreachable")
            return RefundResult("UNAVAILABLE")
        return self._result(res)

    async def retrieve(self, refund_ref: str) -> RefundResult:
        try:
            res = await _send(self._client, "GET", f"/v1/refunds/{refund_ref}")
        except httpx.HTTPError:
            return RefundResult("UNAVAILABLE")
        return self._result(res)

    async def find(self, *, payment_intent: str, request_ref: str) -> RefundResult | None:
        """What the processor already holds for this case, found by the metadata the case sent. None only when the processor answered and holds nothing."""
        try:
            res = await _send(
                self._client,
                "GET",
                "/v1/refunds",
                params={"payment_intent": payment_intent, "limit": "100"},
            )
        except httpx.HTTPError:
            return RefundResult("UNAVAILABLE")
        if res.status_code >= 500 or res.status_code == 429 or not res.is_success:
            return RefundResult("UNAVAILABLE")
        for row in (res.json() if res.content else {}).get("data", []):
            if (
                isinstance(row, dict)
                and (row.get("metadata") or {}).get("zid_refund") == request_ref
            ):
                amount = row.get("amount")
                return RefundResult(
                    "SENT",
                    str(row.get("id")),
                    str(row.get("status") or ""),
                    int(amount) if isinstance(amount, int) else None,
                )
        return None

    async def retrieve_dispute(self, dispute_ref: str) -> dict[str, Any] | None:
        try:
            res = await _send(self._client, "GET", f"/v1/disputes/{dispute_ref}")
        except httpx.HTTPError:
            return None
        return res.json() if res.is_success and res.content else None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def payout_transactions(
        self, payout_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """The payout and every balance transaction in it, each with its source expanded, so a Controller can import the processor's own payout detail. None when the processor
        holds no such payout or cannot be reached."""
        try:
            head = await _send(self._client, "GET", f"/v1/payouts/{payout_id}")
            if not head.is_success:
                return None
            rows: list[dict[str, Any]] = []
            after: str | None = None
            for _ in range(50):  # at most 5,000 transactions in one payout
                params = {"payout": payout_id, "limit": "100", "expand[]": "data.source"}
                if after:
                    params["starting_after"] = after
                page = await _send(self._client, "GET", "/v1/balance_transactions", params=params)
                if not page.is_success:
                    return None
                body = page.json()
                data = [r for r in body.get("data", []) if isinstance(r, dict)]
                rows += data
                if not body.get("has_more") or not data:
                    break
                after = str(data[-1]["id"])
        except httpx.HTTPError:
            return None
        return head.json(), rows
