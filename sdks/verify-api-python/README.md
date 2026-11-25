# zoiko-verify (Python)

Verify your own customers with Zoiko iD **from your own server**. One small package: sign in (a secret, or better a private key), start a verification, read the minimized result, and check the signed webhook. Standard library plus `cryptography`.

> Run this on a server you control. Never put the secret or the private key in a web page or a mobile app. The customer only ever gets the one-time `launch_url`.

## Sign in

```python
from zoiko_verify import VerifyClient

# A private key is the stronger choice: Zoiko iD holds only your public key, so there is nothing to leak.
client = VerifyClient(
    "https://YOUR-API-ADDRESS",
    client_id="zpc_...",
    private_key_pem=open("verify-key.pem", "rb").read(),  # EC P-256 or Ed25519
    key_id="my-key-2026",
)

# Or a secret:
# client = VerifyClient("https://YOUR-API-ADDRESS", client_id="zpc_...", client_secret=os.environ["ZOIKO_CLIENT_SECRET"])
```

The client gets a token, reuses it until a minute before it ends, and on a `401` renews it once and retries that call once. A private-key client signs a fresh single-use assertion for each token request.

## Start and read a verification

```python
session = client.create_session(
    idempotency_key="order-1001-verify",  # yours: repeat the same key after a timeout and you get the same session back
    customer_reference="customer-1001",  # your own id for the customer, not an identity
    country="US",
    purpose_code="customer_onboarding",
)
send_to_customer(session["launch_url"])  # shown once

decision = client.get_decision(session["session_id"])
# decision["outcome"] is verified / not_verified / inconclusive / review_required
```

A created session is **not** an approval. Decide what to do for your customer only from a decision, and only after reading its `assurance` and `checks`: Zoiko iD attests the evidence, and the decision to open an account stays yours.

## Check a webhook

```python
from zoiko_verify import WebhookError, verify_webhook

keys = fetch_signing_keys()  # GET /v1/webhooks/signing-keys, cached; refresh when a delivery names an unknown key id

try:
    event_id = verify_webhook(url=REGISTERED_URL, body=raw_request_body, headers=request.headers, keys=keys)
except WebhookError as e:
    return 401  # e.code says why: missing, stale, digest mismatch, unknown key, invalid signature
```

Pass the **exact raw body**, not re-serialised JSON. Deliveries are at least once and can arrive twice or out of order: deduplicate on the event id and reconcile with `decision_version`.

## Errors

Every error answer raises `VerifyError` with the API's own `code` (for example `INVALID_IDEMPOTENCY_REUSE`, `RATE_LIMITED`, `IP_NOT_ALLOWED`), the HTTP `status`, and `retryable`.

## Sandbox

```python
client.sandbox_session("approved", idempotency_key="sb-1")  # a session already at a simulated result
client.simulate(session_id, "declined")  # move an open sandbox session
```

Sandbox results are synthetic and never a real check.
