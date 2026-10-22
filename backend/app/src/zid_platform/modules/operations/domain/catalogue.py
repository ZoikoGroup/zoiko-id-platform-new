"""The production service catalogue and the alert rules derived from it (ZID-SRE-001 §1, §4-6, §22;
OPS-01/03/04; SRE-03/04). Pure: parsing, validation and rule rendering, no I/O.

One definition, three consumers: the request middleware measures each service's journeys
(`journeys()`), the `/ops/services/{id}/slos` view evaluates the in-process windows, and the
Prometheus rule files in deploy/observability are rendered from the same SLOs
(`application/rules.py`),
so a page in production and the console's burn-rate view use identical windows and thresholds.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TIERS = ("T0", "T1", "T2", "T3")
SLIS = ("availability", "latency")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_RUNBOOK = re.compile(r"^RB-\d{2}$")
_OWNER = re.compile(r"^@zoiko/[a-z0-9-]+$")


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JourneyDef:
    prefix: str
    latency_s: float


@dataclass(frozen=True, slots=True)
class Slo:
    slo_id: str
    sli: str
    target: float


@dataclass(frozen=True, slots=True)
class Service:
    service_id: str
    name: str
    tier: str
    owner: str
    oncall: str
    dashboard: str
    journeys: tuple[JourneyDef, ...]
    slos: tuple[Slo, ...]
    runbooks: tuple[str, ...]

    def view(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "tier": self.tier,
            "owner": self.owner,
            "oncall": self.oncall,
            "dashboard": self.dashboard,
            "journeys": [{"prefix": j.prefix, "latency_s": j.latency_s} for j in self.journeys],
            "slos": [{"slo_id": s.slo_id, "sli": s.sli, "target": s.target} for s in self.slos],
            "runbooks": list(self.runbooks),
        }


@dataclass(frozen=True, slots=True)
class Invariant:
    invariant_id: str
    expr: str
    severity_hypothesis: str
    alert: str  # PAGE | TICKET
    owner: str
    runbook: str
    dashboard: str
    summary: str


@dataclass(frozen=True, slots=True)
class Catalogue:
    version: str
    services: Mapping[str, Service]
    invariants: tuple[Invariant, ...]

    def service(self, service_id: str) -> Service | None:
        return self.services.get(service_id)

    def runbooks(self) -> set[str]:
        return {r for s in self.services.values() for r in s.runbooks} | {
            i.runbook for i in self.invariants
        }

    def journeys(self) -> list[tuple[str, str, float]]:
        """(journey name = service id, path prefix, latency threshold) for the middleware."""
        return [
            (s.service_id, j.prefix, j.latency_s)
            for s in self.services.values()
            for j in s.journeys
        ]


def _alert_class(value: str, where: str) -> str:
    if value not in ("PAGE", "TICKET"):
        raise CatalogueError(f"{where}: class must be PAGE or TICKET")
    return value


def _req(d: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise CatalogueError(f"{where}: {key} is required")
    return d[key]


def parse(doc: Mapping[str, Any]) -> Catalogue:
    """Validate the whole document; any violation refuses the catalogue (ownerless or
    runbook-less paging coverage is a defect, SRE-04)."""
    services: dict[str, Service] = {}
    prefixes: dict[str, str] = {}
    slo_ids: set[str] = set()
    for sid, raw in (doc.get("services") or {}).items():
        where = f"services.{sid}"
        if not _ID.match(sid):
            raise CatalogueError(f"{where}: invalid service id")
        tier = _req(raw, "tier", where)
        if tier not in TIERS:
            raise CatalogueError(f"{where}: tier must be one of {TIERS}")
        owner = _req(raw, "owner", where)
        if not _OWNER.match(owner):
            raise CatalogueError(f"{where}: owner must be a @zoiko/<team> handle")
        journeys = []
        for j in _req(raw, "journeys", where):
            prefix, latency = str(_req(j, "prefix", where)), float(_req(j, "latency_s", where))
            if not prefix.startswith("/") or latency <= 0:
                raise CatalogueError(f"{where}: journey {prefix!r} invalid")
            if prefix in prefixes:
                raise CatalogueError(
                    f"{where}: prefix {prefix} already measured by {prefixes[prefix]}"
                )
            prefixes[prefix] = sid
            journeys.append(JourneyDef(prefix, latency))
        slos = []
        for s in _req(raw, "slos", where):
            slo = Slo(
                str(_req(s, "id", where)),
                str(_req(s, "sli", where)),
                float(_req(s, "target", where)),
            )
            if slo.sli not in SLIS or not 0 < slo.target < 1 or slo.slo_id in slo_ids:
                raise CatalogueError(f"{where}: SLO {slo.slo_id} invalid or duplicated")
            slo_ids.add(slo.slo_id)
            slos.append(slo)
        runbooks = tuple(_req(raw, "runbooks", where))
        if any(not _RUNBOOK.match(r) for r in runbooks):
            raise CatalogueError(f"{where}: runbook ids are RB-NN")
        svc = Service(
            sid,
            str(_req(raw, "name", where)),
            tier,
            owner,
            str(_req(raw, "oncall", where)),
            str(_req(raw, "dashboard", where)),
            tuple(journeys),
            tuple(slos),
            runbooks,
        )
        if tier in ("T0", "T1") and {s.sli for s in svc.slos} != set(SLIS):
            raise CatalogueError(f"{where}: a {tier} service needs availability and latency SLOs")
        services[sid] = svc
    invariants = []
    for raw in doc.get("invariants") or ():
        iid = str(_req(raw, "id", "invariants"))
        where = f"invariants.{iid}"
        owner, runbook = _req(raw, "owner", where), _req(raw, "runbook", where)
        if not _OWNER.match(owner) or not _RUNBOOK.match(runbook):
            raise CatalogueError(f"{where}: owner and runbook are required")
        invariants.append(
            Invariant(
                iid,
                str(_req(raw, "expr", where)),
                str(_req(raw, "severity_hypothesis", where)),
                _alert_class(raw.get("class", "PAGE"), where),
                owner,
                runbook,
                str(_req(raw, "dashboard", where)),
                str(_req(raw, "summary", where)),
            )
        )
    if not services:
        raise CatalogueError("the catalogue lists no services")
    return Catalogue(str(doc.get("version", "0")), services, tuple(invariants))
