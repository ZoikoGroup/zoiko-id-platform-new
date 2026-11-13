"""The /v2 privacy control plane, on top of the v1 registry and rights machinery (ZID-PRI-004). Every function runs inside the caller's tenant
transaction. Decisions and acknowledgments are append-only; erasure, review, DPIA and breach records move through guarded states."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.privacy.application import model_governance as models
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as v1
from zid_platform.modules.privacy.domain import collection as collection_rules
from zid_platform.modules.privacy.domain import control as rules
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.privacy.infrastructure import store
from zid_platform.modules.privacy.infrastructure.packs import packs

CONFIG = Path(
    os.environ.get("ZID_PRIVACY_CONFIG")
    or Path(__file__).resolve().parents[6] / "config" / "privacy"
)
REVIEW_DAYS = (
    30  # how long a reviewer has; the due date is shown to the person, never silently extended
)


def _now() -> datetime:
    return datetime.now(UTC)


def _u(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def _row(r: Any) -> dict[str, Any]:
    out = {}
    for k, v in dict(r._mapping).items():
        out[k] = (
            str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v
        )
    return out


@lru_cache(maxsize=1)
def alternatives_config() -> list[dict[str, Any]]:
    doc = yaml.safe_load((CONFIG / "alternatives.yaml").read_text(encoding="utf-8")) or {}
    return [m for m in doc.get("modes", []) if not m.get("biometric")]


@lru_cache(maxsize=1)
def collection_config() -> dict[str, Any]:
    """Counsel's biometric collection requirements per pack (`config/privacy/collection-rules.yaml`); every field COUNSEL until set."""
    path = CONFIG / "collection-rules.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return dict((doc or {}).get("packs") or {})


def breach_config() -> dict[str, Any]:
    doc = yaml.safe_load((CONFIG / "breach-clocks.yaml").read_text(encoding="utf-8")) or {}
    return dict(doc.get("hours_to_notify_authority") or {})


def alternative_modes() -> list[str]:
    return [m["id"] for m in alternatives_config()]


# ------------------------------------------------------------------------------------------------------------- purpose decisions


async def decide_purpose(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str | None,
    *,
    activity_code: str,
    purpose_code: str,
    data_category: str,
    subject_ref: str | None,
    country: str | None,
    subregion: str | None,
    notice_version: str | None,
    provider_service_ref: str | None,
    pack_id: str | None,
    release_ref: str | None = None,
    retention_policy_ref: str | None = None,
    necessity_ref: str | None = None,
    collection_rules_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Permission to process, not identity verification. Default deny: every missing prerequisite is a reason, and a denied or sensitive
    request always carries the non-biometric alternatives."""
    reasons: list[str] = []
    subject = None
    if subject_ref:
        try:
            subject = str(uuid.UUID(subject_ref))
        except ValueError:
            reasons.append(
                "SUBJECT_REF_NOT_RECOGNIZED"
            )  # an opaque id we cannot resolve gets no consent lookup, so no consent-based route
    base = await v1.check_processing(
        conn,
        tenant_id,
        activity_code=activity_code,
        purpose_code=purpose_code,
        data_category=data_category,
        subject_principal_id=subject,
    )
    if not base.allowed:
        reasons.append(base.reason)
    if data_category in ("D3",) and not notice_version:
        reasons.append("NOTICE_REQUIRED_FOR_SENSITIVE_DATA")
    elif notice_version:
        try:
            await registry.require_notice(conn, notice_version)
        except PrivacyError as e:
            reasons.append(e.code)
    if provider_service_ref:
        pid = provider_service_ref.split(":")[0]
        try:
            await registry.require_processor(conn, pid, data_category)
        except PrivacyError:
            reasons.append("PROVIDER_NOT_APPROVED_FOR_THIS_DATA")
    pack = packs().get(pack_id) if pack_id else None
    if pack_id and pack is None:
        reasons.append("JURISDICTION_PACK_UNKNOWN")
    if (
        data_category == "D3" and pack_id
    ):  # sensitive collection under a pack counsel has rules for (AT-034, AT-035, AT-037)
        cd = collection_rules.evaluate(
            collection_rules_override
            if collection_rules_override is not None
            else collection_config(),
            pack_id=pack_id,
            evidence={
                "notice_version": notice_version,
                "release_ref": release_ref,
                "retention_policy_ref": retention_policy_ref,
                "necessity_ref": necessity_ref,
            },
            on=datetime.now(UTC).date(),
        )
        if cd.applies and not cd.allowed:
            reasons.extend(cd.reasons)
    allowed = not reasons
    alternatives = [] if allowed and data_category != "D3" else alternative_modes()
    decision_id = uuid7()
    version = f"{pack.pack_id}@{pack.version}" if pack else "UNSPECIFIED"
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.purpose_decision (decision_id, tenant_id, subject_ref, purpose_code, activity_code, data_category,
                    country, subregion, allowed, reason_codes, alternative_modes, notice_version, provider_service_ref,
                    effective_policy_version, requested_by)
                VALUES (:id, :t, :s, :p, :a, :d, :c, :sr, :al, :r, :alt, :n, :ps, :v, :by) RETURNING *
            """),
            {
                "id": decision_id,
                "t": _u(tenant_id),
                "s": subject_ref,
                "p": purpose_code,
                "a": activity_code,
                "d": data_category,
                "c": country,
                "sr": subregion,
                "al": allowed,
                "r": reasons or ["PROCESSING_PERMITTED_BY_RECORDED_FACTS"],
                "alt": alternatives,
                "n": notice_version,
                "ps": provider_service_ref,
                "v": version,
                "by": _u(actor) if actor else None,
            },
        )
    ).one()
    await v1._audit(
        conn, tenant_id, None, "privacy.purpose_decision.recorded", actor=actor, target_type="purpose_decision", target_ref=str(decision_id),
        details={"allowed": allowed, "reasons": reasons, "purpose": purpose_code}, outcome="SUCCESS" if allowed else "DENIED",
    )  # fmt: skip
    return _row(row)


