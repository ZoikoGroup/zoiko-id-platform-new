"""Country privacy, cross-border and biometric overlay (ZID-RCG-003 RCG-G09; section 03.1 privacy layer; T31, T32; CR-079). Pure domain, no I/O.

The privacy module decides whether a TENANT may process a person's data for a purpose (activities, purposes, consents, restrictions). A tenant's
verification profile decides where it USES biometrics. Neither knows what a COUNTRY's law allows. This overlay is that: one approved record per
country, owned by the DPO and Legal, that every verification start is checked against before any provider is asked. Rules, each with a test:
- localization and transfer: a provider is used for a country only if the region where it processes that country's data is a home region, or a
  region the overlay lists with its lawful transfer mechanism. A provider whose region is not recorded is refused, never assumed (T32);
- biometrics are ALLOWED, CONSENT_REQUIRED or PROHIBITED by country. A tenant's profile can never widen that: a biometric route is refused where the
  country prohibits it, or where consent is required and the person has not given it. The refusal names the compliant alternate routes (T31);
- a DPIA the overlay requires must be referenced, or nothing is routed;
- the overlay itself counts only when the DPO and Legal have both signed its exact hash; an edit after approval breaks it;
- no overlay for a country is a refusal with its own reason, never a pass (the gate's shadow mode only reports it).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

APPROVER_ROLES = ("dpo", "legal")
COUNTRY = re.compile(r"^[A-Z]{2}$")
REGION = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")


class OverlayError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Biometrics(StrEnum):
    ALLOWED = "ALLOWED"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    PROHIBITED = "PROHIBITED"


@dataclass(frozen=True)
class Approval:
    reference: str
    signed_hash: str


@dataclass(frozen=True)
class Overlay:
    country: str
    version: int
    home_regions: frozenset[str]  # where this country's data may always be processed
    transfer_regions: Mapping[str, str]  # another region -> its lawful transfer mechanism
    provider_regions: Mapping[
        str, str
    ]  # provider -> the region it processes this country's data in
    biometrics: Biometrics
    dpia_required: bool = False
    dpia_ref: str | None = None
    lawful_basis: str = ""
    approvals: Mapping[str, Approval] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    allowed: bool
    reasons: tuple[str, ...]
    region: str | None = None
    mechanism: str | None = (
        None  # the lawful transfer mechanism used, when the region is not a home region
    )


def overlay_hash(o: Overlay) -> str:
    body = {
        "country": o.country,
        "version": o.version,
        "home_regions": sorted(o.home_regions),
        "transfer_regions": dict(sorted(o.transfer_regions.items())),
        "provider_regions": dict(sorted(o.provider_regions.items())),
        "biometrics": o.biometrics.value,
        "dpia_required": o.dpia_required,
        "dpia_ref": o.dpia_ref,
        "lawful_basis": o.lawful_basis,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def problems(o: Overlay) -> list[str]:
    """What makes the overlay itself unusable, whatever the request is."""
    out: list[str] = []
    digest = overlay_hash(o)
    for role in APPROVER_ROLES:
        a = o.approvals.get(role)
        if a is None or not a.reference.strip():
            out.append(f"approval_missing:{role}")
        elif a.signed_hash != digest:
            out.append(f"approval_hash_mismatch:{role}")
    if o.dpia_required and not (o.dpia_ref and REFERENCE.match(o.dpia_ref)):
        out.append("DPIA_MISSING")
    if not o.home_regions:
        out.append("HOME_REGION_MISSING")
    return out


def route(
    overlay: Overlay | None,
    *,
    provider: str | None,
    biometric: bool,
    consent: bool | None,
) -> RouteDecision:
    """May this country's data go to this provider, with or without biometrics? `consent` is whether the person has given the biometric consent
    (None when it was not looked up, which counts as not given). Every reason is listed."""
    if overlay is None:
        return RouteDecision(False, ("PRIVACY_OVERLAY_MISSING",))
    reasons = problems(overlay)
    region = overlay.provider_regions.get(provider or "")
    mechanism = None
    if region is None:
        reasons.append("PROVIDER_REGION_UNKNOWN")
    elif region not in overlay.home_regions:
        mechanism = overlay.transfer_regions.get(region)
        if not mechanism:
            reasons.append("CROSS_BORDER_ROUTE_DENIED")  # T32
    if biometric:
        if overlay.biometrics is Biometrics.PROHIBITED:
            reasons.append("BIOMETRIC_NOT_PERMITTED_IN_COUNTRY")
        elif overlay.biometrics is Biometrics.CONSENT_REQUIRED and consent is not True:
            reasons.append("BIOMETRIC_CONSENT_REQUIRED")
    return RouteDecision(not reasons, tuple(reasons), region, mechanism)


BIOMETRIC_REASONS = frozenset(
    {
        "BIOMETRIC_NOT_PERMITTED_IN_COUNTRY",
        "BIOMETRIC_CONSENT_REQUIRED",
        "BIOMETRIC_METHOD_NOT_ELIGIBLE",
    }
)  # CR-110: a provider's face match or liveness that is not eligible is a biometric obstacle too: the non-biometric alternates still work


def alternates(
    decision: RouteDecision, profiles: Sequence[Mapping[str, Any]], *, country: str
) -> list[Mapping[str, Any]]:
    """T31: when the only obstacle is the biometric route, the tenant's ACTIVE profiles that cover the country WITHOUT biometrics there are the
    compliant alternates. Any other obstacle (region, DPIA, approvals) has no alternate: changing the profile would not fix it."""
    if decision.allowed or not set(decision.reasons) <= BIOMETRIC_REASONS:
        return []
    return [
        p
        for p in profiles
        if p.get("status") == "ACTIVE"
        and country in (p.get("jurisdictions") or ())
        and country not in (p.get("biometric_jurisdictions") or ())
    ]


def parse(doc: Mapping[str, Any]) -> dict[str, Overlay]:
    """Overlays by country (the highest version of each). A malformed row refuses the whole file."""
    out: dict[str, Overlay] = {}
    try:
        for raw in doc.get("overlays") or []:
            country = str(raw["country"]).upper()
            if not COUNTRY.match(country):
                raise OverlayError("PRIVACY_OVERLAYS_INVALID", f"country {raw['country']!r}")
            regions = {str(r).upper() for r in raw.get("home_regions") or ()}
            transfers = {
                str(k).upper(): str(v) for k, v in (raw.get("transfer_regions") or {}).items()
            }
            providers = {
                str(k): str(v).upper() for k, v in (raw.get("provider_regions") or {}).items()
            }
            if any(not REGION.match(r) for r in (*regions, *transfers, *providers.values())):
                raise OverlayError(
                    "PRIVACY_OVERLAYS_INVALID", f"{country}: a region is not a region code"
                )
            if regions & set(transfers):
                raise OverlayError(
                    "PRIVACY_OVERLAYS_INVALID",
                    f"{country}: a home region needs no transfer mechanism",
                )
            dpia = raw.get("dpia") or {}
            o = Overlay(
                country=country,
                version=int(raw["version"]),
                home_regions=frozenset(regions),
                transfer_regions=transfers,
                provider_regions=providers,
                biometrics=Biometrics(str(raw["biometrics"]).upper()),
                dpia_required=bool(dpia.get("required", False)),
                dpia_ref=(str(dpia["reference"]) if dpia.get("reference") else None),
                lawful_basis=str(raw.get("lawful_basis") or ""),
                approvals={
                    str(role): Approval(str(a["reference"]), str(a["signed_hash"]))
                    for role, a in (raw.get("approvals") or {}).items()
                },
            )
            if country in out and out[country].version == o.version:
                raise OverlayError(
                    "PRIVACY_OVERLAYS_INVALID", f"{country}: duplicate version {o.version}"
                )
            if country not in out or o.version > out[country].version:
                out[country] = o
    except OverlayError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise OverlayError("PRIVACY_OVERLAYS_INVALID", str(e)[:80]) from e
    return out
