"""Watching an official register on a schedule (ZID-UAT-005 OPS-03; CR-074): fetch a snapshot, compare it with the last one, close what drifted, keep both.

Every source ships DISABLED with no address (`config/assurance/registry-sources.yaml`). A person enables one by naming its URL after its terms allow
automated reading, and a deployment registers the function that actually fetches it (`register_registry_fetcher`), through whatever egress controls it
has. With the source disabled, or no fetcher registered, nothing is fetched and nothing is closed: the run is refused by name.

The first snapshot of a source is a baseline: it closes nothing, because there is nothing to compare it with. A snapshot taken from a SIMULATED source is
stored as simulated and is never evidence for a release: it exists so the loop can be exercised end to end without any real register.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.assurance.application import service as svc
from zid_platform.modules.assurance.domain import rules
from zid_platform.modules.assurance.domain.rules import RuleError
from zid_platform.modules.assurance.infrastructure import store

CONFIG = Path(
    os.environ.get("ZID_ASSURANCE_CONFIG")
    or Path(__file__).resolve().parents[6] / "config" / "assurance"
)
Fetcher = Callable[[Mapping[str, Any]], Awaitable[Sequence[Mapping[str, Any]]]]
_fetcher: Fetcher | None = None


def register_registry_fetcher(fetcher: Fetcher | None) -> None:
    """A deployment supplies the function that reads the register (through its own egress controls). None by default."""
    global _fetcher
    _fetcher = fetcher


def registry_fetcher() -> Fetcher | None:
    return _fetcher


def sources_config() -> dict[str, Any]:
    path = CONFIG / "registry-sources.yaml"
    return (
        dict((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("sources") or {})
        if path.exists()
        else {}
    )


def _normal(item: Mapping[str, Any]) -> dict[str, Any]:
    vu = item.get("valid_until")
    return {
        "provider_id": str(item["provider_id"]), "service_code": str(item["service_code"]), "status": str(item["status"]),
        "valid_until": date.fromisoformat(vu) if isinstance(vu, str) else vu, "scheme_version": item.get("scheme_version"),
    }  # fmt: skip


async def run_source(
    conn: AsyncConnection,
    tenant: str,
    name: str,
    *,
    config: Mapping[str, Any] | None = None,
    fetch: Fetcher | None = None,
    today: date,
) -> dict[str, Any]:
    cfg = (config if config is not None else sources_config()).get(name)
    if cfg is None:
        raise RuleError("SOURCE_UNKNOWN", name)
    if not cfg.get("enabled"):
        raise RuleError("SOURCE_DISABLED", "this source is not enabled: nothing is fetched")
    getter = fetch or registry_fetcher()
    if getter is None:
        raise RuleError(
            "NO_FETCHER_REGISTERED",
            "a deployment must register the function that reads this register",
        )
    current = [_normal(i) for i in await getter(cfg)]
    digest = svc._sha(
        rules.canonical(
            sorted(
                ({**c, "valid_until": str(c["valid_until"])} for c in current),
                key=lambda c: (c["provider_id"], c["service_code"]),
            )
        )
    )
    last = await store.last_snapshot(conn, name)
    snapshot_id = await store.insert_snapshot(
        conn, tenant, source=name, simulated=bool(cfg.get("simulated")), services=[{**c, "valid_until": str(c["valid_until"]) if c["valid_until"] else None} for c in current], sha256=digest,
    )  # fmt: skip
    if last is None:
        return {
            "source": name,
            "snapshot_id": snapshot_id,
            "first_snapshot": True,
            "changes": [],
            "applied": [],
            "simulated": bool(cfg.get("simulated")),
        }
    previous = [_normal(i) for i in last["services"]]
    out = await svc.watch_registry(conn, tenant, previous=previous, current=current, today=today)
    return {
        "source": name,
        "snapshot_id": snapshot_id,
        "first_snapshot": False,
        "unchanged": last["sha256"] == digest,
        "simulated": bool(cfg.get("simulated")),
        **out,
    }