# ------------------------------------------------------------------------------------------------------------- notices


async def acknowledge_notice(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str | None,
    *,
    session_ref: str,
    notice_version: str,
    language: str,
    subject_ref: str | None,
) -> dict[str, Any]:
    """The person saw this exact notice version for this session. Idempotent: a repeat returns the first record."""
    notice = await registry.require_notice(conn, notice_version)
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.notice_acknowledgment (ack_id, tenant_id, session_ref, subject_ref, notice_code, notice_version,
                    language, acknowledged_by)
                VALUES (:id, :t, :s, :sub, :c, :v, :l, :by)
                ON CONFLICT (tenant_id, session_ref, notice_code, notice_version) DO NOTHING RETURNING *
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "s": session_ref,
                "sub": subject_ref,
                "c": notice.notice_code,
                "v": notice.version,
                "l": language,
                "by": _u(actor) if actor else None,
            },
        )
    ).first()
    if row is None:
        row = (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.notice_acknowledgment WHERE session_ref = :s AND notice_code = :c AND notice_version = :v"
                ),
                {"s": session_ref, "c": notice.notice_code, "v": notice.version},
            )
        ).one()
    return _row(row)


async def acknowledged(conn: AsyncConnection, session_ref: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.notice_acknowledgment WHERE session_ref = :s ORDER BY acknowledged_at"
            ),
            {"s": session_ref},
        )
    ).all()
    return [_row(r) for r in rows]


# ------------------------------------------------------------------------------------------------------------- erasure jobs


