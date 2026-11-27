"""What a draft policy would change, as a finding that can be signed and kept (ZID-CP-WF08 v2.0 E12 counterfactual; CR-170). Pure domain, no I/O.

A reviewer wants to know what changes before a draft goes live. The comparison runs each scenario under the draft and under the ACTIVE release and
reports who would newly get access, who would lose it, and what stays the same. It is ANALYSIS: it is never written onto an existing decision receipt
and it says so in its own body, so a later reader cannot mistake "what would have happened" for "what happened".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "zoiko.policy-what-if.v1"
MAX_SCENARIOS = 200
KINDS = ("UNCHANGED", "NEWLY_ALLOWED", "NEWLY_DENIED", "NOT_COMPARABLE")


def classify(candidate: Mapping[str, Any] | None, active: Mapping[str, Any] | None) -> str:
    """No active release to compare with, or a side that could not be evaluated, is NOT_COMPARABLE: never guessed as unchanged."""
    if candidate is None or active is None:
        return "NOT_COMPARABLE"
    before, after = bool(active.get("allowed")), bool(candidate.get("allowed"))
    if before == after:
        return "UNCHANGED"
    return "NEWLY_ALLOWED" if after else "NEWLY_DENIED"


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    kinds = [classify(r.get("candidate"), r.get("active")) for r in results]
    counts = {k: kinds.count(k) for k in KINDS}
    return {
        "scenarios": len(results),
        "counts": counts,
        "changes": [
            {
                "index": i,
                "kind": k,
                "scenario": results[i]["scenario"],
                "candidate": results[i]["candidate"],
                "active": results[i]["active"],
            }
            for i, k in enumerate(kinds)
            if k != "UNCHANGED"
        ],
        "widens_access": counts["NEWLY_ALLOWED"] > 0,
        "narrows_access": counts["NEWLY_DENIED"] > 0,
    }


def analysis_claims(
    *, comparison: Mapping[str, Any], summary: Mapping[str, Any], requested_by: str, now_iso: str
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "analysis": True,
        "affects_receipts": False,
        "statement": "A what-if comparison of a draft against the active release for the scenarios listed. It describes what would change, not what happened, and it never alters an existing decision receipt. Scenarios outside this list were not examined.",
        "tenant_id": comparison["tenant_id"],
        "candidate": comparison["candidate"],
        "active": comparison["active"],
        "summary": dict(summary),
        "requested_by": requested_by,
        "created_at": now_iso,
    }
