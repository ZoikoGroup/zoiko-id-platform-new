"""Identity observations and the feed pass on PostgreSQL (DTV-20, DTV-21, DTV-22; CR-102): a device seen with five people becomes one review item, the
identifier is a digest and never the device id, a repeat adds nothing, old observations are dropped, and one company never sees another's."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import signals
from zid_platform.modules.verification.application import identity_signal_feed as feed
from zid_platform.modules.verification.domain.identity_signals import Kind

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_self_run_session_pg import ISS, new_profile, start, world

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PEPPER = b"test-pepper"


def uid() -> str:
    return str(uuid.uuid4())


def observe_many(
    rt_url: str, tenant: str, subjects: list[str], device: str, when: datetime = NOW
) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            for s in subjects:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await feed.observe(
                        c,
                        tenant,
                        subject=s,
                        kind=Kind.DEVICE,
                        value=device,
                        pepper=PEPPER,
                        now=when,
                    )

    asyncio.run(run())


def feed_pass(owner_url: str, now: datetime) -> dict[str, int]:
    async def run() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await feed.feed_pass(c, now)

    return asyncio.run(run())


def queue(rt_url: str, tenant: str) -> list:  # type: ignore[type-arg]
    async def run() -> list:  # type: ignore[type-arg]
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await signals.open_queue(c, tenant)

    return asyncio.run(run())


def test_a_device_seen_with_five_people_opens_one_item_and_stores_only_a_digest(
    rt_url: str, owner_url: str
) -> None:
    tenant, other = uid(), uid()
    people = [uid() for _ in range(5)]
    observe_many(rt_url, tenant, people, "device-fingerprint-123")
    observe_many(
        rt_url, tenant, people[:1], "device-fingerprint-123"
    )  # the same person again is the same observation
    observe_many(
        rt_url, other, [uid() for _ in range(2)], "device-fingerprint-123"
    )  # another company, only two people

    async def rows() -> list[str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return [
                r.identifier
                for r in (
                    await c.execute(
                        text(
                            "SELECT identifier FROM verify.identity_observation WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            ]

    stored = asyncio.run(rows())
    assert len(stored) == 5 and all(len(x) == 64 and "device-fingerprint" not in x for x in stored)
    assert len(set(stored)) == 1  # one device, one digest

    first = feed_pass(owner_url, NOW + timedelta(minutes=5))
    assert first["opened"] >= 1 and first["companies"] == 2
    items = queue(rt_url, tenant)
    assert (
        items
        and {i.source for i in items} == {"IDENTITY_SIGNAL"}
        and "device-fingerprint" not in repr([i.detail for i in items])
    )
    assert queue(rt_url, other) == []  # two people on a device is a household, not a signal
    again = feed_pass(owner_url, NOW + timedelta(minutes=10))
    assert again["opened"] == 0  # the same group is the same item


def test_observations_past_retention_are_dropped(rt_url: str, owner_url: str) -> None:
    tenant = uid()
    observe_many(rt_url, tenant, [uid()], "old-device", when=NOW - timedelta(days=120))
    out = feed_pass(owner_url, NOW)
    assert out["dropped"] >= 1


def test_a_verification_started_with_a_device_header_remembers_the_device_and_one_without_does_not(
    rt_url: str, owner_url: str
) -> None:
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.screening_signal_pepper = PEPPER
        pid = new_profile(http, adm, binding=False)
        assert start(http, svc, pid, alice).status_code == 201

        async def count() -> int:
            async with engine(owner_url) as eng, eng.begin() as c:
                return int(
                    (
                        await c.execute(
                            text(
                                "SELECT count(*) FROM verify.identity_observation WHERE subject = :s"
                            ),
                            {"s": uuid.UUID(alice)},
                        )
                    ).scalar_one()
                )

        assert asyncio.run(count()) == 0  # no device id was sent
        out = http.post(
            "/v2/self-run/verifications", headers={**svc, "x-zid-device": "phone-abc"},
            json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"},
        )  # fmt: skip
        assert out.status_code == 201, out.text
        assert asyncio.run(count()) == 1
        http.app.state.screening_signal_pepper = b""
        again = http.post(
            "/v2/self-run/verifications", headers={**svc, "x-zid-device": "phone-xyz"},
            json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"},
        )  # fmt: skip
        assert (
            again.status_code == 201 and asyncio.run(count()) == 1
        )  # no secret, so nothing kept, and the verification still starts