async def open_erasure_job(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    subject_ref: str,
    scope: str,
    reason: str,
    legal_basis: str,
) -> dict[str, Any]:
    """Snapshot every place the subject's data is, dispose what is eligible, hold what a hold or a legal obligation keeps, and open one
    task per provider and per backup. The job is COMPLETE only when every task is acknowledged."""
    subject = str(uuid.UUID(subject_ref))
    now = _now()
    await v1.discover_subject(conn, tenant_id, subject)
    job_id = uuid7()
    await conn.execute(
        text(
            "INSERT INTO privacy.erasure_job (job_id, tenant_id, subject_ref, scope, reason, legal_basis, requested_by) VALUES (:j,:t,:s,:sc,:r,:l,:by)"
        ),
        {
            "j": job_id,
            "t": _u(tenant_id),
            "s": subject,
            "sc": scope,
            "r": reason,
            "l": legal_basis,
            "by": _u(actor),
        },
    )
    tasks: list[dict[str, Any]] = []
    backup_days: list[int] = []

    def add(
        kind: str,
        ref: str,
        state: str,
        *,
        receipt: str | None = None,
        failure: str | None = None,
        due: datetime | None = None,
    ) -> None:
        tasks.append(
            {
                "kind": kind,
                "ref": ref,
                "state": state,
                "receipt": receipt,
                "failure": failure,
                "due": due,
            }
        )

    for e in await store.subject_entries(conn, subject):
        ref = f"{e.store}:{e.object_ref}"
        policy = (
            await store.active_retention_policy(conn, e.retention_policy_code, str(e.tenant_id))
            if e.retention_policy_code
            else None
        )
        if policy is not None and policy.backup_erasure_window_days is not None:
            backup_days.append(int(policy.backup_erasure_window_days))
        if e.status == "DISPOSED":
            continue
        if await v1.is_held(
            conn, tenant_id, subject_principal_id=subject, object_type=e.object_type
        ):
            await store.set_entry_status(conn, e.entry_id, "RESTRICTED")
            add("FIRST_PARTY_STORE", ref, "HELD", failure="LEGAL_HOLD")
        elif policy is not None and policy.legally_required:
            await store.set_entry_status(conn, e.entry_id, "RESTRICTED")
            add("FIRST_PARTY_STORE", ref, "HELD", failure="LEGAL_OBLIGATION")
        elif e.processor_id:
            if scope == "FIRST_PARTY_ONLY":
                continue
            proc = await registry.processor(conn, e.processor_id)
            sla = proc.deletion_sla_days if proc is not None else None
            add(
                "PROVIDER",
                e.processor_id,
                "PENDING",
                due=now + timedelta(days=sla) if sla is not None else None,
            )
        elif scope != "PROVIDERS_ONLY":
            disposer = v1._DISPOSERS.get(e.store)
            if disposer is None:
                add("FIRST_PARTY_STORE", ref, "FAILED", failure="NO_DISPOSER")
                continue
            action = policy.action_on_expiry if policy is not None else "DELETE"
            await disposer(conn, e, action)
            await store.set_entry_status(conn, e.entry_id, "DISPOSED", action=action, now=now)
            add(
                "FIRST_PARTY_STORE",
                ref,
                "ACKNOWLEDGED",
                receipt=hashlib.sha256(f"{ref}|{action}|{now.isoformat()}".encode()).hexdigest(),
            )
    if scope != "PROVIDERS_ONLY":
        erasure = await v1.erase_subject(conn, tenant_id, subject, request_ref=str(job_id))
        if erasure.outcome in ("ERASED", "NOTHING_TO_ERASE"):
            add(
                "FIRST_PARTY_STORE",
                "evidence-vault",
                "ACKNOWLEDGED",
                receipt=hashlib.sha256(
                    f"vault|{erasure.outcome}|{erasure.tombstone_id}".encode()
                ).hexdigest(),
            )
        else:
            add("FIRST_PARTY_STORE", "evidence-vault", "HELD", failure=str(erasure.outcome))
        for hook in v1._ERASURE_HOOKS.values():
            hook(tenant_id, subject)
        add(
            "BACKUP",
            "cell-backups",
            "PENDING",
            due=now + timedelta(days=max(backup_days)) if backup_days else None,
        )
    for t in tasks:
        await conn.execute(
            text("""
                INSERT INTO privacy.erasure_task (task_id, tenant_id, job_id, target_kind, target_ref, state, receipt_hash, failure, due_at)
                VALUES (:id, :t, :j, :k, :r, :s, :h, :f, :d)
                ON CONFLICT (job_id, target_kind, target_ref) DO NOTHING
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "j": job_id,
                "k": t["kind"],
                "r": t["ref"],
                "s": t["state"],
                "h": t["receipt"],
                "f": t["failure"],
                "d": t["due"],
            },
        )
    await _settle(conn, str(job_id))
    await v1._audit(
        conn, tenant_id, None, "privacy.erasure_job.opened", actor=actor, target_type="erasure_job", target_ref=str(job_id),
        details={"scope": scope, "tasks": len(tasks)},
    )  # fmt: skip
    return await job_view(conn, str(job_id))


async def _settle(conn: AsyncConnection, job_id: str) -> str:
    states = [
        r[0]
        for r in (
            await conn.execute(
                text("SELECT state FROM privacy.erasure_task WHERE job_id = :j"), {"j": _u(job_id)}
            )
        ).all()
    ]
    new = rules.job_state(states, held=False)
    await conn.execute(
        text(
            "UPDATE privacy.erasure_job SET state = :s, completed_at = CASE WHEN :s = 'COMPLETE' THEN now() ELSE NULL END WHERE job_id = :j"
        ),
        {"s": new, "j": _u(job_id)},
    )
    return new


async def job_view(conn: AsyncConnection, job_id: str) -> dict[str, Any]:
    job = (
        await conn.execute(
            text("SELECT * FROM privacy.erasure_job WHERE job_id = :j"), {"j": _u(job_id)}
        )
    ).first()
    if job is None:
        raise PrivacyError("NOT_FOUND", "unknown erasure job")
    tasks = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.erasure_task WHERE job_id = :j ORDER BY target_kind, target_ref"
            ),
            {"j": _u(job_id)},
        )
    ).all()
    return {**_row(job), "tasks": [_row(t) for t in tasks]}


async def erasure_proof(conn: AsyncConnection, job_id: str) -> dict[str, Any]:
    v = await job_view(conn, job_id)
    return rules.proof(v, v["tasks"])


async def acknowledge_task(
    conn: AsyncConnection, tenant_id: str, actor: str, *, job_id: str, task_id: str, statement: str
) -> dict[str, Any]:
    """An operator attests a task that has no automated receipt (the backup lifecycle). The statement is hashed into the receipt."""
    if not statement.strip():
        raise PrivacyError("STATEMENT_REQUIRED")
    row = (
        await conn.execute(
            text("""
                UPDATE privacy.erasure_task SET state = 'ACKNOWLEDGED', receipt_hash = :h, updated_at = now()
                WHERE task_id = :t AND job_id = :j AND target_kind = 'BACKUP' AND state IN ('PENDING', 'FAILED') RETURNING task_id
            """),
            {
                "h": hashlib.sha256(f"{actor}|{statement}".encode()).hexdigest(),
                "t": _u(task_id),
                "j": _u(job_id),
            },
        )
    ).first()
    if row is None:
        raise PrivacyError(
            "PRIVACY_INVALID_STATE", "only a pending backup task is attested by an operator"
        )
    await _settle(conn, job_id)
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.erasure_task.attested",
        actor=actor,
        target_type="erasure_task",
        target_ref=task_id,
    )
    return await job_view(conn, job_id)


async def provider_receipt(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    provider_id: str,
    task_id: str,
    job_id: str,
    deleted_at: str,
    object_count: int,
    signature_b64: str,
) -> dict[str, Any]:
    """A provider's signed deletion receipt. It counts only if the Ed25519 signature over the canonical receipt verifies against the key
    registered for THAT provider, and the task is that provider's pending task. An unverifiable receipt changes nothing."""
    proc = await registry.processor(conn, provider_id)
    if proc is None or not proc.receipt_public_key:
        raise PrivacyError("PROVIDER_HAS_NO_RECEIPT_KEY")
    try:
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(proc.receipt_public_key, validate=True)
        )
        key.verify(
            base64.b64decode(signature_b64, validate=True),
            rules.receipt_payload(task_id, job_id, deleted_at, object_count),
        )
    except (InvalidSignature, ValueError, binascii.Error) as e:
        raise PrivacyError("RECEIPT_SIGNATURE_INVALID") from e
    row = (
        await conn.execute(
            text("""
                UPDATE privacy.erasure_task SET state = 'ACKNOWLEDGED', receipt_hash = :h, object_count = :n, updated_at = now()
                WHERE task_id = :t AND job_id = :j AND target_kind = 'PROVIDER' AND target_ref = :p AND state IN ('PENDING', 'FAILED')
                RETURNING task_id
            """),
            {
                "h": hashlib.sha256(
                    rules.receipt_payload(task_id, job_id, deleted_at, object_count)
                    + base64.b64decode(signature_b64)
                ).hexdigest(),
                "n": object_count,
                "t": _u(task_id),
                "j": _u(job_id),
                "p": provider_id,
            },
        )
    ).first()
    if row is None:
        raise PrivacyError(
            "PRIVACY_INVALID_STATE", "no pending task of this provider matches the receipt"
        )
    await _settle(conn, job_id)
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.erasure_task.receipt",
        actor=None,
        target_type="erasure_task",
        target_ref=task_id,
        details={"provider": provider_id},
    )
    return await job_view(conn, job_id)


