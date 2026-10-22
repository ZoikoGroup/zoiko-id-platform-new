"""Attribute, group and role mapping releases (ZID-OVERLAY-001 §11, §20-21; OVL-10, OVL-11; G-W3).

Pure rules, no I/O. A mapping release is an immutable, versioned set of typed rules:

    {"source": "claims.department" | "scim.<attribute path>" | "groups",
     "target": "profile.<name>" | "relation.member_of" | "role_candidate",
     "transform": [{"op": "trim"}, {"op": "lower"}, {"op": "map", "table": {...}}, ...],
     "missing": "IGNORE" | "CLEAR" | "ERROR",
     "multi": "FIRST" | "ALL" | "ERROR"}

- Transforms are a fixed deterministic set - never arbitrary scripting (§11).
- Targets are profile attributes, relationship facts and role *candidates* only: a group or claim can
  never become an AuthorityGrant through a mapping (OVL-10, §35 "No IdP group mapped directly to
  business signing authority").
- Two rules producing different values for one single-valued target is a MAPPING_CONFLICT: the
  record is quarantined, never silently overwritten (OVL-11).
- Every release is simulated against sample/shadow records before it can become ACTIVE; an earlier
  immutable release is the only rollback (no in-place hot edits, §20).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from zid_platform.modules.integration.domain.model import IntegrationError

SOURCE = re.compile(r"^(claims\.[A-Za-z0-9_:.-]{1,128}|scim\.[A-Za-z0-9_:.-]{1,200}|groups)$")
TARGET = re.compile(r"^(profile\.[a-z][a-z0-9_]{0,63}|relation\.member_of|role_candidate)$")
MULTI_TARGETS = frozenset({"relation.member_of", "role_candidate"})
OPS = frozenset({"trim", "lower", "upper", "map", "prefix_strip", "split", "allow"})
MAX_RULES = 200
MAX_VALUES = 100


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class Missing(StrEnum):
    IGNORE = "IGNORE"
    CLEAR = "CLEAR"
    ERROR = "ERROR"


class Multi(StrEnum):
    FIRST = "FIRST"
    ALL = "ALL"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Rule:
    source: str
    target: str
    transform: tuple[Mapping[str, Any], ...] = ()
    missing: Missing = Missing.IGNORE
    multi: Multi = Multi.FIRST


def parse_rules(raw: Sequence[Mapping[str, Any]]) -> tuple[Rule, ...]:
    if not raw or len(raw) > MAX_RULES:
        raise IntegrationError("VALIDATION_FAILED", f"A release has 1-{MAX_RULES} rules.")
    rules = []
    for i, r in enumerate(raw):
        source, target = str(r.get("source", "")), str(r.get("target", ""))
        if not SOURCE.fullmatch(source):
            raise IntegrationError("VALIDATION_FAILED", f"rule {i}: unsupported source {source!r}")
        if not TARGET.fullmatch(target):
            # authority is never a mapping target (OVL-10)
            raise IntegrationError("MAPPING_TARGET_FORBIDDEN", f"rule {i}: target {target!r}")
        steps = tuple(r.get("transform") or ())
        for s in steps:
            if not isinstance(s, Mapping) or s.get("op") not in OPS:
                raise IntegrationError("VALIDATION_FAILED", f"rule {i}: unknown transform {s!r}")
            if s["op"] == "map" and not isinstance(s.get("table"), Mapping):
                raise IntegrationError("VALIDATION_FAILED", f"rule {i}: map needs a table")
            if s["op"] == "allow" and not isinstance(s.get("values"), list):
                raise IntegrationError("VALIDATION_FAILED", f"rule {i}: allow needs values")
        try:
            missing, multi = Missing(r.get("missing", "IGNORE")), Multi(r.get("multi", "FIRST"))
        except ValueError as e:
            raise IntegrationError("VALIDATION_FAILED", f"rule {i}: {e}") from e
        if target not in MULTI_TARGETS and multi is Multi.ALL:
            raise IntegrationError("VALIDATION_FAILED", f"rule {i}: {target} is single-valued")
        rules.append(Rule(source, target, steps, missing, multi))
    return tuple(rules)


def content_hash(rules: Sequence[Rule]) -> str:
    doc = [
        {
            "s": r.source,
            "t": r.target,
            "x": list(r.transform),
            "m": r.missing.value,
            "u": r.multi.value,
        }
        for r in rules
    ]
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _read(record: Mapping[str, Any], source: str) -> list[str] | None:
    if source == "groups":
        value = record.get("groups")
    else:
        kind, _, path = source.partition(".")
        value = (record.get(kind) or {}).get(path)
    if value is None or value == "" or value == []:
        return None
    values = value if isinstance(value, list) else [value]
    return [str(v) for v in values if isinstance(v, str | int | float | bool)][:MAX_VALUES]


def _step(values: list[str], s: Mapping[str, Any]) -> list[str]:
    op = s["op"]
    if op == "trim":
        return [v.strip() for v in values]
    if op == "lower":
        return [v.lower() for v in values]
    if op == "upper":
        return [v.upper() for v in values]
    if op == "map":
        table = s["table"]
        out = [str(table[v]) for v in values if v in table]
        return out if s.get("drop_unmapped", True) else out + [v for v in values if v not in table]
    if op == "prefix_strip":
        p = str(s.get("prefix", ""))
        return [v.removeprefix(p) for v in values]
    if op == "split":
        sep = str(s.get("sep", ","))[:4] or ","
        return [part for v in values for part in v.split(sep) if part]
    if op == "allow":
        allowed = {str(x) for x in s["values"]}
        return [v for v in values if v in allowed]
    raise IntegrationError("VALIDATION_FAILED", f"unknown transform {op}")


@dataclass
class MappingResult:
    facts: dict[str, Any] = field(default_factory=dict)  # target -> value | sorted list
    cleared: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unknown_sources: list[str] = field(default_factory=list)

    @property
    def quarantined(self) -> bool:
        return bool(self.errors or self.conflicts)


def apply(rules: Sequence[Rule], record: Mapping[str, Any]) -> MappingResult:
    """Deterministic: the same rules and record always give the same facts (simulation == runtime)."""
    out = MappingResult()
    consumed: set[str] = set()
    for r in rules:
        consumed.add(r.source)
        values = _read(record, r.source)
        if values is None:
            if r.missing is Missing.ERROR:
                out.errors.append(f"{r.source}: required value missing")
            elif r.missing is Missing.CLEAR:
                out.cleared.add(r.target)
            continue
        for s in r.transform:
            values = _step(values, s)
        values = sorted(dict.fromkeys(values))
        if not values:
            continue
        if r.target in MULTI_TARGETS:
            current = set(out.facts.get(r.target, []))
            out.facts[r.target] = sorted(current | set(values))
            continue
        if len(values) > 1:
            if r.multi is Multi.ERROR:
                out.errors.append(f"{r.source}: {len(values)} values for single-valued {r.target}")
                continue
            values = values[:1]
        if r.target in out.facts and out.facts[r.target] != values[0]:
            out.conflicts.append(f"{r.target}: {r.source} disagrees with an earlier rule")
            continue
        out.facts[r.target] = values[0]
    present = {f"claims.{k}" for k in (record.get("claims") or {})}
    present |= {f"scim.{k}" for k in (record.get("scim") or {})}
    if record.get("groups"):
        present.add("groups")
    out.unknown_sources = sorted(present - consumed)
    return out


def simulate(rules: Sequence[Rule], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The report a release must carry before activation (§11 "Simulation")."""
    if not records:
        raise IntegrationError("VALIDATION_FAILED", "Simulate against at least one record.")
    results = [apply(rules, r) for r in records]
    unknown = sorted({u for x in results for u in x.unknown_sources})
    return {
        "records": len(records),
        "mapped": sum(1 for x in results if not x.quarantined),
        "quarantined": sum(1 for x in results if x.quarantined),
        "conflicts": sum(len(x.conflicts) for x in results),
        "errors": sum(len(x.errors) for x in results),
        "unknown_sources": unknown,  # drift input (§24 "Unknown claims/groups")
        "samples": [
            {
                "record": str(rec.get("id", i)),
                "facts": res.facts,
                "conflicts": res.conflicts,
                "errors": res.errors,
            }
            for i, (rec, res) in enumerate(zip(records, results, strict=True))
        ][:20],
    }


def check_activation(report: Mapping[str, Any] | None) -> None:
    if not report:
        raise IntegrationError("MAPPING_SIMULATION_REQUIRED", "Simulate the release first.")
    if report.get("conflicts") or report.get("errors"):
        raise IntegrationError(
            "MAPPING_CONFLICT",
            f"{report.get('conflicts')} conflicts and {report.get('errors')} errors in simulation.",
        )
