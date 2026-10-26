"""Privacy state after a restore (ZID-PRIV-001 DR; PRIV-32). The database came back from a backup that may predate
erasures, holds and opt-outs. The durable event journal (the broker's retained events) is the record that outlives
the backup, so before normal service resumes it is replayed against the restored state:

- every erasure in the journal is applied again (idempotent: an erased subject stays erased, a subject the backup
  brought back is crypto-erased again);
- every opt-out and restriction preference is set to its last journalled value;
- every hold the journal says is active must exist and be active. A hold cannot be recreated without its approvals,
  so a missing one is reported and keeps processing gated until a person re-creates it.

`service.RESTORE.pending` gates `check_processing` from the moment the restore procedure sets it until this
reconciliation finds nothing outstanding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import current_runtime, erase_subject_ref, hold_status
from zid_platform.modules.privacy.application import service
from zid_platform.modules.privacy.infrastructure import store

Entry = Mapping[str, Any]  # {"kind": ERASURE|PREFERENCE|HOLD, "tenant_id": ..., ...}


@dataclass(slots=True)
class Reconciliation:
    erasures_applied: int = 0
    erasures_already_done: int = 0
    preferences_restored: int = 0
    holds_ok: int = 0
    holds_missing: list[str] = field(
        default_factory=list
    )  # a person must re-create these, with approval

    @property
    def ready(self) -> bool:
        return not self.holds_missing


def mark_restored() -> None:
    """Called by the restore procedure as soon as the database is back: gate processing until reconciled."""
    service.RESTORE.pending = True


async def reconcile_after_restore(
    conn: AsyncConnection, journal: Iterable[Entry], *, now: datetime
) -> Reconciliation:
    """Replay the journal (oldest first) against the restored state, as the BYPASSRLS worker."""
    report = Reconciliation()
    last_pref: dict[tuple[str, str, str], bool] = {}
    for e in journal:
        tenant = str(e["tenant_id"])
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        kind = e["kind"]
        if kind == "ERASURE":
            res = await erase_subject_ref(
                conn,
                tenant,
                str(e["subject_ref"]),
                request_ref=f"restore:{e['request_id']}",
                now=now,
                rt=current_runtime(),
            )
            if res.outcome == "NOTHING_TO_ERASE":
                report.erasures_already_done += 1
            else:
                report.erasures_applied += 1
        elif kind == "PREFERENCE":
            last_pref[(tenant, str(e["subject_principal_id"]), str(e["preference_type"]))] = bool(
                e["value"]
            )
        elif kind == "HOLD":
            if await hold_status(conn, str(e["legal_hold_id"])) == "ACTIVE":
                report.holds_ok += 1
            else:
                report.holds_missing.append(str(e["legal_hold_id"]))
    for (tenant, subject, ptype), value in last_pref.items():
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        current = {p.preference_type: p.value for p in await store.preferences_of(conn, subject)}
        if current.get(ptype) != value:
            await service.set_preference(
                conn, tenant, subject, ptype, value, source="RIGHTS_REQUEST"
            )
            report.preferences_restored += 1
    if report.ready:
        service.RESTORE.pending = False
    return report
