"""Certified provider registry and the certification claims gate (ZID-GAP-001 v2.0 DTV-07, DTV-14, DTV-15; Doc 03). Pure domain.

A certification belongs to one legal entity, for one service, in one place, under one scheme and version, for one period. This
module is the only place that says who holds what, and it fails closed:
- no record is no certification: Zoiko iD holds none today, so any claim to be a certified UK digital verification provider or a
  qualified trust service provider is refused (DTV-14, DTV-15) until an ACTIVE record in Zoiko's own name exists;
- a record counts only on the days it is in force and only while its status is ACTIVE; suspended, withdrawn or expired is nothing;
- a partner's certification is the partner's: a claim must name the entity that holds it, and a record for a COMPONENT of a service
  never supports a claim of the full service ("providers may do certified component");
- losing a certification blocks exactly the packs that need that scheme in that place; unrelated packs are untouched (GLB-28).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Any

ZOIKO_ENTITY = "ZOIKO"


class CertificationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class CertStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class Certification:
    record_id: str
    entity: str  # the legal entity that holds it: ZOIKO or a named partner
    scheme: str  # e.g. UK_DVS, EU_QTSP
    scheme_version: str
    country: str  # where it is recognized
    service_code: str
    certificate_ref: str
    valid_from: date
    valid_until: date | None
    status: CertStatus = CertStatus.ACTIVE
    component_only: bool = False  # certified for a part of the service, not the whole


def in_force(rec: Certification, on: date) -> bool:
    return (
        rec.status is CertStatus.ACTIVE
        and rec.valid_from <= on
        and (rec.valid_until is None or on <= rec.valid_until)
    )


@dataclass(frozen=True, slots=True)
class CertRegistry:
    version: str
    records: Sequence[Certification]


def holds(
    registry: CertRegistry,
    *,
    entity: str,
    scheme: str,
    country: str,
    service_code: str,
    on: date,
    full_service: bool = True,
) -> Certification | None:
    """The record that makes `entity` certified for this exact scheme, place and service on this day, or None."""
    for rec in registry.records:
        if (
            rec.entity == entity
            and rec.scheme == scheme
            and rec.country == country.upper()
            and rec.service_code == service_code
            and in_force(rec, on)
            and not (full_service and rec.component_only)
        ):
            return rec
    return None


@dataclass(frozen=True, slots=True)
class CertClaim:
    """A statement of certification that a page, an API description or a contract wants to make."""

    entity: str
    scheme: str
    country: str
    service_code: str
    full_service: bool = True


def refuse_unbacked_claim(registry: CertRegistry, claim: CertClaim, *, on: date) -> Certification:
    """Return the record that backs the claim or raise `CERTIFICATION_CLAIM_UNBACKED`. A claim by Zoiko iD itself is held to the
    same rule as a partner's: no self-asserted designation (DTV-14, DTV-15)."""
    rec = holds(
        registry,
        entity=claim.entity,
        scheme=claim.scheme,
        country=claim.country,
        service_code=claim.service_code,
        on=on,
        full_service=claim.full_service,
    )
    if rec is None:
        raise CertificationError(
            "CERTIFICATION_CLAIM_UNBACKED",
            f"{claim.entity}/{claim.scheme}/{claim.country}/{claim.service_code}",
        )
    return rec


def scheme_satisfied(
    registry: CertRegistry,
    *,
    scheme: str | None,
    country: str,
    service_code: str,
    entities: Collection[str],
    on: date,
) -> bool:
    """Does any of the entities that would carry out this service hold the scheme? No scheme required means nothing to hold."""
    if scheme is None:
        return True
    return any(
        holds(registry, entity=e, scheme=scheme, country=country, service_code=service_code, on=on)
        is not None
        for e in entities
    )


def blocked_scopes(
    registry: CertRegistry, required: Mapping[tuple[str, str], str], *, on: date
) -> set[tuple[str, str]]:
    """`required` maps (country, service_code) to the scheme it needs. Returns the scopes whose certification is not in force
    for ANY entity: only those are blocked."""
    out: set[tuple[str, str]] = set()
    for (country, service), scheme in required.items():
        if not any(
            r.scheme == scheme
            and r.country == country.upper()
            and r.service_code == service
            and in_force(r, on)
            for r in registry.records
        ):
            out.add((country.upper(), service))
    return out


