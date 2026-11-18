"""WF-05 check methods: what each produces, the data it creates, and when a live call may use it (ZID-DTV-WF-05 §01, §02, §04, §05, §23; CR-112).
Pure domain, no I/O.

A check method is one way of producing WF-05 evidence: an email or phone challenge, an address lookup, a document review, a licensed data source, a
postal code, a device signal, a carrier lookup. Each runs through an adapter (the deployment names it: engine id, version, and its route), and each is
governed by the same lifecycle as a biometric method (`biometric_methods`: OFF, PAUSED, LAB_ONLY, PILOT_GATED, PRODUCTION_ELIGIBLE, two people up, one
person down), in its own tables.

Rules, each with a test:
- a method can only ever produce the assertions it is able to show (`PRODUCES`): an address lookup never produces a document review or an association,
  a challenge never produces anything but contact control, and nothing but a device method produces a device observation;
- a method is available for a live call only when ALL of these hold, and every refusal is named (no silent opt-in, A01):
    the deployment ships an adapter for it (NO_ADAPTER otherwise);
    its lifecycle scope is live for this country, purpose and tenant (METHOD_OFF, METHOD_LAB_ONLY, METHOD_PAUSED, METHOD_NOT_IN_ROLLOUT);
    every data class it creates has an APPROVED retention rule (RETENTION_NOT_APPROVED:<class>; §23: no universal fixed duration is approved here);
    a LICENSED (paid) route is entitled for the tenant (LICENSED_ROUTE_NOT_ENTITLED; §05: no hidden paid fallback);
- the order of the reasons is fixed, so the same inputs give the same answer.

What this does NOT do: send anything, look anything up, or decide anything about a person.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.domain.biometric_methods import Availability, State

ENGINE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
DATA_CLASS = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")


class CheckMethodError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class CheckMethod(StrEnum):
    EMAIL_CHALLENGE = "EMAIL_CHALLENGE"  # DTV-21: a one-time code or link to an inbox
    PHONE_SMS_CHALLENGE = "PHONE_SMS_CHALLENGE"  # DTV-20: a one-time code by SMS
    PHONE_VOICE_CHALLENGE = "PHONE_VOICE_CHALLENGE"  # DTV-20: a one-time code by voice call
    CARRIER_INTELLIGENCE = (
        "CARRIER_INTELLIGENCE"  # DTV-20: SIM swap, porting, line type, from a carrier source
    )
    ADDRESS_LOOKUP = "ADDRESS_LOOKUP"  # DTV-17: parse, normalize, locate (libpostal, UPRN, OSM, a postal reference)
    ADDRESS_DOCUMENT = "ADDRESS_DOCUMENT"  # DTV-17: a proof of address document, reviewed
    ADDRESS_DATA_SOURCE = (
        "ADDRESS_DATA_SOURCE"  # DTV-17: a licensed source that links a person to an address
    )
    ADDRESS_POSTAL_CHALLENGE = (
        "ADDRESS_POSTAL_CHALLENGE"  # DTV-17: a one-time code posted to the address
    )
    DEVICE_RISK = "DEVICE_RISK"  # DTV-22: platform integrity or approved device signals


class Route(StrEnum):
    OPEN = "OPEN"  # open source or platform-native, no per-call fee
    LICENSED = "LICENSED"  # a paid supplier: needs an entitlement, a contract and a cap
    MANUAL = "MANUAL"  # a person does the work (document review)


# the only assertions each method can ever produce (WF-05 §02); a postal code shows access to mail at the address, which is an association, never residence
PRODUCES: Mapping[CheckMethod, frozenset[Assertion]] = {
    CheckMethod.EMAIL_CHALLENGE: frozenset({Assertion.CONTACT_CONTROL_CONFIRMED}),
    CheckMethod.PHONE_SMS_CHALLENGE: frozenset({Assertion.CONTACT_CONTROL_CONFIRMED}),
    CheckMethod.PHONE_VOICE_CHALLENGE: frozenset({Assertion.CONTACT_CONTROL_CONFIRMED}),
    CheckMethod.CARRIER_INTELLIGENCE: frozenset({Assertion.DEVICE_RISK_OBSERVED}),
    CheckMethod.ADDRESS_LOOKUP: frozenset(
        {Assertion.ADDRESS_FORMAT_VALID, Assertion.ADDRESS_LOCATION_MATCH}
    ),
    # a reviewed document, and (only after a second reviewer, `address_review`) acceptable document evidence of the association (§02)
    CheckMethod.ADDRESS_DOCUMENT: frozenset(
        {Assertion.ADDRESS_DOCUMENT_REVIEWED, Assertion.ADDRESS_ASSOCIATION_CONFIRMED}
    ),
    CheckMethod.ADDRESS_DATA_SOURCE: frozenset({Assertion.ADDRESS_ASSOCIATION_CONFIRMED}),
    CheckMethod.ADDRESS_POSTAL_CHALLENGE: frozenset({Assertion.ADDRESS_ASSOCIATION_CONFIRMED}),
    CheckMethod.DEVICE_RISK: frozenset({Assertion.DEVICE_RISK_OBSERVED}),
}

# the data classes each method creates (WF-05 §23); each needs its own approved retention before the method is live
DATA_CLASSES: Mapping[CheckMethod, tuple[str, ...]] = {
    CheckMethod.EMAIL_CHALLENGE: (
        "contact.email",
        "challenge.secret_and_delivery",
        "audit.decision",
    ),
    CheckMethod.PHONE_SMS_CHALLENGE: (
        "contact.phone",
        "challenge.secret_and_delivery",
        "audit.decision",
    ),
    CheckMethod.PHONE_VOICE_CHALLENGE: (
        "contact.phone",
        "challenge.secret_and_delivery",
        "audit.decision",
    ),
    CheckMethod.CARRIER_INTELLIGENCE: ("contact.phone", "carrier.observation", "audit.decision"),
    CheckMethod.ADDRESS_LOOKUP: ("address.claim", "address.lookup_result", "audit.decision"),
    CheckMethod.ADDRESS_DOCUMENT: (
        "address.claim",
        "address.document",
        "address.extraction",
        "audit.decision",
    ),
    CheckMethod.ADDRESS_DATA_SOURCE: ("address.claim", "address.source_result", "audit.decision"),
    CheckMethod.ADDRESS_POSTAL_CHALLENGE: (
        "address.claim",
        "challenge.secret_and_delivery",
        "audit.decision",
    ),
    CheckMethod.DEVICE_RISK: ("device.observation", "audit.decision"),
}
ALL_DATA_CLASSES: frozenset[str] = frozenset(c for cs in DATA_CLASSES.values() for c in cs)


def check_produces(method: CheckMethod, assertion: Assertion) -> None:
    """Refuse an assertion the method cannot show: the platform never promotes one kind of evidence into another."""
    if assertion not in PRODUCES[method]:
        raise CheckMethodError(
            "ASSERTION_NOT_PRODUCED_BY_METHOD", f"{method.value} cannot show {assertion.value}"
        )


# ---------------------------------------------------------------------------------------------------------------------- configuration as data


@dataclass(frozen=True, slots=True)
class Adapter:
    """The adapter a deployment ships for a method: what runs it, and how it is paid for."""

    method: CheckMethod
    engine_id: str
    engine_version: str
    route: Route


@dataclass(frozen=True, slots=True)
class RetentionRule:
    data_class: str
    status: str  # UNSET | SAMPLE | APPROVED
    retention_days: int | None = None
    approved_by: str | None = None
    legal_basis_ref: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == "APPROVED"


def parse_adapters(doc: Mapping[str, Any]) -> dict[CheckMethod, Adapter]:
    """`adapters: {METHOD: {engine_id, engine_version, route}}`. A method not listed has no adapter, and no adapter is ever invented."""
    out: dict[CheckMethod, Adapter] = {}
    for name, body in (doc.get("adapters") or {}).items():
        try:
            method = CheckMethod(str(name))
        except ValueError as e:
            raise CheckMethodError("ADAPTER_METHOD_UNKNOWN", str(name)) from e
        if not isinstance(body, Mapping):
            raise CheckMethodError("ADAPTER_INVALID", method.value)
        engine_id = str(body.get("engine_id", ""))
        version = str(body.get("engine_version", "")).strip()
        if not ENGINE_ID.match(engine_id) or not 1 <= len(version) <= 64:
            raise CheckMethodError("ADAPTER_INVALID", f"{method.value}: engine id and version")
        try:
            route = Route(str(body.get("route", "")))
        except ValueError as e:
            raise CheckMethodError("ADAPTER_ROUTE_INVALID", method.value) from e
        out[method] = Adapter(method, engine_id, version, route)
    return out


def parse_retention(doc: Mapping[str, Any]) -> dict[str, RetentionRule]:
    """`classes: {class: {status, retention_days, approved_by, legal_basis_ref}}`. Every class a method creates must be listed; APPROVED needs a
    period, a named approver and a legal basis. UNSET and SAMPLE are never approved."""
    classes = doc.get("classes")
    if not isinstance(classes, Mapping):
        raise CheckMethodError("RETENTION_INVALID", "classes")
    out: dict[str, RetentionRule] = {}
    for name, body in classes.items():
        if not DATA_CLASS.match(str(name)) or not isinstance(body, Mapping):
            raise CheckMethodError("RETENTION_INVALID", str(name))
        status = str(body.get("status", "UNSET"))
        if status not in {"UNSET", "SAMPLE", "APPROVED"}:
            raise CheckMethodError("RETENTION_INVALID", f"{name}: status")
        days = body.get("retention_days")
        if days is not None and (
            not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650 * 3
        ):
            raise CheckMethodError("RETENTION_INVALID", f"{name}: retention_days")
        approved_by = str(body["approved_by"]) if body.get("approved_by") else None
        legal = str(body["legal_basis_ref"]) if body.get("legal_basis_ref") else None
        if status == "APPROVED" and not (days and approved_by and legal):
            raise CheckMethodError(
                "RETENTION_APPROVAL_INCOMPLETE",
                f"{name}: APPROVED needs retention_days, approved_by and legal_basis_ref",
            )
        out[str(name)] = RetentionRule(str(name), status, days, approved_by, legal)
    missing = ALL_DATA_CLASSES - set(out)
    if missing:
        raise CheckMethodError("RETENTION_CLASS_MISSING", ",".join(sorted(missing)))
    return out


# ---------------------------------------------------------------------------------------------------------------------- eligibility


@dataclass(frozen=True, slots=True)
class MethodEligibility:
    method: CheckMethod
    available: bool
    state: State
    reasons: tuple[str, ...]
    route: Route | None
    engine_id: str | None
    engine_version: str | None
    produces: tuple[str, ...]
    scope_id: str | None = None
    scope_version: int | None = None


def eligibility(
    method: CheckMethod,
    *,
    adapter: Adapter | None,
    availability: Availability | None,
    retention: Mapping[str, RetentionRule],
    licensed_entitled: bool,
) -> MethodEligibility:
    """Whether a live call may use this method now, and every reason it may not. `availability` is the lifecycle answer for the adapter's exact
    engine and version (None when there is no adapter to ask about)."""
    reasons: list[str] = []
    state = State.OFF
    if adapter is None:
        reasons.append("NO_ADAPTER")
    elif availability is None:
        reasons.append("METHOD_OFF")
    else:
        state = availability.state
        if not availability.available:
            reasons.extend(availability.reasons or ("METHOD_OFF",))
    for data_class in DATA_CLASSES[method]:
        rule = retention.get(data_class)
        if rule is None or not rule.approved:
            reasons.append(f"RETENTION_NOT_APPROVED:{data_class}")
    if adapter is not None and adapter.route is Route.LICENSED and not licensed_entitled:
        reasons.append("LICENSED_ROUTE_NOT_ENTITLED")
    notes = (
        tuple(r for r in availability.reasons if r.startswith(("METHOD_PILOT", "METHOD_ROLLOUT_")))
        if availability is not None and availability.available
        else ()
    )
    record = availability.record if availability is not None else None
    return MethodEligibility(
        method=method,
        available=not reasons,
        state=state,
        reasons=tuple(dict.fromkeys(reasons)) or notes,
        route=adapter.route if adapter else None,
        engine_id=adapter.engine_id if adapter else None,
        engine_version=adapter.engine_version if adapter else None,
        produces=tuple(sorted(a.value for a in PRODUCES[method])),
        scope_id=record.scope_id if record is not None else None,
        scope_version=record.version if record is not None else None,
    )


__all__ = [
    "ALL_DATA_CLASSES",
    "DATA_CLASSES",
    "PRODUCES",
    "Adapter",
    "CheckMethod",
    "CheckMethodError",
    "MethodEligibility",
    "RetentionRule",
    "Route",
    "check_produces",
    "eligibility",
    "parse_adapters",
    "parse_retention",
]
