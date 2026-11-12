"""The /v2 privacy control-plane rules (ZID-PRI-004). Pure domain, no I/O.

- Risk scoring (section 18): L x I, 1-5 low, 6-11 moderate, 12-17 high, 18-25 critical. A residual high or critical score blocks a release
  unless the package records a consultation reference. The scores are the controller's: nothing here invents one.
- Release gates (section 23): G0 to G7, each needing its accountable roles to sign with evidence, each by a different person, none by the
  package's author. The strict no-release criteria are blockers, never warnings.
- Transfers (sections 08, 16, 19): default deny. A processor that is not approved, not covering the data, not located in the destination, with
  a pending subprocessor change, a remote-access location that is not an approved location or no usable mechanism for a cross-border
  transfer is denied, and the reason says which.
- Erasure (sections 12, 13, 19): a job is COMPLETE only when every task is acknowledged. A held task keeps the job HELD, a failed one FAILED,
  and the proof never claims completion before that.
- Breach clocks (sections 09, 24): a regulator deadline comes from counsel's configuration. A jurisdiction counsel has not set is reported as
  NOT_SET_BY_COUNSEL, never filled in with a guess.
- Reviews (sections 10, 11): a reviewer is never the person who asked, a decision needs a rationale, and a retry carries no penalty.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------------------------------------------------------- risks

BANDS = ((18, "CRITICAL"), (12, "HIGH"), (6, "MODERATE"), (1, "LOW"))
RISK_ID = re.compile(r"^R[0-9]{2,3}$")


def score(likelihood: int, impact: int) -> int:
    if not (1 <= likelihood <= 5 and 1 <= impact <= 5):
        raise ValueError("likelihood and impact are 1 to 5")
    return likelihood * impact


def band(value: int) -> str:
    for floor, name in BANDS:
        if value >= floor:
            return name
    return "LOW"


def risk_problems(risk: Mapping[str, Any]) -> list[str]:
    """What is missing or wrong in one risk entry. An entry the controller has not fully assessed is not ready."""
    out: list[str] = []
    if not RISK_ID.fullmatch(str(risk.get("risk_id", ""))):
        out.append("RISK_ID_INVALID")
    for key in ("title", "mitigation", "owner"):
        if not str(risk.get(key, "")).strip():
            out.append(f"RISK_{key.upper()}_MISSING")
    for key in ("inherent_likelihood", "inherent_impact", "residual_likelihood", "residual_impact"):
        v = risk.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5:
            out.append(f"RISK_{key.upper()}_INVALID")
    if not out and score(risk["residual_likelihood"], risk["residual_impact"]) > score(
        risk["inherent_likelihood"], risk["inherent_impact"]
    ):
        out.append("RISK_RESIDUAL_ABOVE_INHERENT")
    return out


def risk_view(risk: Mapping[str, Any]) -> dict[str, Any]:
    inherent = score(risk["inherent_likelihood"], risk["inherent_impact"])
    residual = score(risk["residual_likelihood"], risk["residual_impact"])
    return {
        **risk,
        "inherent_score": inherent,
        "inherent_band": band(inherent),
        "residual_score": residual,
        "residual_band": band(residual),
    }


# ---------------------------------------------------------------------------------------------------------------------------- gates

GATES: Mapping[str, tuple[str, frozenset[str]]] = {
    "G0": ("Scope", frozenset({"PRODUCT", "DATA_OWNER"})),
    "G1": ("Legal privacy", frozenset({"LEGAL", "PRIVACY_DPO"})),
    "G2": ("DPIA", frozenset({"CONTROLLER", "PRIVACY_DPO"})),
    "G3": ("Biometric security", frozenset({"SECURITY_CISO"})),
    "G4": ("Quality and fairness", frozenset({"ML_LEAD", "INDEPENDENT_QA"})),
    "G5": ("Supplier", frozenset({"VENDOR_MANAGER", "LEGAL"})),
    "G6": ("Ecosystem", frozenset({"PRODUCT_OWNER"})),
    "G7": ("Launch", frozenset({"CTO", "RELEASE_MANAGER"})),
}


def gate_problem(
    gate: str, role: str, signer: str, author: str, evidence_uri: str, signed: Mapping[str, Any]
) -> str | None:
    """Why this sign-off must be refused, or None. Each role signs once, by a person who is not the package's author and not another
    signer of the same package."""
    if gate not in GATES:
        return "GATE_UNKNOWN"
    if role not in GATES[gate][1]:
        return "ROLE_NOT_ACCOUNTABLE_FOR_GATE"
    if signer == author:
        return "SIGNER_IS_AUTHOR"
    if not evidence_uri.strip():
        return "EVIDENCE_REQUIRED"
    if role in (signed.get(gate) or {}):
        return "ROLE_ALREADY_SIGNED"
    for g in signed.values():
        if any(s.get("signer") == signer for s in g.values()):
            return "SIGNER_ALREADY_SIGNED_ANOTHER_ROLE"
    return None


@dataclass(frozen=True, slots=True)
class Release:
    ready: bool
    blockers: tuple[str, ...]


def release_state(
    *,
    state: str,
    approved_until: datetime | None,
    risks: Sequence[Mapping[str, Any]],
    gates: Mapping[str, Any],
    consultation_ref: str | None,
    now: datetime,
) -> Release:
    """The strict no-release criteria. Every blocker is named; ready only when there are none."""
    blockers: list[str] = []
    if state != "APPROVED":
        blockers.append("DPIA_NOT_APPROVED")
    elif approved_until is None or approved_until <= now:
        blockers.append("DPIA_EXPIRED")
    if not risks:
        blockers.append("NO_RISK_ASSESSMENT")
    for r in risks:
        problems = risk_problems(r)
        if problems:
            blockers.append(f"{r.get('risk_id', 'RISK')}_INCOMPLETE")
            continue
        if (
            score(r["residual_likelihood"], r["residual_impact"]) >= 12
            and not (consultation_ref or "").strip()
        ):
            blockers.append(
                f"{r['risk_id']}_RESIDUAL_{band(score(r['residual_likelihood'], r['residual_impact']))}_UNCONSULTED"
            )
    for gate, (_, roles) in GATES.items():
        signed = set((gates.get(gate) or {}).keys())
        for missing in sorted(roles - signed):
            blockers.append(f"{gate}_{missing}_NOT_SIGNED")
    return Release(not blockers, tuple(blockers))


# ------------------------------------------------------------------------------------------------------------------------ transfers


@dataclass(frozen=True, slots=True)
class TransferFacts:
    processor_found: bool
    processor_status: str | None
    data_categories: frozenset[str]
    locations: frozenset[str]
    processor_mechanism: str | None
    pending_change: bool
    pack_mechanisms: tuple[str, ...]


def decide_transfer(
    facts: TransferFacts,
    *,
    data_category: str,
    origin_country: str,
    destination_country: str,
    remote_access_countries: Iterable[str] = (),
) -> tuple[bool, list[str], str | None]:
    """Default deny. Returns (allowed, reason codes, the mechanism relied on)."""
    reasons: list[str] = []
    if not facts.processor_found:
        return False, ["PROCESSOR_NOT_FOUND"], None
    if facts.processor_status != "APPROVED":
        reasons.append("PROCESSOR_NOT_APPROVED")
    if data_category not in facts.data_categories:
        reasons.append("DATA_CATEGORY_NOT_COVERED")
    if facts.pending_change:
        reasons.append(
            "SUBPROCESSOR_CHANGE_PENDING"
        )  # an unapproved change blocks egress until someone accepts it (AT-039)
    if destination_country not in facts.locations:
        reasons.append("DESTINATION_NOT_AN_APPROVED_LOCATION")
    for cc in sorted({c.upper() for c in remote_access_countries}):
        if cc not in facts.locations:
            reasons.append(
                f"REMOTE_ACCESS_FROM_{cc}_NOT_APPROVED"
            )  # support access from abroad is a transfer too (AT-038)
    mechanism = None
    crosses = {destination_country, *(c.upper() for c in remote_access_countries)} - {
        origin_country
    }
    if crosses:
        mechanism = facts.processor_mechanism
        if not mechanism:
            reasons.append("NO_TRANSFER_MECHANISM_RECORDED")
        elif facts.pack_mechanisms and mechanism not in facts.pack_mechanisms:
            reasons.append("MECHANISM_NOT_SUPPORTED_BY_THE_PACK")
    return not reasons, reasons or ["TRANSFER_PERMITTED_BY_RECORDED_FACTS"], mechanism


# ------------------------------------------------------------------------------------------------------------------------ erasure

TERMINAL_OK = "ACKNOWLEDGED"


def job_state(task_states: Sequence[str], *, held: bool) -> str:
    """COMPLETE only when every task is acknowledged. Anything else says what is outstanding."""
    if not task_states:
        return "PENDING"
    if held or "HELD" in task_states:
        return "HELD"
    if "FAILED" in task_states:
        return "FAILED"
    if all(s == TERMINAL_OK for s in task_states):
        return "COMPLETE"
    return "PARTIAL" if TERMINAL_OK in task_states else "PENDING"


def receipt_payload(task_id: str, job_id: str, deleted_at: str, object_count: int) -> bytes:
    """The exact bytes a provider signs: canonical JSON of the task, the job, when it deleted and how many objects."""
    return json.dumps(
        {
            "task_id": task_id,
            "job_id": job_id,
            "deleted_at": deleted_at,
            "object_count": object_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def proof(job: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A non-reversible proof. It states completion only when every task is acknowledged, and carries a commitment over the receipts, never
    the data."""
    states = [t["state"] for t in tasks]
    complete = job_state(states, held=job["state"] == "HELD") == "COMPLETE"
    commitment = hashlib.sha256(
        json.dumps(
            sorted((str(t["task_id"]), t.get("receipt_hash")) for t in tasks), separators=(",", ":")
        ).encode()
    ).hexdigest()
    return {
        "job_id": str(job["job_id"]),
        "complete": complete,
        "outstanding": [
            {"target_kind": t["target_kind"], "target_ref": t["target_ref"], "state": t["state"]}
            for t in tasks
            if t["state"] != TERMINAL_OK
        ],
        "receipts_commitment": commitment,
        "statement": "Every task is acknowledged by its target."
        if complete
        else "Not complete: the tasks listed are outstanding.",
    }


