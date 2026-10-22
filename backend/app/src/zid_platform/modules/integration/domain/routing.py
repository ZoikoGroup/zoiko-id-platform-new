"""Multi-IdP routing (ZID-OVERLAY-001 §8 "Multiple IdPs", §9; OVL-27; G-W3).

Pure rules, no I/O. A tenant with several identity sources routes a login deterministically:

1. an APPLICATION rule for the application the login is for;
2. otherwise the DOMAIN rule for the login hint's domain, then for each parent domain;
3. otherwise no route (the hosted login asks; it never guesses from an email match).

Within one level, ties are impossible: a (kind, match) pair is unique per tenant, enforced at write
time. Routing only chooses where to authenticate: accounts still link by issuer + subject only, so
two IdPs can never merge into one principal through routing (§9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.integration.domain.model import IntegrationError


class RouteKind(StrEnum):
    APPLICATION = "APPLICATION"
    DOMAIN = "DOMAIN"


@dataclass(frozen=True, slots=True)
class RoutingRule:
    rule_id: str
    kind: RouteKind
    match: str  # application id, or a verified domain (lower-case)
    connection_ref: str  # federation_connection:<id>


def check_rule(kind: RouteKind, match: str, connection_ref: str) -> str:
    if not connection_ref.startswith("federation_connection:"):
        raise IntegrationError("VALIDATION_FAILED", "Routes point at a federation connection.")
    value = match.strip().lower()
    if not value or len(value) > 253:
        raise IntegrationError("VALIDATION_FAILED", "Empty route match.")
    return value


def _parents(domain: str) -> list[str]:
    labels = domain.lower().strip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def resolve(
    rules: Sequence[RoutingRule], *, application_id: str | None, login_domain: str | None
) -> RoutingRule | None:
    by_app = {r.match: r for r in rules if r.kind is RouteKind.APPLICATION}
    if application_id and application_id.lower() in by_app:
        return by_app[application_id.lower()]
    by_domain = {r.match: r for r in rules if r.kind is RouteKind.DOMAIN}
    for d in _parents(login_domain) if login_domain else []:
        if d in by_domain:
            return by_domain[d]
    return None
