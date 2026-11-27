"""The developer's first-verification checklist and the time it takes (ZID-CP-WF10 v2.0, WF-10.01; CR-173). Pure domain, no I/O.

Five steps, each backed by something that really happened and each with the time it happened:

    1 CREDENTIAL         a credential exists for the application
    2 TOKEN              the application's server exchanged it for an access token
    3 SESSION            a sandbox session was created
    4 ENDPOINT           the company has a verified, active webhook endpoint
    5 VERIFIED_WEBHOOK   the partner's server CONFIRMED that it received a delivered event and checked its signature

Step 5 is the only one that counts as "working": a key that was created, or a webhook that was sent, proves nothing about the partner's side. The partner confirms by naming the
event id of a delivery the platform really made. The time to first verified webhook runs from step 1 to step 5 and exists only when all five are done, so the median over a
group is taken from complete installs only and never from people who stopped half way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

STEPS = ("CREDENTIAL", "TOKEN", "SESSION", "ENDPOINT", "VERIFIED_WEBHOOK")
TITLES = {
    "CREDENTIAL": "Create a credential",
    "TOKEN": "Get an access token from your server",
    "SESSION": "Create a test verification session",
    "ENDPOINT": "Register and verify a webhook endpoint",
    "VERIFIED_WEBHOOK": "Confirm you received a signed webhook and checked its signature",
}
NEXT = {
    "CREDENTIAL": "Create a credential for this application. Keep the secret: it is shown once.",
    "TOKEN": "Exchange the credential for a token from your server with POST /v1/verify-api/oauth2/token.",
    "SESSION": "Create a session in the sandbox, or use the Test Lab.",
    "ENDPOINT": "Register an HTTPS endpoint under Webhooks and verify it.",
    "VERIFIED_WEBHOOK": "Verify the signature of a delivered event, then confirm its event id with POST /v1/verify-api/onboarding/webhook-confirmations.",
}


@dataclass(frozen=True, slots=True)
class Facts:
    credential_at: datetime | None
    token_at: datetime | None
    session_at: datetime | None
    endpoint_active: bool
    confirmed_at: datetime | None
    confirmed_event_id: str | None


def checklist(f: Facts) -> dict[str, Any]:
    """Each step as done or not, with its evidence. Nothing is ready until step five is done, and a step does not count if an earlier one is missing."""
    times: dict[str, datetime | None] = {
        "CREDENTIAL": f.credential_at,
        "TOKEN": f.token_at,
        "SESSION": f.session_at,
        "ENDPOINT": None,
        "VERIFIED_WEBHOOK": f.confirmed_at,
    }
    done = {
        "CREDENTIAL": f.credential_at is not None, "TOKEN": f.token_at is not None, "SESSION": f.session_at is not None, "ENDPOINT": f.endpoint_active, "VERIFIED_WEBHOOK": f.confirmed_at is not None,
    }  # fmt: skip
    steps = []
    blocked = False
    first_open: str | None = None
    for step in STEPS:
        ok = done[step] and not blocked
        if not done[step] and first_open is None:
            first_open = step
        if not done[step]:
            blocked = True
        steps.append(
            {
                "step": step,
                "title": TITLES[step],
                "done": done[step],
                "at": times[step].isoformat() if times[step] else None,  # type: ignore[union-attr]
                "evidence": f.confirmed_event_id if step == "VERIFIED_WEBHOOK" and ok else None,
            }
        )
    complete = all(done.values())
    return {
        "steps": steps, "complete": complete, "next_step": first_open, "next_action": NEXT[first_open] if first_open else None,
        "ready": complete,  # no green "ready" until the partner has confirmed a verified signed webhook
        "note": "Ready means a signed webhook was delivered and your server confirmed it verified the signature. Creating a key is not enough.",
    }  # fmt: skip


def seconds_to_first_verified_webhook(f: Facts) -> int | None:
    """From the credential to the confirmed webhook, or None while any step is missing (an incomplete install has no time)."""
    if not (
        f.credential_at and f.token_at and f.session_at and f.endpoint_active and f.confirmed_at
    ):
        return None
    return max(0, int((f.confirmed_at - f.credential_at).total_seconds()))


def cohort_median(durations: Sequence[int | None]) -> dict[str, Any]:
    """The median over installs that finished. Those that did not are counted and left out, never averaged in as zero or as the time they stopped."""
    done = [d for d in durations if d is not None]
    return {
        "installs": len(durations),
        "complete": len(done),
        "incomplete": len(durations) - len(done),
        "median_seconds": int(median(done)) if done else None,
    }


def confirmation_problems(event_known_and_delivered: bool, already_confirmed: bool) -> list[str]:
    """Why a partner's confirmation is refused: it must name a real, delivered event of this company, and one event is confirmed once."""
    out: list[str] = []
    if not event_known_and_delivered:
        out.append("EVENT_NOT_FOUND_OR_NOT_DELIVERED")
    if already_confirmed:
        out.append("ALREADY_CONFIRMED")
    return out


def view(facts: Mapping[str, Any]) -> dict[str, Any]:
    f = Facts(
        facts.get("credential_at"),
        facts.get("token_at"),
        facts.get("session_at"),
        bool(facts.get("endpoint_active")),
        facts.get("confirmed_at"),
        facts.get("confirmed_event_id"),
    )
    return {
        **checklist(f),
        "seconds_to_first_verified_webhook": seconds_to_first_verified_webhook(f),
    }
