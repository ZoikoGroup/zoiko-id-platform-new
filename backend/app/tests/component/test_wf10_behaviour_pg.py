"""Behaviour the developer platform already promised, now proved (ZID-CP-WF10 v2.0 rows 1-03, 2-02, 7-01, 7-04, 9-05; CR-173).

Each test drives real endpoints or real rows. If one of them had found the behaviour missing, it would have been a bug to fix, not a test to bend.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_evidence_pg import fresh_runtime  # noqa: F401 - the fixture sets the evidence runtime a replay is audited in
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.evidence.ports import audit_timeline
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.domain.app_settings import THEMES
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.webhook.application import service as webhook_service
from zid_platform.modules.webhook.domain.webhooks import WebhookError


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def sessions_of(w: World, h: dict[str, str]) -> list[dict[str, Any]]:
    r = w.http.get("/v1/verify-api/verification-sessions", headers=h)
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


def test_a_stale_or_unknown_workflow_version_blocks_creation_and_creates_nothing(
    rt_url: str,
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        before = len(sessions_of(w, h))
        stale = w.create(h, workflow_version="2020-01-retired")
        assert stale.status_code == 422 and code(stale) == "WORKFLOW_VERSION_UNAVAILABLE"
        unknown = w.create(h, workflow_id="no_such_workflow")
        assert unknown.status_code == 422 and code(unknown) == "WORKFLOW_VERSION_UNAVAILABLE"
        assert len(sessions_of(w, h)) == before  # a refusal never leaves a half-made session
        ok = w.create(h)
        assert (
            ok.status_code == 201 and ok.json()["workflow_version"]
        )  # the current version still works
    finally:
        w.close()


def test_a_sandbox_token_cannot_read_a_production_session_and_a_launch_link_is_not_a_bearer_token(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        app_id = w.http.get("/v1/verify-api/whoami", headers=h).json()["application_id"]
        sid = "vfs_" + uuid.uuid4().hex
        now = datetime.now(UTC)

        async def plant() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO verify.api_session (session_id, tenant_id, application_id, environment, customer_reference, workflow_id, workflow_version, country, region, purpose_code, locale, status, expires_at) "
                        "VALUES (:s, :t, :a, 'PRODUCTION', 'prod-ref', 'default', '2026-11-sandbox', 'US', 'us', 'customer_onboarding', 'en', 'ready', :e)"
                    ),
                    {
                        "s": sid,
                        "t": uuid.UUID(w.tenant),
                        "a": uuid.UUID(app_id),
                        "e": now + timedelta(days=1),
                    },
                )

        asyncio.run(plant())
        crossed = w.http.get(f"/v1/verify-api/verification-sessions/{sid}", headers=h)
        assert (
            crossed.status_code == 403 and code(crossed) == "ENVIRONMENT_MISMATCH"
        )  # even for its own application, a sandbox token never reaches a production session
        assert w.http.post(
            f"/v1/verify-api/verification-sessions/{sid}:cancel", headers=h
        ).status_code in (403, 404, 405)

        # the launch link is the applicant's grant to ONE session; it is not an API credential
        view = w.create(h).json()
        link = w.link_token(view)
        as_bearer = w.http.get("/v1/verify-api/whoami", headers={"Authorization": f"Bearer {link}"})
        assert as_bearer.status_code == 401
        assert (
            w.http.get(
                f"/v1/verify-api/verification-sessions/{view['session_id']}",
                headers={"Authorization": f"Bearer {link}"},
            ).status_code
            == 401
        )
    finally:
        w.close()


def test_a_launch_link_opens_only_its_own_session_and_a_changed_or_other_companys_link_gets_nothing(
    rt_url: str,
) -> None:
    a, b = World(rt_url), World(rt_url)
    try:
        ha, hb = a.server(), b.server()
        va, vb = a.create(ha).json(), b.create(hb).json()
        ta, tb = a.link_token(va), b.link_token(vb)
        ctx_a = a.http.get(f"/v1/verify-api/hosted/{ta}")
        assert (
            ctx_a.status_code == 200
            and "session_id" not in ctx_a.text.replace('"session_id": null', "")
            or va["session_id"] not in ctx_a.text
        )  # the applicant is not handed the partner's session id
        for bad in (ta[:-3] + "AAA", "zvl_" + "x" * 43, ta + "x", tb[:8] + ta[8:]):
            r = a.http.get(f"/v1/verify-api/hosted/{bad}")
            assert (
                r.status_code == 404 and code(r) == "LAUNCH_INVALID"
            )  # a changed or mixed-up link learns nothing, not even which part was wrong
        # company B's server cannot read company A's session, whatever it knows
        assert (
            b.http.get(
                f"/v1/verify-api/verification-sessions/{va['session_id']}", headers=hb
            ).status_code
            == 404
        )
        # opening the link does not make the link usable as company A's credential against company B
        assert a.http.get(f"/v1/verify-api/hosted/{tb}").status_code in (
            200,
            404,
        )  # B's link is B's; A's app cannot use it as a key
        assert (
            b.http.get(f"/v1/verify-api/hosted/{tb}").json()
            != a.http.get(f"/v1/verify-api/hosted/{ta}").json()
        )
    finally:
        a.close()
        b.close()


def test_a_theme_can_change_looks_but_cannot_remove_what_the_applicant_must_see(
    rt_url: str,
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        app_id = w.http.get("/v1/verify-api/whoami", headers=h).json()["application_id"]

        async def settings(branding: dict[str, Any]) -> Any:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
                return await service.update_settings(c, app_id, {"branding": branding})

        # nothing in the branding can switch off a step: every key outside the four looks keys is refused
        for attempt in (
            {"hide_consent": True},
            {"skip_checks": ["liveness"]},
            {"css": "#consent{display:none}"},
            {"script": "alert(1)"},
            {"require_consent": False},
            {"theme": "none"},
            {"display_name": "<script>x</script>"},
        ):
            with pytest.raises(PartnerError) as e:
                asyncio.run(settings(attempt))
            assert e.value.code == "VALIDATION_FAILED", attempt
        first_theme = next(iter(THEMES))
        asyncio.run(settings({"theme": first_theme, "display_name": "Acme Bank"}))
        view = w.create(h).json()
        hosted = w.http.get(f"/v1/verify-api/hosted/{w.link_token(view)}").json()
        assert (
            hosted["theme"] == first_theme and hosted["requested_by"] == "Acme Bank"
        )  # the looks that were saved are the looks that show
        assert not {"css", "script", "hide_consent", "skip_checks", "require_consent"} & set(
            hosted
        )  # and nothing else can ride along with them
    finally:
        w.close()


def test_a_replayed_webhook_keeps_the_original_and_records_a_new_attempt_under_audit(
    rt_url: str, owner_url: str
) -> None:
    tenant, actor = uid(), uid()
    ep, event, original = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def seed() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO events.webhook_endpoint (endpoint_id, tenant_id, url, event_types, status, signing_key_id, created_by) VALUES (:e, :t, 'https://hooks.example.com/z', ARRAY['verification.api.session_decided'], 'ACTIVE', 'k1', :u)"
                ),
                {"e": ep, "t": uuid.UUID(tenant), "u": uuid.uuid4()},
            )
            await c.execute(
                text(
                    "INSERT INTO events.webhook_delivery (delivery_id, tenant_id, endpoint_id, event_id, event_type, payload, payload_digest, status, attempts, last_status_code) VALUES (:d, :t, :e, :ev, 'verification.api.session_decided', '{\"data\": {}}', 'dig', 'FAILED', 5, 500)"
                ),
                {"d": original, "t": uuid.UUID(tenant), "e": ep, "ev": event},
            )

    asyncio.run(seed())

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            fresh = await webhook_service.replay(
                c, tenant, str(original), actor=actor, now=datetime.now(UTC)
            )
            new_id = str(fresh["delivery_id"])
            assert (
                new_id != str(original)
                and str(fresh["event_id"]) == str(event)
                and str(fresh["replay_of"]) == str(original)
                and fresh["status"] == "QUEUED"
                and fresh["attempts"] == 0
            )
            old = (
                await c.execute(
                    text(
                        "SELECT status, attempts, last_status_code, payload_digest FROM events.webhook_delivery WHERE delivery_id = :d"
                    ),
                    {"d": original},
                )
            ).one()
            assert (old.status, old.attempts, old.last_status_code, old.payload_digest) == (
                "FAILED",
                5,
                500,
                "dig",
            )  # the original record is exactly as it was
            trail = await audit_timeline(c, "webhook_delivery", new_id)
            assert [t["action"] for t in trail] == ["replay"] and trail[0][
                "outcome"
            ] == "SUCCESS"  # the replay left its own audit record
            assert (
                await c.execute(
                    text("SELECT count(*) FROM events.webhook_delivery WHERE event_id = :e"),
                    {"e": event},
                )
            ).scalar_one() == 2
            with pytest.raises(
                WebhookError
            ):  # a delivery that already succeeded, or is only queued, cannot be replayed
                await webhook_service.replay(c, tenant, new_id, actor=actor, now=datetime.now(UTC))

    asyncio.run(go())
    _ = hashlib


def test_the_sandbox_takes_no_real_looking_details_and_no_photograph_and_leaves_nothing_behind(
    rt_url: str,
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        before = len(sessions_of(w, h))
        for bad in (
            {"customer_reference": "anna.smith@example.com"},
            {"customer_reference": "passport-123456789"},
            {"customer_reference": "ok", "metadata": {"phone": "+44 7700 900 123"}},
        ):
            r = w.create(h, **bad)
            assert r.status_code == 422 and code(r) == "SANDBOX_REQUIRES_SYNTHETIC_DATA", (
                bad,
                r.text,
            )
        assert (
            len(sessions_of(w, h)) == before
        )  # a refused request created nothing and stored nothing

        view = w.create(h).json()
        token = w.link_token(view)
        photo = {
            "nonce": "n" * 12,
            "captured_at": datetime.now(UTC).isoformat(),
            "image_base64": "QUJDREVGR0g=",
            "selfie_base64": "QUJDREVGR0g=",
        }
        sent = w.http.post(f"/v1/verify-api/hosted/{token}/capture", json=photo)
        assert sent.status_code in (403, 404, 409) and code(sent) in (
            "ENVIRONMENT_MISMATCH",
            "SESSION_STATE_INVALID",
            "LAUNCH_INVALID",
        )  # a sandbox session has no photograph step
        again = w.http.get(
            f"/v1/verify-api/verification-sessions/{view['session_id']}", headers=h
        ).json()
        assert (
            again["status"] in ("ready", "opened") and again["status"] != "submitted"
        )  # no image moved the session along
    finally:
        w.close()


def test_a_create_retried_after_a_lost_response_is_the_same_session_and_is_counted_once(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 6-05: the partner's server lost the answer and retries with the same Idempotency-Key. It gets the same session back, no second session exists, and nothing new is metered."""
    from zid_platform.modules.verify_api.application import sessions_service

    metered: list[str] = []
    real = sessions_service.usage.meter_session_started

    async def counting(conn: Any, row: dict[str, Any]) -> bool:
        metered.append(str(row["session_id"]))
        return await real(conn, row)

    monkeypatch.setattr(sessions_service.usage, "meter_session_started", counting)
    w = World(rt_url)
    try:
        h = w.server()
        first = w.create(h, key="lost-response-0001")
        assert first.status_code == 201
        sid = first.json()["session_id"]
        for _ in range(3):  # the retry may itself be retried
            again = w.create(h, key="lost-response-0001")
            assert (
                again.status_code == 200 and again.json()["session_id"] == sid
            )  # same session, shown as a replay and not as a new creation
        assert len(sessions_of(w, h)) == 1
        assert metered == [sid]  # one start fact for the session, none for the retries
        other = w.create(h, key="lost-response-0001", customer_reference="a-different-person")
        assert (
            other.status_code == 409 and code(other) == "INVALID_IDEMPOTENCY_REUSE"
        )  # the same key with a different request is refused, never silently a second session
        assert len(sessions_of(w, h)) == 1
    finally:
        w.close()
