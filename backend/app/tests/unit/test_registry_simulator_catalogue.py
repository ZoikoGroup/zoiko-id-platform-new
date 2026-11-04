"""The synthetic test catalogue the local company claim searches (development only; nothing here is a real company)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from zid_platform.modules.organization.infrastructure.kyb_adapters import default_adapters
from zid_platform.modules.organization.infrastructure.registry_simulator import (
    CATALOGUE,
    COMPANIES,
    SIMULATOR_KEY,
    RegistrySimulator,
)


class Key:
    def get(self, name: str) -> str:
        return SIMULATOR_KEY


def adapter():
    return default_adapters(Key(), RegistrySimulator())["companies-house"]


def test_every_catalogue_company_has_a_unique_number_and_the_one_test_director() -> None:
    numbers = [n for n, *_ in CATALOGUE]
    assert len(numbers) == len(set(numbers)) and all(len(n) == 8 and n.isdigit() for n in numbers)
    for n, name, _, _ in CATALOGUE:
        officers = COMPANIES[n][1]
        assert officers[0]["name"] == "DOE, Jane Mary", (
            name
        )  # one simulated identity can claim any of them
        assert COMPANIES[n][0]["company_name"] == name


def test_a_search_finds_a_company_by_any_word_returns_close_names_together_and_never_picks_for_the_tester() -> (
    None
):
    async def run() -> None:
        now = datetime.now(UTC)
        both = await adapter().search("blueharbor", "GB", now)
        assert [c.registration_number for c in both] == ["05000002", "05000001"] or {
            c.registration_number for c in both
        } == {"05000001", "05000002"}
        assert [c.legal_name for c in await adapter().search("nexalytech", "GB", now)] == [
            "NEXALYTECH LIMITED"
        ]
        assert [c.registration_number for c in await adapter().search("05000003", "GB", now)] == [
            "05000003"
        ]
        assert await adapter().search("no such company", "GB", now) == []
        dissolved = await adapter().get("05000011", "GB", now)
        assert (
            dissolved is not None and dissolved.active is False
        )  # a dissolved company is shown as it is, and cannot be verified

    asyncio.run(run())
