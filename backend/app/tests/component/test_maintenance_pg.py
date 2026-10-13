"""Identity-plane maintenance pass (single-use material purge, lifecycle sweeps)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from conftest import engine
from sqlalchemy import text
from zid_platform.entrypoints.maintenance import run_once


def uid() -> uuid.UUID:
    return uuid.uuid4()


def test_maintenance_purges_and_sweeps(owner_url: str) -> None:
    tenant, principal = uid(), uid()
    now = datetime.now(UTC)
    old, live = now - timedelta(hours=3), now + timedelta(minutes=5)

    async def run() -> tuple[dict[str, int], dict[str, object]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            for nonce, exp in (("old-nonce-1234567890", old), ("live-nonce-1234567890", live)):
                await c.execute(
                    text("INSERT INTO authn.dpop_nonce VALUES (:t, :n, :e)"),
                    {"t": tenant, "n": nonce, "e": exp},
                )
            await c.execute(
                text(
                    "INSERT INTO authn.webauthn_challenge (challenge_hash, tenant_id, ceremony, user_verification, "
                    "expires_at, created_at) VALUES (:h, :t, 'AUTHENTICATION', 'required', :e, :c)"
                ),
                {
                    "h": hashlib.sha256(b"c1").digest(),
                    "t": tenant,
                    "e": old,
                    "c": old - timedelta(minutes=1),
                },
            )
            sid = uid()
            await c.execute(
                text(
                    "INSERT INTO authn.session (session_id, tenant_id, principal_id, auth_time, "
                    "authentication_context, aal, observed_security_revision, risk_class, expires_at) "
                    "VALUES (:s, :t, :p, :a, 'native:webauthn', 2, 0, 1, :e)"
                ),
                {"s": sid, "t": tenant, "p": principal, "a": old - timedelta(hours=1), "e": old},
            )
            counts = await run_once(c, now)
            again = await run_once(c, now)  # idempotent
            state = {
                "nonces": (
                    await c.execute(
                        text("SELECT array_agg(nonce) FROM authn.dpop_nonce WHERE tenant_id = :t"),
                        {"t": tenant},
                    )
                ).scalar_one(),
                "session": (
                    await c.execute(
                        text("SELECT status FROM authn.session WHERE session_id = :s"), {"s": sid}
                    )
                ).scalar_one(),
                "again": again,
            }
        return counts, state

    counts, state = asyncio.run(run())
    assert counts["purged:authn.dpop_nonce"] >= 1 and counts["purged:authn.webauthn_challenge"] >= 1
    assert counts["swept:authn.session"] >= 1
    assert state["nonces"] == ["live-nonce-1234567890"]  # unexpired material is untouched
    assert state["session"] == "EXPIRED"
    assert all(v == 0 for k, v in state["again"].items() if k.startswith("purged:authn.dpop_nonce"))  # type: ignore[union-attr]
