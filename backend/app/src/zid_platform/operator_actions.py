"""The operator-plane action wrapper shared by the regulatory APIs (ZID-RCG-003; CR-067 to CR-075).

An operator action is: the operator gate (done by the route's dependency), one operator capability, the change, and one protected audit record in the
platform partition, all in ONE transaction. If anything is refused (no capability, a domain rule, the database), the attempt rolls back and the refusal
is recorded in a transaction of its own, then raised. Records carry ids, codes, roles and hashes, never the subject matter.

It lives beside `country_gate.py`, outside the modules, so that a module's operator routes can use it without importing `operator_access` internals.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import emit

from zid_platform.api_auth import request_engine
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.operator_access.ports import has_capability


async def announce(
    conn: Any,
    *,
    name: str,
    tenant_id: str,
    aggregate_type: str,
    aggregate_id: str,
    data: dict[str, Any],
    occurred_at: datetime,
) -> str:
    """One outbox event for a platform change. Versions rise per aggregate, so the same record announced twice takes the next number."""
    version = (
        await conn.execute(
            text(
                "SELECT coalesce(max(aggregate_version), 0) + 1 FROM events.outbox_event "
                "WHERE aggregate_type = :t AND aggregate_id = :i"
            ),
            {"t": aggregate_type, "i": aggregate_id},
        )
    ).scalar_one()
    return await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=int(version),
        data=data,
        occurred_at=occurred_at,
    )


# work(conn, now) -> (response, audited target, audited details)
Work = Callable[[Any, datetime], Awaitable[tuple[dict[str, Any], str, dict[str, Any]]]]


class Actions:
    def __init__(
        self,
        *,
        namespace: uuid.UUID,
        refused_event: str,
        status: Mapping[str, int],
        errors: Callable[[], tuple[type[BaseException], ...]],
        refused_code: str,
        capability_hint: str,
    ) -> None:
        self._namespace = namespace
        self._refused_event = refused_event
        self._status = status
        self._errors = errors  # a callable, so a lazily imported module's error classes are looked up on first use
        self._refused_code = refused_code
        self._hint = capability_hint

    def fail(self, e: Any) -> ZidError:
        text = str(e)
        detail = text.split(": ", 1)[1] if ": " in text else ""
        return ZidError(
            e.code,
            detail or e.code.replace("_", " ").capitalize() + ".",
            http_status=self._status.get(e.code, 422),
        )

    @staticmethod
    def denied(capability: str) -> ZidError:
        return ZidError(
            "OPERATOR_CAPABILITY_REQUIRED",
            f"This needs an approved, unexpired {capability} grant.",
            http_status=403,
        )

    async def need(self, conn: Any, op: Any, capability: str, resource: str) -> None:
        if not await has_capability(
            conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
        ):
            raise self.denied(capability)

    async def need_any(
        self, conn: Any, op: Any, capabilities: tuple[str, ...], resource: str
    ) -> None:
        for capability in capabilities:
            if await has_capability(
                conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
            ):
                return
        raise ZidError("OPERATOR_CAPABILITY_REQUIRED", f"This needs {self._hint}.", http_status=403)

    async def audit(
        self,
        conn: Any,
        op: Any,
        *,
        event: str,
        action: str,
        target_type: str,
        target: str,
        outcome: str,
        now: datetime,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        once: bool = True,
    ) -> None:
        """One protected audit record. A success record is idempotent per event, target, role and gate, so a retried request never doubles it;
        a refusal is its own record every time."""
        facts = details or {}
        key = f"{event}|{target}|{facts.get('role', '')}|{facts.get('gate', '')}"
        await record_audit(
            conn,
            None,
            AuditFacts(
                event_type=event,
                actor_type="OPERATOR",
                action=action,
                target_type=target_type,
                target_ref=target,
                outcome=outcome,
                evidence_class=EvidenceClass.E3,
                occurred_at=now,
                actor_principal_id=str(op.principal_id),
                reason_code=reason,
                source_service="operator_access",
                source_event_id=str(uuid.uuid5(self._namespace, key))
                if once
                else str(uuid.uuid4()),
                details=facts,
            ),
        )

    async def act(
        self,
        request: Request,
        op: Any,
        *,
        event: str,
        action: str,
        capability: str,
        target_type: str,
        target: str,
        work: Work,
        role: str | None = None,
        publish: str | None = None,
    ) -> dict[str, Any]:
        """`publish` names the outbox event announcing the change to other systems (ZID-RCG-003 section 09.2), written in the same transaction
        as the change and its audit record, under the operator tenant, with the audited ids, codes and hashes only."""
        now = datetime.now(UTC)
        refusal: ZidError
        code: str
        try:
            async with tenant_transaction(
                request_engine(request), request.state.operator_ctx
            ) as conn:
                await self.need(conn, op, capability, f"regulatory:{target}")
                response, audited, details = await work(conn, now)
                if role:
                    details["role"] = role
                await self.audit(
                    conn,
                    op,
                    event=event,
                    action=action,
                    target_type=target_type,
                    target=audited,
                    outcome="SUCCESS",
                    now=now,
                    details=details,
                )
                if publish:
                    await announce(
                        conn,
                        name=publish,
                        tenant_id=str(request.state.operator_ctx.tenant_id),
                        aggregate_type=target_type,
                        aggregate_id=audited,
                        data={"target": audited, **details},
                        occurred_at=now,
                    )
                return response
        except self._errors() as e:
            refusal, code = self.fail(e), str(getattr(e, "code", "REFUSED"))
        except DBAPIError:
            refusal, code = (
                ZidError(
                    self._refused_code,
                    "The database refused the change; nothing was stored.",
                    http_status=409,
                ),
                self._refused_code,
            )
        except ZidError as e:
            refusal, code = e, e.code
        attempted: dict[str, Any] = {"attempted": event}
        if role:
            attempted["role"] = role
        async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
            await self.audit(
                conn,
                op,
                event=self._refused_event,
                action=action,
                target_type=target_type,
                target=target,
                outcome="DENIED",
                now=now,
                reason=code,
                details=attempted,
                once=False,
            )
        raise refusal
