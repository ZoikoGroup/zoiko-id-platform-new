"""The rules of independent assurance (ZID-UAT-005; CR-074). Pure: no I/O, standard library only.

Everything here exists to stop a release being called ready on anything less than independent evidence:

- a case is PASS only with a complete evidence manifest; a "pass" with no manifest is INCONCLUSIVE, never a hidden PASS;
- a PASS expires by itself when anything it was pinned to changes (build, policy, model, supplier certificate, trust list, fixture, environment);
- a FAIL needs a defect; a waiver is for a non-P0 case only, needs an approver who is not the requester, and expires;
- fixtures are approved by someone other than their author and are then locked by hash; changing a golden outcome is a new revision and never
  rewrites a result that was already recorded;
- the release decision is a pure function of case results, legal and security facts, the privacy gate and signatures. Missing input is a
  failed gate with a named reason, never a pass. No role outside the five release roles can sign, so a product owner, CEO or operator has no way
  around a failed gate.

No number is defaulted here. Thresholds (SLOs, error budgets, false-match limits) come from a signed assurance policy: a gate that needs one
fails until `thresholds_policy_signed` is true.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RuleError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class Status(StrEnum):
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXPIRED = "EXPIRED"
    WAIVED = "WAIVED"


NEXT: dict[Status, frozenset[Status]] = {
    Status.NOT_RUN: frozenset({Status.BLOCKED, Status.PASS, Status.FAIL, Status.INCONCLUSIVE}),
    Status.BLOCKED: frozenset({Status.NOT_RUN, Status.WAIVED}),
    Status.PASS: frozenset({Status.EXPIRED, Status.FAIL}),
    Status.FAIL: frozenset({Status.NOT_RUN, Status.WAIVED}),
    Status.INCONCLUSIVE: frozenset({Status.NOT_RUN, Status.PASS, Status.FAIL, Status.BLOCKED}),
    Status.EXPIRED: frozenset({Status.NOT_RUN, Status.PASS, Status.FAIL}),
    Status.WAIVED: frozenset({Status.NOT_RUN}),
}

MANIFEST_REQUIRED = (
    "run_id", "case_id", "requirement_ids", "build_sha256", "infra_ref", "tester_principal", "tenant_id", "country", "policy_pack",
    "fixture_hashes", "oracle_version", "result", "captured_at", "evidence_signing_key_id", "manifest_signature",
)  # fmt: skip
EVIDENCE_REFS = ("trace_refs", "provider_receipt_refs", "source_snapshot_refs")
WATCHED = (
    "build_sha256",
    "policy_pack",
    "policy_version",
    "model_version",
    "provider_certification",
    "trust_list_snapshot",
    "fixture_hash",
    "environment_id",
)
RELEASE_ROLES = ("QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL")
ROLES_ALWAYS_NEEDED = ("QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY")
NO_GO = {
    "identity_used_as_licence_or_authority": "A valid identity check is used as a professional licence, right to practice, property authority or Right to Rent check",
    "qualified_claim_without_trust_list_evidence": "Qualified status is claimed without historical provider and exact-service trust-list evidence",
    "approval_on_screenshots_or_incomplete_receipts": "Approval rests on screenshots or incomplete source receipts instead of replayable evidence",
    "supplier_substituted_without_scope_check": "A regulated supplier is substituted without verifying exact geographic, service, certificate, privacy and liability scope",
    "privacy_basis_alternative_retention_or_transfer_missing": "A privacy legal basis, user alternative, retention plan or subprocessor transfer protection is absent",
    "promoted_globally_despite_market_gaps": "A service is promoted globally despite market-specific unsupported or expired authorizations",
}  # fmt: skip


# ----------------------------------------------------------------------------------------------------------------- manifests and statuses


def manifest_problems(manifest: Mapping[str, Any] | None) -> list[str]:
    """Why a manifest cannot back a PASS. None, a missing field or no trace, receipt or source snapshot at all is a problem (screenshot-only)."""
    if not manifest:
        return ["MANIFEST_MISSING"]
    out = [
        f"FIELD_MISSING_{k.upper()}"
        for k in MANIFEST_REQUIRED
        if manifest.get(k) in (None, "", [], ())
    ]
    if not any(manifest.get(k) for k in EVIDENCE_REFS):
        out.append("NO_TRACE_RECEIPT_OR_SOURCE_SNAPSHOT")
    return out


def verdict_from(manifest: Mapping[str, Any] | None) -> Status:
    """The status a manifest supports. It can be as good as the result it claims, and never better than its completeness: an incomplete manifest
    that claims PASS is INCONCLUSIVE; one claiming FAIL still counts as FAIL (a failure is not weakened by missing detail)."""
    claimed = str((manifest or {}).get("result", "")).upper()
    if claimed == Status.FAIL.value:
        return Status.FAIL
    if manifest_problems(manifest):
        return Status.INCONCLUSIVE
    try:
        status = Status(claimed)
    except ValueError:
        return Status.INCONCLUSIVE
    return (
        status
        if status in (Status.PASS, Status.INCONCLUSIVE, Status.BLOCKED)
        else Status.INCONCLUSIVE
    )


def move(
    current: Status,
    to: Status,
    *,
    p0: bool,
    manifest: Mapping[str, Any] | None = None,
    defect_id: str | None = None,
    waiver: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Status:
    if to not in NEXT[current]:
        raise RuleError("TRANSITION_NOT_ALLOWED", f"{current.value} cannot move to {to.value}")
    if to is Status.PASS and manifest_problems(manifest):
        raise RuleError("PASS_NEEDS_A_COMPLETE_MANIFEST", ", ".join(manifest_problems(manifest)))
    if to is Status.FAIL and not (defect_id or "").strip():
        raise RuleError("FAIL_NEEDS_A_DEFECT")
    if to is Status.WAIVED:
        check_waiver(p0=p0, waiver=waiver, now=now)
    return to


def check_waiver(
    *, p0: bool, waiver: Mapping[str, Any] | None, now: datetime | None, max_days: int | None = None
) -> None:
    if p0:
        raise RuleError("P0_CANNOT_BE_WAIVED")
    w = waiver or {}
    if not w.get("approver") or not w.get("requested_by") or not w.get("reason"):
        raise RuleError("WAIVER_NEEDS_APPROVER_REQUESTER_AND_REASON")
    if w["approver"] == w["requested_by"]:
        raise RuleError("WAIVER_APPROVER_IS_THE_REQUESTER")
    expires = w.get("expires_at")
    if not isinstance(expires, datetime) or (now is not None and expires <= now):
        raise RuleError("WAIVER_MUST_EXPIRE_IN_THE_FUTURE")
    if max_days is not None and now is not None and (expires - now).days > max_days:
        raise RuleError("WAIVER_LONGER_THAN_POLICY_ALLOWS")


def changed_inputs(pinned: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Which watched inputs differ from what the PASS was pinned to. An input the PASS pinned and that is now absent counts as changed."""
    return sorted(k for k in WATCHED if k in pinned and pinned[k] != current.get(k))