# ------------------------------------------------------------------------------------------------------------- transfers


async def pending_change(conn: AsyncConnection, processor_id: str) -> bool:
    return bool(
        (
            await conn.execute(
                text(
                    "SELECT 1 FROM privacy.subprocessor_change WHERE processor_id = :p AND state = 'PENDING_APPROVAL' LIMIT 1"
                ),
                {"p": processor_id},
            )
        ).first()
    )


async def decide_transfer(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str | None,
    *,
    processor_id: str,
    purpose_code: str,
    data_category: str,
    origin_country: str,
    destination_country: str,
    remote_access_countries: list[str],
    pack_id: str | None,
) -> dict[str, Any]:
    proc = await registry.processor(conn, processor_id)
    pack = packs().get(pack_id) if pack_id else None
    facts = rules.TransferFacts(
        processor_found=proc is not None,
        processor_status=proc.status if proc else None,
        data_categories=frozenset(proc.data_categories) if proc else frozenset(),
        locations=frozenset(x.upper() for x in proc.locations) if proc else frozenset(),
        processor_mechanism=proc.transfer_mechanism if proc else None,
        pending_change=await pending_change(conn, processor_id),
        pack_mechanisms=tuple(pack.transfer_mechanisms) if pack else (),
    )
    allowed, reasons, mechanism = rules.decide_transfer(
        facts, data_category=data_category, origin_country=origin_country.upper(), destination_country=destination_country.upper(),
        remote_access_countries=remote_access_countries,
    )  # fmt: skip
    if pack_id and pack is None:
        allowed, reasons = False, [*reasons, "JURISDICTION_PACK_UNKNOWN"]
    snapshot = {"processor_status": facts.processor_status, "locations": sorted(facts.locations), "pack": f"{pack.pack_id}@{pack.version}" if pack else None,
                "pending_change": facts.pending_change}  # fmt: skip
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.transfer_decision (decision_id, tenant_id, processor_id, purpose_code, data_category, origin_country,
                    destination_country, remote_access_countries, mechanism, allowed, reason_codes, policy_snapshot, requested_by)
                VALUES (:id,:t,:p,:pu,:d,:o,:de,:ra,:m,:al,:r,CAST(:snap AS jsonb),:by) RETURNING *
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "p": processor_id,
                "pu": purpose_code,
                "d": data_category,
                "o": origin_country.upper(),
                "de": destination_country.upper(),
                "ra": sorted({c.upper() for c in remote_access_countries}),
                "m": mechanism,
                "al": allowed,
                "r": reasons,
                "snap": json.dumps(snapshot),
                "by": _u(actor) if actor else None,
            },
        )
    ).one()
    return _row(row)


