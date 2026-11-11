"""Verification case orchestration (ZID-INT-002; CR-071). The routes validate and authorize; this keeps the case and its tasks.

The case holds references only. When the last open task completes the case becomes COMPLETE, and a `verification.case.completed` event
(ids, states and outcomes: no claimed value, no document) is announced once.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.credential.domain import cases as cs
from zid_platform.modules.credential.infrastructure import case_store

_EVENT = "verification.case.completed"


async def open_case(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    purpose: str,
    country: str,
    subdivision: str | None,
    profile_id: str,
    profile_version: str,
    consent_ref: str | None,
    callback_endpoint_id: str | None,
    client_ref: str | None,
    checks: Sequence[str],
    policy_packs: list[dict[str, Any]],
    created_by: str,
    created_at: datetime,
    expires_at: datetime,
) -> str:
    case_id = str(uuid.uuid4())
    await case_store.insert_case(
        conn,
        case_id=case_id,
        tenant_id=tenant_id,
        subject=subject,
        purpose=purpose,
        country=country,
        subdivision=subdivision,
        profile_id=profile_id,
        profile_version=profile_version,
        consent_ref=consent_ref,
        callback_endpoint_id=callback_endpoint_id,
        client_ref=client_ref,
        policy_packs=policy_packs,
        created_by=created_by,
        created_at=created_at,
        expires_at=expires_at,
    )
    for kind in checks:
        await case_store.insert_task(conn, case_id=case_id, tenant_id=tenant_id, check_kind=kind)
    return case_id


async def settle(conn: AsyncConnection, case_id: str, now: datetime) -> dict[str, Any]:
    """Recompute the case from its tasks and record a change once. A COMPLETE case announces itself exactly once; a case whose time
    ran out with work open is EXPIRED. Returns the stored case row as it now stands."""
    case = await case_store.load_case(conn, case_id, lock=True)
    if case is None:
        raise cs.CaseError("CASE_NOT_FOUND")
    tasks = await case_store.load_tasks(conn, case_id)
    current = cs.CaseState(case["state"])
    new = cs.case_state(
        current,
        [cs.TaskState(t["state"]) for t in tasks],
        now=now,
        expires_at=case["expires_at"],
    )
    if new is not current:
        await case_store.close_case(conn, case_id, new.value, now)
        case = {**case, "state": new.value, "closed_at": now}
        if new is cs.CaseState.COMPLETE:
            await emit(
                conn,
                name=_EVENT,
                tenant_id=str(case["tenant_id"]),
                aggregate_type="verification_case",
                aggregate_id=case_id,
                aggregate_version=1,
                data={
                    "case_id": case_id,
                    "state": new.value,
                    "outcomes": {t["check_kind"]: t["outcome"] for t in tasks},
                },
            )
    return case


def case_view(
    case: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    *,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """The answer for a case: its state, and one entry per check. There is no overall pass, only the separate outcomes."""
    views = [
        cs.TaskView(
            t["check_kind"],
            cs.TaskState(t["state"]),
            t["outcome"],
            f"cred_{t['credential_check_id']}" if t["credential_check_id"] else None,
            f"ver_{t['identity_verification_id']}" if t["identity_verification_id"] else None,
        )
        for t in tasks
    ]
    cid = str(case["case_id"])
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "case_id": f"case_{cid}",
        "state": case["state"],
        "policy_pack": list(case["policy_packs"]),
        "requested_checks": [v.check for v in views],
        "tasks": [
            {
                "check": v.check,
                "state": v.state.value,
                "outcome": v.outcome,
                "credential_ref": v.credential_ref,
                "identity_verification_ref": v.identity_verification_ref,
            }
            for v in views
        ],
        "pending_work": cs.pending_work(views),
        "status_url": f"/v2/verifications/case_{cid}",
        "client_ref": case["client_ref"],
        "expires_at": case["expires_at"].isoformat(),
    }
