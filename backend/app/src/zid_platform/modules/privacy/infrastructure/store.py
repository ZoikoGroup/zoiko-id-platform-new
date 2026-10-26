"""PostgreSQL access for the privacy schema (ZID-PRIV-001; CR-013). Every statement runs under
tenant RLS; immutability and the role-map rule are enforced by triggers as well as here."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.domain.model import Activity, LawfulBasis, Purpose


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


# ------------------------------------------------------------------------------------ purposes


def _purpose(r: Row[Any]) -> Purpose:
    return Purpose(
        str(r.purpose_id),
        r.purpose_code,
        int(r.purpose_version),
        tuple(r.allowed_data_categories),
        tuple(r.allowed_recipient_classes),
        tuple(r.compatible_secondary_purposes),
        tuple(r.prohibited_uses),
        r.status,
    )


async def next_version(
    conn: AsyncConnection, table: str, code_col: str, code: str, ver_col: str
) -> int:
    v = (
        await conn.execute(
            text(
                f"SELECT coalesce(max({ver_col}), 0) + 1 FROM privacy.{table} WHERE {code_col} = :c"
            ),
            {"c": code},
        )
    ).scalar_one()
    return int(v)


async def insert_purpose(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO privacy.purpose (purpose_id, tenant_id, purpose_code, purpose_version,
                description, allowed_data_categories, allowed_recipient_classes,
                compatible_secondary_purposes, prohibited_uses, retention_policy_code, created_by)
            VALUES (:id, :t, :code, :ver, :desc, :cats, :recips, :compat, :prohibited, :ret, :by)
        """),
        {
            "id": _u(v["purpose_id"]),
            "t": _u(v["tenant_id"]),
            "code": v["purpose_code"],
            "ver": v["purpose_version"],
            "desc": v["description"],
            "cats": list(v["allowed_data_categories"]),
            "recips": list(v["allowed_recipient_classes"]),
            "compat": list(v["compatible_secondary_purposes"]),
            "prohibited": list(v["prohibited_uses"]),
            "ret": v["retention_policy_code"],
            "by": _u(v["created_by"]),
        },
    )


async def activate_purpose(
    conn: AsyncConnection, purpose_id: str, approver: str, now: datetime
) -> Row[Any] | None:
    """DRAFT -> ACTIVE (the previous ACTIVE version of the code is RETIRED first)."""
    row = (
        await conn.execute(
            text("SELECT * FROM privacy.purpose WHERE purpose_id = :id FOR UPDATE"),
            {"id": _u(purpose_id)},
        )
    ).first()
    if row is None or row.status != "DRAFT":
        return row
    await conn.execute(
        text(
            "UPDATE privacy.purpose SET status = 'RETIRED' WHERE purpose_code = :c AND status = 'ACTIVE'"
        ),
        {"c": row.purpose_code},
    )
    return (
        await conn.execute(
            text("""
                UPDATE privacy.purpose SET status = 'ACTIVE', approved_by = :a, activated_at = :now
                WHERE purpose_id = :id RETURNING *
            """),
            {"id": _u(purpose_id), "a": _u(approver), "now": now},
        )
    ).first()


async def active_purpose(conn: AsyncConnection, code: str) -> Purpose | None:
    row = (
        await conn.execute(
            text("SELECT * FROM privacy.purpose WHERE purpose_code = :c AND status = 'ACTIVE'"),
            {"c": code},
        )
    ).first()
    return _purpose(row) if row else None


async def list_purposes(conn: AsyncConnection, *, status: str | None) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.purpose WHERE (CAST(:s AS text) IS NULL OR status = :s) "
                    "ORDER BY purpose_code, purpose_version DESC"
                ),
                {"s": status},
            )
        ).all()
    )


# ------------------------------------------------------------------------ processing activities


def _activity(r: Row[Any]) -> Activity:
    return Activity(
        str(r.processing_activity_id),
        str(r.tenant_id),
        r.activity_code,
        int(r.version),
        tuple(r.purpose_codes),
        tuple(r.data_categories),
        LawfulBasis(r.lawful_basis),
        tuple(r.recipient_classes),
        r.jurisdiction_pack_id,
        r.residency_profile,
        r.status,
        bool(r.launch_flow),
    )


