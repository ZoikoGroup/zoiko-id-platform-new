"""The Provider Lab (ZID-UAT-005 WF-04): inject each documented fault and report what the PLATFORM did, against what it must do.

It exercises the platform's own guards (webhook verification, entitlement and market checks, controlled failover, outcome routing). It talks to no
provider and proves nothing about any provider's own behaviour; a real adapter's conformance needs that provider's sandbox. Each row is a case a
tester can attach to a run as developer evidence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from zid_platform.modules.privacy.ports import route_outcome
from zid_platform.modules.verification.ports import (
    Entitlement,
    EntitlementStatus,
    RouteRequest,
    entitlement_refusals,
    route,
    sign_webhook,
    verify_webhook,
)

SECRET = b"provider-lab-secret"
BODY = b'{"event":"check.completed"}'


def _headers(
    now: datetime, nonce: str, secret: bytes = SECRET, *, tamper: bool = False
) -> dict[str, str]:
    ts = int(now.timestamp())
    sig = sign_webhook(secret, ts, nonce, BODY)
    return {
        "x-zid-timestamp": str(ts),
        "x-zid-nonce": nonce,
        "x-zid-signature": ("0" * len(sig)) if tamper else sig,
    }


def _entitlement(today: date, **over: Any) -> Entitlement:
    base: dict[str, Any] = {
        "provider_id": "acme", "service_code": "id.document", "countries": frozenset({"GB"}), "purposes": frozenset({"onboarding"}),
        "processing_regions": frozenset({"uk"}), "valid_from": today - timedelta(days=30), "valid_until": today + timedelta(days=30), "assurance": 4,
    }  # fmt: skip
    return Entitlement(**{**base, **over})


def run_all(now: datetime | None = None) -> list[dict[str, Any]]:
    """Every fault with the guard's observed answer. `held` is true when the platform did what the document requires."""
    now = now or datetime.now(UTC)
    today = now.date()
    req = RouteRequest(
        country="GB",
        service_code="id.document",
        purpose="onboarding",
        region="uk",
        required_assurance=3,
    )
    rows: list[dict[str, Any]] = []

    def webhook(fault: str, headers: dict[str, str], seen: set[str], expect: str | None) -> None:
        verdict = verify_webhook(BODY, headers, [SECRET], now=now, seen_nonces=seen)
        observed = None if verdict.ok else verdict.refusal.value  # type: ignore[union-attr]
        rows.append(
            {
                "fault": fault,
                "expected": expect or "ACCEPTED",
                "observed": observed or "ACCEPTED",
                "held": observed == expect,
            }
        )

    webhook("BAD_HMAC", _headers(now, "n1", tamper=True), set(), "BAD_SIGNATURE")
    webhook("REPLAY", _headers(now, "n2"), {"n2"}, "REPLAYED")
    webhook("DUPLICATE", _headers(now, "n3"), {"n3"}, "REPLAYED")
    webhook("STALE_TIMESTAMP", _headers(now - timedelta(hours=2), "n4"), set(), "STALE")
    webhook("MALFORMED_HEADERS", {"x-zid-timestamp": "not-a-number"}, set(), "MALFORMED")
    webhook("CONTROL_VALID_DELIVERY", _headers(now, "n5"), set(), None)

    def entitlement(fault: str, ent: Entitlement, expect: str, request: RouteRequest = req) -> None:
        got = [r.value for r in entitlement_refusals(ent, request, now)]
        rows.append(
            {
                "fault": fault,
                "expected": expect,
                "observed": ",".join(got) or "LAWFUL",
                "held": expect in got,
            }
        )

    entitlement(
        "WRONG_MARKET",
        _entitlement(today),
        "COUNTRY_NOT_COVERED",
        RouteRequest("FR", "id.document", "onboarding", "uk", 3),
    )
    entitlement(
        "STALE_SUPPLIER_AUTHORIZATION",
        _entitlement(today, valid_until=today - timedelta(days=1)),
        "ENTITLEMENT_NOT_IN_PERIOD",
    )
    entitlement(
        "SUSPENDED_ENTITLEMENT",
        _entitlement(today, status=EntitlementStatus.SUSPENDED),
        "ENTITLEMENT_NOT_ACTIVE",
    )
    entitlement(
        "WRONG_REGION",
        _entitlement(today),
        "REGION_NOT_PERMITTED",
        RouteRequest("GB", "id.document", "onboarding", "us", 3),
    )

    strong, weak = _entitlement(today), _entitlement(today, provider_id="zeta", assurance=2)
    refused = route([strong, weak], req, now, failing_over_from=strong)
    rows.append({
        "fault": "FAILOVER_THAT_LOWERS_ASSURANCE", "expected": "FAILOVER_WOULD_LOWER_ASSURANCE",
        "observed": ",".join(r.value for r in refused.refusals) or "ALLOWED", "held": refused.provider_id is None,
    })  # fmt: skip

    for fault in (
        "TIMEOUT",
        "HTTP_429",
        "HTTP_502",
    ):  # no answer arrived: nothing may be invented from it
        r = route_outcome(
            match_band=None,
            pad_band=None,
            injection_suspected=False,
            capture_quality_ok=True,
            high_impact=False,
        )
        rows.append(
            {
                "fault": fault,
                "expected": "HUMAN_REVIEW",
                "observed": r.route,
                "held": r.route == "HUMAN_REVIEW",
            }
        )
    return rows
