"""Biometric collection requirements, driven by counsel's rules per jurisdiction pack (ZID-PRI-004 sections 06, 12 and 16; AT-034, AT-035, AT-037). Pure.

Like the automated-decision guard, this writes NO legal rule. Whether a jurisdiction wants written notice, a separate release or consent, a
published retention schedule or a necessity assessment before sensitive collection is counsel's statement, with an approval reference and an effective
date (`config/privacy/collection-rules.yaml`, every field `COUNSEL` until set). The behaviour guaranteed around it:

- a pack with an entry here and no approved, in-force rules: sensitive collection is DENIED with BIOMETRIC_COLLECTION_RULES_NOT_SET_BY_COUNSEL;
- rules in force: each requirement counsel switches on must be evidenced by name, and every missing one is a reason that blocks collection;
- a pack with no entry here is not touched by this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from zid_platform.modules.privacy.domain.adm import COUNSEL

# requirement switch counsel sets -> (the evidence the caller must show, the reason when it is missing)
REQUIREMENTS: dict[str, tuple[str, str]] = {
    "written_notice_required": ("notice_version", "NOTICE_REQUIRED"),
    "release_required": ("release_ref", "RELEASE_REQUIRED"),
    "retention_schedule_required": ("retention_policy_ref", "RETENTION_SCHEDULE_NOT_ASSERTED"),
    "necessity_assessment_required": ("necessity_ref", "NECESSITY_ASSESSMENT_REQUIRED"),
}


@dataclass(frozen=True, slots=True)
class CollectionDecision:
    applies: bool
    allowed: bool
    reasons: tuple[str, ...]
    rules_ref: str | None = None


def _in_force(rules: Mapping[str, Any], on: date) -> str | None:
    if not rules.get("approval_ref") or rules.get("approval_ref") == COUNSEL:
        return "BIOMETRIC_COLLECTION_RULES_NOT_SET_BY_COUNSEL"
    eff = rules.get("effective_from")
    if eff in (None, COUNSEL):
        return "BIOMETRIC_COLLECTION_RULES_HAVE_NO_EFFECTIVE_DATE"
    when = eff if isinstance(eff, date) else date.fromisoformat(str(eff))
    if on < when:
        return "BIOMETRIC_COLLECTION_RULES_NOT_YET_IN_FORCE"
    return None


def evaluate(
    all_rules: Mapping[str, Any] | None,
    *,
    pack_id: str | None,
    evidence: Mapping[str, str | None],
    on: date,
) -> CollectionDecision:
    rules = (all_rules or {}).get(pack_id) if pack_id else None
    if not isinstance(rules, Mapping):
        return CollectionDecision(False, True, ("NO_COLLECTION_RULES_FOR_THIS_PACK",))
    why = _in_force(rules, on)
    if why:
        return CollectionDecision(True, False, (why,))
    reasons = []
    for switch, (field, reason) in REQUIREMENTS.items():
        on_flag = rules.get(switch)
        if on_flag not in (None, False, COUNSEL) and not (evidence.get(field) or "").strip():
            reasons.append(reason)
    ref = f"{pack_id}@{rules['approval_ref']}"
    if reasons:
        return CollectionDecision(True, False, tuple(reasons), ref)
    return CollectionDecision(True, True, ("COUNSEL_COLLECTION_RULES_SATISFIED",), ref)