async def insert_activity(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO privacy.processing_activity (processing_activity_id, tenant_id,
                activity_code, version, name, purpose_codes, data_categories, data_subject_classes,
                lawful_basis, special_category_condition, source_classes, recipient_classes,
                retention_policy_code, residency_profile, jurisdiction_pack_id, dpia_ref,
                launch_flow, created_by)
            VALUES (:id, :t, :code, :ver, :name, :purposes, :cats, :subjects, :basis, :special,
                :sources, :recips, :ret, :res, :pack, :dpia, :launch, :by)
        """),
        {
            "id": _u(v["processing_activity_id"]),
            "t": _u(v["tenant_id"]),
            "code": v["activity_code"],
            "ver": v["version"],
            "name": v["name"],
            "purposes": list(v["purpose_codes"]),
            "cats": list(v["data_categories"]),
            "subjects": list(v["data_subject_classes"]),
            "basis": v["lawful_basis"],
            "special": v["special_category_condition"],
            "sources": list(v["source_classes"]),
            "recips": list(v["recipient_classes"]),
            "ret": v["retention_policy_code"],
            "res": v["residency_profile"],
            "pack": v["jurisdiction_pack_id"],
            "dpia": v["dpia_ref"],
            "launch": v["launch_flow"],
            "by": _u(v["created_by"]),
        },
    )


async def load_activity(
    conn: AsyncConnection, activity_id: str, *, lock: bool = False
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM privacy.processing_activity WHERE processing_activity_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": _u(activity_id)},
        )
    ).first()


async def set_activity_status(
    conn: AsyncConnection, activity_id: str, status: str, now: datetime
) -> Row[Any]:
    if status == "ACTIVE":
        row = await load_activity(conn, activity_id)
        assert row is not None
        await conn.execute(
            text("""
                UPDATE privacy.processing_activity SET status = 'RETIRED'
                WHERE activity_code = :c AND status = 'ACTIVE' AND processing_activity_id <> :id
            """),
            {"c": row.activity_code, "id": _u(activity_id)},
        )
    return (
        await conn.execute(
            text("""
                UPDATE privacy.processing_activity SET status = :s,
                    activated_at = CASE WHEN :s = 'ACTIVE' THEN CAST(:now AS timestamptz) ELSE activated_at END
                WHERE processing_activity_id = :id RETURNING *
            """),
            {"id": _u(activity_id), "s": status, "now": now},
        )
    ).one()


async def active_activity(conn: AsyncConnection, code: str) -> Activity | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.processing_activity WHERE activity_code = :c AND status = 'ACTIVE'"
            ),
            {"c": code},
        )
    ).first()
    return _activity(row) if row else None


async def list_activities(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.processing_activity ORDER BY activity_code, version DESC"
                )
            )
        ).all()
    )


# ------------------------------------------------------------------------------ role assignments


async def insert_assignment(conn: AsyncConnection, v: Mapping[str, Any]) -> Row[Any]:
    await conn.execute(
        text("""
            UPDATE privacy.processing_role_assignment SET status = 'SUPERSEDED'
            WHERE processing_activity_id = :a AND status = 'APPROVED'
        """),
        {"a": _u(v["processing_activity_id"])},
    )
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.processing_role_assignment (assignment_id, tenant_id,
                    processing_activity_id, data_controller_principal_id, zoiko_role,
                    processor_principal_ids, recipient_classes, jurisdiction_pack_id,
                    contract_or_dpa_ref, effective_from, valid_to, approval_ref, created_by,
                    approved_by)
                VALUES (:id, :t, :a, :controller, :role, :processors, :recips, :pack, :dpa, :from,
                    :to, :approval, :by, :approver)
                RETURNING *
            """),
            {
                "id": _u(v["assignment_id"]),
                "t": _u(v["tenant_id"]),
                "a": _u(v["processing_activity_id"]),
                "controller": _u(v["data_controller_principal_id"]),
                "role": v["zoiko_role"],
                "processors": [uuid.UUID(p) for p in v["processor_principal_ids"]],
                "recips": list(v["recipient_classes"]),
                "pack": v["jurisdiction_pack_id"],
                "dpa": v["contract_or_dpa_ref"],
                "from": v["effective_from"],
                "to": v["valid_to"],
                "approval": v["approval_ref"],
                "by": _u(v["created_by"]),
                "approver": _u(v["approved_by"]),
            },
        )
    ).one()


