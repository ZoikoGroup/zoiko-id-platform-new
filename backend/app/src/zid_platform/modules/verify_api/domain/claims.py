"""The developer platform's public claims, derived from evidence (ZID-CP-WF10 v2.0, WF-10.12; the claims feed for the Trust Center; CR-173). Pure domain, no I/O.

A public claim is a sentence a person might quote. The rule is simple: a sentence may be published only if the platform can show what backs it today, and a sentence it cannot back is
listed as NOT claimable with the reason, so nobody adds it by hand. Nothing here is written by a person: every statement is built from the SDK register, the version catalog, the sandbox
persona catalog, the credential policy and the certification registry.

An absence is a fact too: with no SDK generally available, the true statement is that none is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

FORBIDDEN_WORDS = (
    "certified",
    "compliant",
    "guaranteed",
    "uptime",
    "soc 2",
    "iso 27001",
    "qualified",
)  # a published sentence containing one of these needs a backing record


def build(
    *, sdk_view: Mapping[str, Any], versions_view: Mapping[str, Any], persona_count: int, token_seconds: int, zoiko_certifications: Sequence[Mapping[str, Any]], today: date
) -> dict[str, Any]:  # fmt: skip
    claims: list[dict[str, Any]] = []
    not_claimed: list[dict[str, str]] = []

    ga = int(sdk_view["ga_count"])
    claims.append(
        {
            "id": "sdk-availability",
            "statement": str(sdk_view["statement"]),
            "basis": "the SDK release register",
            "evidence": f"{ga} generally available; as of {sdk_view['as_of']}",
        }
    )
    if not ga:
        not_claimed.append(
            {
                "claim": "SDKs are available for production use",
                "reason": "No package is generally available: none is published with a signed release and a current test run.",
            }
        )

    current = next((r for r in versions_view["releases"] if r["state"] == "CURRENT"), None)
    if current is not None:
        claims.append(
            {
                "id": "api-version",
                "statement": f"The current partner API version is {current['version']}.",
                "basis": "the version catalog",
                "evidence": current["digest"],
            }
        )

    claims.append(
        {
            "id": "sandbox-scenarios",
            "statement": f"The sandbox offers {persona_count} fixed synthetic test scenarios, including duplicate, out-of-order, stale and tampered deliveries.",
            "basis": "the persona catalog",
            "evidence": "GET /v1/verify-api/fixtures",
        }
    )
    claims.append(
        {
            "id": "token-lifetime",
            "statement": f"Access tokens last at most {token_seconds // 60} minutes, and a revoked credential, a suspended application or a removed scope is refused on the next call.",
            "basis": "the credential policy",
            "evidence": "GET /v1/verify-api/credential-policy",
        }
    )

    held = [
        c
        for c in zoiko_certifications
        if c.get("status") == "ACTIVE"
        and (c.get("valid_until") is None or c["valid_until"] >= today)
    ]
    for c in held:
        claims.append(
            {
                "id": f"certification-{c['record_id']}", "statement": f"Zoiko iD holds {c['scheme']} (version {c['scheme_version']}) for {c['service_code']} in {c['country']}" + (" (a component of the service only)." if c.get("component_only") else "."),
                "basis": "the certification registry", "evidence": c["certificate_ref"],
            }
        )  # fmt: skip
    if not held:
        for sentence in (
            "Zoiko iD is a certified identity verification provider",
            "Zoiko iD is a qualified trust service provider",
            "Zoiko iD holds SOC 2 or ISO 27001 certification",
        ):
            not_claimed.append(
                {
                    "claim": sentence,
                    "reason": "No active certification in Zoiko iD's own name is recorded. A partner's or a supplier's certification is theirs, not ours.",
                }
            )

    for sentence, why in (
        (
            "The platform is available a stated percentage of the time",
            "No service objective has been ratified or measured.",
        ),
        (
            "Revocation is instant everywhere",
            "It is refused on the next call to this API only; other systems that cache a token are outside it.",
        ),
        (
            "The sandbox integration test certifies a partner",
            "A passed synthetic run is a test record. It is not a certification and does not approve production.",
        ),
    ):
        not_claimed.append({"claim": sentence, "reason": why})

    return {
        "as_of": today.isoformat(),
        "claims": claims,
        "not_claimed": not_claimed,
        "rule": "A sentence may be published only if it appears under claims. Anything else needs evidence the platform does not hold today.",
    }


def problems(feed: Mapping[str, Any]) -> list[str]:
    """What is wrong with a feed: a published sentence using a word that needs a backing record, without one. Used by a test and by anyone who builds a page from the feed."""
    out: list[str] = []
    for c in feed["claims"]:
        low = c["statement"].lower()
        hit = [w for w in FORBIDDEN_WORDS if w in low]
        if hit and not c["id"].startswith("certification-"):
            out.append(f"UNBACKED_WORD:{c['id']}:{hit[0]}")
        if not c.get("evidence"):
            out.append(f"NO_EVIDENCE:{c['id']}")
    return out
