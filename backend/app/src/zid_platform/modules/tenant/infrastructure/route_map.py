"""The route map (`backend/config/roles/route-map.yaml`): which role-matrix action each tenant-facing route needs (ZID-ENG-DEC-007, slice L-7).

Pure data plus a matcher. A rule is matched against "METHOD /path" where every path parameter is written `{}`; `*` matches any characters.
The first rule that matches decides. A rule either gates (a matrix action and a mode) or exempts (a kind and a reason). A rule that names
an action the matrix does not have, a mode that is not X, S, A or R, or an exemption without a kind and a reason is a start-up error, so
the map cannot quietly say less than it appears to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from zid_platform.modules.tenant.domain.role_matrix import Matrix, Mode
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml
from zid_platform.modules.tenant.infrastructure.role_matrix import role_matrix

EXEMPT_KINDS = frozenset(
    {
        "OWN_ACCOUNT",
        "OPERATOR_PLANE",
        "PROTOCOL",
        "APPROVAL_ENGINE",
        "COMMERCIAL",
        "INLINE_EVALUATION",
        "NO_MATRIX_ACTION",
    }
)
GATE_MODES = {"X": Mode.EXECUTE, "S": Mode.PREPARE, "A": Mode.APPROVE, "R": Mode.READ}
_PARAM = re.compile(r"\{[^}]*\}")


class RouteMapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rule:
    match: str
    pattern: re.Pattern[str]
    action: str | None = None
    mode: Mode | None = None
    exempt: str | None = None
    why: str = ""

    @property
    def gates(self) -> bool:
        return self.action is not None


def normalise(path_format: str) -> str:
    return _PARAM.sub("{}", path_format)


def _pattern(match: str) -> re.Pattern[str]:
    method, _, path = match.partition(" ")
    if not method or not path.startswith("/"):
        raise RouteMapError(f"{match!r}: a rule is 'METHOD /path'")
    m = "[A-Z]+" if method == "*" else re.escape(method)
    return re.compile("^" + m + " " + ".*".join(re.escape(p) for p in path.split("*")) + "$")


def parse(doc: dict[str, Any], matrix: Matrix) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for raw in doc.get("rules") or []:
        match = str(raw.get("match", ""))
        if match in seen:
            raise RouteMapError(f"{match}: listed twice, and the second could never match")
        seen.add(match)
        gate, exempt = raw.get("gate"), raw.get("exempt")
        if bool(gate) == bool(exempt):
            raise RouteMapError(f"{match}: a rule gates or exempts, not both and not neither")
        if gate:
            action, mode_key = str(gate.get("action", "")), str(gate.get("mode", ""))
            row = matrix.row(action)
            if row is None:
                raise RouteMapError(f"{match}: {action} is not an action of the role matrix")
            if mode_key not in GATE_MODES:
                raise RouteMapError(f"{match}: mode {mode_key!r} is not one of X, S, A, R")
            mode = GATE_MODES[mode_key]
            if all(c is Mode.DENIED for c in row.cells.values()):
                pass  # a reserved row: the gate refuses everyone, which is what the record says
            elif mode not in row.cells.values():
                raise RouteMapError(
                    f"{match}: no role holds {action} at {mode_key}, so it could never allow"
                )
            rules.append(Rule(match, _pattern(match), action=action, mode=mode))
        else:
            if str(exempt) not in EXEMPT_KINDS:
                raise RouteMapError(
                    f"{match}: exemption kind {exempt!r} is not one of {sorted(EXEMPT_KINDS)}"
                )
            if not str(raw.get("why", "")).strip():
                raise RouteMapError(f"{match}: an exemption says why")
            rules.append(Rule(match, _pattern(match), exempt=str(exempt), why=str(raw["why"])))
    if not rules:
        raise RouteMapError("an empty route map maps nothing")
    return tuple(rules)


class RouteMap:
    def __init__(self, rules: tuple[Rule, ...]) -> None:
        self.rules = rules

    def classify(self, method: str, path_format: str) -> Rule | None:
        key = f"{method.upper()} {normalise(path_format)}"
        for rule in self.rules:
            if rule.pattern.match(key):
                return rule
        return None


@lru_cache(maxsize=1)
def route_map() -> RouteMap:
    return RouteMap(parse(_yaml(CONFIG / "roles" / "route-map.yaml"), role_matrix()))


@lru_cache(maxsize=1)
def route_matrix_enforced() -> bool:
    """Owner-ruled: do mapped routes refuse a role the matrix does not allow? Only an explicit ENFORCED enforces. The default is
    REPORT_ONLY (the decision and its evidence are recorded, the request goes through) until the owners have read what would be refused."""
    doc = _yaml(CONFIG / "roles" / "role-policy.yaml")
    return str(doc.get("route_matrix_enforcement", "REPORT_ONLY")).upper() == "ENFORCED"


__all__ = [
    "EXEMPT_KINDS",
    "RouteMap",
    "RouteMapError",
    "Rule",
    "normalise",
    "parse",
    "route_map",
    "route_matrix_enforced",
]
