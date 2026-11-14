"""T42 on PostgreSQL: an issuer or accreditation body that loses its status flags the proofs issued under it for review and makes their source
recheck due; consumers are told once per proof, the tenant gets one mandatory alert, and a repeat pass does nothing."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_credential_trust_pg import check, license_claim
from test_credential_trust_pg import gate as claim_gate
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain.issuer_history import Kind, Statement
from zid_platform.modules.credential.infrastructure import issuer_history_store
from zid_platform.modules.credential.infrastructure.registries import current_issuer_registry


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    """The event data, whether the stored payload wraps it or is it."""
    return dict(payload.get("data", payload))


def _statement(issuer_id: str, seq: int, record: dict[str, Any], *, ago: timedelta) -> Statement:
    return Statement(
        statement_id=f"s-{issuer_id}-{seq}",
        issuer_id=issuer_id,
        seq=seq,
        kind=Kind.UPDATE,
        reason=None,
        record=record,
        source_url="https://regulator.example.test/board",
        source_hash="e" * 64,
        evidence_locator="vault:issuers/x",
        recorded_by="reg-ops",
        recorded_at=datetime.now(UTC) - ago,
    )


def _issuer(issuer_id: str, name: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "issuer_id": issuer_id,
        "kind": kind,
        "authority": "OFFICIAL",
        "countries": ["US"],
        "claim_types": ["PROFESSIONAL_LICENSE"] if kind == "REGULATOR" else [],
        "names": [{"name": name}],
        **extra,
    }


def test_t42_a_body_that_loses_its_status_flags_the_proofs_under_it_and_schedules_their_recheck(
    rt_url: str,
) -> None:
    tag = uuid.uuid4().hex[:8]
    body_id, board_id, other_id = f"acc-{tag}", f"board-{tag}", f"other-{tag}"
    board_name, other_name = f"Reviewed Board {tag}", f"Untouched Board {tag}"
    today = datetime.now(UTC).date().isoformat()
    tenant = str(uuid.uuid4())
    accredited = {"accreditations": [{"body": body_id, "from": "2020-01-01"}]}

    async def run(work: Any) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await work(c)

    async def record(c: Any, statement: Statement) -> None:
        await issuer_history_store.record(c, statement, now=datetime.now(UTC))

    async def prove(c: Any, name: str) -> str:
        registry = await current_issuer_registry(c)
        result = await check(
            c,
            tenant,
            str(
                uuid.uuid4()
            ),  # one proof per subject: a second check of the same claim would supersede the first
            claimed=license_claim(tag=uuid.uuid4().hex[:6], regulator=name),
            gate=claim_gate(registry=registry),
        )
        assert result.assertion.decision.value == "VERIFIED", result.assertion.reason_codes
        return str(result.check_id)

    async def monitor(c: Any, check_id: str) -> dict[str, Any]:
        row = (
            await c.execute(
                text(
                    "SELECT review_statement_id, review_reason, next_check_at FROM cred.claim_monitor WHERE check_id = :c"
                ),
                {"c": uuid.UUID(check_id)},
            )
        ).one()
        return dict(row._mapping)

    async def events(c: Any, kind: str) -> list[dict[str, Any]]:
        rows = (
            await c.execute(
                text(
                    "SELECT payload::text FROM events.outbox_event WHERE event_type LIKE :k ORDER BY created_at"
                ),
                {"k": f"%{kind}%"},
            )
        ).all()
        return [_data(json.loads(r[0])) for r in rows]

    async def setup(c: Any) -> tuple[str, str]:
        three_hours = timedelta(hours=3)
        await record(
            c,
            _statement(body_id, 1, _issuer(body_id, f"Body {tag}", "ACCREDITOR"), ago=three_hours),
        )
        await record(
            c,
            _statement(
                board_id,
                1,
                _issuer(board_id, board_name, "REGULATOR", **accredited),
                ago=three_hours,
            ),
        )
        await record(
            c, _statement(other_id, 1, _issuer(other_id, other_name, "REGULATOR"), ago=three_hours)
        )
        return await prove(c, board_name), await prove(c, other_name)

    under_body, unrelated = asyncio.run(run(setup))

    async def nothing_lost_yet(c: Any) -> None:
        await svc.review_proofs_of_lost_issuers(c, datetime.now(UTC))
        assert (await monitor(c, under_body))["review_statement_id"] is None
        assert (await monitor(c, unrelated))["review_statement_id"] is None

    asyncio.run(run(nothing_lost_yet))

    async def lose(c: Any) -> dict[str, int]:
        # the accreditation body is revoked as of today: a status LOST going forward, not an edit of the past
        revoked = _issuer(body_id, f"Body {tag}", "ACCREDITOR", revoked_on=today)
        await record(c, _statement(body_id, 2, revoked, ago=timedelta(minutes=1)))
        return await svc.review_proofs_of_lost_issuers(c, datetime.now(UTC))

    first = asyncio.run(run(lose))
    assert first["issuers_lost"] >= 1 and first["proofs_flagged"] >= 1

    async def after(c: Any) -> None:
        flagged = await monitor(c, under_body)
        assert flagged["review_statement_id"] == f"s-{body_id}-2"
        assert flagged["review_reason"] == "ISSUER_REVOKED"
        assert flagged["next_check_at"] <= datetime.now(UTC)  # the source recheck is due now
        assert (await monitor(c, unrelated))[
            "review_statement_id"
        ] is None  # an unrelated proof is left alone

        told = await events(c, "proof.review_required")
        mine = [e for e in told if e["check_id"] == under_body]
        assert len(mine) == 1
        assert mine[0]["issuer_id"] == body_id and mine[0]["reasons"] == ["ISSUER_REVOKED"]
        assert not [e for e in told if e["check_id"] == unrelated]

        alerts = (
            await c.execute(
                text(
                    "SELECT severity, mandatory, body FROM platform.notification_request WHERE correlation_key = :k"
                ),
                {"k": f"credential.proof.review:{body_id}:s-{body_id}-2"},
            )
        ).all()
        assert len(alerts) == 1 and alerts[0].severity == "HIGH" and alerts[0].mandatory
        assert board_name not in alerts[0].body  # ids and codes only

        # the daily recheck pass announces the proof with the reason
        assert await svc.recheck_due(c, datetime.now(UTC)) >= 1
        due = [e for e in await events(c, "claim.recheck.due") if e["check_id"] == under_body]
        assert due and due[-1]["reason"] == "ISSUER_REVOKED"

        # a repeat pass flags and announces nothing more for the same statement
        again = await svc.review_proofs_of_lost_issuers(c, datetime.now(UTC))
        assert again["proofs_flagged"] == 0
        told_again = await events(c, "proof.review_required")
        assert len([e for e in told_again if e["check_id"] == under_body]) == 1

    asyncio.run(run(after))
