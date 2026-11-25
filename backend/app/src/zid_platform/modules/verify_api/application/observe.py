"""What the Verify API reports about itself (ZID-EXT-IDV-001 section 15, epic E20; CR-160).

Counters in the platform's own registry, scraped from the cell's metrics endpoint like every other module's. The platform refuses a tenant, session, client or address label on a metric (those belong in logs and traces only, and
a metric per company would grow without bound), so these carry only the environment, the outcome and the kind of event. A company's own numbers are in its usage screen; these are for whoever runs the service.

- `zid_verify_api_sessions_total{environment,event}`  started, decided or closed sessions
- `zid_verify_api_decisions_total{environment,outcome}`  final results by outcome (verified, not_verified, inconclusive ...)
- `zid_verify_api_auth_total{method,result}`  token requests and bearer checks: issued, denied, ip_denied, rate_limited
- `zid_verify_api_cases_total{kind,event}`  disputes, appeals and erasure requests: raised, reviewed, resolved, rejected, withdrawn
- `zid_verify_api_reviews_total{event}`  review case events
- `zid_verify_api_fraud_total{event}`  fraud graph events
- `zid_verify_api_go_live_total{event}`  production requests: approved, declined, revoked
- `zid_verify_api_prices_total{event}`  operator price decisions: approved, rejected
- `zid_verify_api_approvals_total{event}`  production-credential approvals: requested, approved, rejected, canceled, spent
Webhook delivery health comes from the webhook module's own metrics: the Verify API uses it as it is.
"""

from __future__ import annotations

from zid_telemetry.metrics import REGISTRY

SESSIONS = REGISTRY.counter(
    "zid_verify_api_sessions_total",
    "Verify API sessions by environment and event (started, decided, closed)",
    ("environment", "event"),
)
DECISIONS = REGISTRY.counter(
    "zid_verify_api_decisions_total",
    "Verify API final results by environment and outcome",
    ("environment", "outcome"),
)
AUTH = REGISTRY.counter(
    "zid_verify_api_auth_total",
    "Verify API token requests and bearer checks by method and result",
    ("method", "result"),
)
CASES = REGISTRY.counter(
    "zid_verify_api_cases_total",
    "Verify API disputes, appeals and erasure requests by kind and event",
    ("kind", "event"),
)
PRICES = REGISTRY.counter(
    "zid_verify_api_prices_total",
    "Verify API price decisions by event (approved, rejected)",
    ("event",),
)
APPROVALS = REGISTRY.counter(
    "zid_verify_api_approvals_total",
    "Verify API production-credential approvals by event",
    ("event",),
)
REVIEWS = REGISTRY.counter(
    "zid_verify_api_reviews_total",
    "Verify API review case events by kind (opened, claimed, proposed, returned, escalated, finalized, appealed, revealed, held)",
    ("event",),
)
FRAUD = REGISTRY.counter(
    "zid_verify_api_fraud_total",
    "Verify API fraud graph events by kind (refreshed, triaged, escalated, finding, challenged, proposed, approved, rejected, revoked)",
    ("event",),
)
GO_LIVE = REGISTRY.counter(
    "zid_verify_api_go_live_total",
    "Verify API production requests by event (approved, declined, revoked)",
    ("event",),
)


def auth_result(code: str | None) -> str:
    """The metric word for how an authentication ended: nothing about who it was."""
    return {
        None: "issued",
        "AUTH_INVALID": "denied",
        "IP_NOT_ALLOWED": "ip_denied",
        "RATE_LIMITED": "rate_limited",
    }.get(code, "error")
