"""Persistence behind one interface: an in-memory store (local, tests) and a Postgres store (the `public_assistant` schema).

Both hold metadata only, except `intake_request.encrypted_fields`, which exists only for a consented, separate submission.
Visitor chat text and model output are never passed to a store (RET-01).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@dataclass(slots=True)
class IntakeRow:
    request_id: str
    purpose: str
    country_code: str
    schema_rev: str
    notice_rev: str
    idempotency_key: str
    payload_digest: str
    encrypted_fields: bytes | None
    reference: str
    status: str = "ACCEPTED"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class OutboxRow:
    outbox_id: str
    request_id: str
    target: str
    adapter_rev: str
    state: str = "PENDING"
    attempts: int = 0
    next_attempt_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_error_code: str | None = None


class Store(Protocol):
    async def ready(self) -> bool: ...
    async def record_feedback(
        self, response_id: str, rating: str, intent_id: str | None, snapshot_id: str
    ) -> None: ...
    async def record_metric(
        self,
        intent_id: str | None,
        decision: str,
        reason: str,
        route: str,
        ai_used: bool,
        latency_ms: int,
        snapshot_id: str,
    ) -> None: ...
    async def record_safety(self, category: str, route: str) -> None: ...
    async def audit(
        self,
        event_type: str,
        purpose: str | None,
        policy_rev: str,
        correlation_id: str | None,
        outcome: str,
    ) -> None: ...
    async def intake_by_key(self, key: str) -> IntakeRow | None: ...
    async def create_intake(self, row: IntakeRow, outbox: OutboxRow) -> None: ...
    async def due_outbox(self, now: datetime, limit: int) -> list[OutboxRow]: ...
    async def intake_fields(self, request_id: str) -> tuple[str, str, bytes] | None: ...
    async def mark_delivered(
        self,
        outbox_id: str,
        request_id: str,
        recipient_ref: str,
        code: int,
        correlation_id: str | None,
    ) -> None: ...
    async def mark_failed(
        self,
        outbox_id: str,
        request_id: str,
        state: str,
        attempts: int,
        next_at: datetime | None,
        error: str,
    ) -> None: ...
    async def record_marketing(
        self, channel: str, country_code: str, notice_rev: str, choice: bool
    ) -> None: ...
    async def erase_intake(self, reference: str) -> bool: ...
    async def purge(
        self, now: datetime, feedback_days: int, safety_days: int
    ) -> dict[str, int]: ...
    async def counts(self) -> dict[str, int]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.feedback: list[dict[str, Any]] = []
        self.metrics: list[dict[str, Any]] = []
        self.safety: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.intakes: dict[str, IntakeRow] = {}
        self.outbox: dict[str, OutboxRow] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.marketing: list[dict[str, Any]] = []

    async def ready(self) -> bool:
        return True

    async def record_feedback(
        self, response_id: str, rating: str, intent_id: str | None, snapshot_id: str
    ) -> None:
        self.feedback.append(
            {
                "response_id": response_id,
                "rating": rating,
                "intent_id": intent_id,
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(UTC),
            }
        )

    async def record_metric(
        self,
        intent_id: str | None,
        decision: str,
        reason: str,
        route: str,
        ai_used: bool,
        latency_ms: int,
        snapshot_id: str,
    ) -> None:
        self.metrics.append(
            {
                "intent_id": intent_id,
                "decision": decision,
                "reason": reason,
                "route": route,
                "ai_used": ai_used,
                "latency_ms": latency_ms,
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(UTC),
            }
        )

    async def record_safety(self, category: str, route: str) -> None:
        self.safety.append({"category": category, "route": route, "created_at": datetime.now(UTC)})

    async def audit(
        self,
        event_type: str,
        purpose: str | None,
        policy_rev: str,
        correlation_id: str | None,
        outcome: str,
    ) -> None:
        self.audits.append(
            {
                "event_type": event_type,
                "purpose": purpose,
                "policy_rev": policy_rev,
                "correlation_id": correlation_id,
                "outcome": outcome,
                "created_at": datetime.now(UTC),
            }
        )

    async def intake_by_key(self, key: str) -> IntakeRow | None:
        return next((r for r in self.intakes.values() if r.idempotency_key == key), None)

    async def create_intake(self, row: IntakeRow, outbox: OutboxRow) -> None:
        self.intakes[row.request_id] = row
        self.outbox[outbox.outbox_id] = outbox

    async def due_outbox(self, now: datetime, limit: int) -> list[OutboxRow]:
        due = [
            o
            for o in self.outbox.values()
            if o.state in ("PENDING", "RETRYING") and o.next_attempt_at <= now
        ]
        return due[:limit]

    async def intake_fields(self, request_id: str) -> tuple[str, str, bytes] | None:
        r = self.intakes.get(request_id)
        if r is None or r.encrypted_fields is None:
            return None
        return r.purpose, r.country_code, r.encrypted_fields

    async def mark_delivered(
        self,
        outbox_id: str,
        request_id: str,
        recipient_ref: str,
        code: int,
        correlation_id: str | None,
    ) -> None:
        self.outbox[outbox_id].state = "DELIVERED"
        if self.intakes[request_id].status != "ERASED":
            self.intakes[request_id].status = "DELIVERED"
        self.receipts[request_id] = {
            "recipient_ref": recipient_ref,
            "response_code": code,
            "correlation_id": correlation_id,
            "accepted_at": datetime.now(UTC),
        }

    async def mark_failed(
        self,
        outbox_id: str,
        request_id: str,
        state: str,
        attempts: int,
        next_at: datetime | None,
        error: str,
    ) -> None:
        o = self.outbox[outbox_id]
        o.state, o.attempts, o.last_error_code = state, attempts, error
        if next_at is not None:
            o.next_attempt_at = next_at
        if (
            self.intakes[request_id].status != "ERASED"
        ):  # an erasure is final: a later delivery outcome never overwrites it
            self.intakes[request_id].status = {
                "RETRYING": "RETRYING",
                "REJECTED": "REJECTED",
                "DEAD_LETTER": "DEAD_LETTER",
            }[state]

    async def record_marketing(
        self, channel: str, country_code: str, notice_rev: str, choice: bool
    ) -> None:
        self.marketing.append(
            {
                "channel": channel,
                "country_code": country_code,
                "notice_rev": notice_rev,
                "choice": choice,
                "created_at": datetime.now(UTC),
            }
        )

    async def erase_intake(self, reference: str) -> bool:
        for r in self.intakes.values():
            if r.reference == reference:
                r.encrypted_fields, r.status = None, "ERASED"
                return True
        return False

    async def purge(self, now: datetime, feedback_days: int, safety_days: int) -> dict[str, int]:
        fb_cut, sf_cut = now - timedelta(days=feedback_days), now - timedelta(days=safety_days)
        before = (len(self.feedback), len(self.metrics), len(self.safety))
        self.feedback = [r for r in self.feedback if r["created_at"] >= fb_cut]
        self.metrics = [r for r in self.metrics if r["created_at"] >= fb_cut]
        self.safety = [r for r in self.safety if r["created_at"] >= sf_cut]
        return {
            "feedback": before[0] - len(self.feedback),
            "metrics": before[1] - len(self.metrics),
            "safety": before[2] - len(self.safety),
        }

    async def counts(self) -> dict[str, int]:
        return {
            "feedback": len(self.feedback),
            "metrics": len(self.metrics),
            "safety": len(self.safety),
            "intake": len(self.intakes),
            "outbox": len(self.outbox),
        }


class SqlStore:
    """Postgres store. Connects as the least-privilege `zid_assistant` runtime role (no access to any platform schema)."""

    def __init__(self, url: str) -> None:
        self._e: AsyncEngine = create_async_engine(
            url, pool_size=5, max_overflow=5, pool_pre_ping=True
        )

    async def dispose(self) -> None:
        await self._e.dispose()

    async def ready(self) -> bool:
        try:
            async with self._e.connect() as c:
                await c.execute(text("SELECT 1 FROM public_assistant.schema_version LIMIT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _exec(self, sql: str, params: dict[str, Any]) -> None:
        async with self._e.begin() as c:
            await c.execute(text(sql), params)

    async def record_feedback(
        self, response_id: str, rating: str, intent_id: str | None, snapshot_id: str
    ) -> None:
        await self._exec(
            "INSERT INTO public_assistant.feedback_event (feedback_id, response_id, rating, intent_id, snapshot_id) VALUES (:i, :r, :g, :n, :s)",
            {"i": uuid.uuid4(), "r": response_id, "g": rating, "n": intent_id, "s": snapshot_id},
        )

    async def record_metric(
        self,
        intent_id: str | None,
        decision: str,
        reason: str,
        route: str,
        ai_used: bool,
        latency_ms: int,
        snapshot_id: str,
    ) -> None:
        await self._exec(
            "INSERT INTO public_assistant.message_metric (metric_id, intent_id, decision, reason, route, ai_used, latency_ms, snapshot_id) VALUES (:i, :n, :d, :r, :o, :a, :l, :s)",
            {
                "i": uuid.uuid4(),
                "n": intent_id,
                "d": decision,
                "r": reason,
                "o": route,
                "a": ai_used,
                "l": latency_ms,
                "s": snapshot_id,
            },
        )

    async def record_safety(self, category: str, route: str) -> None:
        await self._exec(
            "INSERT INTO public_assistant.safety_event (event_id, category, route) VALUES (:i, :c, :r)",
            {"i": uuid.uuid4(), "c": category[:40], "r": route[:40]},
        )

    async def audit(
        self,
        event_type: str,
        purpose: str | None,
        policy_rev: str,
        correlation_id: str | None,
        outcome: str,
    ) -> None:
        await self._exec(
            "INSERT INTO public_assistant.audit_event (event_id, event_type, purpose, policy_rev, correlation_id, outcome) VALUES (:i, :t, :p, :r, :c, :o)",
            {
                "i": uuid.uuid4(),
                "t": event_type,
                "p": purpose,
                "r": policy_rev,
                "c": correlation_id,
                "o": outcome,
            },
        )

    async def intake_by_key(self, key: str) -> IntakeRow | None:
        async with self._e.connect() as c:
            r = (
                await c.execute(
                    text(
                        "SELECT request_id, purpose, country_code, schema_rev, notice_rev, idempotency_key, payload_digest, reference, status, created_at FROM public_assistant.intake_request WHERE idempotency_key = :k"
                    ),
                    {"k": key},
                )
            ).first()
        if r is None:
            return None
        return IntakeRow(str(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], None, r[7], r[8], r[9])

    async def create_intake(self, row: IntakeRow, outbox: OutboxRow) -> None:
        async with (
            self._e.begin() as c
        ):  # one transaction: the intake and its outbox event are durable together
            await c.execute(
                text(
                    "INSERT INTO public_assistant.intake_request (request_id, purpose, country_code, schema_rev, notice_rev, idempotency_key, payload_digest, encrypted_fields, reference, status) VALUES (:id, :p, :c, :s, :n, :k, :d, :f, :r, 'QUEUED')"
                ),
                {
                    "id": uuid.UUID(row.request_id),
                    "p": row.purpose,
                    "c": row.country_code,
                    "s": row.schema_rev,
                    "n": row.notice_rev,
                    "k": row.idempotency_key,
                    "d": row.payload_digest,
                    "f": row.encrypted_fields,
                    "r": row.reference,
                },
            )
            await c.execute(
                text(
                    "INSERT INTO public_assistant.delivery_outbox (outbox_id, request_id, target, adapter_rev, state) VALUES (:o, :r, :t, :a, 'PENDING')"
                ),
                {
                    "o": uuid.UUID(outbox.outbox_id),
                    "r": uuid.UUID(outbox.request_id),
                    "t": outbox.target,
                    "a": outbox.adapter_rev,
                },
            )

    async def due_outbox(self, now: datetime, limit: int) -> list[OutboxRow]:
        async with self._e.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT outbox_id, request_id, target, adapter_rev, state, attempts, next_attempt_at, last_error_code FROM public_assistant.delivery_outbox WHERE state IN ('PENDING','RETRYING') AND next_attempt_at <= :n ORDER BY next_attempt_at LIMIT :l"
                    ),
                    {"n": now, "l": limit},
                )
            ).all()
        return [OutboxRow(str(r[0]), str(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows]

    async def intake_fields(self, request_id: str) -> tuple[str, str, bytes] | None:
        async with self._e.connect() as c:
            r = (
                await c.execute(
                    text(
                        "SELECT purpose, country_code, encrypted_fields FROM public_assistant.intake_request WHERE request_id = :i"
                    ),
                    {"i": uuid.UUID(request_id)},
                )
            ).first()
        if r is None or r[2] is None:
            return None
        return r[0], r[1], bytes(r[2])

    async def mark_delivered(
        self,
        outbox_id: str,
        request_id: str,
        recipient_ref: str,
        code: int,
        correlation_id: str | None,
    ) -> None:
        async with self._e.begin() as c:
            await c.execute(
                text(
                    "UPDATE public_assistant.delivery_outbox SET state='DELIVERED', updated_at=now() WHERE outbox_id=:o"
                ),
                {"o": uuid.UUID(outbox_id)},
            )
            await c.execute(
                text(
                    "UPDATE public_assistant.intake_request SET status='DELIVERED', updated_at=now() WHERE request_id=:r AND status <> 'ERASED'"
                ),
                {"r": uuid.UUID(request_id)},
            )
            await c.execute(
                text(
                    "INSERT INTO public_assistant.receiver_receipt (request_id, recipient_ref, accepted_at, response_code, correlation_id) VALUES (:r, :f, now(), :c, :k) ON CONFLICT (request_id) DO NOTHING"
                ),
                {"r": uuid.UUID(request_id), "f": recipient_ref, "c": code, "k": correlation_id},
            )

    async def mark_failed(
        self,
        outbox_id: str,
        request_id: str,
        state: str,
        attempts: int,
        next_at: datetime | None,
        error: str,
    ) -> None:
        async with self._e.begin() as c:
            await c.execute(
                text(
                    "UPDATE public_assistant.delivery_outbox SET state=:s, attempts=:a, next_attempt_at=COALESCE(:n, next_attempt_at), last_error_code=:e, updated_at=now() WHERE outbox_id=:o"
                ),
                {
                    "s": state,
                    "a": attempts,
                    "n": next_at,
                    "e": error[:60],
                    "o": uuid.UUID(outbox_id),
                },
            )
            await c.execute(
                text(
                    "UPDATE public_assistant.intake_request SET status=:s, updated_at=now() WHERE request_id=:r AND status <> 'ERASED'"
                ),
                {"s": state, "r": uuid.UUID(request_id)},
            )

    async def record_marketing(
        self, channel: str, country_code: str, notice_rev: str, choice: bool
    ) -> None:
        await self._exec(
            "INSERT INTO public_assistant.marketing_evidence (evidence_id, channel, country_code, notice_rev, choice) VALUES (:i, :c, :k, :n, :h)",
            {"i": uuid.uuid4(), "c": channel, "k": country_code, "n": notice_rev, "h": choice},
        )

    async def erase_intake(self, reference: str) -> bool:
        async with self._e.begin() as c:
            res = await c.execute(
                text(
                    "UPDATE public_assistant.intake_request SET encrypted_fields = NULL, status = 'ERASED', updated_at = now() WHERE reference = :r"
                ),
                {"r": reference},
            )
        return (res.rowcount or 0) > 0

    async def purge(self, now: datetime, feedback_days: int, safety_days: int) -> dict[str, int]:
        out: dict[str, int] = {}
        async with self._e.begin() as c:
            for key, table, days in (
                ("feedback", "feedback_event", feedback_days),
                ("metrics", "message_metric", feedback_days),
                ("safety", "safety_event", safety_days),
            ):
                r = await c.execute(
                    text(f"DELETE FROM public_assistant.{table} WHERE created_at < :c"),
                    {"c": now - timedelta(days=days)},
                )
                out[key] = r.rowcount or 0
        return out

    async def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        async with self._e.connect() as c:
            for key, table in (
                ("feedback", "feedback_event"),
                ("metrics", "message_metric"),
                ("safety", "safety_event"),
                ("intake", "intake_request"),
                ("outbox", "delivery_outbox"),
            ):
                out[key] = int(
                    (
                        await c.execute(text(f"SELECT count(*) FROM public_assistant.{table}"))
                    ).scalar_one()
                )
        return out


def json_dumps(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)
