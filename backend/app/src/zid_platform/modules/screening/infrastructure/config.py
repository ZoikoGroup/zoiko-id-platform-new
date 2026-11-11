"""Load the screening policy and country packs (`backend/config/screening`; ZID_PLATFORM_CONFIG overrides the root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.screening.domain.decision import Policy, ScreeningError
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.domain.matching import MatchPolicy
from zid_platform.modules.screening.domain.ownership import CountryPack

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "screening/screening-policy.yaml"
EVALUATORS = frozenset({"ofac_50", "ofsi"})
OWNER_RULES = frozenset({"uk_psc"})


@dataclass(frozen=True, slots=True)
class Source:
    """Where a list comes from. `urls` is empty until a verified address is on record, and then nothing is fetched."""

    list_id: ListId
    urls: dict[str, str]  # file name -> address ("file", or sdn, alt and add for the OFAC files)
    redirect_hosts: tuple[str, ...]  # the only other hosts a redirect may lead to
    format: str

    @property
    def configured(self) -> bool:
        return bool(self.urls) and ("file" in self.urls or "sdn" in self.urls)


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    policy: Policy
    packs: dict[str, CountryPack]
    sources: dict[ListId, Source]

    def pack(self, country: str) -> CountryPack | None:
        return self.packs.get(country)


def parse(doc: dict[str, Any]) -> ScreeningConfig:
    p = doc.get("policy") or {}
    try:
        required = tuple(ListId(x) for x in p.get("required_lists") or ())
    except ValueError as e:
        raise ScreeningError("CONFIG_INVALID", "unknown list in required_lists") from e
    if not required:
        raise ScreeningError("CONFIG_INVALID", "no required lists: nothing could ever be screened")
    try:
        high_risk = tuple(ListId(x) for x in p.get("high_risk_required_lists") or ())
    except ValueError as e:
        raise ScreeningError("CONFIG_INVALID", "unknown list in high_risk_required_lists") from e
    version = str(doc.get("version", "0"))
    policy = Policy(
        version=version,
        required_lists=required,
        list_max_age=timedelta(hours=int(p.get("list_max_age_hours", 48))),
        validity=timedelta(days=int(p.get("validity_days", 365))),
        validity_elevated=timedelta(days=int(p.get("validity_elevated_days", 90))),
        high_risk_required_lists=high_risk,
        match=MatchPolicy(
            version=version,
            threshold=float(p.get("match_threshold", 0.88)),
            strong_name=float(p.get("strong_name", 0.97)),
        ),
    )
    packs: dict[str, CountryPack] = {}
    for country, body in (doc.get("packs") or {}).items():
        rule = body.get("beneficial_owner_rule")
        evaluators = tuple(body.get("sanctions_evaluators") or ())
        threshold = body.get("beneficial_owner_threshold")
        if len(country) != 2 or (rule and rule not in OWNER_RULES) or set(evaluators) - EVALUATORS:
            raise ScreeningError("CONFIG_INVALID", f"pack {country}")
        if rule and threshold is None:
            raise ScreeningError("CONFIG_INVALID", f"pack {country}: a rule needs its threshold")
        packs[country] = CountryPack(
            country=country,
            version=str(body.get("version", "0")),
            beneficial_owner_rule=rule,
            beneficial_owner_threshold=Decimal(str(threshold)) if threshold is not None else None,
            sanctions_evaluators=evaluators,
            status=str(body.get("status", "PROPOSED")),
            local_list_required=bool(body.get("local_list_required", False)),
        )
    sources: dict[ListId, Source] = {}
    for key, body in (doc.get("sources") or {}).items():
        try:
            list_id = ListId(key)
        except ValueError as e:
            raise ScreeningError("CONFIG_INVALID", f"unknown list {key} in sources") from e
        urls = {k: v for k, v in (body.get("urls") or {}).items() if v}
        if body.get("url"):
            urls["file"] = body["url"]
        sources[list_id] = Source(
            list_id,
            urls,
            tuple(h.lower() for h in body.get("redirect_hosts") or ()),
            str(body.get("format", "")),
        )
    return ScreeningConfig(policy, packs, sources)


@lru_cache(maxsize=1)
def config() -> ScreeningConfig:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "ScreeningConfig", "config", "parse"]
