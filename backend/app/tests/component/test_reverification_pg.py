"""The re-verification sweep on PostgreSQL (DTV-29, CR-102): what has lapsed is EXPIRED, what rests on a distrusted provider is STALE, and a person is
reminded before the rest lapses. It never revokes, never verifies and never starts a verification."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_platform.modules.evidence.application.trust import TrustConfig
from zid_platform.modules.verification.ports import reverification_sweep

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from epassport_fixture import make_chip
from test_self_run_session_pg import ISS, chip_body, new_profile, start, world


def swept(owner_url: str, now: datetime) -> dict[str, int]:
    async def run() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await reverification_sweep(c, now)

    return asyncio.run(run())


def assertions(owner_url: str, subject: str) -> list[tuple[str, list[str]]]:
    async def run() -> list[tuple[str, list[str]]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT status, reason_codes FROM verify.verification_assertion WHERE subject_principal_id = :s ORDER BY performed_at"
                    ),
                    {"s": uuid.UUID(subject)},
                )
            ).all()
            return [(r.status, list(r.reason_codes)) for r in rows]

    return asyncio.run(run())


def verified_session(http: TestClient, svc: dict[str, str], pid: str, alice: str, chip) -> None:  # type: ignore[no-untyped-def]
    vid = start(http, svc, pid, alice).json()["verification_id"]
    out = http.post(
        f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
    ).json()
    assert out["status"] == "VERIFIED", out


def test_a_lapsed_assertion_expires_a_distrusted_provider_makes_it_stale_and_a_reminder_comes_first(
    rt_url: str, owner_url: str
) -> None:
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    now = datetime.now(UTC)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        verified_session(http, svc, pid, alice, chip)

        assert swept(owner_url, now) == {
            "expired": 0,
            "stale": 0,
            "reminded": 0,
        }  # fresh: nothing to do
        assert [a[0] for a in assertions(owner_url, alice)].count(
            "VALID"
        ) >= 2  # a name and a birth date

        soon = swept(owner_url, now + timedelta(days=340))  # inside the 30 days before it lapses
        assert soon["expired"] == 0 and soon["reminded"] >= 1 and soon["stale"] == 0
        again = swept(owner_url, now + timedelta(days=340))
        assert (
            again["reminded"] >= 1
        )  # the alert folds by person and claim; asking twice is harmless
        assert all(
            s == "VALID" for s, _ in assertions(owner_url, alice)
        )  # a reminder changes nothing about the assertion

        gone = swept(owner_url, now + timedelta(days=400))
        assert gone["expired"] >= 2 and gone["reminded"] == 0
        assert {s for s, _ in assertions(owner_url, alice)} == {"EXPIRED"}
        assert swept(owner_url, now + timedelta(days=401))["expired"] == 0  # once

        # a second verification, then the provider it rests on stops being trusted
        verified_session(http, svc, pid, alice, chip)

        async def distrust() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                tenant = (
                    await c.execute(
                        text(
                            "SELECT tenant_id FROM verify.verification_assertion WHERE subject_principal_id = :s LIMIT 1"
                        ),
                        {"s": uuid.UUID(alice)},
                    )
                ).scalar_one()
                await c.execute(
                    text(
                        "INSERT INTO verify.provider_adapter (adapter_id, tenant_id, provider, mapping_version, submit_endpoint, callback_jwks, "
                        "equivalence_group, supported_evidence, jurisdictions, status) VALUES ('self-run', :t, 'self-run', 'v1', 'https://x.example', "
                        "CAST('{\"keys\": []}' AS jsonb), 'g', ARRAY['GOVERNMENT_ID'], ARRAY['GB'], 'QUARANTINED')"
                    ),
                    {"t": tenant},
                )

        asyncio.run(distrust())
        stale = swept(owner_url, now)
        assert stale["stale"] >= 2 and stale["expired"] == 0
        latest = [a for a in assertions(owner_url, alice) if a[0] == "STALE"]
        assert latest and all("PROVIDER_NO_LONGER_TRUSTED" in reasons for _, reasons in latest)
