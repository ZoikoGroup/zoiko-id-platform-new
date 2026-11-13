"""England Right to Rent for Zoiko Rooms (ZID-RCG-003 section 10.2 and its operational boundary; T06, T27, T28, T29; CR-084). Pure domain.

What the document fixes, each with a test:
- England Right to Rent applies ONLY to a property in England. A property in Scotland, Wales or Northern Ireland gets no England Right to Rent
  requirement and no such claim; these rules are never generalised to UK-wide or global rentals (T27);
- a digital Right to Rent check runs only through a provider certified for EXACTLY the Right to Rent service (from 1 October 2026: registered on
  the UK digital verification services register for that service), on current official-register evidence. A provider certified for another service
  is blocked, never treated as covering this one (T28); a lapsed or expired supplementary code blocks new invocations (T06);
- the Home Office online share-code route is a separate, prescribed route: the landlord checks through the Home Office service. Zoiko never substitutes
  its own identity or biometric result for it (T29);
- the landlord keeps the statutory responsibility for the check and for keeping its record. Zoiko never states that a statutory excuse is established:
  the result says which route applies and what evidence Zoiko produced, nothing more;
- retention is the local statutory schedule the owners configure; with none configured, the result says so instead of inventing a period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

ENGLAND = "GB-ENG"
UK_NON_ENGLAND = frozenset({"GB-SCT", "GB-WLS", "GB-NIR"})
DVS_REGISTRATION_REQUIRED_FROM = date(2026, 10, 1)  # the document's operational boundary
RTR_SERVICE = "RTR"


class Route(StrEnum):
    DIGITAL_IDSP = "DIGITAL_IDSP"  # identity document validation through a certified provider (British and Irish passport holders)
    HOME_OFFICE_SHARE_CODE = "HOME_OFFICE_SHARE_CODE"  # the prescribed Home Office online service
    LANDLORD_MANUAL = (
        "LANDLORD_MANUAL"  # the landlord's own manual document check: no Zoiko evidence
    )


class Outcome(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"  # no England Right to Rent requirement for this property
    ROUTE_AVAILABLE = "ROUTE_AVAILABLE"  # Zoiko may produce evidence on this route; the landlord still performs and keeps the check
    PRESCRIBED_ROUTE = "PRESCRIBED_ROUTE"  # the landlord must use the Home Office service; Zoiko does not substitute anything
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ProviderStatus:
    """What the official register and the contract say about the provider for this service, today (CR-073, CR-074, CR-078)."""

    provider: str
    certified_services: frozenset[str]  # the exact services the register lists, current and fresh
    registered_for_rtr: bool  # on the digital verification services register for Right to Rent
    supplementary_code_valid: bool  # the Right to Rent supplementary code is current
    entitled: bool  # Zoiko's contract permits this provider for this use
    suspended: bool  # a kill switch covers this scope


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    route: Route | None
    reasons: tuple[str, ...]
    zoiko_evidence: bool  # whether Zoiko produces evidence on this route at all
    statutory_excuse_by_zoiko: bool = False  # never True: the landlord establishes it
    retention: str | None = (
        None  # the configured local schedule, or None (not configured: said, never invented)
    )


def applies(property_subdivision: str | None, property_country: str) -> bool:
    return property_country.upper() == "GB" and (property_subdivision or "").upper() == ENGLAND


def decide(
    *,
    property_country: str,
    property_subdivision: str | None,
    route: Route,
    on: date,
    provider: ProviderStatus | None = None,
    retention: str | None = None,
) -> Decision:
    if not applies(property_subdivision, property_country):
        reason = (
            "ENGLAND_RTR_NOT_APPLICABLE_IN_UK_NATION"
            if (property_subdivision or "").upper() in UK_NON_ENGLAND
            else "ENGLAND_RTR_NOT_APPLICABLE_OUTSIDE_ENGLAND"
        )
        return Decision(Outcome.NOT_APPLICABLE, None, (reason,), zoiko_evidence=False)
    if route is Route.HOME_OFFICE_SHARE_CODE:
        # T29: the prescribed service; nothing of Zoiko's (identity check, biometric) stands in for it
        return Decision(
            Outcome.PRESCRIBED_ROUTE,
            route,
            ("HOME_OFFICE_ONLINE_CHECK_REQUIRED", "NO_SUBSTITUTED_BIOMETRIC_APPROVAL"),
            zoiko_evidence=False,
            retention=retention,
        )
    if route is Route.LANDLORD_MANUAL:
        return Decision(
            Outcome.ROUTE_AVAILABLE,
            route,
            ("LANDLORD_PERFORMS_MANUAL_CHECK",),
            zoiko_evidence=False,
            retention=retention,
        )
    reasons: list[str] = []
    if provider is None:
        reasons.append("RTR_PROVIDER_UNKNOWN")
    else:
        if RTR_SERVICE not in provider.certified_services:
            reasons.append(
                "PROVIDER_NOT_CERTIFIED_FOR_RTR"
            )  # T28: certified for something else is not this
        if on >= DVS_REGISTRATION_REQUIRED_FROM and not provider.registered_for_rtr:
            reasons.append("PROVIDER_NOT_ON_DVS_REGISTER_FOR_RTR")
        if not provider.supplementary_code_valid:
            reasons.append("RTR_SUPPLEMENTARY_CODE_EXPIRED")  # T06
        if not provider.entitled:
            reasons.append("PROVIDER_NOT_ENTITLED")
        if provider.suspended:
            reasons.append("PROVIDER_SCOPE_SUSPENDED")
    if reasons:
        return Decision(
            Outcome.BLOCKED, route, tuple(reasons), zoiko_evidence=False, retention=retention
        )
    reasons = ["LANDLORD_RETAINS_STATUTORY_RESPONSIBILITY"]
    if retention is None:
        reasons.append("RETENTION_SCHEDULE_NOT_CONFIGURED")
    return Decision(
        Outcome.ROUTE_AVAILABLE, route, tuple(reasons), zoiko_evidence=True, retention=retention
    )
