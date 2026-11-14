"""T40 on PostgreSQL: a regional policy update re-evaluates the verification sessions of that region that had not finished, and tells only their own
organizations. Sessions of another market, and finished ones, are left alone; the same change is evaluated once; nothing about the subject is announced."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import _draft
from test_privacy_overlay_http_pg import _profile
from zid_db import tenant_transaction
from zid_platform.modules.verification.application import policy_packs as packs
from zid_platform.modules.verification.application.policy_review import reevaluate_pending
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE, Rules, pack_hash
from zid_platform.modules.verification.domain.verification import SessionStatus
from zid_platform.modules.verification.infrastructure import verification_store as store

SUBJECT_MARKER = "subject_principal_id"


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    """The event data, whether the stored payload wraps it or is it."""
    return dict(payload.get("data", payload))


def uid() -> str:
    return str(uuid.uuid4())


def test_t40_a_policy_change_re_evaluates_the_pending_sessions_of_that_region_and_tells_only_them(
    rt_url: str, owner_url: str
) -> None:
    product, other_product = f"P-{uid()[:10]}", f"Q-{uid()[:10]}"
    t1, t2 = uid(), uid()
    key = {"country": "GB", "purpose": PACK_PURPOSE, "subdivision": None, "profession": None}
    v1 = _draft(product=product, **key)
    other = _draft(product=other_product, **key)
    now = datetime.now(UTC)

    def ref(pack: Any) -> dict[str, Any]:
        return {"pack_id": pack.pack_id, "version": pack.version, "pack_hash": pack_hash(pack)}

    async def seed(url: str) -> dict[str, str]:
        out: dict[str, str] = {}
        async with engine(url) as eng, eng.begin() as c:
            await _publish(c, v1)
            await _publish(c, other)
        for tenant in (t1, t2):
            async with engine(url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                profile = await _profile(c, tenant, f"p-{uid()[:6]}", [])

                async def session(
                    name: str,
                    status: SessionStatus,
                    pack: Any,
                    capability: str,
                    c: Any = c,
                    tenant: str = tenant,
                    profile: str = profile,
                ) -> None:
                    sid = uid()
                    await store.create_session(
                        c,
                        store.VSession(
                            sid, tenant, profile, uid(), "onboarding", "GB", status, None, None, (), None,
                            None, 1, now - timedelta(minutes=5), now + timedelta(hours=1),
                        ),
                    )  # fmt: skip
                    await store.set_regulatory_basis(
                        c,
                        sid,
                        {
                            "country": "GB",
                            "capability": capability,
                            "purpose": PACK_PURPOSE,
                            "pack": ref(pack),
                        },
                    )
                    out[f"{name}-{tenant}"] = sid

                await session("in_progress", SessionStatus.IN_PROGRESS, v1, product)
                await session("created", SessionStatus.CREATED, v1, product)
                await session("verified", SessionStatus.VERIFIED, v1, product)
                await session("other_market", SessionStatus.IN_PROGRESS, other, other_product)
        return out

    sessions = asyncio.run(seed(rt_url))

    async def rows(url: str) -> dict[str, dict[str, Any]]:
        async with engine(url) as eng, eng.connect() as c:
            found = (
                await c.execute(
                    text(
                        "SELECT session_id::text, status, policy_review FROM verify.verification_session WHERE session_id = ANY(CAST(:i AS uuid[]))"
                    ),
                    {"i": list(sessions.values())},
                )
            ).all()
        return {r[0]: {"status": r[1], "review": r[2]} for r in found}

    async def events(url: str) -> list[tuple[str, dict[str, Any]]]:
        async with engine(url) as eng, eng.connect() as c:
            found = (
                await c.execute(
                    text(
                        "SELECT tenant_id::text, payload::text FROM events.outbox_event WHERE event_type LIKE '%session.policy_reevaluated%' "
                        "AND aggregate_id = ANY(:i)"
                    ),
                    {"i": list(sessions.values())},
                )
            ).all()
        return [(r[0], _data(json.loads(r[1]))) for r in found]

    async def run_pass(url: str) -> dict[str, int]:
        total = {"checked": 0, "unchanged": 0, "continues": 0, "review_required": 0}
        for tenant in (t1, t2):
            async with engine(url) as eng, eng.begin() as c:
                got = await reevaluate_pending(
                    c, datetime.now(UTC), mode="enforce", only_tenant=tenant
                )
            total = {k: total[k] + got[k] for k in total}
        return total

    # nothing has changed: every pending session is checked and none is touched
    quiet = asyncio.run(run_pass(owner_url))
    assert quiet["unchanged"] >= 6 and quiet["continues"] == quiet["review_required"] == 0
    assert asyncio.run(events(owner_url)) == []

    # the regional pack is revised and the new version approved
    async def revise() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            v2 = await packs.revise_pack(
                c,
                v1.pack_id,
                author="maker2",
                rules=Rules(evidence_minimum=("LICENSE_STANDING", "INSURANCE")),
                now=now,
            )
            await _publish(c, v2)

    asyncio.run(revise())
    changed = asyncio.run(run_pass(owner_url))
    assert changed["continues"] == 4  # in progress and created, in both organizations
    state = asyncio.run(rows(owner_url))
    for tenant in (t1, t2):
        mine = state[sessions[f"in_progress-{tenant}"]]
        assert (
            mine["status"] == "IN_PROGRESS"
            and mine["review"]["result"] == "CONTINUES_UNDER_NEW_PACK"
        )
        assert mine["review"]["started_under"].endswith(pack_hash(v1))
        assert (
            state[sessions[f"created-{tenant}"]]["review"]["result"] == "CONTINUES_UNDER_NEW_PACK"
        )
        assert state[sessions[f"verified-{tenant}"]]["review"] is None  # finished: left alone
        assert (
            state[sessions[f"other_market-{tenant}"]]["review"] is None
        )  # another market: left alone

    told = asyncio.run(events(owner_url))
    assert len(told) == 4
    by_tenant = {t: [d["session_id"] for tt, d in told if tt == t] for t in (t1, t2)}
    for tenant in (t1, t2):  # scoped: each organization is told about its own sessions only
        assert sorted(by_tenant[tenant]) == sorted(
            [sessions[f"in_progress-{tenant}"], sessions[f"created-{tenant}"]]
        )
    assert all(d["country"] == "GB" and d["capability"] == product for _, d in told)
    assert not any(SUBJECT_MARKER in d for _, d in told)  # the subject is never announced

    # a repeat pass is quiet: one evaluation per change
    asyncio.run(run_pass(owner_url))
    assert len(asyncio.run(events(owner_url))) == 4

    # the market loses every pack: sessions with a provider go to a person, a session not yet started stays and is flagged
    async def retire_all() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await packs.retire_pack(c, v1.pack_id, 2, now=now)
            await packs.retire_pack(c, v1.pack_id, 1, now=now)

    asyncio.run(retire_all())
    lost = asyncio.run(run_pass(owner_url))
    assert lost["review_required"] == 4
    state = asyncio.run(rows(owner_url))
    for tenant in (t1, t2):
        moved = state[sessions[f"in_progress-{tenant}"]]
        assert moved["status"] == "MANUAL_REVIEW" and moved["review"]["result"] == "REVIEW_REQUIRED"
        waiting = state[sessions[f"created-{tenant}"]]
        assert waiting["status"] == "CREATED" and waiting["review"]["result"] == "REVIEW_REQUIRED"
        assert state[sessions[f"verified-{tenant}"]]["status"] == "VERIFIED"
        assert state[sessions[f"other_market-{tenant}"]]["review"] is None
    assert len(asyncio.run(events(owner_url))) == 8