# ------------------------------------------------------------------------------------------------------------------------ breaches

COUNSEL = "COUNSEL"


def breach_clocks(
    config: Mapping[str, Any],
    jurisdictions: Sequence[str],
    detected_at: datetime,
    notified: Mapping[str, datetime],
    now: datetime,
) -> list[dict[str, Any]]:
    """One clock per affected jurisdiction. `config` maps a jurisdiction to hours to notify the authority, or COUNSEL when counsel has not set it."""
    out = []
    for j in sorted({x.upper() for x in jurisdictions}):
        hours = config.get(j, COUNSEL)
        item: dict[str, Any] = {
            "jurisdiction": j,
            "notified_at": notified[j].isoformat() if j in notified else None,
        }
        if not isinstance(hours, int) or isinstance(hours, bool) or hours <= 0:
            item |= {"state": "NOT_SET_BY_COUNSEL", "hours": None, "due_at": None}
        else:
            due = detected_at + timedelta(hours=hours)
            state = "NOTIFIED" if j in notified else ("OVERDUE" if now > due else "RUNNING")
            item |= {"state": state, "hours": hours, "due_at": due.isoformat()}
        out.append(item)
    return out


# ------------------------------------------------------------------------------------------------------------------------ reviews


def review_problem(requested_by: str, reviewer: str, rationale: str, outcome: str) -> str | None:
    if outcome not in ("UPHELD", "OVERTURNED", "CORRECTED"):
        return "OUTCOME_INVALID"
    if reviewer == requested_by:
        return "REVIEWER_IS_REQUESTER"
    if not rationale.strip():
        return "RATIONALE_REQUIRED"
    return None