async def provider_controls(conn: AsyncConnection, service_id: str, country: str) -> dict[str, Any]:
    proc = await registry.processor(conn, service_id)
    if proc is None:
        return {
            "service_id": service_id,
            "known": False,
            "usable": False,
            "reasons": ["PROVIDER_NOT_REGISTERED"],
        }
    reasons = [] if proc.status == "APPROVED" else ["PROVIDER_NOT_APPROVED"]
    locations = [x.upper() for x in proc.locations]
    if country.upper() not in locations:
        reasons.append("COUNTRY_NOT_AN_APPROVED_LOCATION")
    if await pending_change(conn, service_id):
        reasons.append("SUBPROCESSOR_CHANGE_PENDING")
    return {
        "service_id": service_id, "known": True, "usable": not reasons, "reasons": reasons, "status": proc.status, "role": proc.role,
        "data_categories": list(proc.data_categories), "locations": locations, "transfer_mechanism": proc.transfer_mechanism,
        "deletion_sla_days": proc.deletion_sla_days, "has_receipt_key": bool(proc.receipt_public_key),
    }  # fmt: skip


async def register_receipt_key(
    conn: AsyncConnection, tenant_id: str, actor: str, processor_id: str, public_key_b64: str
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
    except (ValueError, binascii.Error) as e:
        raise PrivacyError("PUBLIC_KEY_INVALID") from e
    done = await conn.execute(
        text(
            "UPDATE privacy.processor SET receipt_public_key = :k, updated_at = now() WHERE processor_id = :p"
        ),
        {"k": public_key_b64, "p": processor_id},
    )
    if done.rowcount == 0:
        raise PrivacyError("NOT_FOUND", "unknown processor")
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.processor.receipt_key_registered",
        actor=actor,
        target_type="processor",
        target_ref=processor_id,
    )


async def notify_change(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    processor_id: str,
    kind: str,
    detail: str,
    notified_at: datetime,
) -> dict[str, Any]:
    if await registry.processor(conn, processor_id) is None:
        raise PrivacyError("NOT_FOUND", "unknown processor")
    row = (
        await conn.execute(
            text(
                "INSERT INTO privacy.subprocessor_change (change_id, tenant_id, processor_id, kind, detail, notified_at, requested_by) VALUES (:id,:t,:p,:k,:d,:n,:by) RETURNING *"
            ),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "p": processor_id,
                "k": kind,
                "d": detail,
                "n": notified_at,
                "by": _u(actor),
            },
        )
    ).one()
    return _row(row)


async def decide_change(
    conn: AsyncConnection, tenant_id: str, actor: str, change_id: str, approve: bool
) -> dict[str, Any]:
    pending = (
        await conn.execute(
            text(
                "SELECT requested_by FROM privacy.subprocessor_change WHERE change_id = :c AND state = 'PENDING_APPROVAL'"
            ),
            {"c": _u(change_id)},
        )
    ).first()
    if pending is None:
        raise PrivacyError("PRIVACY_INVALID_STATE", "no pending change with that id")
    if str(pending.requested_by) == actor:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "the person who raised a change cannot decide it"
        )
    row = (
        await conn.execute(
            text(
                "UPDATE privacy.subprocessor_change SET state = :s, decided_by = :by, decided_at = now() WHERE change_id = :c AND state = 'PENDING_APPROVAL' RETURNING *"
            ),
            {"s": "APPROVED" if approve else "REJECTED", "by": _u(actor), "c": _u(change_id)},
        )
    ).first()
    if row is None:
        raise PrivacyError("PRIVACY_INVALID_STATE", "no pending change with that id")
    return _row(row)


# ------------------------------------------------------------------------------------------------------------- DPIA packages


async def create_package(
    conn: AsyncConnection, tenant_id: str, actor: str, *, market: str, product: str, version: str, controller: str, purpose_code: str,
    provider_service_ref: str | None, model_version: str | None,
) -> dict[str, Any]:  # fmt: skip
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.dpia_package (package_id, tenant_id, market, product, version, controller, purpose_code, provider_service_ref,
                    model_version, created_by) VALUES (:id,:t,:m,:p,:v,:c,:pu,:ps,:mv,:by) RETURNING *
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "m": market.upper(),
                "p": product,
                "v": version,
                "c": controller,
                "pu": purpose_code,
                "ps": provider_service_ref,
                "mv": model_version,
                "by": _u(actor),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.dpia_package.created",
        actor=actor,
        target_type="dpia_package",
        target_ref=str(row.package_id),
    )
    return await package_view(conn, str(row.package_id))


async def _package(conn: AsyncConnection, package_id: str) -> Any:
    row = (
        await conn.execute(
            text("SELECT * FROM privacy.dpia_package WHERE package_id = :p FOR UPDATE"),
            {"p": _u(package_id)},
        )
    ).first()
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown DPIA package")
    return row


