"""What an organization's readiness state refuses (`backend/config/onboarding/readiness-policy.yaml`; slice L-13, CR-053).

Checked against the approval registry when loaded: a rule that names an action the registry does not have, a state nobody defined, or a mode
nobody knows stops the process from starting, so a typo can never quietly switch a protection off.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from zid_platform.modules.tenant.infrastructure import approvals as approvals_store
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml

MODES = frozenset({"OFF", "REPORT_ONLY", "ENFORCED"})
STATES = frozenset(
    {"DRAFT", "LIMITED_SETUP", "READY_FOR_ACTIVATION", "ACTIVE", "ACTIVE_LIMITED", "RESTRICTED"}
)


class ReadinessPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rule:
    refuses: frozenset[str]
    all_except: bool  # True: `refuses` is what stays allowed, and everything else is refused


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    version: str
    enforcement: str
    rules: dict[str, Rule]

    def refuses(self, state: str, action_id: str) -> bool:
        rule = self.rules.get(state)
        if rule is None:
            return False
        return (action_id not in rule.refuses) if rule.all_except else (action_id in rule.refuses)


def parse(doc: dict[str, Any], registry: Any) -> ReadinessPolicy:
    mode = str(doc.get("enforcement", "REPORT_ONLY")).upper()
    if mode not in MODES:
        raise ReadinessPolicyError(f"enforcement {mode!r} is not one of {sorted(MODES)}")
    rules: dict[str, Rule] = {}
    for state, body in (doc.get("states") or {}).items():
        if state not in STATES:
            raise ReadinessPolicyError(f"{state}: not a readiness state")
        listed, all_except = body.get("refuses"), False
        if body.get("refuses_all_except") is not None:
            if listed is not None:
                raise ReadinessPolicyError(f"{state}: refuses and refuses_all_except together")
            listed, all_except = body["refuses_all_except"], True
        actions = frozenset(str(a) for a in (listed or ()))
        unknown = sorted(a for a in actions if a not in registry.actions)
        if unknown:
            raise ReadinessPolicyError(
                f"{state}: not actions in the approval registry: {', '.join(unknown)}"
            )
        rules[state] = Rule(actions, all_except)
    return ReadinessPolicy(str(doc.get("version", "")), mode, rules)


@lru_cache(maxsize=1)
def readiness_policy() -> ReadinessPolicy:
    return parse(_yaml(CONFIG / "onboarding" / "readiness-policy.yaml"), approvals_store.registry())
