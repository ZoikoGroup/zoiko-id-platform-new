"""PostgreSQL access for routing: capabilities, decisions, attempt events, releases (ZID-CP-WF09; CR-171). Constant SQL only; every value is a bound parameter."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

# ------------------------------------------------------------------------------------------------------------------- capabilities


async def insert_capability(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO verify.route_capability (provider_id, method, version, state, manifest, manifest_digest, qualified_until, contract_until, legal_determination_ref,
                legal_determination_valid_until, proposed_by, approved_by, reason)
            VALUES (:provider_id, :method, :version, :state, CAST(:manifest AS jsonb), :manifest_digest, :qualified_until, :contract_until, :legal_determination_ref,
                :legal_determination_valid_until, :proposed_by, :approved_by, :reason)
        """),
        {**v, "manifest": json.dumps(v["manifest"])},
    )


async def latest_capabilities(
    conn: AsyncConnection, provider_id: str | None = None
) -> list[Row[Any]]:
    """The newest version of each provider and method."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT DISTINCT ON (provider_id, method) * FROM verify.route_capability
                    WHERE (CAST(:p AS text) IS NULL OR provider_id = :p)
                    ORDER BY provider_id, method, version DESC
                """),
                {"p": provider_id},
            )
        ).all()
    )


async def latest_capability(
    conn: AsyncConnection, provider_id: str, method: str
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM verify.route_capability WHERE provider_id = :p AND method = :m ORDER BY version DESC LIMIT 1"
            ),
            {"p": provider_id, "m": method},
        )
    ).first()


# ----------------------------------------------------------------------------------------------------------------------- decisions


async def insert_decision(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO verify.route_decision (decision_id, tenant_id, session_id, idempotency_key, route_version, algorithm_version, input_digest, snapshot_digest,
                decision_digest, selected, status, body)
            VALUES (:decision_id, :tenant_id, :session_id, :idempotency_key, :route_version, :algorithm_version, :input_digest, :snapshot_digest, :decision_digest,
                :selected, :status, CAST(:body AS jsonb))
        """),
        {
            **v,
            "decision_id": uuid.UUID(v["decision_id"]),
            "tenant_id": uuid.UUID(v["tenant_id"]),
            "body": json.dumps(v["body"]),
        },
    )


async def decision_by_key(conn: AsyncConnection, key: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM verify.route_decision WHERE idempotency_key = :k"), {"k": key}
        )
    ).first()


async def load_decision(conn: AsyncConnection, decision_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM verify.route_decision WHERE decision_id = :i"),
            {"i": uuid.UUID(decision_id)},
        )
    ).first()


async def decisions_for_session(conn: AsyncConnection, session_id: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM verify.route_decision WHERE session_id = :s ORDER BY created_at, decision_id"
                ),
                {"s": session_id},
            )
        ).all()
    )


# ---------------------------------------------------------------------------------------------------------------------- attempts


async def insert_event(conn: AsyncConnection, v: Mapping[str, Any]) -> bool:
    """False when it was the first event of an idempotency key that already exists (the caller returns the existing attempt)."""
    result = await conn.execute(
        text("""
            INSERT INTO verify.route_attempt_event (event_id, tenant_id, attempt_id, seq, decision_id, session_id, provider_id, idempotency_key, external_operation_id,
                from_state, to_state, actor, reason, expected_cost_minor, currency)
            VALUES (:event_id, :tenant_id, :attempt_id, :seq, :decision_id, :session_id, :provider_id, :idempotency_key, :external_operation_id, :from_state, :to_state,
                :actor, :reason, :expected_cost_minor, :currency)
            ON CONFLICT DO NOTHING
        """),
        {
            **v,
            "event_id": uuid.uuid4(),
            "tenant_id": uuid.UUID(v["tenant_id"]),
            "attempt_id": uuid.UUID(v["attempt_id"]),
            "decision_id": uuid.UUID(v["decision_id"]),
        },
    )
    return bool(result.rowcount)


async def first_event_for_key(
    conn: AsyncConnection, session_id: str, provider_id: str, key: str
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM verify.route_attempt_event WHERE session_id = :s AND provider_id = :p AND idempotency_key = :k AND seq = 1"
            ),
            {"s": session_id, "p": provider_id, "k": key},
        )
    ).first()


async def events_of(conn: AsyncConnection, attempt_id: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("SELECT * FROM verify.route_attempt_event WHERE attempt_id = :a ORDER BY seq"),
                {"a": uuid.UUID(attempt_id)},
            )
        ).all()
    )


async def attempts_of_decision(conn: AsyncConnection, decision_id: str) -> list[Row[Any]]:
    """The newest event of each attempt of a decision."""
    return list(
        (
            await conn.execute(
                text(
                    "SELECT DISTINCT ON (attempt_id) * FROM verify.route_attempt_event WHERE decision_id = :d ORDER BY attempt_id, seq DESC"
                ),
                {"d": uuid.UUID(decision_id)},
            )
        ).all()
    )


async def ledger_rows(conn: AsyncConnection, since: datetime | None = None) -> list[Row[Any]]:
    """The newest event of every attempt, for cost reconciliation."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT DISTINCT ON (attempt_id) attempt_id, provider_id, external_operation_id, to_state, expected_cost_minor, currency, created_at
                    FROM verify.route_attempt_event WHERE (CAST(:since AS timestamptz) IS NULL OR created_at >= :since) ORDER BY attempt_id, seq DESC
                """),
                {"since": since},
            )
        ).all()
    )


async def external_operation_of(conn: AsyncConnection, attempt_id: str) -> str | None:
    row = (
        await conn.execute(
            text(
                "SELECT external_operation_id FROM verify.route_attempt_event WHERE attempt_id = :a AND external_operation_id IS NOT NULL ORDER BY seq DESC LIMIT 1"
            ),
            {"a": uuid.UUID(attempt_id)},
        )
    ).first()
    return str(row.external_operation_id) if row else None


# ---------------------------------------------------------------------------------------------------------------------- releases


async def insert_release(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO verify.route_release (release_id, version, state, maker, risk, dependency_digest, dependencies, scope, preflight, reason, recorded_by)
            VALUES (:release_id, :version, :state, :maker, :risk, :dependency_digest, CAST(:dependencies AS jsonb), CAST(:scope AS jsonb), CAST(:preflight AS jsonb), :reason, :recorded_by)
        """),
        {
            **v,
            "release_id": uuid.UUID(v["release_id"]),
            "dependencies": json.dumps(v["dependencies"]),
            "scope": json.dumps(v["scope"]),
            "preflight": json.dumps(v["preflight"]) if v.get("preflight") is not None else None,
        },
    )


