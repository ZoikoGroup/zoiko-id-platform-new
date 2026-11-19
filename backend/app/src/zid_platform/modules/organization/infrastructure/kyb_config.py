"""Load the company-verification routing and policy (`backend/config/kyb`; ZID_PLATFORM_CONFIG overrides the root)."""

from __future__ import annotations

import os
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.organization.domain.kyb import KybError, Policy, Route, Routing

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "kyb/kyb-routing.yaml"


def parse(doc: dict[str, Any]) -> Routing:
    policy = doc.get("policy") or {}
    adapters = {
        k: str((v or {}).get("status", "DISABLED")) for k, v in (doc.get("adapters") or {}).items()
    }
    routes: dict[str, Route] = {}
    for country, body in (doc.get("routes") or {}).items():
        used = [*(body.get("first_pass") or []), *(body.get("exception") or [])]
        if body.get("lei"):
            used.append(body["lei"])
        unknown = [a for a in used if a not in adapters]
        if len(country) != 2 or unknown:
            raise KybError("ROUTING_INVALID", f"{country}: unknown adapter {unknown}")
        routes[country] = Route(
            first_pass=tuple(body.get("first_pass") or ()),
            lei=body.get("lei"),
            exception=tuple(body.get("exception") or ()),
            lei_primary=bool(body.get("lei_as_primary", False)),
        )
    coverage = {
        c: frozenset(ids or ()) for c, ids in (doc.get("supplier_coverage_proven") or {}).items()
    }
    for country, ids in coverage.items():
        if any(a not in adapters for a in ids):
            raise KybError("ROUTING_INVALID", f"{country}: coverage names an unknown adapter")
    return Routing(
        version=str(doc.get("version", "0")),
        policy=Policy(
            version=str(doc.get("version", "0")),
            validity=timedelta(days=int(policy.get("validity_days", 365))),
            name_threshold=float(policy.get("name_threshold", 0.85)),
            refresh_lead=timedelta(days=int(policy.get("refresh_lead_days", 30))),
            draft_period=timedelta(days=int(policy.get("draft_days", 30))),
        ),
        routes=routes,
        adapter_status=adapters,
        supplier_coverage=coverage,
    )


@lru_cache(maxsize=1)
def routing() -> Routing:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "parse", "routing"]