def age(
    status: Status, pinned: Mapping[str, str], current: Mapping[str, str]
) -> tuple[Status, list[str]]:
    """A PASS whose pinned inputs changed is EXPIRED. Nothing else ages here: a FAIL does not heal by itself."""
    why = changed_inputs(pinned, current)
    return (Status.EXPIRED, why) if status is Status.PASS and why else (status, [])


# ------------------------------------------------------------------------------------------------------------------------- fixtures


def review_fixture(*, author: str, reviewer: str, approve: bool, rationale: str) -> str:
    """The fixture's next state. The reviewer is never the author, and a rejection keeps its reason."""
    if not reviewer or reviewer == author:
        raise RuleError("REVIEWER_IS_THE_AUTHOR")
    if not rationale.strip():
        raise RuleError("RATIONALE_REQUIRED")
    return "APPROVED" if approve else "REJECTED"


def amend_fixture(*, state: str, old_hash: str, new_hash: str) -> str:
    """What an edit becomes. An approved fixture is locked: a changed hash is a new DRAFT revision, and the approved one stays as it was."""
    if state == "APPROVED" and old_hash != new_hash:
        return "NEW_DRAFT_REVISION"
    if state == "APPROVED":
        return "UNCHANGED"
    return "EDIT_IN_PLACE"


