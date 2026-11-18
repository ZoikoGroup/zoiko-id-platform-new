"""Load the signed market screening policies (`backend/config/screening/market-policies.yaml`; ZID_PLATFORM_CONFIG overrides the config root; ZID-DTV-WF-06 S01, ENG-01; CR-113).

Versioned configuration, default off: the shipped file lists NO market, so a request for any market is refused as having no policy. A market is read strictly (an unknown key, an unknown
class, tier or subject type, or a missing signature date refuses the whole file), and a pack names the tenants it is enabled for. Nothing here approves anything: a pack counts only while two
different people have signed it and it is inside its dates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.screening.domain.coverage import Klass, Tier
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.eligibility import MarketPolicy, SubjectType

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "screening/market-policies.yaml"
_KEYS = frozenset(
    {
        "version",
        "purposes",
        "regions",
        "subject_types",
        "classes",
        "legal_basis",
        "signed_by",
        "valid_from",
        "valid_to",
        "evidence_max_age_days",
        "enabled_tenants",
    }
)


@dataclass(frozen=True, slots=True)
class MarketPack:
    policy: MarketPolicy
    enabled_tenants: frozenset[str]

    def entitled(self, tenant_id: str) -> frozenset[Klass]:
        """The classes a tenant may use: the policy's, for a tenant the pack names, and nothing for any other."""
        return frozenset(self.policy.classes) if tenant_id in self.enabled_tenants else frozenset()


def _when(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as e:
        raise ScreeningError("CONFIG_INVALID", f"{field} is not a date") from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse(doc: dict[str, Any]) -> dict[str, MarketPack]:
    packs: dict[str, MarketPack] = {}
    for market, body in (doc.get("markets") or {}).items():
        if len(str(market)) != 2 or not isinstance(body, dict):
            raise ScreeningError("CONFIG_INVALID", f"market {market}")
        unknown = set(body) - _KEYS
        if unknown:
            raise ScreeningError("CONFIG_INVALID", f"{market}: unknown key {min(unknown)}")
        try:
            classes = {Klass(k): Tier(v) for k, v in (body.get("classes") or {}).items()}
            subject_types = frozenset(SubjectType(x) for x in body.get("subject_types") or ())
        except ValueError as e:
            raise ScreeningError(
                "CONFIG_INVALID", f"{market}: unknown class, tier or subject type"
            ) from e
        if body.get("valid_from") is None or not body.get("version"):
            raise ScreeningError(
                "CONFIG_INVALID", f"{market}: a version and a start date are required"
            )
        policy = MarketPolicy(
            market=str(market),
            version=str(body["version"]),
            purposes=frozenset(str(p) for p in body.get("purposes") or ()),
            regions=frozenset(str(r) for r in body.get("regions") or ()),
            subject_types=subject_types,
            classes=classes,
            legal_basis=body.get("legal_basis"),
            signed_by=tuple(str(s) for s in body.get("signed_by") or ()),
            valid_from=_when(body["valid_from"], "valid_from"),
            valid_to=_when(body["valid_to"], "valid_to") if body.get("valid_to") else None,
            evidence_max_age=timedelta(days=int(body.get("evidence_max_age_days", 365))),
        )
        packs[str(market)] = MarketPack(
            policy, frozenset(str(t) for t in body.get("enabled_tenants") or ())
        )
    return packs


@lru_cache(maxsize=1)
def market_packs() -> dict[str, MarketPack]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "MarketPack", "market_packs", "parse"]
