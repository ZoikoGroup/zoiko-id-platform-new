"""Load the licensed screening providers (`backend/config/screening/providers.yaml`; ZID_PLATFORM_CONFIG overrides the config root; ZID-DTV-WF-06 S11, ENG-13; CR-113).

Versioned configuration, default off: the shipped file lists NO provider. A provider is read strictly (an unknown key, state or class refuses the whole file, a price is a decimal string), and what is
read is only the contract's terms. Nothing here calls a provider or holds a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.provider_controls import Provider, ProviderState

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "screening/providers.yaml"
LICENSED_CLASSES = frozenset({"PEP_RCA", "ADVERSE_MEDIA"})
_KEYS = frozenset(
    {
        "state",
        "adapter",
        "classes",
        "markets",
        "regions",
        "contract_version",
        "price_version",
        "finance_authorised_by",
        "unit_cost",
        "monthly_budget",
        "monthly_quota",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    provider: Provider
    adapter: str


def _money(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise ScreeningError("CONFIG_INVALID", f"{field} is not a number") from e
    if d < 0:
        raise ScreeningError("CONFIG_INVALID", f"{field} is negative")
    return d


def parse(doc: dict[str, Any]) -> dict[str, ProviderEntry]:
    out: dict[str, ProviderEntry] = {}
    for provider_id, body in (doc.get("providers") or {}).items():
        if not isinstance(body, dict):
            raise ScreeningError("CONFIG_INVALID", f"provider {provider_id}")
        unknown = set(body) - _KEYS
        if unknown:
            raise ScreeningError("CONFIG_INVALID", f"{provider_id}: unknown key {min(unknown)}")
        try:
            state = ProviderState(str(body.get("state", "DISABLED")))
        except ValueError as e:
            raise ScreeningError("CONFIG_INVALID", f"{provider_id}: unknown state") from e
        classes = frozenset(str(c) for c in body.get("classes") or ())
        if classes - LICENSED_CLASSES:
            raise ScreeningError(
                "CONFIG_INVALID",
                f"{provider_id}: only {sorted(LICENSED_CLASSES)} are licensed classes",
            )
        quota = body.get("monthly_quota")
        out[str(provider_id)] = ProviderEntry(
            Provider(
                provider_id=str(provider_id),
                state=state,
                classes=classes,
                markets=frozenset(str(m) for m in body.get("markets") or ()),
                regions=frozenset(str(r) for r in body.get("regions") or ()),
                contract_version=body.get("contract_version"),
                price_version=body.get("price_version"),
                finance_authorised_by=body.get("finance_authorised_by"),
                unit_cost=_money(body.get("unit_cost", 0), "unit_cost") or Decimal(0),
                monthly_budget=_money(body.get("monthly_budget"), "monthly_budget"),
                monthly_quota=int(quota) if quota is not None else None,
            ),
            str(body.get("adapter", "")),
        )
    return out


@lru_cache(maxsize=1)
def providers() -> dict[str, ProviderEntry]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "LICENSED_CLASSES", "ProviderEntry", "parse", "providers"]
