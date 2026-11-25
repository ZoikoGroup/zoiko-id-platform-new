"""Who may see what in the Command Center (ZID-CPS-WF-02 section 09, 07.12). Same shape as billing access: the five tenant roles stay as they are;
the Owner holds every analytics key; anyone else holds exactly the bundles an Owner granted, and no bundle carries the right to grant. Cost and
price figures are not here: they stay behind the billing permission layer, so Billing Admin remains its own authority (D-04, Q29)."""

from __future__ import annotations

from datetime import datetime

PERMISSIONS: dict[str, str] = {
    "analytics.read": "See aggregate verification, conversion, decision, provider and delivery metrics",
    "analytics.drill": "Open one session's event timeline from a metric (no evidence, no media)",
    "analytics.operations.read": "See API, webhook, incident and SLO health",
    "analytics.export": "Create and download exports of aggregate results",
    "analytics.corrections.request": "Ask for a fact to be excluded from the metrics",
    "analytics.corrections.approve": "Approve another person's correction",
    "analytics.access.delegate": "Give or take away analytics access",
}
OWNER_ONLY = frozenset({"analytics.access.delegate"})

BUNDLES: dict[str, frozenset[str]] = {
    "ANALYTICS_VIEWER": frozenset({"analytics.read"}),
    "OPERATIONS_LEAD": frozenset({"analytics.read", "analytics.drill", "analytics.operations.read", "analytics.export"}),
    "DEVELOPER_ADMIN": frozenset({"analytics.read", "analytics.operations.read"}),
    "COMPLIANCE_REVIEWER": frozenset({"analytics.read", "analytics.drill"}),
    "AUDITOR": frozenset({"analytics.read", "analytics.export"}),
    "DATA_STEWARD": frozenset({"analytics.read", "analytics.corrections.request", "analytics.corrections.approve"}),
}


class AccessError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


def validate_catalogue() -> None:
    for name, keys in BUNDLES.items():
        if keys - set(PERMISSIONS) or keys & OWNER_ONLY:
            raise AccessError("ANALYTICS_CATALOGUE_INVALID", name)


def allowed(roles: tuple[str, ...], granted: frozenset[str], permission: str) -> bool:
    """The Owner holds every key; anyone else needs a live grant naming it. An unknown key is refused."""
    if permission not in PERMISSIONS:
        return False
    if "OWNER" in roles:
        return True
    return permission not in OWNER_ONLY and permission in granted


def check_grant(*, actor_roles: tuple[str, ...], actor: str, grantee: str, grantee_is_member: bool, bundle: str, now: datetime, expires_at: datetime | None) -> frozenset[str]:
    if "OWNER" not in actor_roles:
        raise AccessError("ANALYTICS_DELEGATION_OWNER_ONLY", "Only an Owner gives analytics access.")
    if bundle not in BUNDLES:
        raise AccessError("ANALYTICS_BUNDLE_UNKNOWN", "Unknown analytics access bundle.")
    if actor == grantee:
        raise AccessError("ANALYTICS_SELF_GRANT", "Nobody gives analytics access to themselves.")
    if not grantee_is_member:
        raise AccessError("ANALYTICS_GRANTEE_NOT_MEMBER", "Analytics access goes to an active member only.")
    if expires_at is not None and expires_at <= now:
        raise AccessError("ANALYTICS_EXPIRY_PAST", "The expiry is already past.")
    return BUNDLES[bundle]


validate_catalogue()