async def latest_release(conn: AsyncConnection, release_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM verify.route_release WHERE release_id = :r ORDER BY version DESC LIMIT 1"
            ),
            {"r": uuid.UUID(release_id)},
        )
    ).first()


async def list_releases(conn: AsyncConnection, limit: int = 50) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM (SELECT DISTINCT ON (release_id) * FROM verify.route_release ORDER BY release_id, version DESC) r ORDER BY recorded_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    )


async def live_release(conn: AsyncConnection) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM (SELECT DISTINCT ON (release_id) * FROM verify.route_release ORDER BY release_id, version DESC) r WHERE state = 'LIVE' ORDER BY recorded_at DESC LIMIT 1"
            )
        )
    ).first()


async def insert_approval(
    conn: AsyncConnection, release_id: str, approver: str, role: str, dependency_digest: str
) -> bool:
    result = await conn.execute(
        text(
            "INSERT INTO verify.route_release_approval (release_id, approver, role, dependency_digest) VALUES (:r, :a, :ro, :d) ON CONFLICT DO NOTHING"
        ),
        {"r": uuid.UUID(release_id), "a": approver, "ro": role, "d": dependency_digest},
    )
    return bool(result.rowcount)


async def approvals_of(conn: AsyncConnection, release_id: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM verify.route_release_approval WHERE release_id = :r ORDER BY recorded_at"
                ),
                {"r": uuid.UUID(release_id)},
            )
        ).all()
    )


# ---------------------------------------------------------------------------------------------------------- data flow and measured health


async def insert_data_flow(
    conn: AsyncConnection, provider_id: str, version: int, hops: Any, graph_digest: str, by: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.route_data_flow (provider_id, version, hops, graph_digest, recorded_by) VALUES (:p, :v, CAST(:h AS jsonb), :d, :b)"
        ),
        {"p": provider_id, "v": version, "h": json.dumps(hops), "d": graph_digest, "b": by},
    )


async def latest_data_flow(conn: AsyncConnection, provider_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM verify.route_data_flow WHERE provider_id = :p ORDER BY version DESC LIMIT 1"
            ),
            {"p": provider_id},
        )
    ).first()


async def dispatched_outcomes(
    conn: AsyncConnection, provider_id: str, since: datetime
) -> list[Row[Any]]:
    """The newest state and time of each attempt this provider was actually sent since `since` (the platform's own measurement of the provider)."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT DISTINCT ON (e.attempt_id) e.attempt_id, e.to_state, e.created_at
                    FROM verify.route_attempt_event e
                    WHERE e.provider_id = :p AND e.created_at >= :s
                      AND EXISTS (SELECT 1 FROM verify.route_attempt_event d WHERE d.attempt_id = e.attempt_id AND d.to_state = 'DISPATCHED')
                    ORDER BY e.attempt_id, e.seq DESC
                """),
                {"p": provider_id, "s": since},
            )
        ).all()
    )


async def recent_attempts(conn: AsyncConnection, provider_id: str, since: datetime) -> int:
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM verify.route_attempt_event WHERE provider_id = :p AND seq = 1 AND created_at >= :s"
            ),
            {"p": provider_id, "s": since},
        )
    ).one()
    return int(row.n)