def can_rewrite_history(*, recorded_results: int) -> bool:
    """Changing a golden expected outcome never retroactively converts a recorded FAIL to PASS: results already recorded keep their oracle version."""
    return recorded_results == 0


# ------------------------------------------------------------------------------------------------------------------ release decision


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    suite: str
    risk: str  # P0 | P1 | P2 | PROPOSED_P0 | PROPOSED_P1
    status: Status
    applicable: bool = True


@dataclass(frozen=True, slots=True)
class Signature:
    role: str
    principal: str
    decision: str  # APPROVE | REJECT


@dataclass(frozen=True, slots=True)
class Candidate:
    cases: Sequence[Case]
    unresolved_requirement_links: int | None = None
    official_source_status_in_date: bool | None = None
    critical_false_pass_open: bool | None = None
    open_critical_or_high_security: int | None = None
    privacy_release_ready: bool | None = None
    thresholds_policy_signed: bool = False
    country_legal_required: bool = True
    signatures: Sequence[Signature] = ()
    engineering_owner: str | None = None
    commit_authors: Collection[str] = field(default_factory=frozenset)
    flag_documented: bool = False
    post_launch_owner: str | None = None
    no_go_facts: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Gate:
    gate: str
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    status: str  # GO | NO_GO
    reason: str
    gates: tuple[Gate, ...]
    p0_required: int
    p0_pass: int
    open_critical: int
    missing: tuple[str, ...]


def _applicable(c: Candidate, suites: Collection[str] | None = None) -> list[Case]:
    return [x for x in c.cases if x.applicable and (suites is None or x.suite in suites)]


def _is_p0(x: Case) -> bool:
    return x.risk in ("P0", "PROPOSED_P0")


def g0(c: Candidate) -> Gate:
    why: list[str] = []
    p0 = [x for x in _applicable(c) if _is_p0(x)]
    if any(x.risk.startswith("PROPOSED") for x in p0):
        why.append("RISK_NOT_CONFIRMED_BY_QA_LEAD")
    if not p0:
        why.append("NO_P0_CASES_IN_SCOPE")
    why += [f"{x.case_id}_{x.status.value}" for x in p0 if x.status is not Status.PASS]
    if c.unresolved_requirement_links is None:
        why.append("REQUIREMENT_LINKS_NOT_CHECKED")
    elif c.unresolved_requirement_links:
        why.append(f"{c.unresolved_requirement_links}_REQUIREMENT_LINKS_UNRESOLVED")
    return Gate("UAT-G0", not why, tuple(why))


def g1(c: Candidate) -> Gate:
    ok = c.official_source_status_in_date is True
    return Gate("UAT-G1", ok, () if ok else ("OFFICIAL_SOURCE_STATUS_NOT_VALIDATED_IN_DATE",))


def g2(c: Candidate) -> Gate:
    why: list[str] = []
    if c.critical_false_pass_open is None:
        why.append("FALSE_PASS_REVIEW_NOT_DONE")
    elif c.critical_false_pass_open:
        why.append("CRITICAL_FALSE_PASS_OPEN")
    why += [
        f"{x.case_id}_{x.status.value}"
        for x in _applicable(c, {"IDV", "DOC", "PRO", "RNT"})
        if _is_p0(x) and x.status is Status.FAIL
    ]
    return Gate("UAT-G2", not why, tuple(why))


def g3(c: Candidate) -> Gate:
    why: list[str] = []
    if c.open_critical_or_high_security is None:
        why.append("SECURITY_REVIEW_NOT_DONE")
    elif c.open_critical_or_high_security:
        why.append("OPEN_CRITICAL_OR_HIGH_SECURITY_ISSUE")
    if c.privacy_release_ready is not True:
        why.append("PRIVACY_RELEASE_GATE_NOT_READY")
    return Gate("UAT-G3", not why, tuple(why))


def _all_pass(c: Candidate, suites: Collection[str], gate: str) -> Gate:
    scope = _applicable(c, suites)
    why = [f"{x.case_id}_{x.status.value}" for x in scope if x.status is not Status.PASS]
    if not scope:
        why.append("NO_CASES_IN_SCOPE")
    return Gate(gate, not why, tuple(why))


def g4(c: Candidate) -> Gate:
    return _all_pass(c, {"INT", "VC", "DOC"}, "UAT-G4")


