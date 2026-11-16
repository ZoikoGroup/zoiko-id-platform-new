"""Running an approved verification workflow: a state machine that waits for a check or a person (ZID-GAP-001 v2.0 DTV-30; CR-099). Pure domain.

`workflow.py` decides whether a definition may be published and replays it for a person to see. This module is the executor: given an approved workflow it moves
one applicant's run forward as results arrive. It runs nothing itself. It never calls a check or a provider; it says which check it is waiting for, and the caller
reports the result. That keeps every side effect outside the machine and every step of it reproducible.

- a run is pinned to one workflow version by its content hash, so a later edit cannot change a run that is under way;
- the machine only accepts the event it is waiting for: a result for a check it did not ask for, or a second result for one it already has, is refused;
- a REVIEW step waits for a person, and the person who completes it cannot be the applicant;
- a NOTIFY step records that a template should be sent (the machine does not send it) and moves on;
- the end outcome is one of the three the definition may use; there is no way for the machine to decline anyone;
- it agrees with the simulator: for the same results the run ends where `workflow.simulate` says it would, and a property test checks that over every combination.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from zid_platform.modules.verification.domain import workflow as wf

MAX_EVENTS = wf.MAX_STEPS * 3


class RunError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Status(StrEnum):
    WAITING_FOR_CHECK = "WAITING_FOR_CHECK"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    ENDED = "ENDED"


@dataclass(frozen=True, slots=True)
class Event:
    step: str
    kind: str  # ENTERED | CHECK_RESULT | REVIEW_COMPLETED | NOTIFY_QUEUED | BRANCH | ENDED
    detail: str = (
        ""  # a check name and an outcome word, a role, a template id: never a name or a value
    )


@dataclass(frozen=True, slots=True)
class RunState:
    workflow_hash: str
    status: Status
    step: str
    results: Mapping[str, str] = field(default_factory=dict)
    awaiting_check: str | None = None
    awaiting_role: str | None = None
    outcome: str | None = None
    events: tuple[Event, ...] = ()
    notifications: tuple[str, ...] = ()  # templates the caller should send, in order


def _advance(w: wf.Workflow, state: RunState) -> RunState:
    """Move through every step that does not wait, until the machine waits for a check or a person, or ends."""
    here, results, events, notes = (
        state.step,
        dict(state.results),
        list(state.events),
        list(state.notifications),
    )
    for _ in range(wf.MAX_STEPS + 1):
        step = w.steps[here]
        if step.kind == "END":
            events.append(Event(here, "ENDED", step.outcome or ""))
            return replace(
                state,
                status=Status.ENDED,
                step=here,
                results=results,
                outcome=step.outcome,
                events=tuple(events),
                notifications=tuple(notes),
                awaiting_check=None,
                awaiting_role=None,
            )
        if step.kind == "CHECK" and step.check is not None:
            if (
                step.check.value in results
            ):  # already answered earlier on this path: do not ask twice
                here = step.next or ""
                continue
            events.append(Event(here, "ENTERED", step.check.value))
            return replace(
                state,
                status=Status.WAITING_FOR_CHECK,
                step=here,
                results=results,
                awaiting_check=step.check.value,
                awaiting_role=None,
                events=tuple(events),
                notifications=tuple(notes),
            )
        if step.kind == "REVIEW":
            events.append(Event(here, "ENTERED", step.role or ""))
            return replace(
                state,
                status=Status.WAITING_FOR_REVIEW,
                step=here,
                results=results,
                awaiting_check=None,
                awaiting_role=step.role,
                events=tuple(events),
                notifications=tuple(notes),
            )
        if step.kind == "NOTIFY":
            notes.append(step.template or "")
            events.append(Event(here, "NOTIFY_QUEUED", step.template or ""))
            here = step.next or ""
            continue
        if step.kind == "DECISION":
            target = next(
                (t for cond, t in step.branches if wf._holds(cond, results)), step.otherwise or ""
            )
            events.append(Event(here, "BRANCH", target))
            here = target
            continue
        raise RunError(
            "STEP_NOT_RUNNABLE", here
        )  # a step kind the machine does not know: refused, never skipped
    raise RunError(
        "RUN_DID_NOT_END", "the workflow loops"
    )  # analyze() refuses loops, so this is defence in depth


def begin(w: wf.Workflow) -> RunState:
    """A new run, advanced to the first thing it waits for."""
    if len(w.content_hash) != 64:
        raise RunError("WORKFLOW_NOT_HASHED")
    return _advance(w, RunState(w.content_hash, Status.WAITING_FOR_CHECK, w.start))


def _guard(w: wf.Workflow, state: RunState) -> None:
    if state.workflow_hash != w.content_hash:
        raise RunError("WORKFLOW_CHANGED", "a run stays on the version it started on")
    if state.status is Status.ENDED:
        raise RunError("RUN_ENDED")
    if len(state.events) > MAX_EVENTS:
        raise RunError("RUN_TOO_LONG")


def report_check(w: wf.Workflow, state: RunState, check: str, outcome: str) -> RunState:
    """The result of the check the machine is waiting for. Anything else is refused."""
    _guard(w, state)
    if outcome not in wf.OUTCOMES:
        raise RunError("OUTCOME_UNKNOWN", outcome)
    if state.status is not Status.WAITING_FOR_CHECK or state.awaiting_check != check:
        raise RunError(
            "CHECK_NOT_AWAITED", f"waiting for {state.awaiting_check or state.awaiting_role}"
        )
    if check in state.results:
        raise RunError("CHECK_ALREADY_REPORTED", check)
    step = w.steps[state.step]
    results = {**state.results, check: outcome}
    moved = replace(
        state,
        results=results,
        events=(*state.events, Event(state.step, "CHECK_RESULT", f"{check}:{outcome}")),
        step=step.next or "",
    )
    return _advance(w, moved)


def complete_review(w: wf.Workflow, state: RunState, *, reviewer: str, applicant: str) -> RunState:
    """A person finishes the review the machine is waiting for. The applicant cannot review their own run."""
    _guard(w, state)
    if state.status is not Status.WAITING_FOR_REVIEW:
        raise RunError("REVIEW_NOT_AWAITED", state.awaiting_check or "")
    if not reviewer or reviewer == applicant:
        raise RunError("REVIEWER_NOT_INDEPENDENT", "the applicant cannot review their own run")
    step = w.steps[state.step]
    moved = replace(
        state,
        events=(*state.events, Event(state.step, "REVIEW_COMPLETED", state.awaiting_role or "")),
        step=step.next or "",
    )
    return _advance(w, moved)
