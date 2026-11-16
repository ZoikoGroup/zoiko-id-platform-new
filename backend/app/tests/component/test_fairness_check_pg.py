"""T44 on PostgreSQL: the disparity check reads what real checks recorded, holds the affected cohort, and the next match of that cohort goes to a
person; with no approved threshold it fails closed for every cohort. The shared hold table is cleared around the test: a hold left behind would
change what other tests' name matches decide."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_credential_trust_pg import check, license_claim
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import fairness as fairness_app
from zid_platform.modules.credential.domain import fairness as fa

SAME, OTHER = "Dana Lee", "Dana Lee Smith Jones"


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    """The event data, whether the stored payload wraps it or is it."""
    return dict(payload.get("data", payload))


class NamedSource(checks.SimulatorSource):
    """The simulator, with the register naming the person it holds: the claim itself can never carry the source's name."""

    def __init__(self, name: str) -> None:
        super().__init__("test")
        self.name = name

    async def lookup(self, *args: Any, **kw: Any) -> Any:
        answer = await super().lookup(*args, **kw)
        return replace(answer, fields={**answer.fields, "subject_name": self.name})


@pytest.fixture
def clean_holds(owner_url: str) -> Iterator[None]:
    async def wipe() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("DELETE FROM cred.fairness_hold"))

    asyncio.run(wipe())
    yield
    asyncio.run(wipe())


def test_t44_a_material_disparity_holds_the_cohort_and_with_no_threshold_every_cohort_fails_closed(
    rt_url: str, owner_url: str, clean_holds: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = str(uuid.uuid4())

    async def match(name: str, source_name: str) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await check(
                c,
                tenant,
                str(uuid.uuid4()),
                claimed=license_claim(tag=uuid.uuid4().hex[:6]),
                source=NamedSource(source_name),
                holder=checks.Holder(name),
            )

    async def seed() -> None:
        for _ in range(4):  # the Latin cohort is matched every time
            assert (await match("Dana Lee", SAME)).extras["fairness"]["automated"] == "MATCHED"
        for _ in range(4):  # the Cyrillic cohort is told "a different person" every time
            r = await match("Сергей Иванов", "Dana Lee")
            assert r.extras["fairness"] == {
                "cohort": "CYRILLIC",
                "automated": "NOT_MATCHED",
                "held": False,
            }
            assert (
                r.assertion.subject_match.value == "NOT_MATCHED"
            )  # no hold yet: the automation decides

    asyncio.run(seed())

    async def run_check_disparity() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:  # the worker role sees every tenant
            return await fairness_app.check_disparity(
                c, datetime.now(UTC), window=timedelta(minutes=30)
            )

    async def holds() -> dict[str, tuple[bool, str | None]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(text("SELECT cohort, held, reason FROM cred.fairness_hold"))
            ).all()
        return {r.cohort: (r.held, r.reason) for r in rows}

    async def path_held_events() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT payload::text FROM events.outbox_event WHERE event_type LIKE '%fairness.path_held%' "
                        "AND created_at >= now() - interval '30 minutes'"
                    )
                )
            ).all()
        return [_data(json.loads(r[0])) for r in rows]

    # an approved threshold: the Cyrillic cohort is far above the Latin one, so only it is held
    monkeypatch.setattr(
        fairness_app, "fairness_threshold", lambda: fa.Threshold(0.5, 3, "owner", "DEC-T44")
    )
    first = asyncio.run(run_check_disparity())
    assert first["newly_held"] >= 1
    state = asyncio.run(holds())
    assert state["CYRILLIC"] == (True, "MATERIAL_DISPARITY") and state["LATIN"] == (False, None)

    told = [e for e in asyncio.run(path_held_events()) if e["cohort"] == "CYRILLIC"]
    assert (
        told
        and told[-1]["reason"] == "MATERIAL_DISPARITY"
        and told[-1]["rate"] > told[-1]["reference_rate"]
    )
    assert "Сергей" not in json.dumps(told, ensure_ascii=False)  # aggregate counts only
    again = asyncio.run(run_check_disparity())
    assert again["newly_held"] == 0  # announced once, not on every pass

    # the held path routes to a person: the next Cyrillic "different person" is AMBIGUOUS, a Latin one is still decided
    held_one = asyncio.run(match("Сергей Иванов", "Dana Lee"))
    assert held_one.assertion.subject_match.value == "AMBIGUOUS"
    assert held_one.extras["fairness"] == {
        "cohort": "CYRILLIC",
        "automated": "NOT_MATCHED",
        "held": True,
    }
    assert asyncio.run(match("Dana Lee", SAME)).assertion.subject_match.value == "MATCHED"

    # no approved threshold: fail closed for every cohort with outcomes, so even the Latin cohort goes to a person
    monkeypatch.setattr(fairness_app, "fairness_threshold", lambda: None)
    asyncio.run(run_check_disparity())
    state = asyncio.run(holds())
    assert state["LATIN"] == (True, "NO_APPROVED_THRESHOLD")
    assert state["CYRILLIC"] == (True, "NO_APPROVED_THRESHOLD")
    latin_other = asyncio.run(match("Jane Doe", OTHER))
    assert latin_other.extras["fairness"]["automated"] == "NOT_MATCHED"
    assert latin_other.assertion.subject_match.value == "AMBIGUOUS"
