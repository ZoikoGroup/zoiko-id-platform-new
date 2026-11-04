"""Billing permissions and who may hold them (ZID-COM-CL-002, ZID-UX-COM-010; slice L-14).

Billing authority is its own thing. An Administrator has none by being an Administrator; the Owner has all of it by being the Owner;
anyone else has exactly what the Owner granted, and a grant can never carry the right to grant. The catalogue is the ten permission keys
of the record. Billing Admin and Billing Viewer are bundles of those keys (a tenant role is not added: the five roles of ZID-ENG-DEC-007
stay as they are, so the open ruling on role 11 does not block this). The decision is pure: roles and grants in, an answer and the
reason out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

PERMISSIONS: dict[str, str] = {
    "billing.overview.read": "See the billing overview, plan and usage",
    "billing.invoice.read": "Read issued invoices",
    "billing.invoice.download": "Download an invoice document",
    "billing.payment.initiate": "Pay an invoice",
    "billing.payment_method.read_masked": "See the saved payment method (brand, last four, expiry)",
    "billing.payment_method.manage": "Add, replace or remove the payment method",
    "billing.profile.manage": "Change the billing contact, address and tax identifiers",
    "billing.subscription.request_change": "Ask for a plan or renewal change",
    "billing.access.delegate": "Give or take away billing access",
    "billing.audit.read": "Read the billing activity record",
}
OWNER_ONLY = frozenset({"billing.access.delegate"})  # never part of a bundle, never granted


class Bundle(StrEnum):
    BILLING_ADMIN = "BILLING_ADMIN"
    BILLING_VIEWER = "BILLING_VIEWER"
    INVOICE_READER = "INVOICE_READER"


BUNDLES: dict[Bundle, frozenset[str]] = {
    Bundle.BILLING_ADMIN: frozenset(PERMISSIONS) - OWNER_ONLY,
    Bundle.BILLING_VIEWER: frozenset(
        {"billing.overview.read", "billing.invoice.read", "billing.invoice.download"}
    ),
    Bundle.INVOICE_READER: frozenset({"billing.invoice.read", "billing.invoice.download"}),
}


class BillingAccessError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


@dataclass(frozen=True)
class Grant:
    grant_id: str
    grantee_principal_id: str
    bundle: Bundle
    permissions: frozenset[
        str
    ]  # frozen when granted: a later change to a bundle does not widen an old grant
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def live(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True)
class Decision:
    allowed: bool
    permission: str
    via: str  # "OWNER", "GRANT:<id>", or the reason for the refusal
    grant_id: str | None = None


def validate_catalogue() -> None:
    """A bundle that carries the right to delegate, or a key nobody described, is a defect that must stop the process."""
    for bundle, keys in BUNDLES.items():
        unknown = keys - set(PERMISSIONS)
        if unknown:
            raise BillingAccessError("BILLING_CATALOGUE_INVALID", f"{bundle}: {sorted(unknown)}")
        if keys & OWNER_ONLY:
            raise BillingAccessError(
                "BILLING_CATALOGUE_INVALID", f"{bundle} carries an owner-only right"
            )


def decide(
    roles: tuple[str, ...], grants: tuple[Grant, ...], permission: str, now: datetime
) -> Decision:
    """The Owner holds every key. Anyone else needs a live grant that names the key. An unknown key is refused, never allowed."""
    if permission not in PERMISSIONS:
        return Decision(False, permission, "UNKNOWN_PERMISSION")
    if "OWNER" in roles:
        return Decision(True, permission, "OWNER")
    if permission in OWNER_ONLY:
        return Decision(False, permission, "OWNER_ONLY")
    for g in grants:
        if g.live(now) and permission in g.permissions:
            return Decision(True, permission, f"GRANT:{g.grant_id}", g.grant_id)
    return Decision(False, permission, "NO_BILLING_GRANT")


def effective_permissions(
    roles: tuple[str, ...], grants: tuple[Grant, ...], now: datetime
) -> frozenset[str]:
    return frozenset(p for p in PERMISSIONS if decide(roles, grants, p, now).allowed)


def check_grant(
    *,
    actor_roles: tuple[str, ...],
    actor_person: str,
    grantee_person: str,
    grantee_is_member: bool,
    bundle: str,
    now: datetime,
    expires_at: datetime | None,
) -> Bundle:
    """Only an Owner gives billing access, never to themselves, only to a member of the organization, and a short or past expiry is
    refused rather than stored. Returns the bundle."""
    if "OWNER" not in actor_roles:
        raise BillingAccessError(
            "BILLING_DELEGATION_OWNER_ONLY", "Only an Owner gives billing access."
        )
    try:
        chosen = Bundle(bundle)
    except ValueError:
        raise BillingAccessError(
            "BILLING_BUNDLE_UNKNOWN", "Unknown billing access bundle."
        ) from None
    if actor_person == grantee_person:
        raise BillingAccessError("BILLING_SELF_GRANT", "Nobody gives billing access to themselves.")
    if not grantee_is_member:
        raise BillingAccessError(
            "BILLING_GRANTEE_NOT_MEMBER", "Billing access goes to an active member only."
        )
    if expires_at is not None and expires_at <= now:
        raise BillingAccessError("BILLING_EXPIRY_PAST", "The expiry is already past.")
    return chosen


def check_revoke(*, actor_roles: tuple[str, ...]) -> None:
    if "OWNER" not in actor_roles:
        raise BillingAccessError(
            "BILLING_DELEGATION_OWNER_ONLY", "Only an Owner takes billing access away."
        )
