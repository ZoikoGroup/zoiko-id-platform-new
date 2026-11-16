"""Verification workflows as data: definition, static safety analysis, simulation and publish gates (ZID-GAP-001 v2.0 DTV-30; CR-099). Pure domain.

A workflow says which checks run, in what order, and what happens on each result. It is DATA, never code: a definition is a small graph of steps and a
fixed vocabulary of conditions, and nothing in it can run anything. That keeps a customer- or compliance-authored workflow safe to store, simulate and review.

The rules that hold whatever someone writes, checked BEFORE a workflow can be published (`analyze`):

- the graph is finite: it starts at one step, every step is reachable, every branch target exists, there is no cycle (no unbounded loop), no more than
  `MAX_STEPS` steps and no more than `MAX_PATHS` distinct paths (too complex to review is refused, not guessed at);
- every path ends: a DECISION always has an `else`, so an unexpected result (INDETERMINATE, UNAVAILABLE, PENDING) goes somewhere on purpose and is never
  dropped;
- the system never declines anyone. The only end outcomes are APPROVED, REFER_TO_PERSON and COMPLETED_NO_DECISION. An adverse result leads to a person;
- APPROVED is guarded: on every path that ends APPROVED, each required check was run AND the path was taken only because that check came back VERIFIED (or
  VERIFIED_LIMITED where the workflow allows it). A path that reaches APPROVED without that is refused at publish, not at run time;
- a regulated check (screening, source of funds) never auto-approves: a REVIEW step must stand between it and APPROVED;
- a published version never changes (it has a content hash), the person who wrote a version cannot approve it, and a check that needs a product line that is
  not available cannot be published.

Nothing here runs a check, calls a provider or stores anything. `simulate` replays a definition against supplied results so a person can see what would
happen; it has no side effects.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_STEPS = 50
MAX_PATHS = 2000
MAX_BRANCHES = 6
MAX_COND_DEPTH = 3
ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
OUTCOMES = frozenset(
    {
        "VERIFIED",
        "VERIFIED_LIMITED",
        "NOT_VERIFIED",
        "INDETERMINATE",
        "PENDING",
        "REVOKED",
        "EXPIRED",
        "UNAVAILABLE",
    }
)
GOOD = frozenset({"VERIFIED", "VERIFIED_LIMITED"})
END_OUTCOMES = frozenset(
    {"APPROVED", "REFER_TO_PERSON", "COMPLETED_NO_DECISION"}
)  # there is no DECLINED: only a person declines


class WorkflowError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Check(StrEnum):
    IDENTITY_DOCUMENT = "IDENTITY_DOCUMENT"
    LIVENESS = "LIVENESS"
    ADDRESS = "ADDRESS"
    AGE = "AGE"
    CREDENTIAL_CLAIM = "CREDENTIAL_CLAIM"
    AML_SCREENING = "AML_SCREENING"
    SOURCE_OF_FUNDS = "SOURCE_OF_FUNDS"


# a regulated check needs its product line to be available (publish) and a person between it and APPROVED (analysis)
REGULATED: Mapping[Check, str | None] = {
    Check.AML_SCREENING: None,
    Check.SOURCE_OF_FUNDS: "source_of_funds",
}


@dataclass(frozen=True, slots=True)
class Step:
    step_id: str
    kind: str  # CHECK | DECISION | REVIEW | NOTIFY | END
    check: Check | None = None
    next: str | None = None
    branches: tuple[tuple[Mapping[str, Any], str], ...] = ()  # (condition, target)
    otherwise: str | None = None
    role: str | None = None
    template: str | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class Workflow:
    workflow_id: str
    version: str
    start: str
    steps: Mapping[str, Step]
    required_checks: frozenset[Check]
    allow_limited: bool
    content_hash: str
    author: str | None = None


# ------------------------------------------------------------------------------------------------------------------- parsing


def _cond(c: Any, depth: int = 0) -> Mapping[str, Any]:
    """A condition from the fixed vocabulary: {check, outcome_in}, or all_of / any_of of those. Nothing else is accepted."""
    if depth >= MAX_COND_DEPTH or not isinstance(c, Mapping):
        raise WorkflowError("CONDITION_INVALID", "too deep or not a mapping")
    if set(c) == {"check", "outcome_in"}:
        try:
            Check(str(c["check"]))
        except ValueError as e:
            raise WorkflowError("CONDITION_INVALID", f"check {c['check']!r}") from e
        values = c["outcome_in"]
        if (
            not isinstance(values, Sequence)
            or isinstance(values, str)
            or not values
            or set(map(str, values)) - OUTCOMES
        ):
            raise WorkflowError("CONDITION_INVALID", "outcome_in must list known outcomes")
        return {"check": str(c["check"]), "outcome_in": sorted(set(map(str, values)))}
    if len(c) == 1 and next(iter(c)) in {"all_of", "any_of"}:
        key = next(iter(c))
        parts = c[key]
        if (
            not isinstance(parts, Sequence)
            or isinstance(parts, str)
            or not 1 <= len(parts) <= MAX_BRANCHES
        ):
            raise WorkflowError("CONDITION_INVALID", key)
        return {key: [_cond(p, depth + 1) for p in parts]}
    raise WorkflowError("CONDITION_INVALID", "only check/outcome_in, all_of and any_of are allowed")


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise WorkflowError("STEP_INVALID", field_name)
    return value.strip()


def parse(doc: Mapping[str, Any], *, author: str | None = None) -> Workflow:
    """Build a workflow from a definition. The structure is validated here; the safety rules (`analyze`) are checked at publish."""
    workflow_id, version, start = (
        str(doc.get("id", "")),
        str(doc.get("version", "")),
        str(doc.get("start", "")),
    )
    if not ID.match(workflow_id) or not version or not ID.match(start):
        raise WorkflowError("WORKFLOW_INVALID", "id, version and start")
    raw_steps = doc.get("steps")
    if not isinstance(raw_steps, Mapping) or not 1 <= len(raw_steps) <= MAX_STEPS:
        raise WorkflowError("WORKFLOW_INVALID", f"1 to {MAX_STEPS} steps")
    steps: dict[str, Step] = {}
    for sid, spec in raw_steps.items():
        if not ID.match(str(sid)) or not isinstance(spec, Mapping):
            raise WorkflowError("STEP_INVALID", str(sid))
        kind = str(spec.get("type", ""))
        known = {"type", "check", "next", "branches", "else", "role", "template", "outcome"}
        if set(spec) - known:
            raise WorkflowError("STEP_INVALID", f"{sid}: unknown keys {sorted(set(spec) - known)}")
        if kind == "CHECK":
            try:
                check = Check(str(spec.get("check")))
            except ValueError as e:
                raise WorkflowError("STEP_INVALID", f"{sid}: check") from e
            steps[sid] = Step(sid, kind, check=check, next=_target(spec, "next", sid))
        elif kind == "DECISION":
            raw = spec.get("branches")
            if (
                not isinstance(raw, Sequence)
                or isinstance(raw, str)
                or not 1 <= len(raw) <= MAX_BRANCHES
            ):
                raise WorkflowError("STEP_INVALID", f"{sid}: branches")
            branches = tuple(
                (_cond(b.get("when")), _target(b, "next", sid))
                for b in raw
                if isinstance(b, Mapping)
            )
            if len(branches) != len(raw):
                raise WorkflowError("STEP_INVALID", f"{sid}: branches")
            if "else" not in spec:
                raise WorkflowError(
                    "DECISION_WITHOUT_ELSE", sid
                )  # an unexpected result must go somewhere on purpose
            steps[sid] = Step(sid, kind, branches=branches, otherwise=_target(spec, "else", sid))
        elif kind == "REVIEW":
            steps[sid] = Step(
                sid,
                kind,
                role=_text(spec.get("role"), f"{sid}: role"),
                next=_target(spec, "next", sid),
            )
        elif kind == "NOTIFY":
            steps[sid] = Step(
                sid,
                kind,
                template=_text(spec.get("template"), f"{sid}: template"),
                next=_target(spec, "next", sid),
            )
        elif kind == "END":
            outcome = str(spec.get("outcome", ""))
            if outcome not in END_OUTCOMES:
                raise WorkflowError(
                    "END_OUTCOME_NOT_ALLOWED", f"{sid}: {outcome!r}; only a person declines"
                )
            steps[sid] = Step(sid, kind, outcome=outcome)
        else:
            raise WorkflowError("STEP_INVALID", f"{sid}: type {kind!r}")
    required = frozenset(Check(str(c)) for c in doc.get("required_checks") or ())
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()
    return Workflow(
        workflow_id,
        version,
        start,
        steps,
        required,
        bool(doc.get("allow_limited", False)),
        hashlib.sha256(canonical).hexdigest(),
        author,
    )


def _target(spec: Mapping[str, Any], key: str, sid: str) -> str:
    target = spec.get(key)
    if not isinstance(target, str) or not ID.match(target):
        raise WorkflowError("STEP_INVALID", f"{sid}: {key}")
    return target


# ------------------------------------------------------------------------------------------------------------------- analysis


def _targets(step: Step) -> list[str]:
    if step.kind == "END":
        return []
    if step.kind == "DECISION":
        return [t for _, t in step.branches] + ([step.otherwise] if step.otherwise else [])
    return [step.next] if step.next else []


def _atoms(cond: Mapping[str, Any]) -> list[tuple[str, frozenset[str]]]:
    """The (check, outcomes) facts a condition GUARANTEES when it is true. any_of guarantees only what every alternative shares."""
    if "check" in cond:
        return [(cond["check"], frozenset(cond["outcome_in"]))]
    if "all_of" in cond:
        return [a for p in cond["all_of"] for a in _atoms(p)]
    alts = [dict(_atoms(p)) for p in cond["any_of"]]
    shared = set.intersection(*(set(a) for a in alts)) if alts else set()
    return [(c, frozenset().union(*(a[c] for a in alts))) for c in shared]


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    where: str


def analyze(wf: Workflow, *, available_lines: frozenset[str] | None = None) -> tuple[Problem, ...]:
    """Every reason this workflow may not be published. Empty means it may. `available_lines` are the product lines currently available."""
    problems: list[Problem] = []
    steps = wf.steps
    if wf.start not in steps:
        return (Problem("START_MISSING", wf.start),)
    for s in steps.values():
        for t in _targets(s):
            if t not in steps:
                problems.append(Problem("TARGET_MISSING", f"{s.step_id}->{t}"))
    if problems:
        return tuple(problems)

    reachable: set[str] = set()
    stack = [wf.start]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(_targets(steps[cur]))
    problems += [Problem("STEP_UNREACHABLE", sid) for sid in sorted(set(steps) - reachable)]

    # cycles: depth-first with colours
    colour: dict[str, int] = {}

    def visit(sid: str) -> bool:
        colour[sid] = 1
        for t in _targets(steps[sid]):
            if colour.get(t) == 1 or (colour.get(t) is None and visit(t)):
                return True
        colour[sid] = 2
        return False

    if visit(wf.start):
        return (*problems, Problem("CYCLE", "a loop has no end"))

    if not any(s.kind == "END" for s in steps.values()):
        problems.append(Problem("NO_END", "no step ends the workflow"))

    paths = _paths(wf)
    if paths is None:
        return (*problems, Problem("TOO_COMPLEX", f"more than {MAX_PATHS} paths to review"))
    problems += _guards(wf, paths)

    used = {s.check for s in steps.values() if s.check}
    for missing in sorted(wf.required_checks - used):
        problems.append(Problem("REQUIRED_CHECK_NOT_RUN", missing.value))
    for check in sorted(used, key=lambda c: c.value):
        line = REGULATED.get(check)
        if line and available_lines is not None and line not in available_lines:
            problems.append(Problem("PRODUCT_LINE_NOT_AVAILABLE", f"{check.value} needs {line}"))
    return tuple(problems)


# a path is the list of (step, facts guaranteed by the branch taken to leave it)
_Path = list[tuple[str, dict[str, frozenset[str]]]]


def _paths(wf: Workflow) -> list[_Path] | None:
    out: list[_Path] = []

    def walk(sid: str, trail: _Path) -> bool:
        step = wf.steps[sid]
        if step.kind == "END":
            out.append([*trail, (sid, {})])
            return len(out) <= MAX_PATHS
        if step.kind == "DECISION":
            for cond, target in step.branches:
                if not walk(target, [*trail, (sid, {c: o for c, o in _atoms(cond)})]):
                    return False
            return walk(step.otherwise or "", [*trail, (sid, {})])
        return walk(step.next or "", [*trail, (sid, {})])

    return out if walk(wf.start, []) else None


def _guards(wf: Workflow, paths: list[_Path]) -> list[Problem]:
    problems: list[Problem] = []
    allowed = GOOD if wf.allow_limited else frozenset({"VERIFIED"})
    for path in paths:
        end = wf.steps[path[-1][0]]
        if end.outcome != "APPROVED":
            continue
        executed = {c for s, _ in path if (c := wf.steps[s].check) is not None}
        guaranteed: dict[str, frozenset[str]] = {}
        for _, facts in path:
            for check, outcomes in facts.items():
                guaranteed[check] = guaranteed.get(check, outcomes) & outcomes
        label = " > ".join(s for s, _ in path)
        for check in sorted(wf.required_checks, key=lambda c: c.value):
            if check not in executed:
                problems.append(
                    Problem("APPROVAL_SKIPS_A_REQUIRED_CHECK", f"{check.value}: {label}")
                )
            elif not (
                check.value in guaranteed
                and guaranteed[check.value]
                and guaranteed[check.value] <= allowed
            ):
                problems.append(Problem("APPROVAL_NOT_GUARDED", f"{check.value}: {label}"))
        reviews = [i for i, (s, _) in enumerate(path) if wf.steps[s].kind == "REVIEW"]
        for ran in executed:
            if ran in REGULATED:
                ran_at = next(i for i, (s, _) in enumerate(path) if wf.steps[s].check is ran)
                if not any(
                    i > ran_at for i in reviews
                ):  # a person must look AFTER the regulated check ran, before anything approves
                    problems.append(
                        Problem("REGULATED_CHECK_AUTO_APPROVES", f"{ran.value}: {label}")
                    )
    return list(dict.fromkeys(problems))


# ------------------------------------------------------------------------------------------------------------------- simulation


@dataclass(frozen=True, slots=True)
class Trace:
    steps: tuple[str, ...]
    outcome: str
    needs_person: tuple[str, ...] = field(default=())  # the roles asked to review along the way


def _holds(cond: Mapping[str, Any], results: Mapping[str, str]) -> bool:
    if "check" in cond:
        return results.get(cond["check"], "UNAVAILABLE") in cond["outcome_in"]
    if "all_of" in cond:
        return all(_holds(p, results) for p in cond["all_of"])
    return any(_holds(p, results) for p in cond["any_of"])


def simulate(wf: Workflow, results: Mapping[str, str]) -> Trace:
    """Replay the workflow against supplied check results. A check with no result is UNAVAILABLE, never assumed fine. No side effects."""
    unknown = set(results.values()) - OUTCOMES
    if unknown:
        raise WorkflowError("OUTCOME_UNKNOWN", ",".join(sorted(unknown)))
    seen: list[str] = []
    roles: list[str] = []
    known: dict[
        str, str
    ] = {}  # a check's result is revealed only when its step is reached, exactly as the executor will see it
    here = wf.start
    for _ in range(MAX_STEPS + 1):
        step = wf.steps[here]
        seen.append(here)
        if step.kind == "END":
            return Trace(tuple(seen), step.outcome or "", tuple(roles))
        if step.kind == "REVIEW" and step.role:
            roles.append(step.role)
        if step.kind == "CHECK" and step.check is not None:
            known.setdefault(step.check.value, results.get(step.check.value, "UNAVAILABLE"))
        if step.kind == "DECISION":
            here = next(
                (t for cond, t in step.branches if _holds(cond, known)), step.otherwise or ""
            )
        else:
            here = step.next or ""
    raise WorkflowError(
        "SIMULATION_DID_NOT_END", "the workflow loops"
    )  # analyze() refuses cycles, so this is defence in depth


# ------------------------------------------------------------------------------------------------------------------- publishing


def may_publish(
    wf: Workflow, *, approver: str, available_lines: frozenset[str]
) -> tuple[Problem, ...]:
    """The reasons this version may not be approved now. Empty means it may. The author cannot approve their own version."""
    problems = list(analyze(wf, available_lines=available_lines))
    if not approver:
        problems.append(Problem("APPROVER_MISSING", ""))
    elif wf.author and approver == wf.author:
        problems.append(Problem("APPROVER_IS_AUTHOR", "a different person approves a workflow"))
    return tuple(problems)
