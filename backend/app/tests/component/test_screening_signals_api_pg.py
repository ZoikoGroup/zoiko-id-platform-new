"""Fraud and velocity signals, wired into the journeys they watch, and the reviewer queue (CR-068; ZID-GAP-001 WP-14).

A sign-up, a verification and a claim each count themselves against hashed contact, network and device keys; a breach opens one review item and
never blocks the journey. A reviewer sees the queue over HTTP, challenges or resolves an item with a reason and an evidence reference, and a
tenant role never grants that. No raw address is ever stored, and old attempt rows are pruned while signals and their review items stay.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from test_registration_pg import World, world  # noqa: F401  (a fixture, used by name)
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.screening.domain.signals import Action, Dimension, network_prefix
from zid_platform.modules.screening.infrastructure import signal_store as store
from zid_platform.modules.screening.ports import (
    SIGNAL_EVENT_CHANNEL,
    AttemptAction,
    note_attempt,
    prune_velocity,
    retry_failed_notifications,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.settings import Settings

QUEUE = "/v1/reviews/signals"
REVIEW_ACTIONS = ["verification.screening.signal.read", "verification.screening.signal.review"]


def test_the_network_key_is_coarse_and_local_hops_say_nothing() -> None:
    assert network_prefix("203.0.113.77") == "203.0.113.0/24"
    assert network_prefix("203.0.113.5") == network_prefix("203.0.113.200")  # one office, one key
    assert network_prefix("2001:db8:abcd:12::1") == "2001:db8:abcd::/48"
    assert network_prefix("127.0.0.1") is None and network_prefix("::1") is None
    assert (
        network_prefix("testclient") is None
        and network_prefix("") is None
        and network_prefix(None) is None
    )


def test_a_repeated_sign_up_raises_one_signal_and_never_blocks_or_stores_the_address(
    world: World,  # noqa: F811
) -> None:
    w = world
    w.http.app.state.screening_velocity_mode = (
        "review"  # the default is shadow; this test is about the item a breach opens
    )
    email = "Repeat.Person@Example.com"
    first = w.start(email)
    second = w.start(email)
    third = w.start(email)
    assert [r.status_code for r in (first, second, third)] == [
        202,
        202,
        202,
    ]  # a breach never blocks the journey
    signals = w.rows(
        "SELECT source, rule, risk_tier, subject_ref, detail::text AS detail FROM verify.screening_signal"
    )
    breached = [s for s in signals if s.rule.startswith("VELOCITY_CONTACT_SIGNUP")]
    assert (
        len(breached) == 1 and breached[0].risk_tier == "ELEVATED"
    )  # one item, not one per attempt
    items = w.rows("SELECT status FROM verify.screening_signal_review")
    assert [i.status for i in items] == ["OPEN"]
    everything = " ".join(
        [s.detail + s.subject_ref for s in signals]
        + [r.key_hash for r in w.rows("SELECT key_hash FROM verify.screening_velocity_event")]
    ).lower()
    assert "example.com" not in everything and "repeat" not in everything  # only hashes were stored


def test_the_reviewer_queue_challenge_and_resolution_over_http(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url, extra_actions=REVIEW_ACTIONS, extra_resources=["screening_signal:*"])
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL,
        ),
        keyring=o.ring,
    )  # fmt: skip
    now = datetime.now(UTC)

    async def breach() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for _ in range(
                3
            ):  # the third sign-up from one device inside a day is the proposed breach
                await note_attempt(
                    c,
                    o.tenant,
                    subject_ref="registration:abc",
                    action=AttemptAction.SIGNUP,
                    now=now,
                    device="device-1",
                )

    async def a_stranger() -> str:
        """A real, signed-in person in the same tenant who holds none of the reviewer actions."""
        pid, sid, t = str(uuid.uuid4()), str(uuid.uuid4()), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(pid, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            principal, _ = await load_principal(c, pid)
            await create_session(
                c,
                Session(
                    sid, o.tenant, pid, t - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8),
                ),
            )  # fmt: skip
        o.sessions[pid] = sid
        return pid

    asyncio.run(breach())
    with TestClient(app, base_url=ISS) as http:
        owner = o.bearer(o.owner)
        assert http.get(QUEUE).status_code == 401
        listed = http.get(QUEUE, headers=owner)
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 1 and items[0]["source"] == "VELOCITY" and items[0]["status"] == "OPEN"
        item = items[0]
        assert (
            item["detail"]["count"] == 3
            and item["detail"]["threshold"] == 3
            and item["detail"]["dimension"] == "DEVICE"
        )
        assert item["created_at"] and "device-1" not in str(item)  # a hash, never the device id
        url = f"{QUEUE}/{item['review_id']}"

        # a challenge needs a real statement and an evidence reference; the item stays open
        assert (
            http.post(
                f"{url}/challenge",
                headers=owner,
                json={"statement": "too short", "evidence_ref": "case-1"},
            ).status_code
            == 422
        )
        challenged = http.post(
            f"{url}/challenge",
            headers=owner,
            json={
                "statement": "This is a family sharing one laptop; they signed up one after another.",
                "evidence_ref": "case-1",
            },
        )
        assert challenged.status_code == 200 and challenged.json()["status"] == "CHALLENGED"

        # a ruling needs an outcome from the list, a reason and evidence
        bad_outcome = http.post(
            f"{url}/resolution",
            headers=owner,
            json={"outcome": "APPROVED", "rationale": "x" * 30, "evidence_ref": "e"},
        )
        assert bad_outcome.status_code == 422
        short = http.post(
            f"{url}/resolution",
            headers=owner,
            json={"outcome": "CLEARED_BY_REVIEWER", "rationale": "ok", "evidence_ref": "e"},
        )
        assert short.status_code == 422 and short.json()["error"]["code"] == "RATIONALE_REQUIRED"
        ruled = http.post(
            f"{url}/resolution",
            headers=owner,
            json={
                "outcome": "CLEARED_BY_REVIEWER",
                "rationale": "Checked the household; one laptop, three relatives.",
                "evidence_ref": "case-1",
            },
        )
        assert ruled.status_code == 200, ruled.text
        done = ruled.json()
        assert (
            done["status"] == "RESOLVED"
            and done["outcome"] == "CLEARED_BY_REVIEWER"
            and done["resolved_by"] == o.owner
        )
        assert (
            http.post(
                f"{url}/resolution",
                headers=owner,
                json={"outcome": "ESCALATED", "rationale": "y" * 30, "evidence_ref": "e"},
            ).status_code
            == 409
        )
        assert (
            http.post(
                f"{QUEUE}/{uuid.uuid4()}/resolution",
                headers=owner,
                json={"outcome": "ESCALATED", "rationale": "y" * 30, "evidence_ref": "e"},
            ).status_code
            == 404
        )
        assert (
            http.get(QUEUE, headers=owner).json()["items"] == []
        )  # a resolved item leaves the queue

        # a role is not a reviewer: someone without the protected action sees nothing and rules on nothing
        stranger = o.bearer(asyncio.run(a_stranger()))
        assert http.get(QUEUE, headers=stranger).status_code == 403
        assert (
            http.post(
                f"{url}/resolution",
                headers=stranger,
                json={"outcome": "ESCALATED", "rationale": "y" * 30, "evidence_ref": "e"},
            ).status_code
            == 403
        )

    # the signal that was raised stays exactly as raised, with its ruling beside it
    async def kept() -> tuple[int, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            n = (
                await c.execute(
                    text("SELECT count(*) FROM verify.screening_signal WHERE tenant_id = :t"),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            outcome = (
                await c.execute(
                    text("SELECT outcome FROM verify.screening_signal_review WHERE tenant_id = :t"),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            return int(n), str(outcome)

    assert asyncio.run(kept()) == (1, "CLEARED_BY_REVIEWER")


def test_old_attempt_rows_are_pruned_and_signals_are_not(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)
    now = datetime.now(UTC)
    digest = "a" * 64

    async def go() -> tuple[int, int, int]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await store.record_event(
                c, o.tenant, Dimension.DEVICE, digest, Action.SIGNUP, now - timedelta(days=10)
            )
            await store.record_event(
                c, o.tenant, Dimension.DEVICE, digest, Action.SIGNUP, now - timedelta(hours=1)
            )
            for _ in range(3):
                await note_attempt(
                    c,
                    o.tenant,
                    subject_ref="registration:p",
                    action=AttemptAction.SIGNUP,
                    now=now,
                    device="d-prune",
                )
        async with engine(owner_url) as eng, eng.begin() as c:
            pruned = await prune_velocity(c, now)
            events = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM verify.screening_velocity_event WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            signals = (
                await c.execute(
                    text("SELECT count(*) FROM verify.screening_signal WHERE tenant_id = :t"),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            return pruned, int(events), int(signals)

    pruned, events, signals = asyncio.run(go())
    assert pruned >= 1  # the ten-day-old row
    assert events == 4  # the recent row and the three attempts remain
    assert signals == 1  # the signal the breach raised is never pruned


def test_a_claim_and_a_verification_each_count_themselves(rt_url: str, monkeypatch: Any) -> None:
    o = onboard(rt_url, extra_actions=["verification.read"], extra_resources=["verification:*"])
    seen: list[dict[str, Any]] = []

    async def spy(conn: Any, tenant_id: str, **kwargs: Any) -> list[Any]:
        seen.append({"tenant": tenant_id, **kwargs})
        return []

    monkeypatch.setattr("zid_platform.modules.screening.ports.note_attempt", spy)
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL,
        ),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS, raise_server_exceptions=False) as http:
        h = {**o.bearer(o.owner), "X-Zid-Device": "browser-77"}
        http.post(
            "/v1/org-claims",
            headers={**h, "Idempotency-Key": "claim-key-0001"},
            json={"country": "GB", "registration_number": "01234567", "legal_name": "Acme Ltd"},
        )
        vr = http.post(
            "/verifications",
            headers={**o.bearer(o.owner, scope="verification:execute"), "X-Zid-Device": "browser-78"},
            json={
                "profile_id": str(uuid.uuid4()), "subject_principal_id": o.owner, "purpose": "kyc", "jurisdiction": "GB",
            },
        )  # fmt: skip
    by_action = {call["action"]: call for call in seen}
    assert AttemptAction.VERIFICATION in by_action, (vr.status_code, vr.text[:300])
    claim, verification = by_action[AttemptAction.CLAIM], by_action[AttemptAction.VERIFICATION]
    assert (
        claim["tenant"] == o.tenant
        and claim["contact"] == o.owner
        and claim["device"] == "browser-77"
    )
    assert (
        verification["tenant"] == o.tenant
        and verification["contact"] == o.owner
        and verification["device"] == "browser-78"
    )
    assert (
        claim["now"] and claim["pepper"] == b""
    )  # no pepper configured here; a deployed cell sets one


def test_shadow_mode_counts_and_logs_without_opening_an_item_and_off_does_nothing(
    world: World,
) -> None:  # noqa: F811
    """The default mode: the owners read what the proposed thresholds WOULD have flagged on real traffic before anyone is asked to review it."""
    w = world
    assert w.http.app.state.screening_velocity_mode == "shadow"  # the safe default
    for _ in range(4):
        assert w.start("Shadow.Person@Example.com").status_code == 202
    assert (
        w.rows("SELECT 1 FROM verify.screening_signal") == []
    )  # no signal, so no review item and nobody notified
    assert w.rows("SELECT 1 FROM verify.screening_signal_review") == []
    assert (
        len(w.rows("SELECT 1 FROM verify.screening_velocity_event")) >= 4
    )  # but the attempts were counted
    shadow = w.rows("SELECT reason_code FROM evidence.audit_record WHERE action = 'shadow_breach'")
    assert [r.reason_code for r in shadow].count(
        "VELOCITY_CONTACT_SIGNUP_86400S"
    ) == 1  # the first crossing once, not every attempt after it

    w.http.app.state.screening_velocity_mode = "off"
    before = len(w.rows("SELECT 1 FROM verify.screening_velocity_event"))
    assert w.start("Off.Person@Example.com").status_code == 202
    assert (
        len(w.rows("SELECT 1 FROM verify.screening_velocity_event")) == before
    )  # off records nothing


def test_a_raised_signal_publishes_one_event_and_a_failed_delivery_is_retried(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    now = datetime.now(UTC)

    async def broken(_row: Any) -> None:
        raise ConnectionError("the channel is down")

    async def attempts(channel: Any) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for _ in range(3):
                await note_attempt(
                    c, o.tenant, subject_ref="registration:evt", action=AttemptAction.SIGNUP, now=now,
                    device="device-evt", notify=channel,
                )  # fmt: skip

    async def state() -> tuple[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            notify = (
                await c.execute(
                    text(
                        "SELECT notify_state FROM verify.screening_signal_review WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            events = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND event_type = 'com.zoiko.id.screening.signal.raised.v1'"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            return str(notify), int(events)

    # the channel is down: the item is open and marked FAILED, nothing is lost or cleared
    asyncio.run(attempts(broken))
    assert asyncio.run(state()) == ("FAILED", 0)

    async def retry() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await retry_failed_notifications(c, SIGNAL_EVENT_CHANNEL, now)

    assert asyncio.run(retry())["sent"] >= 1  # the sweep delivers it through the platform channel
    assert asyncio.run(state()) == ("SENT", 1)  # exactly one event, and the item is marked
    assert asyncio.run(retry())["sent"] == 0  # a second sweep finds nothing to do
    assert asyncio.run(state()) == ("SENT", 1)
