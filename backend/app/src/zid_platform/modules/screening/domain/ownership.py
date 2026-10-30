"""Ownership and control (ZID-ED-04 section 03). Pure domain, no I/O.

Three different questions are kept apart, because the countries answer them differently and a universal percentage is wrong:
- who must be identified as a beneficial owner or controller (the UK people-with-significant-control test, for instance);
- whether designated persons together own enough of the company to block it (the U.S. OFAC 50 Percent Rule aggregates their
  holdings, and an entity blocked by that rule counts as blocked for what it owns in turn);
- whether a single designated person owns or controls it (the UK OFSI test: over 50 percent of the shares or votes, the right
  to appoint a majority of the board, or practical control; unrelated designated holders are not added together).
Which of these apply to a country is a versioned country pack. A country without a pack has no rule here, and that is a
review, never a pass. A chain with an owner nobody identified, or a percentage nobody verified, is unresolved, never clear.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

HUNDRED = Decimal(100)
FIFTY = Decimal(50)


class HolderKind(StrEnum):
    PERSON = "PERSON"
    ENTITY = "ENTITY"
    UNKNOWN = "UNKNOWN"


class Control(StrEnum):
    APPOINT_BOARD_MAJORITY = "APPOINT_BOARD_MAJORITY"
    SIGNIFICANT_INFLUENCE = "SIGNIFICANT_INFLUENCE"
    OTHER_CONTROL = "OTHER_CONTROL"
    NOMINEE = "NOMINEE"
    TRUST = "TRUST"


@dataclass(frozen=True, slots=True)
class Holder:
    ref: str
    kind: HolderKind
    terminal: bool = False  # an entity that needs no owner behind it (publicly listed, a state body), as the policy allows


@dataclass(frozen=True, slots=True)
class Edge:
    owner: str
    owned: str
    shares_pct: Decimal | None = None
    votes_pct: Decimal | None = None
    controls: frozenset[Control] = frozenset()
    verified: bool = True  # False: the applicant declared it and no source confirmed it


@dataclass(frozen=True, slots=True)
class Graph:
    subject: str
    holders: Mapping[str, Holder]
    edges: tuple[Edge, ...]

    def incoming(self, ref: str) -> list[Edge]:
        return [e for e in self.edges if e.owned == ref]


@dataclass(frozen=True, slots=True)
class Interest:
    holder: str
    shares: Decimal  # effective share of the subject, products along every chain summed
    votes: Decimal
    controls: frozenset[Control]


@dataclass(frozen=True, slots=True)
class OwnershipResult:
    unresolved: tuple[str, ...] = ()  # reasons the structure cannot be relied on
    identify: tuple[
        str, ...
    ] = ()  # owners and controllers the country's rule says must be identified
    flags: tuple[str, ...] = ()  # sanctions ownership findings, for a reviewer
    pack_version: str = ""
    extra: Mapping[str, str] = field(default_factory=dict)


def unresolved_reasons(g: Graph) -> list[str]:
    """Gaps that stop the structure being relied on (ED-04 section 08): a missing intermediary or an unverified figure."""
    out: list[str] = []
    for ref, holder in g.holders.items():
        if ref == g.subject:
            continue
        if holder.kind is HolderKind.UNKNOWN:
            out.append("OWNER_UNKNOWN")
        if holder.kind is HolderKind.ENTITY and not holder.terminal and not g.incoming(ref):
            out.append("MISSING_INTERMEDIARY_OWNER")
    for e in g.edges:
        if e.shares_pct is None and e.votes_pct is None and not e.controls:
            out.append("HOLDING_UNQUANTIFIED")
        if not e.verified:
            out.append("HOLDING_UNVERIFIED")
    if not g.incoming(g.subject):
        out.append("NO_OWNERS_DECLARED")
    return sorted(set(out))


def _paths(
    g: Graph, ref: str, seen: frozenset[str] = frozenset()
) -> Iterable[tuple[str, Decimal, Decimal, frozenset[Control]]]:
    """Every chain from an ultimate owner down to `ref`: (owner, shares, votes, controls), products multiplied."""
    incoming = g.incoming(ref)
    for e in incoming:
        if e.owner in seen:
            continue  # a circular holding adds nothing the first pass did not
        shares = (e.shares_pct or Decimal(0)) / HUNDRED
        votes = (e.votes_pct if e.votes_pct is not None else (e.shares_pct or Decimal(0))) / HUNDRED
        yield e.owner, shares, votes, e.controls
        for owner, s, v, c in _paths(g, e.owner, seen | {ref}):
            # an owner behind an intermediary: shares and votes multiply down the chain, and control passes only
            # through a link that itself carries a majority of the votes
            yield owner, s * shares, v * votes, (c if votes > Decimal("0.5") else frozenset())


def ultimate_interests(g: Graph) -> list[Interest]:
    """The effective interest of every holder in the subject, summed over all chains (direct holders included)."""
    totals: dict[str, tuple[Decimal, Decimal, set[Control]]] = {}
    for owner, shares, votes, controls in _paths(g, g.subject):
        s, v, c = totals.get(owner, (Decimal(0), Decimal(0), set()))
        totals[owner] = (s + shares, v + votes, c | set(controls))
    return [
        Interest(o, s * HUNDRED, v * HUNDRED, frozenset(c))
        for o, (s, v, c) in sorted(totals.items())
    ]


def uk_psc(g: Graph, threshold: Decimal) -> list[str]:
    """People of significant control: a natural person with more than `threshold` percent of the shares or votes, the right
    to appoint a majority of the board, or significant influence or control. Intermediaries are not people."""
    return sorted(
        i.holder
        for i in ultimate_interests(g)
        if g.holders.get(i.holder, Holder(i.holder, HolderKind.UNKNOWN)).kind is HolderKind.PERSON
        and (
            i.shares > threshold
            or i.votes > threshold
            or Control.APPOINT_BOARD_MAJORITY in i.controls
            or Control.SIGNIFICANT_INFLUENCE in i.controls
        )
    )


def ofac_50(g: Graph, designated: frozenset[str]) -> bool:
    """True when designated persons, added together, hold 50 percent or more of the subject. An entity they block in this
    way counts as blocked for what it owns in turn, so the rule is applied until nothing changes."""
    blocked = set(designated)
    changed = True
    while changed:
        changed = False
        for ref, holder in g.holders.items():
            if ref in blocked or holder.kind is HolderKind.PERSON and ref != g.subject:
                continue
            total = sum(
                ((e.shares_pct or Decimal(0)) for e in g.incoming(ref) if e.owner in blocked),
                Decimal(0),
            )
            if total >= FIFTY:
                blocked.add(ref)
                changed = True
    return g.subject in blocked


def ofsi_owned_or_controlled(g: Graph, designated: frozenset[str]) -> bool:
    """True when one designated person owns more than 50 percent of the shares or votes, can appoint a majority of the board,
    or controls the subject, directly or through a chain of such holdings. Holders are never added together."""
    held = set(designated)
    changed = True
    while changed:
        changed = False
        for ref in list(g.holders):
            if ref in held:
                continue
            for e in g.incoming(ref):
                if e.owner in held and (
                    (e.shares_pct or Decimal(0)) > FIFTY
                    or (e.votes_pct or Decimal(0)) > FIFTY
                    or e.controls & {Control.APPOINT_BOARD_MAJORITY, Control.OTHER_CONTROL}
                ):
                    held.add(ref)
                    changed = True
                    break
    return g.subject in held and g.subject not in designated


@dataclass(frozen=True, slots=True)
class CountryPack:
    """A versioned country rule set. Approved by Legal per country; no pack means no rule, which is a review."""

    country: str
    version: str
    beneficial_owner_rule: str | None  # "uk_psc" or None
    beneficial_owner_threshold: Decimal | None
    sanctions_evaluators: tuple[str, ...]  # "ofac_50", "ofsi"
    status: str = "PROPOSED"
    local_list_required: bool = (
        False  # the country's own list must also be current before anyone is screened
    )


def evaluate(g: Graph, pack: CountryPack, *, designated: frozenset[str]) -> OwnershipResult:
    flags: list[str] = []
    if "ofac_50" in pack.sanctions_evaluators and ofac_50(g, designated):
        flags.append("OFAC_50_PERCENT_AGGREGATE")
    if "ofsi" in pack.sanctions_evaluators and ofsi_owned_or_controlled(g, designated):
        flags.append("OFSI_OWNED_OR_CONTROLLED")
    identify: list[str] = []
    if pack.beneficial_owner_rule == "uk_psc" and pack.beneficial_owner_threshold is not None:
        identify = uk_psc(g, pack.beneficial_owner_threshold)
    return OwnershipResult(
        unresolved=tuple(unresolved_reasons(g)),
        identify=tuple(identify),
        flags=tuple(flags),
        pack_version=pack.version,
    )