async def package_view(conn: AsyncConnection, package_id: str) -> dict[str, Any]:
    p = await _package(conn, package_id)
    out = _row(p)
    out["risks"] = [
        rules.risk_view(r)
        if not rules.risk_problems(r)
        else {**r, "problems": rules.risk_problems(r)}
        for r in p.risks
    ]
    rel = rules.release_state(
        state=p.state,
        approved_until=p.approved_until,
        risks=p.risks,
        gates=p.gates,
        consultation_ref=p.consultation_ref,
        now=_now(),
    )
    extra = await models.blockers_for(
        conn, p.model_version
    )  # a named model must be the evaluated one in service (AT-015, AT-016)
    blockers = [*rel.blockers, *extra]
    out["release"] = {"ready": rel.ready and not extra, "blockers": blockers}
    return out


async def set_risks(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    package_id: str,
    risks: list[dict[str, Any]],
    consultation_ref: str | None,
) -> dict[str, Any]:
    p = await _package(conn, package_id)
    if p.state not in ("DRAFT", "IN_REVIEW"):
        raise PrivacyError(
            "PRIVACY_INVALID_STATE", "a decided package cannot be edited: open a new version"
        )
    for r in risks:
        problems = rules.risk_problems(r)
        if problems:
            raise PrivacyError("RISK_INVALID", f"{r.get('risk_id')}: {', '.join(problems)}")
    if len({r["risk_id"] for r in risks}) != len(risks):
        raise PrivacyError("RISK_INVALID", "risk ids must be unique")
    await conn.execute(
        text(
            "UPDATE privacy.dpia_package SET risks = CAST(:r AS jsonb), consultation_ref = :c, updated_at = now() WHERE package_id = :p"
        ),
        {"r": json.dumps(risks), "c": consultation_ref, "p": _u(package_id)},
    )
    return await package_view(conn, package_id)


async def sign_gate(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    package_id: str,
    *,
    gate: str,
    role: str,
    evidence_uri: str,
) -> dict[str, Any]:
    p = await _package(conn, package_id)
    if p.state not in ("DRAFT", "IN_REVIEW"):
        raise PrivacyError("PRIVACY_INVALID_STATE", "a decided package cannot be signed")
    problem = rules.gate_problem(gate, role, actor, str(p.created_by), evidence_uri, p.gates)
    if problem:
        raise PrivacyError(problem)
    gates = {
        **p.gates,
        gate: {
            **(p.gates.get(gate) or {}),
            role: {"signer": actor, "evidence_uri": evidence_uri, "signed_at": _now().isoformat()},
        },
    }
    await conn.execute(
        text(
            "UPDATE privacy.dpia_package SET gates = CAST(:g AS jsonb), state = 'IN_REVIEW', updated_at = now() WHERE package_id = :p"
        ),
        {"g": json.dumps(gates), "p": _u(package_id)},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.dpia_package.gate_signed",
        actor=actor,
        target_type="dpia_package",
        target_ref=package_id,
        details={"gate": gate, "role": role},
    )
    return await package_view(conn, package_id)


async def decide_package(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    package_id: str,
    *,
    approve: bool,
    rationale: str,
    valid_days: int,
) -> dict[str, Any]:
    p = await _package(conn, package_id)
    if p.state not in ("DRAFT", "IN_REVIEW"):
        raise PrivacyError("PRIVACY_INVALID_STATE", "already decided")
    if str(p.created_by) == actor:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "the author cannot decide their own package"
        )
    if not rationale.strip():
        raise PrivacyError("RATIONALE_REQUIRED")
    if approve:
        pending = rules.release_state(
            state="APPROVED",
            approved_until=_now() + timedelta(days=1),
            risks=p.risks,
            gates=p.gates,
            consultation_ref=p.consultation_ref,
            now=_now(),
        )
        model_blockers = await models.blockers_for(conn, p.model_version)
        if not pending.ready or model_blockers:
            raise PrivacyError(
                "RELEASE_BLOCKED", ", ".join([*pending.blockers, *model_blockers])
            )  # approval is refused while a no-release criterion stands
    await conn.execute(
        text("""
            UPDATE privacy.dpia_package SET state = :s, decided_by = :by, decision_rationale = :r, updated_at = now(),
                approved_until = CASE WHEN :ok THEN now() + make_interval(days => :d) END
            WHERE package_id = :p
        """),
        {
            "s": "APPROVED" if approve else "REJECTED",
            "by": _u(actor),
            "r": rationale,
            "ok": approve,
            "d": valid_days,
            "p": _u(package_id),
        },
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.dpia_package.decided",
        actor=actor,
        target_type="dpia_package",
        target_ref=package_id,
        details={"approved": approve},
    )
    if (
        approve
    ):  # a newer approved version supersedes the earlier ones for the same market and product
        await conn.execute(
            text(
                "UPDATE privacy.dpia_package SET state = 'SUPERSEDED', updated_at = now() WHERE market = :m AND product = :pr AND package_id <> :p AND state = 'APPROVED'"
            ),
            {"m": p.market, "pr": p.product, "p": _u(package_id)},
        )
    return await package_view(conn, package_id)