async def assignments(conn: AsyncConnection, activity_id: str | None = None) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.processing_role_assignment WHERE "
                    "(CAST(:a AS uuid) IS NULL OR processing_activity_id = :a) ORDER BY created_at DESC"
                ),
                {"a": _u(activity_id)},
            )
        ).all()
    )


async def launch_flows_without_role(conn: AsyncConnection, now: datetime) -> list[Row[Any]]:
    """DEC-F3 release check: launch-flow activities with no approved, current role assignment."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT a.processing_activity_id, a.activity_code, a.version, a.status
                    FROM privacy.processing_activity a
                    WHERE a.launch_flow AND a.status <> 'RETIRED' AND NOT EXISTS (
                        SELECT 1 FROM privacy.processing_role_assignment r
                        WHERE r.processing_activity_id = a.processing_activity_id
                          AND r.status = 'APPROVED' AND r.effective_from <= :now
                          AND (r.valid_to IS NULL OR r.valid_to > :now))
                    ORDER BY a.activity_code
                """),
                {"now": now},
            )
        ).all()
    )


# ------------------------------------------------------------------------------------- consent


async def insert_consent(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            UPDATE privacy.consent_receipt SET status = 'SUPERSEDED'
            WHERE subject_principal_id = :s AND purpose_code = :p AND status = 'ACTIVE'
        """),
        {"s": _u(v["subject_principal_id"]), "p": v["purpose_code"]},
    )
    await conn.execute(
        text("""
            INSERT INTO privacy.consent_receipt (consent_id, tenant_id, subject_principal_id,
                controller_principal_id, recipient_principal_id, purpose_code, purpose_version,
                data_categories, processing_operations, notice_version, jurisdiction_pack_id,
                capture_method, affirmative_action, granted_at, expires_at, receipt_hash)
            VALUES (:id, :t, :s, :c, :r, :p, :pv, :cats, :ops, :notice, :pack, :method, :action,
                :granted, :expires, :hash)
        """),
        {
            "id": _u(v["consent_id"]),
            "t": _u(v["tenant_id"]),
            "s": _u(v["subject_principal_id"]),
            "c": _u(v["controller_principal_id"]),
            "r": _u(v["recipient_principal_id"]),
            "p": v["purpose_code"],
            "pv": v["purpose_version"],
            "cats": list(v["data_categories"]),
            "ops": list(v["processing_operations"]),
            "notice": v["notice_version"],
            "pack": v["jurisdiction_pack_id"],
            "method": v["capture_method"],
            "action": v["affirmative_action"],
            "granted": v["granted_at"],
            "expires": v["expires_at"],
            "hash": v["receipt_hash"],
        },
    )


async def load_consent(
    conn: AsyncConnection, consent_id: str, *, lock: bool = False
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM privacy.consent_receipt WHERE consent_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": _u(consent_id)},
        )
    ).first()


async def withdraw_consent(
    conn: AsyncConnection, consent_id: str, reason: str, now: datetime
) -> Row[Any]:
    return (
        await conn.execute(
            text("""
                UPDATE privacy.consent_receipt SET status = 'WITHDRAWN', withdrawn_at = :now,
                    withdrawal_reason = :r
                WHERE consent_id = :id RETURNING *
            """),
            {"id": _u(consent_id), "r": reason, "now": now},
        )
    ).one()


async def active_consent(
    conn: AsyncConnection, subject: str, purpose_code: str, now: datetime
) -> Row[Any] | None:
    return (
        await conn.execute(
            text("""
                SELECT consent_id, purpose_version, notice_version FROM privacy.consent_receipt
                WHERE subject_principal_id = :s AND purpose_code = :p AND status = 'ACTIVE'
                  AND (expires_at IS NULL OR expires_at > :now)
                ORDER BY granted_at DESC LIMIT 1
            """),
            {"s": _u(subject), "p": purpose_code, "now": now},
        )
    ).first()


async def consents_of(conn: AsyncConnection, subject: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.consent_receipt WHERE subject_principal_id = :s ORDER BY granted_at"
                ),
                {"s": _u(subject)},
            )
        ).all()
    )


# --------------------------------------------------------------------------------- preferences


async def set_preference(
    conn: AsyncConnection, tenant: str, subject: str, ptype: str, value: bool, source: str
) -> Row[Any]:
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.preference (tenant_id, subject_principal_id, preference_type,
                    value, source)
                VALUES (:t, :s, :p, :v, :src)
                ON CONFLICT (tenant_id, subject_principal_id, preference_type) DO UPDATE
                SET value = :v, source = :src, revision = privacy.preference.revision + 1,
                    updated_at = now()
                RETURNING *
            """),
            {"t": _u(tenant), "s": _u(subject), "p": ptype, "v": value, "src": source},
        )
    ).one()