def g5(c: Candidate) -> Gate:
    scope = [x for x in _applicable(c) if x.case_id in ("PRI-07", "PRI-08")]
    why = [f"{x.case_id}_{x.status.value}" for x in scope if x.status is not Status.PASS]
    if len(scope) < 2:
        why.append("ACCESSIBILITY_OR_FAIRNESS_CASE_NOT_IN_SCOPE")
    return Gate("UAT-G5", not why, tuple(why))


def g6(c: Candidate) -> Gate:
    ops = _all_pass(c, {"OPS"}, "UAT-G6")
    why = list(ops.reasons)
    if not c.thresholds_policy_signed:
        why.append("THRESHOLDS_POLICY_NOT_SIGNED")
    return Gate("UAT-G6", not why, tuple(why))


def signature_problems(c: Candidate) -> list[str]:
    out: list[str] = []
    seen_roles: set[str] = set()
    seen_principals: dict[str, str] = {}
    for s in c.signatures:
        if s.role not in RELEASE_ROLES:
            out.append(f"ROLE_{s.role}_CANNOT_SIGN_A_RELEASE")
            continue
        if s.role in seen_roles:
            out.append(f"ROLE_{s.role}_SIGNED_TWICE")
        seen_roles.add(s.role)
        if s.principal in seen_principals and seen_principals[s.principal] != s.role:
            out.append(f"ONE_PERSON_HOLDS_{seen_principals[s.principal]}_AND_{s.role}")
        seen_principals[s.principal] = s.role
        if s.role == "QA_LEAD" and (
            s.principal == c.engineering_owner or s.principal in c.commit_authors
        ):
            out.append("QA_LEAD_IS_NOT_INDEPENDENT_OF_THE_BUILD")
    return out


def g7(c: Candidate) -> Gate:
    needed = [*ROLES_ALWAYS_NEEDED, *(["COUNTRY_LEGAL"] if c.country_legal_required else [])]
    approved = {s.role for s in c.signatures if s.decision == "APPROVE"}
    why = [f"{r}_NOT_APPROVED" for r in needed if r not in approved]
    why += [f"{s.role}_REJECTED" for s in c.signatures if s.decision == "REJECT"]
    why += signature_problems(c)
    if not c.flag_documented:
        why.append("FEATURE_FLAG_NOT_DOCUMENTED")
    if not (c.post_launch_owner or "").strip():
        why.append("POST_LAUNCH_OWNER_NOT_NAMED")
    return Gate("UAT-G7", not why, tuple(why))


def no_go_conditions(facts: Mapping[str, bool]) -> list[str]:
    """The mandatory no-go conditions that hold. An unknown fact is treated as true: the platform cannot say a condition is absent."""
    return [k for k in NO_GO if facts.get(k, True) is not False]


def evaluate(c: Candidate) -> Decision:
    gates = (g0(c), g1(c), g2(c), g3(c), g4(c), g5(c), g6(c), g7(c))
    p0 = [x for x in _applicable(c) if _is_p0(x)]
    open_critical = sum(1 for x in p0 if x.status is Status.FAIL)
    missing = tuple(
        sorted(
            f"{x.case_id}:{x.status.value}"
            for x in _applicable(c)
            if _is_p0(x)
            and x.status in (Status.NOT_RUN, Status.BLOCKED, Status.INCONCLUSIVE, Status.EXPIRED)
        )
    )
    blocked_by = no_go_conditions(c.no_go_facts)
    failed = [g for g in gates if not g.passed]
    if blocked_by:
        reason = "NO_GO_CONDITION"
    elif any(x.status is Status.NOT_RUN for x in p0) or not p0:
        reason = "NOT_RUN"
    elif failed:
        reason = failed[0].gate
    else:
        reason = "ALL_GATES_PASSED"
    go = not blocked_by and not failed
    return Decision(
        "GO" if go else "NO_GO",
        reason,
        gates,
        len(p0),
        sum(1 for x in p0 if x.status is Status.PASS),
        open_critical,
        missing,
    )


# ------------------------------------------------------------------------------------------------------------------- the event chain

