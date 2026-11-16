"""Ownership chains and ultimate beneficial owner candidates (ZID-GAP-001 v2.0 DTV-24; CR-101). Pure domain.

A company is owned by people and by other companies, which are owned by people and other companies. The question a compliance team asks is: which real people
end up with a material share of this company, and what do we not know. This module answers it with exact arithmetic over the ownership links someone has
recorded, and is honest about the links nobody has.

- percentages are exact decimal strings (never floats). A person's EFFECTIVE share of the target is the sum, over every path from that person to the target, of the
  product of the percentages along the path. 60 percent of a company that holds 50 percent of the target is 30 percent of the target;
- a circular ownership chain (A owns B owns A) cannot be resolved and is refused whole (`OWNERSHIP_CYCLE`), naming the entities, rather than guessed around;
- an entity cannot be more than 100 percent owned (`OWNED_OVER_100_PERCENT`). The part below 100 that nobody explains is an UNEXPLAINED_REMAINDER, and is reported
  as a gap when its share of the target would itself be material;
- a chain that ends at a company or trust with no recorded owners is an UNRESOLVED_CHAIN, not a clean answer. A publicly listed company or a government is a
  recognised end of a chain and is recorded as such, not as a gap (the policy decides whether that counts as resolved);
- a person who controls by other means (a recorded CONTROL link: board appointment, veto, agreement) is a candidate whatever their share;
- the result is a list of CANDIDATES for a person to confirm, never a finding that someone IS the beneficial owner. A link nobody has evidenced marks every
  candidate that depends on it NEEDS_EVIDENCE.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

DEFAULT_THRESHOLD = Decimal(25)
MAX_ENTITIES = 200
MAX_PATHS = 10_000
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class OwnershipError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Kind(StrEnum):
    PERSON = "PERSON"
    COMPANY = "COMPANY"
    TRUST = "TRUST"
    LISTED_COMPANY = "LISTED_COMPANY"  # a recognised end of a chain: owned by the public
    GOVERNMENT = "GOVERNMENT"  # a recognised end of a chain


RECOGNISED_ENDS = frozenset({Kind.LISTED_COMPANY, Kind.GOVERNMENT})


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    kind: Kind


@dataclass(frozen=True, slots=True)
class Link:
    owner: str
    owned: str
    percent: Decimal | None  # None for a CONTROL link, which carries no share
    control: bool
    evidenced: bool


@dataclass(frozen=True, slots=True)
class Graph:
    entities: Mapping[str, Entity]
    links: tuple[Link, ...]


def _percent(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise OwnershipError("PERCENT_INVALID", "a decimal string, never a number")
    try:
        d = Decimal(value)
    except InvalidOperation as e:
        raise OwnershipError("PERCENT_INVALID", value) from e
    if not d.is_finite() or not Decimal(0) < d <= Decimal(100):
        raise OwnershipError("PERCENT_INVALID", "must be above 0 and at most 100")
    return d


def build(entities: Iterable[Mapping[str, Any]], links: Iterable[Mapping[str, Any]]) -> Graph:
    """The graph from records. Refuses an unknown entity, a self-owned entity, a repeated link, and an entity owned for more than 100 percent."""
    ents: dict[str, Entity] = {}
    for e in entities:
        eid = str(e.get("id", ""))
        try:
            kind = Kind(str(e.get("kind")))
        except ValueError as err:
            raise OwnershipError("ENTITY_INVALID", eid) from err
        if not ID.match(eid) or eid in ents:
            raise OwnershipError("ENTITY_INVALID", eid or "id")
        ents[eid] = Entity(eid, kind)
    if len(ents) > MAX_ENTITIES:
        raise OwnershipError("GRAPH_TOO_LARGE", f"more than {MAX_ENTITIES} entities")
    out: list[Link] = []
    seen: set[tuple[str, str, bool]] = set()
    for item in links:
        owner, owned = str(item.get("owner", "")), str(item.get("owned", ""))
        if owner not in ents or owned not in ents:
            raise OwnershipError("LINK_ENTITY_UNKNOWN", f"{owner}->{owned}")
        if owner == owned:
            raise OwnershipError("SELF_OWNERSHIP", owner)
        control = bool(item.get("control", False))
        key = (owner, owned, control)
        if key in seen:
            raise OwnershipError("LINK_REPEATED", f"{owner}->{owned}")
        seen.add(key)
        if ents[owned].kind is Kind.PERSON:
            raise OwnershipError("PERSON_CANNOT_BE_OWNED", owned)
        out.append(
            Link(
                owner,
                owned,
                None if control else _percent(item.get("percent")),
                control,
                bool(item.get("evidenced", False)),
            )
        )
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for link in out:
        if link.percent is not None:
            totals[link.owned] += link.percent
    for owned, total in totals.items():
        if total > Decimal(100):
            raise OwnershipError("OWNED_OVER_100_PERCENT", owned)
    return Graph(ents, tuple(out))


def _find_cycle(graph: Graph) -> list[str] | None:
    up: dict[str, list[str]] = defaultdict(list)
    for link in graph.links:
        if not link.control:
            up[link.owned].append(link.owner)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in up.get(node, ()):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if state.get(nxt) is None and (found := visit(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph.entities):
        if state.get(node) is None and (found := visit(node)):
            return found
    return None


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_id: str
    effective_percent: Decimal
    reasons: tuple[str, ...]  # OWNERSHIP_AT_OR_ABOVE_THRESHOLD, CONTROL_BY_OTHER_MEANS
    paths: int
    status: str  # NEEDS_CONFIRMATION | NEEDS_EVIDENCE


@dataclass(frozen=True, slots=True)
class Gap:
    code: str  # UNRESOLVED_CHAIN | UNEXPLAINED_REMAINDER
    entity_id: str
    effective_percent: Decimal  # how much of the TARGET this unknown part could be


@dataclass(frozen=True, slots=True)
class Findings:
    target: str
    threshold: Decimal
    candidates: tuple[Candidate, ...]
    gaps: tuple[Gap, ...]
    recognised_ends: tuple[str, ...]
    complete: (
        bool  # no material gap: every part of the target at or above the threshold is explained
    )
    recommendation: str = "REVIEW"
    notes: tuple[str, ...] = (
        "These are candidates for a person to confirm. Nothing here says anyone IS a beneficial owner.",
        "Effective shares are exact products and sums of the recorded percentages; an unrecorded link is a gap, not a zero.",
    )
    fallback: str | None = (
        None  # set when there is no candidate and no material gap: a senior managing official is identified by a person instead
    )


def analyze(graph: Graph, target: str, *, threshold: Decimal = DEFAULT_THRESHOLD) -> Findings:
    """Who ends up with a material share of `target`, and what is not known. Deterministic, and independent of the order links were recorded in."""
    if target not in graph.entities or graph.entities[target].kind is Kind.PERSON:
        raise OwnershipError("TARGET_INVALID", target)
    if not Decimal(0) < threshold <= Decimal(100):
        raise OwnershipError("THRESHOLD_INVALID", str(threshold))
    cycle = _find_cycle(graph)
    if cycle:
        raise OwnershipError("OWNERSHIP_CYCLE", " > ".join(cycle))
    owners_of: dict[str, list[Link]] = defaultdict(list)
    for link in graph.links:
        owners_of[link.owned].append(link)

    effective: dict[str, Decimal] = defaultdict(Decimal)
    paths: dict[str, int] = defaultdict(int)
    unevidenced: set[str] = set()
    reach: dict[str, Decimal] = defaultdict(
        Decimal
    )  # share of the target held (in total) by each entity above it
    count = 0
    stack: list[tuple[str, Decimal, bool]] = [(target, Decimal(1), False)]
    while stack:
        node, share, weak = stack.pop()
        for link in owners_of.get(node, ()):
            if link.control:
                continue
            count += 1
            if count > MAX_PATHS:
                raise OwnershipError("GRAPH_TOO_COMPLEX", f"more than {MAX_PATHS} paths")
            held = share * (link.percent or Decimal(0)) / Decimal(100)
            link_weak = weak or not link.evidenced
            reach[link.owner] += held
            if graph.entities[link.owner].kind is Kind.PERSON:
                effective[link.owner] += held * 100
                paths[link.owner] += 1
                if link_weak:
                    unevidenced.add(link.owner)
            else:
                stack.append((link.owner, held, link_weak))

    controllers = {
        link.owner: link
        for link in graph.links
        if link.control and graph.entities[link.owner].kind is Kind.PERSON
    }
    candidates: list[Candidate] = []
    for person in sorted(set(effective) | set(controllers)):
        reasons = []
        pct = effective.get(person, Decimal(0))
        if pct >= threshold:
            reasons.append("OWNERSHIP_AT_OR_ABOVE_THRESHOLD")
        if person in controllers:
            reasons.append("CONTROL_BY_OTHER_MEANS")
            if not controllers[person].evidenced:
                unevidenced.add(person)
        if reasons:
            status = "NEEDS_EVIDENCE" if person in unevidenced else "NEEDS_CONFIRMATION"
            candidates.append(Candidate(person, pct, tuple(reasons), paths.get(person, 0), status))

    gaps: list[Gap] = []
    ends: list[str] = []
    for entity_id in sorted(graph.entities):
        entity = graph.entities[entity_id]
        if entity.kind is Kind.PERSON or (
            entity_id != target and reach.get(entity_id, Decimal(0)) == 0
        ):
            continue  # a person ends a chain; an entity that holds none of the target is not part of it
        if entity.kind in RECOGNISED_ENDS:
            ends.append(entity_id)
            continue
        exposure = Decimal(100) if entity_id == target else reach[entity_id] * 100
        owners = [link for link in owners_of.get(entity_id, ()) if not link.control]
        known = sum((link.percent or Decimal(0) for link in owners), Decimal(0))
        if not owners:
            gaps.append(Gap("UNRESOLVED_CHAIN", entity_id, exposure))
        elif known < Decimal(100):
            gaps.append(
                Gap(
                    "UNEXPLAINED_REMAINDER",
                    entity_id,
                    exposure * (Decimal(100) - known) / Decimal(100),
                )
            )
    material = [g for g in gaps if g.effective_percent >= threshold]
    complete = not material
    return Findings(
        target,
        threshold,
        tuple(candidates),
        tuple(sorted(gaps, key=lambda g: (g.code, g.entity_id))),
        tuple(ends),
        complete,
        fallback="NO_CANDIDATE: a person identifies a senior managing official"
        if not candidates and complete
        else None,
    )


def summary(findings: Findings) -> Mapping[str, object]:
    """Counts for a reviewer screen; never an entity name."""
    return {
        "candidates": len(findings.candidates),
        "needs_evidence": sum(1 for c in findings.candidates if c.status == "NEEDS_EVIDENCE"),
        "gaps": len(findings.gaps),
        "material_gaps": sum(1 for g in findings.gaps if g.effective_percent >= findings.threshold),
        "complete": findings.complete,
        "recommendation": findings.recommendation,
    }


def percent_text(value: Decimal) -> str:
    """A share as a string with the trailing zeros trimmed, for display. Exact, never rounded."""
    text = format(value.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".")
