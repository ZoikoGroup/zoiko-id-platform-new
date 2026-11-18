"""Ownership statements from more than one source, and what to do when they disagree (ZID-DTV-WF-02 section 14, acceptance T20; CR-115). Pure: statements in, link items and conflicts out.

A shareholder paper, a register extract and an applicant's own declaration can say different things about the same holding. This module never picks a winner. When two sources give different
shares for the same owner and owned company, or one says a holding exists and a source that covers it says nothing, the link is marked disputed (so the ownership analysis raises `DISPUTED_LINK`
and the candidate needs evidence) and every source's statement is kept next to it, named, so a reviewer can see who said what. A holding every covering source agrees on is not disputed.

Sources are ranked only to tell the reviewer which kind is usually stronger, never to settle it: an official register extract is listed before a signed paper, a paper before a declaration.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

KIND_ORDER = {"REGISTER": 0, "SIGNED_PAPER": 1, "DECLARATION": 2}
TOLERANCE = Decimal(
    "0.01"
)  # a share differing by less than this many percentage points is the same share (rounding in a document)


class SourceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Statement:
    owner: str
    owned: str
    percent: Decimal
    source_kind: str
    source_ref: str  # which document or extract, so the statement can be found again


@dataclass(frozen=True, slots=True)
class Conflict:
    owner: str
    owned: str
    reason: str  # SHARE_DIFFERS | NOT_IN_COVERING_SOURCE
    statements: tuple[Statement, ...]  # every source's word, named, strongest kind first


def parse(items: Iterable[Mapping[str, Any]]) -> list[Statement]:
    out: list[Statement] = []
    for item in items:
        kind = str(item.get("source_kind", ""))
        ref = str(item.get("source_ref", "")).strip()
        if kind not in KIND_ORDER or not ref:
            raise SourceError(
                "SOURCE_REQUIRED", "every statement names its source kind and reference"
            )
        try:
            pct = Decimal(str(item["percent"]))
        except (KeyError, InvalidOperation) as e:
            raise SourceError("PERCENT_INVALID", f"{item.get('owner')}->{item.get('owned')}") from e
        if not Decimal(0) <= pct <= Decimal(100):
            raise SourceError("PERCENT_INVALID", f"{item.get('owner')}->{item.get('owned')}")
        out.append(Statement(str(item["owner"]), str(item["owned"]), pct, kind, ref))
    return out


def reconcile(
    statements: Iterable[Statement], *, covering: Mapping[str, frozenset[str]] | None = None
) -> tuple[list[dict[str, Any]], list[Conflict]]:
    """Link items for `ownership.build` and the conflicts found.

    `covering` maps a source reference to the owned companies it is meant to give the full holder list for (a register extract usually does, a single signed paper does not). A covering source
    that omits a holding another source states is a conflict, because silence from a complete list is a statement.
    """
    covering = covering or {}
    groups: dict[tuple[str, str], list[Statement]] = defaultdict(list)
    for s in statements:
        groups[(s.owner, s.owned)].append(s)

    items: list[dict[str, Any]] = []
    conflicts: list[Conflict] = []
    for (owner, owned), group in sorted(groups.items()):
        ordered = tuple(sorted(group, key=lambda s: (KIND_ORDER[s.source_kind], s.source_ref)))
        shares = {s.percent for s in ordered}
        differs = max(shares) - min(shares) > TOLERANCE
        said = {s.source_ref for s in ordered}
        silent = [
            ref for ref, owned_set in covering.items() if owned in owned_set and ref not in said
        ]
        reason = "SHARE_DIFFERS" if differs else ("NOT_IN_COVERING_SOURCE" if silent else None)
        if reason:
            conflicts.append(Conflict(owner, owned, reason, ordered))
        # the link carries the strongest kind's share for arithmetic, but is never treated as evidenced while any source disagrees
        items.append(
            {
                "owner": owner,
                "owned": owned,
                "percent": str(ordered[0].percent),
                "evidenced": reason is None,
                "disputed": reason is not None,
                "sources": [
                    {"kind": s.source_kind, "ref": s.source_ref, "percent": str(s.percent)}
                    for s in ordered
                ],
            }
        )
    return items, conflicts


def needs_exception(conflicts: Iterable[Conflict]) -> bool:
    """Any conflict at all sends the company to a person: contradictory papers are never resolved by the platform."""
    return any(True for _ in conflicts)