GENESIS = "0" * 64
RAW = re.compile(
    r"(^|_)(image|photo|selfie|template|embedding|vector|frame|video|audio|document_number|passport_number|id_number|raw|biometric)(_|$)",
    re.IGNORECASE,
)


def raw_unsafe_keys(payload: Any, path: str = "") -> list[str]:
    """Keys anywhere in a payload whose name says raw biometric or document material. Events are 'raw-safe': they carry references and digests only."""
    out: list[str] = []
    if isinstance(payload, Mapping):
        for k, v in payload.items():
            here = f"{path}.{k}" if path else str(k)
            if RAW.search(str(k)):
                out.append(here)
            out += raw_unsafe_keys(v, here)
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            out += raw_unsafe_keys(v, f"{path}[{i}]")
    return out


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def chain_hash(
    prev_hash: str, seq: int, case_id: str | None, kind: str, payload: Mapping[str, Any]
) -> str:
    body = "|".join((prev_hash, str(seq), case_id or "", kind, canonical(payload)))
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Why a run's event chain does not hold: a gap, a wrong link, or a payload that no longer matches its hash. Empty means intact."""
    out: list[str] = []
    prev = GENESIS
    for i, e in enumerate(events, start=1):
        if int(e["seq"]) != i:
            out.append(f"SEQUENCE_GAP_AT_{i}")
            break
        if e["prev_hash"] != prev:
            out.append(f"BROKEN_LINK_AT_{i}")
            break
        if chain_hash(prev, i, e.get("case_id"), e["kind"], e["payload"]) != e["hash"]:
            out.append(f"PAYLOAD_CHANGED_AT_{i}")
            break
        prev = e["hash"]
    return out


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """The digest a signature binds: the manifest without its own signature field."""
    body = {k: v for k, v in manifest.items() if k != "manifest_signature"}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------- policy thresholds

POLICY_KEYS: dict[str, str] = {  # threshold key -> the direction it is judged in
    "availability_percent": ">=",
    "p50_ms": "<=",
    "p95_ms": "<=",
    "p99_ms": "<=",
    "error_budget_consumed_percent": "<=",
    "rpo_minutes": "<=",
    "rto_minutes": "<=",
    "max_false_match_rate": "<=",
    "max_false_reject_rate": "<=",
}
POLICY_ROLES = ("QA_LEAD", "CTO", "SECURITY_GRC")


def policy_problems(thresholds: Mapping[str, Any]) -> list[str]:
    """Why a policy cannot be recorded. Every number is a person's: nothing here supplies a default, and an unknown key or a non-number is refused."""
    out = [f"THRESHOLD_UNKNOWN_{k.upper()}" for k in thresholds if k not in POLICY_KEYS]
    out += [
        f"THRESHOLD_NOT_A_POSITIVE_NUMBER_{k.upper()}"
        for k, v in thresholds.items()
        if k in POLICY_KEYS and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0)
    ]
    if not thresholds:
        out.append("POLICY_HAS_NO_THRESHOLDS")
    return out


def check_thresholds(thresholds: Mapping[str, Any], measured: Mapping[str, Any]) -> list[str]:
    """Which thresholds the measurements breach. A threshold with no measurement is a breach (NOT_MEASURED), never a pass, and a measurement the
    policy has no threshold for is ignored (it cannot be judged against a number nobody set)."""
    out: list[str] = []
    for key, limit in sorted(thresholds.items()):
        if key not in POLICY_KEYS:
            continue
        got = measured.get(key)
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            out.append(f"{key.upper()}_NOT_MEASURED")
        elif (POLICY_KEYS[key] == "<=" and got > limit) or (
            POLICY_KEYS[key] == ">=" and got < limit
        ):
            out.append(f"{key.upper()}_BREACHED")
    return out


def policy_signed(signatures: Sequence[Mapping[str, Any]]) -> bool:
    """A policy counts only when QA lead, CTO and Security/GRC have each approved it, as three different people, and nobody rejected it."""
    if any(s["decision"] == "REJECT" for s in signatures):
        return False
    approved = {s["role"]: str(s["principal"]) for s in signatures if s["decision"] == "APPROVE"}
    return set(POLICY_ROLES) <= set(approved) and len({approved[r] for r in POLICY_ROLES}) == len(
        POLICY_ROLES
    )
