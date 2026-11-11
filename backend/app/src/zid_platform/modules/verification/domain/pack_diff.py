"""Dry-run comparison of two service-pack registries (ZID-INT-002 section 07.1; GLB-29).

"Publish dry-run mode and scenario simulation; compare old/new rule outputs before approval and preserve a tested rollback path."
Pure and read-only: it resolves the same scenarios against the old and the new registry at the same instant and reports every one
whose answer differs. It changes nothing, so running it can never publish anything.

A change that makes something NEWLY_AVAILABLE is the dangerous direction (a market opens), so `widening` lists those for the reviewer
who approves the publication. Rolling back is proved by `identical`: the restored registry must answer every scenario exactly as the
original did.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from zid_platform.modules.verification.domain.certification import CertRegistry
from zid_platform.modules.verification.domain.service_packs import (
    PackError,
    PackRegistry,
    resolve,
)


@dataclass(frozen=True, slots=True)
class Scenario:
    country: str
    service_code: str
    profession: str | None = None
    subdivision: str | None = None

    def label(self) -> str:
        return "/".join(
            x for x in (self.country, self.subdivision, self.profession, self.service_code) if x
        )


@dataclass(frozen=True, slots=True)
class Answer:
    available: bool
    pack_id: str | None = None
    version: int | None = None
    state: str | None = None
    policy_hash: str | None = None


class Change(StrEnum):
    NEWLY_AVAILABLE = "NEWLY_AVAILABLE"
    NEWLY_UNAVAILABLE = "NEWLY_UNAVAILABLE"
    DIFFERENT_PACK = "DIFFERENT_PACK"  # another pack id answers
    NEW_VERSION = "NEW_VERSION"
    CONTENT_CHANGED = "CONTENT_CHANGED"  # same pack and version, different content hash
    STATE_CHANGED = "STATE_CHANGED"


@dataclass(frozen=True, slots=True)
class Difference:
    scenario: Scenario
    change: Change
    before: Answer
    after: Answer


def scenarios_of(*registries: PackRegistry) -> list[Scenario]:
    """Every scope either registry publishes, so a pack that is dropped is noticed as well as one that is added."""
    seen: dict[Scenario, None] = {}
    for reg in registries:
        for p in reg.packs:
            seen[
                Scenario(p.country_iso2, p.service_code, p.profession_code, p.subdivision_code)
            ] = None
    return list(seen)


def answer(
    registry: PackRegistry,
    s: Scenario,
    *,
    now: datetime,
    environment: str,
    certifications: CertRegistry | None = None,
) -> Answer:
    try:
        p = resolve(
            registry,
            country=s.country,
            service_code=s.service_code,
            profession=s.profession,
            subdivision=s.subdivision,
            now=now,
            environment=environment,
            certifications=certifications,
        )
    except PackError:
        return Answer(False)
    return Answer(True, p.pack_id, p.version, p.feature_state.value, p.policy_hash)


def _change(before: Answer, after: Answer) -> Change | None:
    if before == after:
        return None
    if not before.available:
        return Change.NEWLY_AVAILABLE
    if not after.available:
        return Change.NEWLY_UNAVAILABLE
    if before.pack_id != after.pack_id:
        return Change.DIFFERENT_PACK
    if before.version != after.version:
        return Change.NEW_VERSION
    if before.policy_hash != after.policy_hash:
        return Change.CONTENT_CHANGED
    return Change.STATE_CHANGED


def compare(
    old: PackRegistry,
    new: PackRegistry,
    *,
    now: datetime,
    environment: str,
    scenarios: Iterable[Scenario] | None = None,
    certifications: CertRegistry | None = None,
) -> list[Difference]:
    """Every scenario whose answer differs between the registries, in a stable order. Nothing is mutated."""
    out: list[Difference] = []
    for s in scenarios if scenarios is not None else scenarios_of(old, new):
        b = answer(old, s, now=now, environment=environment, certifications=certifications)
        a = answer(new, s, now=now, environment=environment, certifications=certifications)
        change = _change(b, a)
        if change is not None:
            out.append(Difference(s, change, b, a))
    return sorted(out, key=lambda d: (d.change.value, d.scenario.label()))


def widening(diffs: Sequence[Difference]) -> list[Difference]:
    """Changes that open something up: a market, or a pack answering where none did. These need the closest review."""
    return [d for d in diffs if d.change is Change.NEWLY_AVAILABLE]


def identical(
    a: PackRegistry,
    b: PackRegistry,
    *,
    now: datetime,
    environment: str,
    certifications: CertRegistry | None = None,
) -> bool:
    """A rollback is proved when the restored registry answers every scenario as the original did."""
    return not compare(a, b, now=now, environment=environment, certifications=certifications)