async def preferences_of(conn: AsyncConnection, subject: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.preference WHERE subject_principal_id = :s ORDER BY preference_type"
                ),
                {"s": _u(subject)},
            )
        ).all()
    )


# -------------------------------------------------------------------------------- restrictions


async def add_restriction(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO privacy.processing_restriction (restriction_id, tenant_id,
                subject_principal_id, kind, purpose_code, reason_code, rights_request_id)
            VALUES (:id, :t, :s, :k, :p, :r, :req)
        """),
        {
            "id": _u(v["restriction_id"]),
            "t": _u(v["tenant_id"]),
            "s": _u(v["subject_principal_id"]),
            "k": v["kind"],
            "p": v["purpose_code"],
            "r": v["reason_code"],
            "req": _u(v["rights_request_id"]),
        },
    )


async def lift_restriction(
    conn: AsyncConnection, restriction_id: str, by: str, now: datetime
) -> bool:
    result = await conn.execute(
        text("""
            UPDATE privacy.processing_restriction SET active = false, lifted_at = :now, lifted_by = :by
            WHERE restriction_id = :id AND active
        """),
        {"id": _u(restriction_id), "by": _u(by), "now": now},
    )
    return bool(result.rowcount)


async def restrictions_of(
    conn: AsyncConnection, subject: str, *, active_only: bool = True
) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.processing_restriction WHERE subject_principal_id = :s "
                    + ("AND active " if active_only else "")
                    + "ORDER BY created_at"
                ),
                {"s": _u(subject)},
            )
        ).all()
    )


# ------------------------------------------------------------------------------ rights requests


async def insert_request(conn: AsyncConnection, v: Mapping[str, Any]) -> Row[Any]:
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.rights_request (request_id, tenant_id, request_type,
                    subject_principal_id, requester_principal_id, authorized_agent_ref,
                    jurisdiction_pack_id, received_at, deadline_at, deadline_source,
                    identity_verification_state, verification_level, status, scope,
                    linked_request_id)
                VALUES (:id, :t, :type, :s, :req, :agent, :pack, :received, :deadline, :src,
                    :ver, :lvl, :status, CAST(:scope AS jsonb), :linked)
                RETURNING *
            """),
            {
                "id": _u(v["request_id"]),
                "t": _u(v["tenant_id"]),
                "type": v["request_type"],
                "s": _u(v["subject_principal_id"]),
                "req": _u(v["requester_principal_id"]),
                "agent": v["authorized_agent_ref"],
                "pack": v["jurisdiction_pack_id"],
                "received": v["received_at"],
                "deadline": v["deadline_at"],
                "src": v["deadline_source"],
                "ver": v["identity_verification_state"],
                "lvl": v["verification_level"],
                "status": v["status"],
                "scope": json.dumps(v["scope"]),
                "linked": _u(v["linked_request_id"]),
            },
        )
    ).one()


