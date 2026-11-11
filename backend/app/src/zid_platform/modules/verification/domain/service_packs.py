"""Country / profession service packs (ZID-GAP-001 v2.0 GID-036; section 07.1). Pure domain, no I/O.

A pack is policy data, not code: one versioned record per country, optional subdivision, optional profession and service,
saying which legal basis, sources, residency and review rules apply and how far it may be relied on. It complements the
country capability registry (`countries.py`), which gates a whole capability per country; it does not replace it.

Rules, each with a test:
- the default is UNAVAILABLE: no pack, a disabled country, a BLOCKED pack, a pack outside its dates, or a pack whose state
  is not usable in this environment all give the same answer, `PACK_UNAVAILABLE`, never a guess or a neighbouring pack;
- the most specific pack wins (profession and subdivision beat a country-wide pack), and among versions of one pack the
  highest effective version wins; naming an older version explicitly is refused (`PACK_VERSION_STALE`);
- a pack cannot be LIVE_PROVEN, or published, without a legal basis, owners, a QA gate, retention and review rules, and a
  home processing region that its allowed regions include; and every source it names must be currently available
  (GLB-29: a pack listing an unavailable primary source as LIVE is refused);
- the recorded `policy_hash` must match the pack's content, so an edited pack cannot keep an old approval;
- one pack losing its status (a certified provider, say) blocks that pack only; other packs are untouched (GLB-28).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.certification import CertRegistry, scheme_satisfied

ISO_ALPHA2 = re.compile(r"^[A-Z]{2}$")
SANDBOX_ENVIRONMENTS = frozenset({"local", "test"})


class PackError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class FeatureState(StrEnum):
    LIVE_PROVEN = (
        "LIVE_PROVEN"  # repository, runtime, QA, contract, legal and monitoring evidence all exist
    )
    PARTNER_ONLY = (
        "PARTNER_ONLY"  # available only through a named partner, not by Zoiko's own sources
    )
    STAGING = "STAGING"
    DESIGN = "DESIGN"
    BLOCKED = "BLOCKED"


# states a caller may rely on in a deployed environment
RELIABLE = frozenset({FeatureState.LIVE_PROVEN, FeatureState.PARTNER_ONLY})
# states that need evidence behind them before they are published
EVIDENCED = frozenset({FeatureState.LIVE_PROVEN, FeatureState.PARTNER_ONLY})


@dataclass(frozen=True, slots=True)
class ServicePack:
    pack_id: str
    version: int
    country_iso2: str
    service_code: str
    feature_state: FeatureState
    data_residency_region: str
    allowed_processing_regions: tuple[str, ...]
    subdivision_code: str | None = None
    profession_code: str | None = None
    legal_basis_ref: str | None = None
    source_registry_ids: tuple[str, ...] = ()
    verification_methods: tuple[str, ...] = ()
    evidence_tier_required: str | None = None
    identity_assurance_required: str | None = None
    applicable_regulatory_scheme: str | None = None
    recognition_rule_id: str | None = None
    lawful_transfer_mechanism: str | None = None
    mandatory_consent_notice_id: str | None = None
    retention_policy_id: str | None = None
    human_review_rule_id: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    policy_hash: str = ""
    legal_owner: str | None = None
    product_owner: str | None = None
    qa_gate_id: str | None = None

    def specificity(self) -> int:
        return (2 if self.profession_code else 0) + (1 if self.subdivision_code else 0)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_canonical(v) for v in value]
    return value


def compute_hash(pack: ServicePack) -> str:
    """SHA-256 over every field except `policy_hash` itself, in a fixed order."""
    body = {
        f.name: _canonical(getattr(pack, f.name)) for f in fields(pack) if f.name != "policy_hash"
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sealed(pack: ServicePack) -> ServicePack:
    """The pack with its `policy_hash` set to the hash of its content."""
    return ServicePack(
        **{
            **{f.name: getattr(pack, f.name) for f in fields(pack)},
            "policy_hash": compute_hash(pack),
        }
    )


_EVIDENCE_FIELDS = (
    "legal_basis_ref",
    "retention_policy_id",
    "human_review_rule_id",
    "legal_owner",
    "product_owner",
    "qa_gate_id",
)


def validate_pack(pack: ServicePack, *, available_sources: Collection[str] | None = None) -> None:
    """Refuse a pack that claims more than it can back. `available_sources` is the set of source ids that are open now;
    leave it None only to check the shape without the source check."""
    if not ISO_ALPHA2.match(pack.country_iso2):
        raise PackError("PACK_COUNTRY_INVALID", pack.country_iso2)
    if pack.version < 1 or not pack.pack_id or not pack.service_code:
        raise PackError("PACK_IDENTITY_INVALID", pack.pack_id)
    if pack.valid_from and pack.valid_until and pack.valid_until <= pack.valid_from:
        raise PackError("PACK_DATES_INVALID", pack.pack_id)
    if pack.feature_state in EVIDENCED:
        # a draft claims nothing, so only a pack that can be relied on must match its approved content
        if pack.policy_hash != compute_hash(pack):
            raise PackError("PACK_HASH_MISMATCH", pack.pack_id)
        missing = [n for n in _EVIDENCE_FIELDS if not getattr(pack, n)]
        if pack.valid_from is None:
            missing.append("valid_from")
        if not pack.allowed_processing_regions:
            missing.append("allowed_processing_regions")
        elif pack.data_residency_region not in pack.allowed_processing_regions:
            raise PackError("PACK_RESIDENCY_INCONSISTENT", pack.pack_id)
        if missing:
            raise PackError("PACK_EVIDENCE_MISSING", f"{pack.pack_id}: {','.join(missing)}")
        if not pack.source_registry_ids:
            raise PackError("PACK_SOURCE_MISSING", pack.pack_id)
        if pack.feature_state is FeatureState.LIVE_PROVEN and available_sources is not None:
            closed = [s for s in pack.source_registry_ids if s not in available_sources]
            if closed:
                raise PackError("PACK_SOURCE_UNAVAILABLE", f"{pack.pack_id}: {','.join(closed)}")


@dataclass(frozen=True, slots=True)
class PackRegistry:
    version: str
    packs: Sequence[ServicePack]
    disabled_countries: frozenset[str] = field(default_factory=frozenset)


def validate_registry(
    registry: PackRegistry, *, available_sources: Collection[str] | None = None
) -> None:
    seen: set[tuple[str, int]] = set()
    for pack in registry.packs:
        key = (pack.pack_id, pack.version)
        if key in seen:
            raise PackError("PACK_VERSION_DUPLICATE", f"{pack.pack_id} v{pack.version}")
        seen.add(key)
        validate_pack(pack, available_sources=available_sources)


def _usable(pack: ServicePack, *, now: datetime, environment: str) -> bool:
    if pack.feature_state is FeatureState.BLOCKED:
        return False
    if pack.valid_from is None or now < pack.valid_from:
        return False
    if pack.valid_until is not None and now >= pack.valid_until:
        return False
    if pack.feature_state in RELIABLE:
        return True
    return (
        environment in SANDBOX_ENVIRONMENTS
    )  # STAGING and DESIGN never serve a deployed environment


def resolve(
    registry: PackRegistry,
    *,
    country: str,
    service_code: str,
    profession: str | None = None,
    subdivision: str | None = None,
    now: datetime,
    environment: str,
    pack_id: str | None = None,
    version: int | None = None,
    certifications: CertRegistry | None = None,
) -> ServicePack:
    """The pack that applies, or `PACK_UNAVAILABLE`. Never falls back to a different country or service. When a certification
    registry is given, a pack that names a regulatory scheme is unavailable unless some entity holds that scheme in force for
    this place and service: losing a certification blocks that pack only (GLB-28)."""
    code = country.upper()
    if not ISO_ALPHA2.match(code) or code in {c.upper() for c in registry.disabled_countries}:
        raise PackError("PACK_UNAVAILABLE", code)
    matches = [
        p
        for p in registry.packs
        if p.country_iso2 == code
        and p.service_code == service_code
        and (p.profession_code is None or p.profession_code == profession)
        and (p.subdivision_code is None or p.subdivision_code == subdivision)
        and (pack_id is None or p.pack_id == pack_id)
    ]
    if not matches:
        raise PackError("PACK_UNAVAILABLE", f"{code}/{service_code}")
    # the most specific scope decides, even when its pack is blocked: a blocked profession pack must not quietly
    # fall back to the broader country pack
    best = max(p.specificity() for p in matches)
    usable = [
        p
        for p in matches
        if p.specificity() == best and _usable(p, now=now, environment=environment)
    ]
    if not usable:
        raise PackError("PACK_UNAVAILABLE", f"{code}/{service_code}")
    chosen = max(usable, key=lambda p: p.version)
    if version is not None and version != chosen.version:
        raise PackError(
            "PACK_VERSION_STALE", f"{chosen.pack_id}: v{version} is not v{chosen.version}"
        )
    if certifications is not None and not scheme_satisfied(
        certifications,
        scheme=chosen.applicable_regulatory_scheme,
        country=code,
        service_code=service_code,
        entities={r.entity for r in certifications.records},
        on=now.date(),
    ):
        raise PackError("PACK_UNAVAILABLE", f"{chosen.pack_id}: certification not in force")
    return chosen


def parse(doc: Mapping[str, Any]) -> PackRegistry:
    """Build a registry from a loaded YAML/JSON document. An unknown state or a malformed row is refused, not skipped."""
    packs: list[ServicePack] = []
    for raw in doc.get("packs") or []:
        try:
            body = dict(raw)
            body["feature_state"] = FeatureState(body.get("feature_state", "DESIGN"))
            for key in (
                "source_registry_ids",
                "verification_methods",
                "allowed_processing_regions",
            ):
                body[key] = tuple(body.get(key) or ())
            for key in ("valid_from", "valid_until"):
                if body.get(key) is not None:
                    value = body[key]
                    body[key] = (
                        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
                    )
                    if body[key].tzinfo is None:
                        body[key] = body[key].replace(tzinfo=UTC)
            packs.append(ServicePack(**body))
        except (ValueError, TypeError) as e:
            raise PackError("PACK_REGISTRY_INVALID", str(raw.get("pack_id", "?"))) from e
    return PackRegistry(
        version=str(doc.get("version", "0")),
        packs=tuple(packs),
        disabled_countries=frozenset(str(c).upper() for c in doc.get("disabled_countries") or ()),
    )


def public_view(registry: PackRegistry, *, now: datetime, environment: str) -> list[dict[str, Any]]:
    """What a caller may be told: scope and state only; never a legal reference, an owner or a source id."""
    out: list[dict[str, Any]] = []
    for p in registry.packs:
        usable = _usable(p, now=now, environment=environment) and p.country_iso2 not in {
            c.upper() for c in registry.disabled_countries
        }
        out.append(
            {
                "pack_id": p.pack_id,
                "version": p.version,
                "country": p.country_iso2,
                "subdivision": p.subdivision_code,
                "profession": p.profession_code,
                "service": p.service_code,
                "state": p.feature_state.value if usable else "UNAVAILABLE",
            }
        )
    return out
