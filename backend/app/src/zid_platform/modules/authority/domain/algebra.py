"""Formal authority algebra (DEC-C1 §3; ZID-AUTHZ-001 §5; ZID-MANDATE-001 §4; P0-02).

Authority is a bounded capability over independent dimensions. Delegation intersects:

    Effective(child) = Effective(parent) ∩ child.constraints

and a child is valid only if what it asks for is already inside Effective(parent). Every dimension
is monotonic, so delegation can preserve or reduce authority and never widen it. Unbounded
dimensions are explicit (`ANY` / `None`), never a huge magic number. Pure domain, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

# ---------------------------------------------------------------- set dimensions


class _Any:
    """The unrestricted set for a dimension. Only roots and direct authority sources start here."""

    _instance: _Any | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "ANY"


ANY = _Any()
SetScope = _Any | frozenset[str]


def scope(*items: str) -> frozenset[str]:
    return frozenset(items)


def _covers(pattern: str, item: str) -> bool:
    """Resource patterns: exact `type:id`, or `type:*` for every resource of a type."""
    if pattern == item:
        return True
    return pattern.endswith(":*") and item.startswith(pattern[:-1])


def set_contains(parent: SetScope, child: SetScope, *, patterns: bool = False) -> bool:
    if parent is ANY:
        return True
    if child is ANY:
        return False
    assert isinstance(parent, frozenset) and isinstance(child, frozenset)
    if not patterns:
        return child <= parent
    return all(any(_covers(p, c) for p in parent) for c in child)


def set_intersect(a: SetScope, b: SetScope, *, patterns: bool = False) -> SetScope:
    if a is ANY:
        return b
    if b is ANY:
        return a
    assert isinstance(a, frozenset) and isinstance(b, frozenset)
    if not patterns:
        return a & b
    return frozenset(
        {x for x in a if any(_covers(y, x) for y in b)}
        | {y for y in b if any(_covers(x, y) for x in a)}
    )


def set_permits(allowed: SetScope, item: str | None, *, patterns: bool = False) -> bool:
    if item is None or allowed is ANY:
        return True
    assert isinstance(allowed, frozenset)
    return any(_covers(p, item) for p in allowed) if patterns else item in allowed


# ---------------------------------------------------------------- limits and windows


def _min_limit(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


@dataclass(frozen=True, slots=True)
class ValueLimit:
    """A monetary ceiling in fixed decimal. Floating point is prohibited (ZID-MANDATE-001 §11.1)."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < 0:
            raise ValueError("value limit must be a finite, non-negative Decimal")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be an ISO 4217 code")


NOTHING_SPENDABLE = ValueLimit(Decimal(0), "XXX")  # ISO 4217 "no currency"


def _value_contains(parent: ValueLimit | None, child: ValueLimit | None) -> bool:
    if parent is None:
        return True
    if child is None:
        return False
    if child.amount == 0:  # a zero ceiling allows nothing of value, so it sits inside every limit
        return True
    return child.currency == parent.currency and child.amount <= parent.amount


def _value_intersect(a: ValueLimit | None, b: ValueLimit | None) -> ValueLimit | None:
    if a is None:
        return b
    if b is None:
        return a
    if a.currency != b.currency:
        return NOTHING_SPENDABLE  # incompatible currency bases leave nothing to spend
    return a if a.amount <= b.amount else b


