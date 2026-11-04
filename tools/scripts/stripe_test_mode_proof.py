"""Test-mode proof of the refund and dispute adapter against Stripe's real TEST API (CR-058). Opt-in, never part of CI.

    python tools/scripts/stripe_test_mode_proof.py [path-to-secret-key-file]

It refuses any key that is not a test key, creates test payments, sends one refund twice under one key (one refund), finds it by the case id carried as metadata, refuses an
over-refund, and reads a test dispute with its deadline. It prints outcomes only, never the key.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "app" / "src"))

import httpx  # noqa: E402

from zid_platform.modules.commercial.application.adapters import StripeRefunds  # noqa: E402

KEY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "backend" / ".zid" / "stripe_key").read_text(encoding="utf-8").strip()
assert KEY.startswith("sk_test_"), "refusing to run against anything but a test key"


async def main() -> None:
    async with httpx.AsyncClient(base_url="https://api.stripe.com", auth=(KEY, ""), timeout=20) as c:

        async def pay(card: str, amount: int) -> dict:
            r = await c.post(
                "/v1/payment_intents",
                data={"amount": amount, "currency": "usd", "payment_method": card, "confirm": "true", "payment_method_types[]": "card"},
                headers={"Idempotency-Key": f"proof-{uuid.uuid4()}"},
            )
            return r.json()

        ok = await pay("pm_card_visa", 5000)
        print("payment:", ok.get("status"))
        refunds = StripeRefunds(KEY)
        case = str(uuid.uuid4())
        key = f"refund-{case}"
        first = await refunds.create(payment_intent=ok["id"], amount_minor=2000, currency="USD", idempotency_key=key, request_ref=case)
        print("first send:", first.outcome, first.status, first.amount_minor)
        again = await refunds.create(payment_intent=ok["id"], amount_minor=2000, currency="USD", idempotency_key=key, request_ref=case)
        print("same key again is the same refund:", again.refund_ref == first.refund_ref)
        held = await refunds.find(payment_intent=ok["id"], request_ref=case)
        print("found by case id:", None if held is None else (held.refund_ref == first.refund_ref, held.status))
        got = await refunds.retrieve(first.refund_ref)
        print("retrieve:", got.outcome, got.status, got.amount_minor)
        print("a case never sent is held by nobody:", await refunds.find(payment_intent=ok["id"], request_ref=str(uuid.uuid4())) is None)
        over = await refunds.create(payment_intent=ok["id"], amount_minor=999_900, currency="USD", idempotency_key=f"refund-{uuid.uuid4()}", request_ref=str(uuid.uuid4()))
        print("more than was paid:", over.outcome, over.failure_code)

        bad = await pay("pm_card_createDispute", 7000)
        await asyncio.sleep(4)
        found = await c.get("/v1/disputes", params={"payment_intent": bad["id"], "limit": 1})
        items = found.json().get("data", [])
        print("disputes listed:", len(items))
        if items:
            d = await refunds.retrieve_dispute(items[0]["id"])
            print("dispute read:", None if d is None else (d.get("status"), d.get("reason"), "deadline given" if (d.get("evidence_details") or {}).get("due_by") else "no deadline"))


asyncio.run(main())