async def load_request(
    conn: AsyncConnection, request_id: str, *, lock: bool = False
) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM privacy.rights_request WHERE request_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": _u(request_id)},
        )
    ).first()


async def update_request(conn: AsyncConnection, row: Row[Any], **changes: Any) -> Row[Any]:
    sets, params = [], {"id": row.request_id, "v": row.version}
    for k, val in changes.items():
        if k in ("exemptions", "fulfilment_items", "scope"):
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(val)
        else:
            sets.append(f"{k} = :{k}")
            params[k] = _u(val) if k == "response_package_ref" else val
    out = (
        await conn.execute(
            text(
                f"UPDATE privacy.rights_request SET {', '.join(sets)}, version = version + 1, "
                "updated_at = now() WHERE request_id = :id AND version = :v RETURNING *"
            ),
            params,
        )
    ).first()
    if out is None:
        raise LookupError("CONCURRENT_MODIFICATION")
    return out


async def list_requests(
    conn: AsyncConnection, *, status: str | None, subject: str | None, limit: int
) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("""
                    SELECT * FROM privacy.rights_request
                    WHERE (CAST(:st AS text) IS NULL OR status = :st)
                      AND (CAST(:s AS uuid) IS NULL OR subject_principal_id = :s)
                    ORDER BY received_at DESC LIMIT :n
                """),
                {"st": status, "s": _u(subject), "n": limit},
            )
        ).all()
    )


async def open_requests(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("""
                    SELECT request_id, tenant_id, request_type, received_at, deadline_at
                    FROM privacy.rights_request
                    WHERE status NOT IN ('COMPLETED', 'PARTIALLY_DENIED', 'DENIED', 'WITHDRAWN')
                    ORDER BY deadline_at NULLS FIRST
                """)
            )
        ).all()
    )


# ------------------------------------------------------------------------- retention, data map


async def insert_retention_policy(conn: AsyncConnection, v: Mapping[str, Any]) -> Row[Any]:
    await conn.execute(
        text(
            "UPDATE privacy.retention_policy SET status = 'RETIRED' WHERE policy_code = :c AND status = 'ACTIVE'"
        ),
        {"c": v["policy_code"]},
    )
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.retention_policy (retention_policy_id, tenant_id, policy_code,
                    version, object_type, purpose_code, jurisdiction_pack_id, retention_trigger,
                    retention_days, action_on_expiry, backup_erasure_window_days,
                    legal_hold_suspends, legally_required, created_by, approved_by)
                VALUES (:id, :t, :code, :ver, :otype, :purpose, :pack, :trigger, :days, :action,
                    :backup, :suspends, :legal, :by, :approver)
                RETURNING *
            """),
            {
                "id": _u(v["retention_policy_id"]),
                "t": _u(v["tenant_id"]),
                "code": v["policy_code"],
                "ver": v["version"],
                "otype": v["object_type"],
                "purpose": v["purpose_code"],
                "pack": v["jurisdiction_pack_id"],
                "trigger": v["retention_trigger"],
                "days": v["retention_days"],
                "action": v["action_on_expiry"],
                "backup": v["backup_erasure_window_days"],
                "suspends": v["legal_hold_suspends"],
                "legal": v["legally_required"],
                "by": _u(v["created_by"]),
                "approver": _u(v["approved_by"]),
            },
        )
    ).one()


async def active_retention_policy(conn: AsyncConnection, code: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM privacy.retention_policy WHERE policy_code = :c AND status = 'ACTIVE'"
            ),
            {"c": code},
        )
    ).first()


async def list_retention_policies(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("SELECT * FROM privacy.retention_policy ORDER BY policy_code, version DESC")
            )
        ).all()
    )


async def upsert_data_map(conn: AsyncConnection, v: Mapping[str, Any]) -> Row[Any]:
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.data_map_entry (entry_id, tenant_id, subject_principal_id,
                    store, object_type, object_ref, purpose_code, data_category,
                    retention_policy_code, processor_id, region, trigger_at, due_at)
                VALUES (:id, :t, :s, :store, :otype, :ref, :purpose, :cat, :ret, :proc, :region,
                    :trigger, :due)
                ON CONFLICT (tenant_id, store, object_ref) DO UPDATE
                SET purpose_code = EXCLUDED.purpose_code, retention_policy_code =
                    EXCLUDED.retention_policy_code, region = EXCLUDED.region
                RETURNING *
            """),
            {
                "id": _u(v["entry_id"]),
                "t": _u(v["tenant_id"]),
                "s": _u(v["subject_principal_id"]),
                "store": v["store"],
                "otype": v["object_type"],
                "ref": v["object_ref"],
                "purpose": v["purpose_code"],
                "cat": v["data_category"],
                "ret": v["retention_policy_code"],
                "proc": v["processor_id"],
                "region": v["region"],
                "trigger": v["trigger_at"],
                "due": v["due_at"],
            },
        )
    ).one()