async def release_gate(
    conn: AsyncConnection, market: str, product: str, version: str
) -> dict[str, Any]:
    p = (
        await conn.execute(
            text(
                "SELECT package_id FROM privacy.dpia_package WHERE market = :m AND product = :p AND version = :v"
            ),
            {"m": market.upper(), "p": product, "v": version},
        )
    ).first()
    if p is None:
        return {
            "market": market.upper(),
            "product": product,
            "version": version,
            "ready": False,
            "blockers": ["NO_DPIA_PACKAGE"],
            "package_id": None,
        }
    v = await package_view(conn, str(p.package_id))
    return {
        "market": market.upper(),
        "product": product,
        "version": version,
        **v["release"],
        "package_id": v["package_id"],
    }


# ------------------------------------------------------------------------------------------------------------- reviews


async def open_review(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    decision_ref: str,
    kind: str,
    subject_ref: str,
    statement: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO privacy.decision_review (review_id, tenant_id, decision_ref, kind, subject_ref, statement, requested_by, due_at) VALUES (:id,:t,:d,:k,:s,:st,:by,:due) RETURNING *"
            ),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "d": decision_ref,
                "k": kind,
                "s": subject_ref,
                "st": statement,
                "by": _u(actor),
                "due": _now() + timedelta(days=REVIEW_DAYS),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        f"privacy.decision_review.{kind.lower()}_opened",
        actor=actor,
        target_type="decision_review",
        target_ref=str(row.review_id),
    )
    return _row(row)


