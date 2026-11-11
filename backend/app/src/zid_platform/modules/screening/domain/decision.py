"""The screening decision (ZID-ED-04 sections 02, 04, 06). Pure domain, no I/O.

Four separate assertions are never merged: the entity exists (ED-03), the person is who they claim (ED-05), the person is
authorized to act for the entity (here), and the relationship is permitted under sanctions and risk policy (here).
Passing one never satisfies another. Rules:
- a list that is missing or stale means the party was not screened: UNAVAILABLE, never VERIFIED;
- any alert, a sanctions ownership finding, an unresolved ownership chain, a country with no pack, or authority nobody
  proved goes to a person (REVIEW_REQUIRED); no provider and no rule here approves or rejects anyone;
- REJECTED is a reviewer's ruling and cannot come from a rule;
- what the official register says about directors and people with significant control is screened too, and a person the
  register lists that the customer did not declare is an explicit task (GATHERING), never a quiet pass; a register that
  cannot be read is a review, not a clear outcome;
- a decision names the exact list versions and policy that produced it, and the customer sees only a safe outcome.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.screening.domain.lists import ListId, Snapshot
from zid_platform.modules.screening.domain.matching import (
    Match,
    MatchPolicy,
    Party,
    name_score,
    screen_party,
)
from zid_platform.modules.screening.domain.ownership import (
    CountryPack,
    Graph,
    OwnershipResult,
    evaluate,
)


class ScreeningError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class ScreeningStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GATHERING = "GATHERING"
    SCREENING = "SCREENING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class Role(StrEnum):
    ENTITY = "ENTITY"
    REPRESENTATIVE = "REPRESENTATIVE"
    DIRECTOR = "DIRECTOR"
    OWNER = "OWNER"


class AuthorityKind(StrEnum):
    REGISTER_OFFICER = "REGISTER_OFFICER"  # a current director or officer on the official register
    DELEGATED_MANDATE = "DELEGATED_MANDATE"  # a lawful delegation with its own reference and expiry
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    kind: AuthorityKind
    reference: str | None = None  # an id of the register entry or the mandate, never a name
    expires_at: datetime | None = None
    relationship: str | None = (
        None  # how the person says they act for the company (DIRECTOR_OR_OFFICER, DELEGATED_EMPLOYEE, EXTERNAL_REPRESENTATIVE)
    )
    confirmed: bool = (
        False  # a reviewer read the mandate and confirmed it (only a delegated mandate needs this)
    )

    def valid(self, now: datetime) -> bool:
        """An officer the register lists is proof by itself. A delegated mandate is the applicant's own document: it proves nothing until a
        person has read it and confirmed it, and it stops proving anything on its expiry."""
        if self.kind is AuthorityKind.NONE or not self.reference:
            return False
        if self.kind is AuthorityKind.DELEGATED_MANDATE and not self.confirmed:
            return False
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    required_lists: tuple[ListId, ...]
    list_max_age: timedelta  # a list older than this was not current when the party was screened
    validity: timedelta  # how long a clear outcome stands
    validity_elevated: timedelta  # for elevated risk: refreshed more often
    match: MatchPolicy = field(default_factory=MatchPolicy)
    # lists that are required only when the case is elevated (high) risk: a stale or missing one blocks that approval (CR-068)
    high_risk_required_lists: tuple[ListId, ...] = ()


@dataclass(frozen=True, slots=True)
class Subject:
    role: Role
    party: Party


@dataclass(frozen=True, slots=True)
class RegisterPerson:
    """A director/officer or a person with significant control as the official register lists them."""

    ref: str  # opaque: `psc1`, `off3`
    kind: str  # "PSC" or "OFFICER"
    party: Party
    natures: tuple[str, ...] = ()  # the register's own words for the control, for the reviewer
    ceased: bool = False


@dataclass(frozen=True, slots=True)
class RegisterEvidence:
    """`status` is OK (read), UNAVAILABLE (the register could not be read: a review, never a pass) or NOT_APPLICABLE (the country
    has no source for this); `seq` numbers the snapshot a case has seen."""

    status: str
    people: tuple[RegisterPerson, ...] = ()
    content_hash: str = ""
    seq: int = 0


@dataclass(frozen=True, slots=True)
class ScreeningInput:
    country: str
    subjects: tuple[Subject, ...]
    graph: Graph | None
    authority: AuthorityEvidence
    elevated_risk: bool
    snapshots: Mapping[ListId, Snapshot]
    pack: CountryPack | None
    policy: Policy
    now: datetime
    register: RegisterEvidence | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    status: ScreeningStatus
    reasons: tuple[str, ...]
    matches: tuple[Match, ...]
    ownership: OwnershipResult | None
    authority_verified: bool
    lists: tuple[tuple[str, str, str], ...]  # (list id, version, content hash) that were checked
    expiry_at: datetime | None


def _lists_state(inp: ScreeningInput) -> tuple[list[str], list[Snapshot]]:
    problems: list[str] = []
    usable: list[Snapshot] = []
    required = list(inp.policy.required_lists)
    if inp.pack is not None and inp.pack.local_list_required and ListId.LOCAL not in required:
        required.append(ListId.LOCAL)  # the country's own list, where its pack says it is needed
    high_risk_only = [
        x for x in inp.policy.high_risk_required_lists if inp.elevated_risk and x not in required
    ]
    required += high_risk_only
    for list_id in required:
        snap = inp.snapshots.get(list_id)
        if snap is None or snap.size == 0:
            problems.append(f"LIST_UNAVAILABLE:{list_id.value}")
        elif inp.now - snap.fetched_at > inp.policy.list_max_age:
            problems.append(f"LIST_STALE:{list_id.value}")
        else:
            usable.append(snap)
    if any(p.split(":", 1)[1] in {x.value for x in high_risk_only} for p in problems):
        problems.append(
            "HIGH_RISK_APPROVAL_BLOCKED"
        )  # a high-risk approval never rests on a list that was not current
    return problems, usable


def decide(inp: ScreeningInput) -> Decision:
    problems, usable = _lists_state(inp)
    checked = tuple(sorted((s.list_id.value, s.version, s.content_hash) for s in usable))
    if problems:  # not screened: never clear (ED-04 section 08)
        return Decision(
            ScreeningStatus.UNAVAILABLE, tuple(problems), (), None, False, checked, None
        )

    roles = {s.role for s in inp.subjects}
    missing = [
        f"MISSING_PARTY:{r.value}" for r in (Role.ENTITY, Role.REPRESENTATIVE) if r not in roles
    ]
    if (
        missing
    ):  # entity and authorized representative are always required before a verified outcome
        return Decision(ScreeningStatus.GATHERING, tuple(missing), (), None, False, checked, None)

    needs_ownership = inp.pack is not None and bool(
        inp.pack.sanctions_evaluators or inp.pack.beneficial_owner_rule
    )
    if needs_ownership and inp.graph is None:
        return Decision(
            ScreeningStatus.GATHERING, ("MISSING_OWNERSHIP",), (), None, False, checked, None
        )

    register = inp.register if inp.register is not None and inp.register.status == "OK" else None
    # the people the register itself lists are screened as well: a person the customer left out must not escape the lists
    listed = [
        Subject(
            Role.OWNER if p.kind == "PSC" else Role.DIRECTOR, replace(p.party, ref=f"reg:{p.ref}")
        )
        for p in (register.people if register else ())
        if not p.ceased
    ]
    matches: list[Match] = []
    for subject in (*inp.subjects, *listed):
        matches += screen_party(subject.party, usable, inp.policy.match)
    # a PEP match is not a designation: it must never make an ownership chain look sanctioned
    alerted = frozenset(m.party_ref for m in matches if m.list_id is not ListId.PEP)

    reasons: list[str] = []
    ownership: OwnershipResult | None = None
    if inp.pack is None:
        reasons.append("COUNTRY_PACK_MISSING")  # no universal threshold is ever assumed
    elif inp.graph is not None:
        ownership = evaluate(inp.graph, inp.pack, designated=alerted)
        reasons += list(ownership.flags)
        reasons += [f"OWNERSHIP_{r}" for r in ownership.unresolved]
        owner_refs = {s.party.ref for s in inp.subjects if s.role is Role.OWNER}
        reasons += [f"OWNER_NOT_SCREENED:{r}" for r in ownership.identify if r not in owner_refs]
    if matches:
        reasons.append("SCREENING_ALERT")
        if any(m.list_id is ListId.PEP for m in matches):
            reasons.append("PEP_MATCH")  # exposure, not a finding: a person decides
        reasons += sorted({f"ALERT_{m.strength.value}" for m in matches})
    gaps = reconcile_register(inp)
    if inp.register is not None and inp.register.status == "UNAVAILABLE":
        reasons.append(
            "REGISTER_OWNERSHIP_UNAVAILABLE"
        )  # evidence we could not read is never read as clear
    reasons += [
        g
        for g in gaps
        if g.startswith(("DECLARED_DIRECTOR_NOT_ON_REGISTER", "REGISTER_PSC_PROTECTED"))
    ]
    undeclared = [g for g in gaps if g.startswith("REGISTER_PSC_UNDECLARED")]
    authority_ok = inp.authority.valid(inp.now)
    if not authority_ok:
        # a registered company is not a person's authority to act for it; a mandate the applicant supplied waits for a person to read it
        waiting = (
            inp.authority.kind is AuthorityKind.DELEGATED_MANDATE
            and inp.authority.reference
            and (inp.authority.expires_at is None or inp.authority.expires_at > inp.now)
        )
        reasons.append("AUTHORITY_MANDATE_REVIEW" if waiting else "AUTHORITY_UNPROVEN")
    if reasons:
        return Decision(
            ScreeningStatus.REVIEW_REQUIRED,
            tuple(dict.fromkeys([*reasons, *undeclared])),
            tuple(matches),
            ownership,
            authority_ok,
            checked,
            None,
        )
    if undeclared:  # nothing for a person to decide yet: the customer has details to add
        return Decision(
            ScreeningStatus.GATHERING, tuple(undeclared), (), ownership, authority_ok, checked, None
        )
    validity = inp.policy.validity_elevated if inp.elevated_risk else inp.policy.validity
    return Decision(
        ScreeningStatus.VERIFIED,
        ("SCREENED_CLEAR", "AUTHORITY_PROVEN"),
        (),
        ownership,
        True,
        checked,
        bounded_expiry(inp.now + validity, inp.authority),
    )


def bounded_expiry(expiry: datetime, authority: AuthorityEvidence) -> datetime:
    """A clear outcome never outlives the mandate it rests on."""
    if authority.kind is AuthorityKind.DELEGATED_MANDATE and authority.expires_at is not None:
        return min(expiry, authority.expires_at)
    return expiry


def _same_person(a: Party, b: Party, threshold: float) -> bool:
    return a.kind == b.kind and name_score(a.name, b.name) >= threshold


def reconcile_register(inp: ScreeningInput) -> list[str]:
    """Where the customer's declaration and the official register disagree (ZID-ED-04 section 04, step 03). A person with significant
    control the register lists and the customer did not declare is `REGISTER_PSC_UNDECLARED:<ref>` (a task); a director the customer
    declared who is not a current officer on the register is `DECLARED_DIRECTOR_NOT_ON_REGISTER:<ref>` (a review). Only current
    entries count. Names are compared as in screening, so word order, accents and case never create a difference."""
    reg = inp.register
    if reg is None or reg.status != "OK":
        return []
    t = inp.policy.match.threshold
    declared = [
        s.party for s in inp.subjects if s.role in (Role.OWNER, Role.DIRECTOR, Role.REPRESENTATIVE)
    ]
    out: list[str] = []
    for p in reg.people:
        if p.ceased or p.kind != "PSC":
            continue
        if (
            not p.party.name.strip()
        ):  # a protected entry the register does not name: a person must look at it
            out.append(f"REGISTER_PSC_PROTECTED:{p.ref}")
            continue
        if not any(_same_person(p.party, d, t) for d in declared):
            out.append(f"REGISTER_PSC_UNDECLARED:{p.ref}")
    officers = [p.party for p in reg.people if p.kind == "OFFICER" and not p.ceased]
    for s in inp.subjects:
        if s.role is Role.DIRECTOR and not any(_same_person(s.party, o, t) for o in officers):
            out.append(f"DECLARED_DIRECTOR_NOT_ON_REGISTER:{s.party.ref}")
    return out


def effective_status(
    status: ScreeningStatus, expiry_at: datetime | None, now: datetime
) -> ScreeningStatus:
    if status is ScreeningStatus.VERIFIED and (expiry_at is None or expiry_at <= now):
        return ScreeningStatus.EXPIRED
    return status


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def evidence_object(
    *,
    tenant_id: str,
    case_id: str,
    inp: ScreeningInput,
    decision: Decision,
    version: int,
    reviewed: bool = False,
    carry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The decision as signed evidence. It holds ids, codes, scores and list versions, never a name or a date of birth."""
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "version": version,
        "country": inp.country,
        "decision_status": decision.status.value,
        "reasons": list(decision.reasons),
        "authority_verified": decision.authority_verified,
        "authority_kind": inp.authority.kind.value,
        "authority_reference": inp.authority.reference,
        "authority_relationship": inp.authority.relationship,
        # remembered in every record that follows, so a rescreen or an appeal never loses a reviewer's confirmation of the mandate
        "mandate_confirmed_reference": inp.authority.reference if inp.authority.confirmed else None,
        "lists": [{"list_id": a, "version": b, "content_hash": c} for a, b, c in decision.lists],
        "policy_version": inp.policy.version,
        "match_policy_version": inp.policy.match.version,
        "country_pack_version": inp.pack.version if inp.pack else None,
        "parties": [{"ref": s.party.ref, "role": s.role.value} for s in inp.subjects],
        "matches": [
            {
                "party_ref": m.party_ref,
                "list_id": m.list_id.value,
                "list_version": m.list_version,
                "source_ref": m.source_ref,
                "score": m.score,
                "strength": m.strength.value,
                "reasons": list(m.reasons),
            }
            for m in decision.matches
        ],
        "ownership": {
            "flags": list(decision.ownership.flags) if decision.ownership else [],
            "unresolved": list(decision.ownership.unresolved) if decision.ownership else [],
            "identify": list(decision.ownership.identify) if decision.ownership else [],
        },
        "register": {
            "status": inp.register.status if inp.register else "NOT_APPLICABLE",
            "seq": inp.register.seq if inp.register else 0,
            "content_hash": inp.register.content_hash if inp.register else "",
            "people": [
                {"ref": p.ref, "kind": p.kind, "ceased": p.ceased}
                for p in (inp.register.people if inp.register else ())
            ],
        },
        "screened_at": inp.now.isoformat(),
        "expiry_at": decision.expiry_at.isoformat() if decision.expiry_at else None,
        "reviewed": reviewed,
    }
    if carry:  # a reviewer's ruling keeps the alerts and ownership findings it answers
        body.update(carry)
    body["evidence_hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def tasks_for(reasons: Sequence[str]) -> list[dict[str, Any]]:
    """What the customer can do next, as explicit tasks (ZID-ED-04 section 04, step 03). Codes and counts only: no name, and no
    detail of any list or of a third party's record."""
    undeclared = sum(1 for r in reasons if r.startswith("REGISTER_PSC_UNDECLARED"))
    out: list[dict[str, Any]] = []
    if undeclared:
        who = "person" if undeclared == 1 else "people"
        out.append(
            {
                "code": "DECLARE_REGISTER_PSC",
                "count": undeclared,
                "text": f"The official register lists {undeclared} {who} with significant control that you have not declared. "
                "Add them to the owners and controllers, then submit again.",
            }
        )
    if any(r.startswith("MISSING_PARTY") for r in reasons):
        out.append(
            {"code": "ADD_PARTIES", "count": 0, "text": "Add the company and your own details."}
        )
    if "MISSING_OWNERSHIP" in reasons:
        out.append(
            {
                "code": "DECLARE_OWNERSHIP",
                "count": 0,
                "text": "Say who owns and controls the company.",
            }
        )
    unscreened = sum(1 for r in reasons if r.startswith("OWNER_NOT_SCREENED"))
    if unscreened:
        out.append(
            {
                "code": "ADD_IDENTIFIED_OWNERS",
                "count": unscreened,
                "text": "Add the owners or controllers your country's rules ask to be identified.",
            }
        )
    return out


def safe_view(status: ScreeningStatus, reasons: Sequence[str]) -> dict[str, Any]:
    """What a customer administrator sees: the outcome and the next action, never a watchlist or a third party's data."""
    next_action = {
        ScreeningStatus.VERIFIED: "None. The checks are complete.",
        ScreeningStatus.GATHERING: "More information is needed. Provide the details requested.",
        ScreeningStatus.REVIEW_REQUIRED: "Additional review is required. We will contact you if we need anything.",
        ScreeningStatus.UNAVAILABLE: "Checks are temporarily unavailable. Try again later.",
        ScreeningStatus.REJECTED: "This could not be verified. You may appeal.",
        ScreeningStatus.EXPIRED: "The checks are out of date and must be refreshed.",
        ScreeningStatus.SUSPENDED: "Verified privileges are suspended while a review is open.",
    }.get(status, "Checks have not started.")
    return {"status": status.value, "next_action": next_action, "tasks": tasks_for(reasons)}


REVIEW_OUTCOMES = frozenset(
    {ScreeningStatus.VERIFIED, ScreeningStatus.REJECTED, ScreeningStatus.REVIEW_REQUIRED}
)


@dataclass(frozen=True, slots=True)
class Ruling:
    reviewer_id: str
    outcome: ScreeningStatus
    rationale: str
    confirm_mandate: bool = False  # the reviewer read the delegated mandate and confirms the person may act for the company


APPEAL_KINDS = ("APPEAL", "CORRECTION")
APPEAL_OPEN = "APPEAL_REQUESTED"


def check_appeal(status: ScreeningStatus, kind: str, statement: str, *, open_appeal: bool) -> None:
    """A customer may appeal a rejection or ask for a correction while a case is not clear (ZID-ED-04 section 06, decision fairness).
    A correction can be asked for while the case is gathering, in review or rejected; an appeal only after a rejection. One request
    is open at a time; the statement is what the customer wrote, at least a sentence."""
    if kind not in APPEAL_KINDS:
        raise ScreeningError("APPEAL_KIND_INVALID", kind)
    if kind == "APPEAL" and status is not ScreeningStatus.REJECTED:
        raise ScreeningError("APPEAL_NOT_AVAILABLE", status.value)
    if kind == "CORRECTION" and status not in (
        ScreeningStatus.GATHERING,
        ScreeningStatus.REVIEW_REQUIRED,
        ScreeningStatus.REJECTED,
    ):
        raise ScreeningError("APPEAL_NOT_AVAILABLE", status.value)
    if open_appeal:
        raise ScreeningError("APPEAL_ALREADY_OPEN")
    if not 20 <= len(statement.strip()) <= 2000:
        raise ScreeningError(
            "STATEMENT_INVALID", "write at least a sentence, at most 2000 characters"
        )


def check_appeal_reviewer(reviewer_id: str, earlier_deciders: Sequence[str]) -> None:
    """An appeal is decided by someone who did not decide the case before (an independent second look)."""
    if reviewer_id in earlier_deciders:
        raise ScreeningError(
            "REVIEWER_CONFLICT",
            "an appeal is decided by someone who did not decide the case before",
        )


def check_ruling(
    current: ScreeningStatus,
    ruling: Ruling,
    *,
    requester_id: str | None,
    subject_refs: Sequence[str],
) -> None:
    if current is not ScreeningStatus.REVIEW_REQUIRED:
        raise ScreeningError("CASE_NOT_IN_REVIEW", current.value)
    if ruling.outcome not in REVIEW_OUTCOMES:
        raise ScreeningError("RULING_OUTCOME_INVALID", ruling.outcome.value)
    if ruling.confirm_mandate and ruling.outcome is not ScreeningStatus.VERIFIED:
        raise ScreeningError(
            "RULING_OUTCOME_INVALID", "a mandate is confirmed only by a ruling that verifies"
        )
    if len(ruling.rationale.strip()) < 20:
        raise ScreeningError("RATIONALE_REQUIRED", "a reviewer's decision needs a recorded reason")
    if ruling.reviewer_id == requester_id or ruling.reviewer_id in subject_refs:
        raise ScreeningError(
            "REVIEWER_CONFLICT", "the requester or a screened party cannot decide the case"
        )
