"""Sector, transaction and crypto monitoring scope (ZID-RCG-003 RCG-G12; section 03.1 Fraud/AML layer; CR-081). Pure domain, no I/O.

Document 03.1: sanctions, PEP, adverse media, transactions, bank ownership and crypto are enabled only when a local legal basis AND a contract that authorizes it
is demonstrated, and there is no global background monitoring by default. G12 makes that a rule the code asks before any ongoing monitoring runs:
- monitoring of a kind is allowed in a country, for a sector, only under an approval that names the legal basis and the authorizing contract
  (the contract that pays for and permits it), is in its dates, and is signed by Legal and Compliance over its exact hash. Nothing else counts;
- no approval is no monitoring: the answer is MONITORING_NOT_APPROVED, never a default yes;
- a crypto kind (travel rule, wallet screening) also needs the crypto-specific authorization it runs under (for example a MiCA CASP licence or a VASP
  registration), named in the approval; a general AML approval never stretches to crypto;
- an approval for one sector does not cover another; `ANY` must be written to mean any;
- screening at onboarding (a company being verified now) is not background monitoring and is not governed here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

APPROVER_ROLES = ("legal", "compliance")
COUNTRY = re.compile(r"^[A-Z]{2}$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")
ANY_SECTOR = "ANY"


class MonitoringError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Kind(StrEnum):
    SANCTIONS_ONGOING = "SANCTIONS_ONGOING"  # screening an existing customer again over time
    PEP = "PEP"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    TRANSACTION_MONITORING = "TRANSACTION_MONITORING"
    BANK_OWNERSHIP = "BANK_OWNERSHIP"
    CRYPTO_TRAVEL_RULE = "CRYPTO_TRAVEL_RULE"
    CRYPTO_WALLET_SCREENING = "CRYPTO_WALLET_SCREENING"


CRYPTO_KINDS = frozenset({Kind.CRYPTO_TRAVEL_RULE, Kind.CRYPTO_WALLET_SCREENING})


@dataclass(frozen=True)
class Approval:
    reference: str
    signed_hash: str


@dataclass(frozen=True)
class MonitoringApproval:
    approval_id: str
    version: int
    country: str
    kind: Kind
    sectors: frozenset[str]
    legal_basis_ref: str  # the legal basis, demonstrated
    contract_ref: str  # the authorizing contract: the contract that permits and pays for it
    valid_from: datetime
    valid_until: datetime | None = None
    crypto_authorization_ref: str | None = None  # required for a crypto kind
    approvals: Mapping[str, Approval] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitoringDecision:
    allowed: bool
    reasons: tuple[str, ...]
    approval_id: str | None = None


def approval_hash(a: MonitoringApproval) -> str:
    body = {
        "approval_id": a.approval_id,
        "version": a.version,
        "country": a.country,
        "kind": a.kind.value,
        "sectors": sorted(a.sectors),
        "legal_basis_ref": a.legal_basis_ref,
        "contract_ref": a.contract_ref,
        "valid_from": a.valid_from.isoformat(),
        "valid_until": a.valid_until.isoformat() if a.valid_until else None,
        "crypto_authorization_ref": a.crypto_authorization_ref,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def problems(a: MonitoringApproval, *, at: datetime) -> list[str]:
    """What makes this approval unusable at `at`."""
    out: list[str] = []
    digest = approval_hash(a)
    for role in APPROVER_ROLES:
        sig = a.approvals.get(role)
        if sig is None or not sig.reference.strip():
            out.append(f"approval_missing:{role}")
        elif sig.signed_hash != digest:
            out.append(f"approval_hash_mismatch:{role}")
    if not REFERENCE.match(a.legal_basis_ref):
        out.append("LEGAL_BASIS_MISSING")
    if not REFERENCE.match(a.contract_ref):
        out.append("CONTRACT_MISSING")
    if a.kind in CRYPTO_KINDS and not (
        a.crypto_authorization_ref and REFERENCE.match(a.crypto_authorization_ref)
    ):
        out.append("CRYPTO_AUTHORIZATION_MISSING")
    if at < a.valid_from:
        out.append("APPROVAL_NOT_YET_VALID")
    if a.valid_until is not None and at >= a.valid_until:
        out.append("APPROVAL_EXPIRED")
    return out


def decide(
    approvals: Iterable[MonitoringApproval],
    *,
    country: str,
    kind: Kind,
    sector: str = ANY_SECTOR,
    at: datetime,
) -> MonitoringDecision:
    """May monitoring of `kind` run in `country` for `sector` at `at`? Only under a usable approval; every reason is listed otherwise."""
    candidates = [a for a in approvals if a.country == country.upper() and a.kind is kind]
    if not candidates:
        return MonitoringDecision(False, ("MONITORING_NOT_APPROVED",))
    covering = [a for a in candidates if ANY_SECTOR in a.sectors or sector in a.sectors]
    if not covering:
        return MonitoringDecision(False, ("SECTOR_NOT_COVERED",))
    reasons: list[str] = []
    for a in sorted(covering, key=lambda a: (-a.version, a.approval_id)):
        found = problems(a, at=at)
        if not found:
            return MonitoringDecision(True, (), a.approval_id)
        reasons.extend(r for r in found if r not in reasons)
    return MonitoringDecision(False, tuple(reasons))


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise MonitoringError("MONITORING_APPROVALS_INVALID", "times carry a time zone")
    return parsed


def parse(doc: Mapping[str, Any]) -> list[MonitoringApproval]:
    """A malformed row refuses the whole file; an approval that is merely unusable (unsigned, expired) still loads so people see why."""
    out: list[MonitoringApproval] = []
    try:
        for raw in doc.get("approvals") or []:
            country = str(raw["country"]).upper()
            if not COUNTRY.match(country):
                raise MonitoringError("MONITORING_APPROVALS_INVALID", f"country {raw['country']!r}")
            valid_from = _when(raw["valid_from"])
            if valid_from is None:
                raise MonitoringError("MONITORING_APPROVALS_INVALID", "valid_from is required")
            sectors = frozenset(str(s).upper() for s in raw.get("sectors") or ())
            if not sectors:
                raise MonitoringError(
                    "MONITORING_APPROVALS_INVALID",
                    f"{raw['approval_id']}: name the sectors, or ANY",
                )
            out.append(
                MonitoringApproval(
                    approval_id=str(raw["approval_id"]),
                    version=int(raw["version"]),
                    country=country,
                    kind=Kind(str(raw["kind"]).upper()),
                    sectors=sectors,
                    legal_basis_ref=str(raw.get("legal_basis_ref") or ""),
                    contract_ref=str(raw.get("contract_ref") or ""),
                    valid_from=valid_from,
                    valid_until=_when(raw.get("valid_until")),
                    crypto_authorization_ref=(
                        str(raw["crypto_authorization_ref"])
                        if raw.get("crypto_authorization_ref")
                        else None
                    ),
                    approvals={
                        str(role): Approval(str(s["reference"]), str(s["signed_hash"]))
                        for role, s in (raw.get("approvals") or {}).items()
                    },
                )
            )
    except MonitoringError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise MonitoringError("MONITORING_APPROVALS_INVALID", str(e)[:80]) from e
    keys = [(a.approval_id, a.version) for a in out]
    if len(keys) != len(set(keys)):
        raise MonitoringError("MONITORING_APPROVALS_INVALID", "duplicate approval version")
    return out