async def decide_review(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    review_id: str,
    *,
    outcome: str,
    rationale: str,
    correction_ref: str | None,
) -> dict[str, Any]:
    r = (
        await conn.execute(
            text("SELECT * FROM privacy.decision_review WHERE review_id = :r FOR UPDATE"),
            {"r": _u(review_id)},
        )
    ).first()
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown review")
    if r.state != "OPEN":
        raise PrivacyError("PRIVACY_INVALID_STATE", "already decided")
    problem = rules.review_problem(str(r.requested_by), actor, rationale, outcome)
    if problem:
        raise PrivacyError(problem)
    if outcome == "CORRECTED" and not correction_ref:
        raise PrivacyError("CORRECTION_REF_REQUIRED")
    row = (
        await conn.execute(
            text(
                "UPDATE privacy.decision_review SET state = :s, reviewer = :by, rationale = :ra, correction_ref = :c, decided_at = now() WHERE review_id = :r RETURNING *"
            ),
            {
                "s": outcome,
                "by": _u(actor),
                "ra": rationale,
                "c": correction_ref,
                "r": _u(review_id),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.decision_review.decided",
        actor=actor,
        target_type="decision_review",
        target_ref=review_id,
        details={"outcome": outcome},
    )
    # the person is told the outcome (ids and the outcome only: the rationale stays on the record, never in an event)
    await v1._event(
        conn,
        tenant_id,
        "privacy.review.decided",
        ("decision_review", review_id),
        {"review_id": review_id, "outcome": outcome, "reviewer_is_independent": True},
        actor=actor,
    )
    return _row(row)


async def get_review(conn: AsyncConnection, review_id: str) -> dict[str, Any]:
    r = (
        await conn.execute(
            text("SELECT * FROM privacy.decision_review WHERE review_id = :r"), {"r": _u(review_id)}
        )
    ).first()
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown review")
    return _row(r)


# ------------------------------------------------------------------------------------------------------------- breaches


def _breach_view(r: Any, config: dict[str, Any], now: datetime) -> dict[str, Any]:
    notified = {
        n["jurisdiction"]: datetime.fromisoformat(n["notified_at"]) for n in r.notifications
    }
    out = _row(r)
    out["clocks"] = rules.breach_clocks(config, r.jurisdictions, r.detected_at, notified, now)
    return out


async def open_breach(
    conn: AsyncConnection, tenant_id: str, actor: str, *, summary: str, severity: str, jurisdictions: list[str], subjects_estimate: int | None,
    detected_at: datetime, config: dict[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    row = (
        await conn.execute(
            text(
                "INSERT INTO privacy.breach_case (breach_id, tenant_id, summary, severity, jurisdictions, subjects_estimate, detected_at, opened_by) VALUES (:id,:t,:s,:sev,:j,:n,:d,:by) RETURNING *"
            ),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "s": summary,
                "sev": severity,
                "j": sorted({x.upper() for x in jurisdictions}),
                "n": subjects_estimate,
                "d": detected_at,
                "by": _u(actor),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.breach.opened",
        actor=actor,
        target_type="breach_case",
        target_ref=str(row.breach_id),
        details={"severity": severity},
    )
    return _breach_view(row, config if config is not None else breach_config(), _now())


async def get_breach(
    conn: AsyncConnection, breach_id: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    r = (
        await conn.execute(
            text("SELECT * FROM privacy.breach_case WHERE breach_id = :b"), {"b": _u(breach_id)}
        )
    ).first()
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown breach case")
    return _breach_view(r, config if config is not None else breach_config(), _now())


async def record_notification(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    breach_id: str,
    *,
    jurisdiction: str,
    notified_at: datetime,
    reference: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r = (
        await conn.execute(
            text("SELECT * FROM privacy.breach_case WHERE breach_id = :b FOR UPDATE"),
            {"b": _u(breach_id)},
        )
    ).first()
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown breach case")
    j = jurisdiction.upper()
    if j not in r.jurisdictions:
        raise PrivacyError("JURISDICTION_NOT_AFFECTED")
    if any(n["jurisdiction"] == j for n in r.notifications):
        raise PrivacyError("ALREADY_NOTIFIED")
    notes = [
        *r.notifications,
        {
            "jurisdiction": j,
            "notified_at": notified_at.isoformat(),
            "reference": reference,
            "recorded_by": actor,
        },
    ]
    await conn.execute(
        text(
            "UPDATE privacy.breach_case SET notifications = CAST(:n AS jsonb) WHERE breach_id = :b"
        ),
        {"n": json.dumps(notes), "b": _u(breach_id)},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.breach.notified",
        actor=actor,
        target_type="breach_case",
        target_ref=breach_id,
        details={"jurisdiction": j},
    )
    return await get_breach(conn, breach_id, config)


async def close_breach(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    breach_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = await get_breach(conn, breach_id, config)
    open_clocks = [
        c["jurisdiction"] for c in view["clocks"] if c["state"] in ("RUNNING", "OVERDUE")
    ]
    if open_clocks:
        raise PrivacyError(
            "NOTIFICATIONS_OUTSTANDING", ", ".join(open_clocks)
        )  # a case with a running or overdue clock is not closed quietly
    await conn.execute(
        text(
            "UPDATE privacy.breach_case SET state = 'CLOSED', closed_at = now() WHERE breach_id = :b AND state <> 'CLOSED'"
        ),
        {"b": _u(breach_id)},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.breach.closed",
        actor=actor,
        target_type="breach_case",
        target_ref=breach_id,
    )
    return await get_breach(conn, breach_id, config)


# ------------------------------------------------------------------------------------------------------------- KPIs


async def kpis(conn: AsyncConnection, days: int = 30) -> dict[str, Any]:
    """Measured from the records, with the window stated. A rate with no denominator is null, never zero."""
    since = _now() - timedelta(days=days)
    d = (
        await conn.execute(
            text(
                "SELECT count(*) AS n, count(*) FILTER (WHERE NOT allowed) AS denied FROM privacy.purpose_decision WHERE evaluated_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    e = (
        await conn.execute(
            text(
                "SELECT count(*) AS jobs, count(*) FILTER (WHERE state = 'COMPLETE') AS complete, "
                "(SELECT count(*) FROM privacy.erasure_task WHERE state IN ('PENDING','FAILED') AND due_at IS NOT NULL AND due_at < now()) AS overdue_tasks, "
                "(SELECT count(*) FROM privacy.erasure_task WHERE state = 'ACKNOWLEDGED') AS acknowledged_tasks FROM privacy.erasure_job WHERE created_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    p = (
        await conn.execute(
            text(
                "SELECT count(*) AS active, count(*) FILTER (WHERE state = 'APPROVED' AND approved_until > now()) AS current "
                "FROM (SELECT DISTINCT ON (market, product) * FROM privacy.dpia_package WHERE state <> 'SUPERSEDED' ORDER BY market, product, created_at DESC) latest"
            )
        )
    ).one()
    r = (
        await conn.execute(
            text(
                "SELECT count(*) AS n, count(*) FILTER (WHERE completed_at IS NOT NULL AND deadline_at IS NOT NULL AND completed_at <= deadline_at) AS on_time, "
                "count(*) FILTER (WHERE completed_at IS NULL AND deadline_at IS NOT NULL AND deadline_at < now() AND status NOT IN ('DENIED','WITHDRAWN','PARTIALLY_DENIED')) AS overdue "
                "FROM privacy.rights_request WHERE received_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    b = (
        await conn.execute(
            text(
                "SELECT count(*) AS n, count(*) FILTER (WHERE state <> 'CLOSED') AS open FROM privacy.breach_case WHERE created_at >= :s"
            ),
            {"s": since},
        )
    ).one()

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    return {
        "window_days": days,
        "purpose_denial": {"decisions": d.n, "denied": d.denied, "rate": rate(d.denied, d.n)},
        "erasure": {
            "jobs": e.jobs,
            "complete": e.complete,
            "overdue_tasks": e.overdue_tasks,
            "acknowledged_tasks": e.acknowledged_tasks,
            "completion_rate": rate(e.complete, e.jobs),
        },
        "dpia_currency": {
            "active_use_cases": p.active,
            "with_current_approved_dpia": p.current,
            "rate": rate(p.current, p.active),
        },
        "rights_requests": {
            "received": r.n,
            "completed_on_time": r.on_time,
            "overdue": r.overdue,
            "on_time_rate": rate(r.on_time, r.n),
        },
        "breaches": {"opened": b.n, "open": b.open},
        "not_measured": [
            "false rejection and appeal rate by cohort",
            "fraud false positives",
            "supplier incident response time",
            "cross-tenant violations (see the isolation tests)",
        ],
    }