@dataclass(frozen=True, slots=True)
class Window:
    """Validity window [valid_from, valid_to). `None` on either side means unbounded."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def contains(self, other: Window) -> bool:
        lo = self.valid_from is None or (
            other.valid_from is not None and other.valid_from >= self.valid_from
        )
        hi = self.valid_to is None or (
            other.valid_to is not None and other.valid_to <= self.valid_to
        )
        return lo and hi

    def intersect(self, other: Window) -> Window:
        froms = [t for t in (self.valid_from, other.valid_from) if t is not None]
        tos = [t for t in (self.valid_to, other.valid_to) if t is not None]
        return Window(max(froms) if froms else None, min(tos) if tos else None)

    def includes(self, at: datetime) -> bool:
        return (self.valid_from is None or at >= self.valid_from) and (
            self.valid_to is None or at < self.valid_to
        )


# ---------------------------------------------------------------- the bounded capability


class Dimension(StrEnum):
    ACTIONS = "actions"
    RESOURCES = "resources"
    PURPOSES = "purposes"
    DATA = "data"
    JURISDICTIONS = "jurisdictions"
    TOOLS = "tools"
    VALUE = "value"
    COUNT = "count"
    DATA_BYTES = "data_bytes"
    ASSURANCE = "assurance"
    TIME = "time"


@dataclass(frozen=True, slots=True)
class Authority:
    """Effective constraints of a grant or chain. Every field narrows; nothing here grants alone."""

    actions: SetScope = ANY
    resources: SetScope = ANY  # `type:id` or `type:*` patterns
    purposes: SetScope = ANY
    data: SetScope = ANY
    jurisdictions: SetScope = ANY
    tools: SetScope = ANY
    value: ValueLimit | None = None  # None = unbounded
    count: int | None = None
    data_bytes: int | None = None
    min_assurance: int = 0  # minimum assurance level required of the actor (higher = stricter)
    window: Window = field(default_factory=Window)

    def __post_init__(self) -> None:
        for name in ("count", "data_bytes"):
            v = getattr(self, name)
            if v is not None and v < 0:
                raise ValueError(f"{name} limit cannot be negative")

    def intersect(self, other: Authority) -> Authority:
        return Authority(
            actions=set_intersect(self.actions, other.actions),
            resources=set_intersect(self.resources, other.resources, patterns=True),
            purposes=set_intersect(self.purposes, other.purposes),
            data=set_intersect(self.data, other.data),
            jurisdictions=set_intersect(self.jurisdictions, other.jurisdictions),
            tools=set_intersect(self.tools, other.tools),
            value=_value_intersect(self.value, other.value),
            count=_min_limit(self.count, other.count),
            data_bytes=_min_limit(self.data_bytes, other.data_bytes),
            min_assurance=max(self.min_assurance, other.min_assurance),
            window=self.window.intersect(other.window),
        )

    def widenings(self, child: Authority) -> list[Dimension]:
        """Dimensions on which `child` asks for more than `self` allows (empty = contained)."""
        out = []
        checks = [
            (Dimension.ACTIONS, set_contains(self.actions, child.actions)),
            (Dimension.RESOURCES, set_contains(self.resources, child.resources, patterns=True)),
            (Dimension.PURPOSES, set_contains(self.purposes, child.purposes)),
            (Dimension.DATA, set_contains(self.data, child.data)),
            (Dimension.JURISDICTIONS, set_contains(self.jurisdictions, child.jurisdictions)),
            (Dimension.TOOLS, set_contains(self.tools, child.tools)),
            (Dimension.VALUE, _value_contains(self.value, child.value)),
            (
                Dimension.COUNT,
                self.count is None or (child.count is not None and child.count <= self.count),
            ),
            (
                Dimension.DATA_BYTES,
                self.data_bytes is None
                or (child.data_bytes is not None and child.data_bytes <= self.data_bytes),
            ),
            (Dimension.ASSURANCE, child.min_assurance >= self.min_assurance),
            (Dimension.TIME, self.window.contains(child.window)),
        ]
        for dim, ok in checks:
            if not ok:
                out.append(dim)
        return out

    def contains(self, child: Authority) -> bool:
        return not self.widenings(child)

    def with_window(self, window: Window) -> Authority:
        return replace(self, window=window)


@dataclass(frozen=True, slots=True)
class Request:
    """One concrete thing an actor wants to do. A request is covered or it is not; no partial credit."""

    action: str
    resource: str
    at: datetime
    purpose: str | None = None
    data_category: str | None = None
    jurisdiction: str | None = None
    tool: str | None = None
    value: ValueLimit | None = None  # the transaction amount, if any
    count: int = 1
    data_bytes: int = 0
    actor_assurance: int = 0


def uncovered(authority: Authority, req: Request) -> list[Dimension]:
    """Dimensions of `req` that `authority` does not cover (empty = fully covered)."""
    out = []
    if not set_permits(authority.actions, req.action):
        out.append(Dimension.ACTIONS)
    if not set_permits(authority.resources, req.resource, patterns=True):
        out.append(Dimension.RESOURCES)
    if not set_permits(authority.purposes, req.purpose):
        out.append(Dimension.PURPOSES)
    if not set_permits(authority.data, req.data_category):
        out.append(Dimension.DATA)
    if not set_permits(authority.jurisdictions, req.jurisdiction):
        out.append(Dimension.JURISDICTIONS)
    if not set_permits(authority.tools, req.tool):
        out.append(Dimension.TOOLS)
    if req.value is not None and not _value_contains(authority.value, req.value):
        out.append(Dimension.VALUE)
    if authority.count is not None and req.count > authority.count:
        out.append(Dimension.COUNT)
    if authority.data_bytes is not None and req.data_bytes > authority.data_bytes:
        out.append(Dimension.DATA_BYTES)
    if req.actor_assurance < authority.min_assurance:
        out.append(Dimension.ASSURANCE)
    if not authority.window.includes(req.at):
        out.append(Dimension.TIME)
    return out