def parse(doc: Mapping[str, Any]) -> CertRegistry:
    records: list[Certification] = []
    seen: set[str] = set()
    try:
        for raw in doc.get("certifications") or []:
            rec = Certification(
                record_id=raw["record_id"],
                entity=raw["entity"],
                scheme=raw["scheme"],
                scheme_version=str(raw["scheme_version"]),
                country=raw["country"].upper(),
                service_code=raw["service_code"],
                certificate_ref=raw["certificate_ref"],
                valid_from=_d(raw["valid_from"]),
                valid_until=_d(raw["valid_until"]) if raw.get("valid_until") else None,
                status=CertStatus(raw.get("status", "ACTIVE")),
                component_only=bool(raw.get("component_only", False)),
            )
            if rec.record_id in seen:
                raise CertificationError(
                    "CERTIFICATION_REGISTRY_INVALID", f"duplicate {rec.record_id}"
                )
            if rec.valid_until is not None and rec.valid_until < rec.valid_from:
                raise CertificationError("CERTIFICATION_REGISTRY_INVALID", rec.record_id)
            seen.add(rec.record_id)
            records.append(rec)
    except (KeyError, ValueError, TypeError) as e:
        if isinstance(e, CertificationError):
            raise
        raise CertificationError("CERTIFICATION_REGISTRY_INVALID", str(e)[:80]) from e
    return CertRegistry(str(doc.get("version", "0")), tuple(records))


def _d(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


# ------------------------------------------------------------------------------------------ register reconciliation (RCG-G02)

REGISTER_REGISTERED = "REGISTERED"
REGISTER_SUSPENDED = "SUSPENDED"
REGISTER_WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True, slots=True)
class RegisterEntry:
    """One line of an official register (a trusted list, a regulator's register of certified providers) as a fetcher normalized it."""

    entity: str
    scheme: str
    country: str
    service_code: str
    certificate_ref: str
    status: str  # REGISTERED | SUSPENDED | WITHDRAWN
    valid_until: date | None = None


@dataclass(frozen=True, slots=True)
class SuspensionProof:
    """Why a record stopped counting: what we held, what the register said, which snapshot said it, and when it was read."""

    record_id: str
    previous_status: str
    effective_status: str
    reason: str  # NOT_IN_REGISTER | REGISTER_SUSPENDED | REGISTER_WITHDRAWN | REGISTER_EXPIRES_EARLIER | REGISTER_STATUS_UNRECOGNIZED
    source_id: str
    snapshot_sha256: str
    checked_at: str


def reconcile(
    registry: CertRegistry,
    entries: Sequence[RegisterEntry],
    *,
    source_id: str,
    snapshot_sha256: str,
    checked_at: str,
) -> tuple[CertRegistry, tuple[SuspensionProof, ...]]:
    """Compare what we hold with what the official register says, and DOWNGRADE every record the register does not support. It never upgrades.

    - a record the register does not list is not relied on (SUSPENDED, NOT_IN_REGISTER): a certification we cannot see on its own register is not
      one we can claim, and silence is not confirmation;
    - a register entry that is suspended or withdrawn makes the record so, at once, with no one having to edit a file;
    - a register date earlier than ours shortens the record to the register's date;
    - a status the register uses that we do not know is treated as not certified;
    - a record already SUSPENDED, WITHDRAWN or EXPIRED stays so: reinstatement is a person's decision with new evidence, never a side effect of a fetch;
    - register entries with no record of ours are ignored (nothing is claimed on their account).
    Pure: nothing is fetched here. Each downgrade comes with the proof of what was read and when."""
    index = {
        (e.entity, e.scheme, e.country.upper(), e.service_code, e.certificate_ref): e
        for e in entries
    }
    out: list[Certification] = []
    proofs: list[SuspensionProof] = []
    for rec in registry.records:
        if rec.status is not CertStatus.ACTIVE:
            out.append(rec)
            continue
        entry = index.get(
            (rec.entity, rec.scheme, rec.country, rec.service_code, rec.certificate_ref)
        )
        new, reason = rec, ""
        if entry is None:
            new, reason = replace(rec, status=CertStatus.SUSPENDED), "NOT_IN_REGISTER"
        elif entry.status == REGISTER_WITHDRAWN:
            new, reason = replace(rec, status=CertStatus.WITHDRAWN), "REGISTER_WITHDRAWN"
        elif entry.status == REGISTER_SUSPENDED:
            new, reason = replace(rec, status=CertStatus.SUSPENDED), "REGISTER_SUSPENDED"
        elif entry.status != REGISTER_REGISTERED:
            new, reason = replace(rec, status=CertStatus.SUSPENDED), "REGISTER_STATUS_UNRECOGNIZED"
        elif entry.valid_until is not None and (
            rec.valid_until is None or entry.valid_until < rec.valid_until
        ):
            new, reason = replace(rec, valid_until=entry.valid_until), "REGISTER_EXPIRES_EARLIER"
        out.append(new)
        if reason:
            proofs.append(
                SuspensionProof(
                    record_id=rec.record_id,
                    previous_status=rec.status.value,
                    effective_status=new.status.value,
                    reason=reason,
                    source_id=source_id,
                    snapshot_sha256=snapshot_sha256,
                    checked_at=checked_at,
                )
            )
    return CertRegistry(registry.version, tuple(out)), tuple(proofs)
