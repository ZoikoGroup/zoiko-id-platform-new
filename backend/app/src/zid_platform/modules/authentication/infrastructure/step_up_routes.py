"""Load the route to step-up action map (`backend/config/step-up/route-actions.yaml`; ZID-ENG-DEC-009, slice L-9).

Pure data plus a matcher, validated on load against the step-up catalogue: a rule naming an action the catalogue does not know, a rule listed twice, or an
empty map does not start, so the map cannot quietly guard less than it appears to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from zid_platform.modules.authentication.domain.step_up import Catalogue, StepUpError
from zid_platform.modules.authentication.infrastructure.step_up_config import (
    CONFIG,
    step_up_catalogue,
)

_PARAM = re.compile(r"\{[^}]*\}")


@dataclass(frozen=True, slots=True)
class RouteAction:
    match: str
    pattern: re.Pattern[str]
    action: str
    critical: bool = False
    machine_exempt: bool = (
        False  # CR-066: a signed-key service caller needs no human proof for this rule
    )


def normalise(path_format: str) -> str:
    return _PARAM.sub("{}", path_format)


def _pattern(match: str) -> re.Pattern[str]:
    method, _, path = match.partition(" ")
    if not method or not path.startswith("/"):
        raise StepUpError("ROUTE_MAP_INVALID", f"{match!r}: a rule is 'METHOD /path'")
    m = "[A-Z]+" if method == "*" else re.escape(method)
    return re.compile("^" + m + " " + ".*".join(re.escape(p) for p in path.split("*")) + "$")


def parse(doc: dict[str, Any], catalogue: Catalogue) -> tuple[RouteAction, ...]:
    out: list[RouteAction] = []
    seen: set[str] = set()
    for raw in doc.get("rules") or []:
        match, action = str(raw.get("match", "")), str(raw.get("action", ""))
        if match in seen:
            raise StepUpError(
                "ROUTE_MAP_INVALID", f"{match}: listed twice, and the second could never match"
            )
        seen.add(match)
        if action not in catalogue.actions:
            raise StepUpError(
                "ROUTE_MAP_INVALID", f"{match}: {action} is not an action of the step-up catalogue"
            )
        machine = raw.get("machine")
        if machine not in (None, "exempt"):
            raise StepUpError(
                "ROUTE_MAP_INVALID", f"{match}: machine must be 'exempt' or absent, not {machine!r}"
            )
        out.append(
            RouteAction(
                match,
                _pattern(match),
                action,
                bool(raw.get("critical", False)),
                machine == "exempt",
            )
        )
    if not out:
        raise StepUpError("ROUTE_MAP_INVALID", "an empty route map guards nothing")
    return tuple(out)


class RouteActions:
    def __init__(self, rules: tuple[RouteAction, ...]) -> None:
        self.rules = rules

    def classify(self, method: str, path_format: str) -> RouteAction | None:
        key = f"{method.upper()} {normalise(path_format)}"
        for rule in self.rules:
            if rule.pattern.match(key):
                return rule
        return None


@lru_cache(maxsize=1)
def route_actions() -> RouteActions:
    doc = yaml.safe_load((CONFIG / "step-up" / "route-actions.yaml").read_text(encoding="utf-8"))
    return RouteActions(parse(doc, step_up_catalogue()))
