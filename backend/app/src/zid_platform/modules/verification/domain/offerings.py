"""Zoiko iD's service offerings and where each may be shown as available (Global Service Offerings, Nov 2026; CR-088). Pure domain, no I/O.

The offerings document's delivery rule: publish coverage only when provider rights, country rules, privacy controls and independent tests are
verified; design specifications are not production certification. Rules, each with a test:
- the catalog says what an offering is and what it depends on, never where it is available; its wording may not claim certification (that goes
  through the regulated-claims engine only);
- an offering is AVAILABLE in a country only when every capability it lists would be allowed there under enforce (approved pack, launched
  release, entitled provider, no hold), the country's privacy overlay is usable (and allows biometrics, for a biometric offering), and the latest
  ACCEPTED conformance run passes every case it lists;
- an offering that lists no case cannot be independently verified, so it is never available; nothing unknown counts as passing;
- every reason is reported, so the people who close the gaps see all of them at once.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

OFFERING_ID = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
CASE_ID = re.compile(r"^T(0[1-9]|[1-3][0-9]|4[0-8])$")
CAPABILITIES = frozenset(
    {"INDIVIDUAL_VERIFICATION", "BUSINESS_VERIFICATION", "CREDENTIAL_ISSUANCE", "ENTERPRISE_USE"}
)
CERTIFICATION_WORDS = re.compile(r"\bcertif(ied|ication|icate)s?\b", re.IGNORECASE)


class OfferingError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Status(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class Offering:
    offering_id: str
    title: str
    wording: str
    capabilities: tuple[str, ...]
    personal_data: bool
    biometric: bool
    cases: tuple[str, ...]
    code: tuple[str, ...]


@dataclass(frozen=True)
class Privacy:
    """The country's privacy overlay as the offering decision needs it. None = no approved overlay."""

    usable: bool
    biometrics: str  # ALLOWED | CONSENT_REQUIRED | PROHIBITED


@dataclass(frozen=True)
class Coverage:
    offering_id: str
    country: str
    status: Status
    reasons: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.status is Status.AVAILABLE


def parse(doc: Mapping[str, Any]) -> list[Offering]:
    """The catalog. A malformed entry stops the load: an offering we cannot describe precisely is not one we may publish."""
    out: list[Offering] = []
    seen: set[str] = set()
    for raw in doc.get("offerings") or []:
        oid = str(raw.get("id", ""))
        if not OFFERING_ID.match(oid):
            raise OfferingError("OFFERING_ID_INVALID", oid)
        if oid in seen:
            raise OfferingError("OFFERING_DUPLICATE", oid)
        seen.add(oid)
        wording = str(raw.get("wording", "")).strip()
        if not wording or not str(raw.get("title", "")).strip():
            raise OfferingError("OFFERING_TEXT_MISSING", oid)
        if CERTIFICATION_WORDS.search(wording):
            raise OfferingError(
                "OFFERING_CLAIMS_CERTIFICATION",
                f"{oid}: certification is claimed only through the regulated-claims engine",
            )
        caps = tuple(str(c) for c in raw.get("capabilities") or ())
        unknown = [c for c in caps if c not in CAPABILITIES]
        if unknown:
            raise OfferingError("OFFERING_CAPABILITY_UNKNOWN", f"{oid}: {unknown}")
        cases = tuple(str(c) for c in raw.get("cases") or ())
        bad = [c for c in cases if not CASE_ID.match(c)]
        if bad:
            raise OfferingError("OFFERING_CASE_UNKNOWN", f"{oid}: {bad}")
        out.append(
            Offering(
                offering_id=oid,
                title=str(raw["title"]).strip(),
                wording=wording,
                capabilities=caps,
                personal_data=bool(
                    raw.get("personal_data", True)
                ),  # unknown = treated as personal data
                biometric=bool(raw.get("biometric", False)),
                cases=cases,
                code=tuple(str(c) for c in raw.get("code") or ()),
            )
        )
    return out


def decide(
    offering: Offering,
    country: str,
    *,
    capability_refusals: Mapping[str, Sequence[str]],
    privacy: Privacy | None,
    accepted_run: Mapping[str, str] | None,
) -> Coverage:
    """Is this offering available in this country now?

    capability_refusals: for each capability the country lists, what enforce would refuse there today (empty = allowed); a capability the
        country does not list is absent.
    privacy: the country's overlay, or None when none is approved.
    accepted_run: case -> outcome of the latest ACCEPTED conformance run, or None when no run has been accepted.
    """
    reasons: list[str] = []
    for cap in offering.capabilities:
        if cap not in capability_refusals:
            reasons.append(f"{cap}:NOT_OFFERED_IN_COUNTRY")
            continue
        reasons.extend(f"{cap}:{r}" for r in capability_refusals[cap])
    if offering.personal_data:
        if privacy is None:
            reasons.append("PRIVACY_OVERLAY_MISSING")
        elif not privacy.usable:
            reasons.append("PRIVACY_OVERLAY_UNUSABLE")
    if offering.biometric and privacy is not None and privacy.biometrics == "PROHIBITED":
        reasons.append("BIOMETRICS_PROHIBITED")
    if not offering.cases:
        reasons.append("NO_INDEPENDENT_TESTS")
    elif accepted_run is None:
        reasons.append("CONFORMANCE_NOT_ACCEPTED")
    else:
        reasons.extend(
            f"TEST_NOT_PASSED:{c}" for c in offering.cases if accepted_run.get(c) != "PASS"
        )
    return Coverage(
        offering.offering_id,
        country,
        Status.NOT_AVAILABLE if reasons else Status.AVAILABLE,
        tuple(reasons),
    )
