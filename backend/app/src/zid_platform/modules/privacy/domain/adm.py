"""Automated-decision guards, driven by counsel's rules per jurisdiction (ZID-PRI-004 sections 10, 16 and 22; AT-032, AT-033). Pure: no I/O.

The engineering rule, and the only legal claim made here: this module writes NO legal rule. What a UK or EU safeguard set must contain is
counsel's to state (`config/privacy/adm-rules.yaml`, each jurisdiction `COUNSEL` until set, with an approval reference and effective date).
What this module guarantees is how the platform behaves around whatever counsel states:

- no rules from counsel for the pack: a significant, solely automated decision is BLOCKED and must go to a person (fail closed); the platform
  never assumes a safeguard set that nobody approved;
- rules present but the approval reference or the effective date missing, or not yet in force: the same;
- rules in force: every safeguard counsel lists must be offered by the caller, and a special-category decision needs the recorded condition
  counsel names; anything missing is reported by name and blocks the decision;
- a decision that is not significant, or is not solely automated, claims nothing from this module and is not blocked by it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

COUNSEL = "COUNSEL"


@dataclass(frozen=True, slots=True)
class AdmDecision:
    allowed: bool
    outcome: str  # NOT_SIGNIFICANT | NOT_SOLELY_AUTOMATED | PERMITTED_WITH_SAFEGUARDS | BLOCKED
    reasons: tuple[str, ...]
    missing_safeguards: tuple[str, ...] = ()
    route_to_person: bool = False
    rules_ref: str | None = None


def rules_in_force(rules: Mapping[str, Any] | None, on: date) -> tuple[bool, str | None]:
    """(in force, reason when not). A rule set counts only with counsel's approval reference and an effective date that has arrived."""
    if not isinstance(rules, Mapping) or rules.get("required_safeguards") in (None, COUNSEL):
        return False, "ADM_RULES_NOT_SET_BY_COUNSEL"
    if not rules.get("approval_ref") or rules.get("approval_ref") == COUNSEL:
        return False, "ADM_RULES_NOT_APPROVED"
    effective = rules.get("effective_from")
    if effective in (None, COUNSEL):
        return False, "ADM_RULES_HAVE_NO_EFFECTIVE_DATE"
    eff = effective if isinstance(effective, date) else date.fromisoformat(str(effective))
    if on < eff:
        return False, "ADM_RULES_NOT_YET_IN_FORCE"
    ended = rules.get("effective_until")
    if ended not in (None, COUNSEL):
        end = ended if isinstance(ended, date) else date.fromisoformat(str(ended))
        if on > end:
            return False, "ADM_RULES_NO_LONGER_IN_FORCE"
    return True, None


def evaluate(
    rules: Mapping[str, Any] | None,
    *,
    pack_id: str,
    significant: bool,
    solely_automated: bool,
    special_category: bool,
    safeguards_offered: Collection[str],
    special_condition_ref: str | None,
    on: date,
) -> AdmDecision:
    if not significant:
        return AdmDecision(True, "NOT_SIGNIFICANT", ("NO_RESTRICTION_CLAIMED_BY_THIS_CHECK",))
    if not solely_automated:
        return AdmDecision(True, "NOT_SOLELY_AUTOMATED", ("A_PERSON_TAKES_THE_DECISION",))
    ok, why = rules_in_force(rules, on)
    if not ok:
        return AdmDecision(
            False, "BLOCKED", (why or "ADM_RULES_NOT_SET_BY_COUNSEL",), route_to_person=True
        )
    assert rules is not None
    required = {str(s) for s in rules["required_safeguards"]}
    missing = tuple(sorted(required - {str(s) for s in safeguards_offered}))
    reasons: list[str] = []
    if missing:
        reasons.append("REQUIRED_SAFEGUARDS_NOT_OFFERED")
    if special_category:
        needs = rules.get("special_category_condition_required", True)
        if needs not in (False, None) and not (special_condition_ref or "").strip():
            reasons.append("SPECIAL_CATEGORY_CONDITION_NOT_RECORDED")
    ref = f"{pack_id}@{rules['approval_ref']}"
    if reasons:
        return AdmDecision(
            False, "BLOCKED", tuple(reasons), missing, route_to_person=True, rules_ref=ref
        )
    return AdmDecision(
        True, "PERMITTED_WITH_SAFEGUARDS", ("COUNSEL_RULES_SATISFIED",), (), rules_ref=ref
    )