async def subject_entries(conn: AsyncConnection, subject: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.data_map_entry WHERE subject_principal_id = :s "
                    "ORDER BY store, created_at"
                ),
                {"s": _u(subject)},
            )
        ).all()
    )


async def schedule_open_entries(conn: AsyncConnection) -> int:
    """Fix due_at for entries whose retention policy was published after they were registered,
    from their original trigger (never from today)."""
    result = await conn.execute(
        text("""
            UPDATE privacy.data_map_entry e SET due_at = e.trigger_at + make_interval(days => p.retention_days)
            FROM privacy.retention_policy p
            WHERE e.due_at IS NULL AND e.retention_policy_code IS NOT NULL AND e.status <> 'DISPOSED'
              AND p.tenant_id = e.tenant_id AND p.policy_code = e.retention_policy_code AND p.status = 'ACTIVE'
        """)
    )
    return int(result.rowcount or 0)


async def due_entries(
    conn: AsyncConnection,
    now: datetime,
    limit: int,
    after: tuple[datetime, Any] | None = None,
) -> list[Row[Any]]:
    """Due entries oldest first. `after` is the (due_at, entry_id) of the last entry of the previous page, so a
    drain moves forward through the backlog and entries that stay HELD cannot fill every page."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT * FROM privacy.data_map_entry
                    WHERE status IN ('ACTIVE', 'HELD') AND due_at IS NOT NULL AND due_at <= :now
                      AND (CAST(:after_due AS timestamptz) IS NULL OR (due_at, entry_id) > (:after_due, :after_id))
                    ORDER BY due_at, entry_id LIMIT :n
                """),
                {
                    "now": now,
                    "n": limit,
                    "after_due": after[0] if after else None,
                    "after_id": after[1] if after else None,
                },
            )
        ).all()
    )


async def recent_request_count(
    conn: AsyncConnection, *, subject: str, requester: str | None, since: datetime
) -> tuple[int, int]:
    """(requests about this subject, requests made by this requester) received since `since`."""
    row = (
        await conn.execute(
            text("""
                SELECT count(*) FILTER (WHERE subject_principal_id = :s) AS about_subject,
                       count(*) FILTER (WHERE requester_principal_id = :r) AS by_requester
                FROM privacy.rights_request WHERE received_at >= :since
            """),
            {"s": _u(subject), "r": _u(requester), "since": since},
        )
    ).one()
    return int(row.about_subject), int(row.by_requester)


async def set_entry_status(
    conn: AsyncConnection,
    entry_id: Any,
    status: str,
    *,
    action: str | None = None,
    now: datetime | None = None,
) -> None:
    await conn.execute(
        text("""
            UPDATE privacy.data_map_entry SET status = :s,
                disposal_action = coalesce(:a, disposal_action),
                disposed_at = CASE WHEN :s = 'DISPOSED' THEN CAST(:now AS timestamptz) ELSE NULL END
            WHERE entry_id = :id AND status <> 'DISPOSED'
        """),
        {"id": entry_id, "s": status, "a": action, "now": now},
    )


def rows(items: Sequence[Row[Any]]) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in items]
