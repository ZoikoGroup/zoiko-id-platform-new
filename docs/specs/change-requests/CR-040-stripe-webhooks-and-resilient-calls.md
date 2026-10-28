# CR-040: Stripe webhooks, resilient processor calls and plain payment errors

- **Target:** ZID-COM-001 §11 (payment collection), §30 (processor failure) · **Raised by:** Production review of the card flow (CR-034) · **Status:** Open (built)

## Gap

The platform only learned a payment's outcome by asking Stripe in the same request that made it. A payment that finished later (a delayed method, a retry Stripe made), a refund, a dispute or a failed charge never reached the ledger. A single dropped connection to Stripe also showed the owner a raw provider error (`PROVIDER_UNAVAILABLE`).

## Change

1. **Webhook endpoint** `POST /v1/provider-events/stripe`, not under a session. The only credential is Stripe's `Stripe-Signature`: HMAC-SHA256 over `<timestamp>.<raw body>`, constant-time compared, refused when older than five minutes, and nothing in the body is read before it passes. Secrets come from `ZID_STRIPE_WEBHOOK_SECRET_FILE`, one per line so a secret can be rolled without a gap. Unset, the endpoint answers 404 (it does not advertise itself).
2. **Events applied exactly once.** New table `commercial.stripe_event` (event id primary key, type, customer, tenant, outcome; no payload stored), checked under an advisory lock so concurrent deliveries cannot both run. A redelivery answers 200 `DUPLICATE`.
3. **The tenant is never taken from the event.** It is found through the platform's own `payment_customer` link, and the invoice named in the payment's metadata must belong to that customer's account.
4. **What each event does.**

   | Event | Outcome |
   |---|---|
   | `payment_intent.succeeded` | Appended as a CAPTURED attempt (keyed by the event); a past-due account returns to ACTIVE when nothing else is overdue. Already recorded: ignored. Invoice no longer collectable: NEEDS_REVIEW. |
   | `payment_intent.payment_failed` | Appended as a FAILED attempt with Stripe's decline code, unless that payment is already recorded. |
   | `charge.refunded`, `charge.dispute.*` | Recorded NEEDS_REVIEW. A refund or dispute needs a credit note or a decision by a person, so it is never applied automatically. |
   | Anything else, or a payment naming no invoice of this customer | IGNORED or UNMATCHED. |

5. **Resilient calls.** Every Stripe call retries (two pauses: 0.3 s, 1 s) on a network failure, 429 or 5xx. A write is repeated only under an idempotency key, the caller's or one made for the call and held across attempts, so a retry cannot create or charge twice. The connect timeout rises from 5 s to 10 s.
6. **Plain errors.** The payment-setup API answers `PROVIDER_UNAVAILABLE` as a retryable 503 with a sentence a person can act on; the support reference and code stay in the response.

## Rules

- A failure to record an event is a 503, so Stripe redelivers; an event is never acknowledged and lost.
- A forged, unsigned, stale or oversized request is refused before any parsing.
- `NEEDS_REVIEW` and `UNMATCHED` rows are the operator's work queue; showing them in the operator console is follow-up work.

Tests: `test_stripe_webhook_rules.py` (signature, tolerance, rotation, parsing), `test_stripe_retry.py` (retry, idempotency key held, refusal not retried), `test_stripe_webhook_pg.py` (late success once, redelivery, forged call, refund flagged, stray event).
