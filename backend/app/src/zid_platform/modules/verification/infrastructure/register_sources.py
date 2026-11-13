"""Register sources and their connectors (`backend/config/regulatory/register-sources.yaml`; ZID_PLATFORM_CONFIG overrides the root; CR-087).

The shipped file lists NO source: a real register is added only once the contract or published terms that permit automated queries are recorded,
and its connector is written against that register's documented interface. Until then an operator records what a register says by hand
(POST /v1/regulatory/attestations), and in local/test/dev the built-in simulator stands in.

A connector answers one question: what does the register say about this exact scope? It returns the register's response byte for byte, or None
when the scope is not listed. It never decides anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from zid_platform.modules.verification.domain.attestation import Scope
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.domain.register_sources import (
    SIMULATOR,
    SIMULATOR_BASE,
    Reading,
    Source,
    parse,
    simulator_source,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "regulatory/register-sources.yaml"


class Connector(Protocol):
    async def read(self, scope: Scope, *, now: datetime) -> Reading | None: ...


class SimulatorConnector:
    """A development stand-in for an official register. Deterministic, so tests and demos can rely on it:
    an entity starting `sim-` is listed ACTIVE for one year from the first of this month, `sim-withdrawn-` is listed WITHDRAWN, `sim-component-`
    is certified for a component only, anything else is not listed."""

    async def read(self, scope: Scope, *, now: datetime) -> Reading | None:
        if not scope.entity.startswith("sim-"):
            return None
        if scope.entity.startswith("sim-withdrawn-"):
            status = CertStatus.WITHDRAWN
        else:
            status = CertStatus.ACTIVE
        start = date(now.year, now.month, 1)
        entry = f"{scope.scheme}-{scope.country}-{scope.service_code}-{scope.entity}"
        body = {
            "register": "simulator",
            "entry": entry,
            "entity": scope.entity,
            "scheme": scope.scheme,
            "country": scope.country,
            "service": scope.service_code,
            "status": status.value,
            "valid_from": start.isoformat(),
            "valid_until": (start + timedelta(days=365)).isoformat(),
            "read_at": now.isoformat(),
        }
        return Reading(
            scope=scope,
            scheme_version="sim-1",
            certificate_ref=f"SIM-{entry}",
            component_only=scope.entity.startswith("sim-component-"),
            status=status,
            valid_from=start,
            valid_until=start + timedelta(days=365),
            register_url=f"{SIMULATOR_BASE}{entry}",
            register_entry_id=entry,
            cab="simulated-cab",
            fetched_at=now,
            body=json.dumps(body, sort_keys=True).encode(),
        )


# Real connectors register here by source id once written against a register's documented interface. None is shipped: guessing a register's
# format would produce attestations nobody can stand behind.
CONNECTORS: dict[str, Connector] = {SIMULATOR: SimulatorConnector()}


@lru_cache(maxsize=1)
def configured_sources() -> dict[str, Source]:
    path = CONFIG / FILE
    doc: Mapping[str, Any] = (
        yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    )
    return parse(doc)


def sources() -> dict[str, Source]:
    """Every source, configured ones plus the built-in simulator (which is usable only in sandbox environments)."""
    return {SIMULATOR: simulator_source(), **configured_sources()}


def connector(source_id: str) -> Connector | None:
    return CONNECTORS.get(source_id)
