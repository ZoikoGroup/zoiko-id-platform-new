"""The six evidence assertions of ZID-DTV-WF-05 §02 (address, contact and device). Pure domain.

Each assertion proves one narrow thing and is kept apart from the others: none is ever merged into a single "verified", and none is promoted into another
by the platform. A lookup that finds a location is not a document review; a document review is not an association; controlling an inbox is not being a
person; a device signal is not a human. The text for each is what the assertion proves and what it does not, and is shown wherever the assertion is.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class Assertion(StrEnum):
    ADDRESS_FORMAT_VALID = "ADDRESS_FORMAT_VALID"
    ADDRESS_LOCATION_MATCH = "ADDRESS_LOCATION_MATCH"
    ADDRESS_DOCUMENT_REVIEWED = "ADDRESS_DOCUMENT_REVIEWED"
    ADDRESS_ASSOCIATION_CONFIRMED = "ADDRESS_ASSOCIATION_CONFIRMED"
    CONTACT_CONTROL_CONFIRMED = "CONTACT_CONTROL_CONFIRMED"
    DEVICE_RISK_OBSERVED = "DEVICE_RISK_OBSERVED"


# what each proves / what it does not prove (WF-05 §02, word for word in substance)
PROVES: Mapping[Assertion, tuple[str, str]] = {
    Assertion.ADDRESS_FORMAT_VALID: (
        "The address can be parsed and normalized for its country.",
        "That the location exists.",
    ),
    Assertion.ADDRESS_LOCATION_MATCH: (
        "Directory or geospatial records locate an addressable place.",
        "That the person lives at it or owns it.",
    ),
    Assertion.ADDRESS_DOCUMENT_REVIEWED: (
        "An approved document was inspected against the policy and the address claimed.",
        "That the person lives there, unless the document's authenticity and its link to the person are established.",
    ),
    Assertion.ADDRESS_ASSOCIATION_CONFIRMED: (
        "An authorized source or acceptable document evidence links a named person or entity to the address.",
        "Legal identity, residence for every purpose, or authority to act.",
    ),
    Assertion.CONTACT_CONTROL_CONFIRMED: (
        "The person controlled the inbox or number at the time of the challenge.",
        "Legal identity, ownership in later sessions, or who the carrier's subscriber is.",
    ),
    Assertion.DEVICE_RISK_OBSERVED: (
        "Approved signals describe the session's integrity or an anomaly.",
        "Who the person is, consent to tracking, or fraud.",
    ),
}

# the address assertions from weakest to strongest; used only to choose which one to SHOW, never to infer one from another
ADDRESS_ORDER: tuple[Assertion, ...] = (
    Assertion.ADDRESS_FORMAT_VALID,
    Assertion.ADDRESS_LOCATION_MATCH,
    Assertion.ADDRESS_DOCUMENT_REVIEWED,
    Assertion.ADDRESS_ASSOCIATION_CONFIRMED,
)


def strongest_address(held: frozenset[Assertion] | set[Assertion]) -> Assertion | None:
    """The strongest address assertion actually held, to label it. Holding a stronger one says nothing about a weaker one, and the reverse."""
    for a in reversed(ADDRESS_ORDER):
        if a in held:
            return a
    return None


__all__ = ["ADDRESS_ORDER", "PROVES", "Assertion", "strongest_address"]
