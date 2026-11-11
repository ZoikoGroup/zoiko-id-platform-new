"""A recording fake adverse-media provider for tests and local runs (CR-068). No network, no real vendor.

It answers from a script, records every query it received (a subject reference and a party reference, never a name), and can be told
to raise or to hang so the fail-safe paths can be exercised."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.signals import AdverseMediaResult, MediaOutcome, RiskTier


@dataclass
class FakeAdverseMedia:
    name: str = "fake-media"
    outcome: MediaOutcome = MediaOutcome.NO_HITS
    hit_count: int = 0
    severity: RiskTier = RiskTier.ELEVATED
    rationale: str = "scripted result"
    raises: Exception | None = None
    hangs: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def search(self, *, subject_ref: str, party: Party) -> AdverseMediaResult:
        self.calls.append((subject_ref, party.ref))
        if self.hangs:
            await asyncio.sleep(3600)
        if self.raises is not None:
            raise self.raises
        return AdverseMediaResult(
            self.name,
            datetime.now(UTC),
            self.outcome,
            self.hit_count,
            self.severity,
            self.rationale,
        )
