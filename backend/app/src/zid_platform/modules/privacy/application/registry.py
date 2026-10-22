"""Privacy notices, processor registry and automated-decision profiles (ZID-PRIV-001 §20, §22-23).

- Notices: a published version is immutable and content-addressed; consent cites `CODE@version`
  and is refused for a version that was never published (PRIV-26). Each version is also an
  immutable evidence artifact (EVIDENCE §9 ConsentNoticeVersion).
- Processors: registered PENDING, approved by someone other than the registrant, suspendable.
  Personal data may be recorded with a processor only if it is APPROVED for that data category.
- Automated-decision profiles: decision class -> significance, solely-automated flag,
  explanation and human-review route. HUMAN_REVIEW / ADMT requests resolve through them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_evidence import hash_bytes
from zid_outbox import emit

from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    record_audit,
    register_artifact,
)
from zid_platform.modules.privacy.domain.model import DataCategory, PrivacyError, check_code

SIGNIFICANCE = ("NONE", "MATERIAL", "LEGAL_OR_SIMILARLY_SIGNIFICANT")
PROCESSOR_ROLES = ("PROCESSOR", "SUBPROCESSOR", "INDEPENDENT_CONTROLLER", "RECIPIENT")


def _now() -> datetime:
    return datetime.now(UTC)


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event_id: str | None,
    name: str,
    *,
    actor: str | None,
    target_type: str,
    target_ref: str,
    details: Mapping[str, Any],
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SYSTEM",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="privacy",
            source_event_id=event_id,
            details=dict(details),
        ),
    )


# -------------------------------------------------------------------------------------- notices


async def publish_notice(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    code: str,
    audience: str,
    content: str,
    created_by: str,
    approved_by: str,
    jurisdictions: Sequence[str] = (),
    processing_activity_codes: Sequence[str] = (),
    content_uri: str | None = None,
) -> Row[Any]:
    check_code(code, "notice_code")
    if created_by == approved_by:
        raise PrivacyError("PRIVACY_APPROVAL_REQUIRED", "a notice needs an independent approver")
    version = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM privacy.notice WHERE notice_code = :c"
                ),
                {"c": code},
            )
        ).scalar_one()
    )
    content_hash = str(hash_bytes(content.encode("utf-8"), alg="sha-256"))
    await conn.execute(
        text(
            "UPDATE privacy.notice SET status = 'RETIRED' WHERE notice_code = :c AND status = 'ACTIVE'"
        ),
        {"c": code},
    )
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.notice (notice_id, tenant_id, notice_code, version, audience, jurisdictions,
                    processing_activity_codes, content_hash, content_uri, created_by, approved_by)
                VALUES (:id, :t, :c, :v, :a, :j, :pa, :h, :u, :by, :appr) RETURNING *
            """),
            {
                "id": uuid7(),
                "t": uuid.UUID(tenant_id),
                "c": code,
                "v": version,
                "a": audience,
                "j": [j.upper() for j in jurisdictions],
                "pa": list(processing_activity_codes),
                "h": content_hash,
                "u": content_uri,
                "by": uuid.UUID(created_by),
                "appr": uuid.UUID(approved_by),
            },
        )
    ).one()
    await register_artifact(
        conn,
        tenant_id,
        "CONSENT_NOTICE_VERSION",
        code,
        str(version),
        {
            "notice_code": code,
            "version": version,
            "audience": audience,
            "content_hash": content_hash,
            "processing_activity_codes": list(processing_activity_codes),
        },
    )
    event_id = await emit(
        conn,
        name="privacy.notice.published",
        tenant_id=tenant_id,
        aggregate_type="privacy_notice",
        aggregate_id=f"{code}@{version}",
        aggregate_version=1,
        data={"notice": f"{code}@{version}", "content_hash": content_hash, "evidence_class": "E2"},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "privacy.notice.published",
        actor=approved_by,
        target_type="privacy_notice",
        target_ref=f"{code}@{version}",
        details={"content_hash": content_hash},
    )
    return row


async def require_notice(conn: AsyncConnection, notice_version: str) -> Row[Any]:
    """`CODE@version` of a notice that was published (it may since have been superseded)."""
    code, _, version = notice_version.partition("@")
    if not version.isdigit():
        raise PrivacyError("PRIVACY_NOTICE_UNKNOWN", "cite the notice as CODE@version")
    row = (
        await conn.execute(
            text("SELECT * FROM privacy.notice WHERE notice_code = :c AND version = :v"),
            {"c": code, "v": int(version)},
        )
    ).first()
    if row is None:
        raise PrivacyError("PRIVACY_NOTICE_UNKNOWN", f"{notice_version} was never published")
    return row


async def notices(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("SELECT * FROM privacy.notice ORDER BY notice_code, version DESC")
            )
        ).all()
    )


# ----------------------------------------------------------------------------------- processors


async def register_processor(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    processor_id: str,
    name: str,
    role: str,
    data_categories: Sequence[str],
    locations: Sequence[str],
    created_by: str,
    processing_activity_codes: Sequence[str] = (),
    purposes: Sequence[str] = (),
    transfer_mechanism: str | None = None,
    deletion_sla_days: int | None = None,
    security_assurance: str | None = None,
    contract_ref: str | None = None,
) -> Row[Any]:
    if role not in PROCESSOR_ROLES:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown processor role")
    cats = sorted({DataCategory(c).value for c in data_categories})
    if not locations:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "processing locations are required (§23, §24)")
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.processor (tenant_id, processor_id, name, role, processing_activity_codes,
                    data_categories, purposes, locations, transfer_mechanism, deletion_sla_days,
                    security_assurance, contract_ref, created_by)
                VALUES (:t, :id, :n, :r, :pa, :dc, :p, :l, :tm, :sla, :sa, :cr, :by) RETURNING *
            """),
            {
                "t": uuid.UUID(tenant_id),
                "id": processor_id,
                "n": name,
                "r": role,
                "pa": list(processing_activity_codes),
                "dc": cats,
                "p": list(purposes),
                "l": [x.upper() for x in locations],
                "tm": transfer_mechanism,
                "sla": deletion_sla_days,
                "sa": security_assurance,
                "cr": contract_ref,
                "by": uuid.UUID(created_by),
            },
        )
    ).one()


async def set_processor_status(
    conn: AsyncConnection, tenant_id: str, processor_id: str, status: str, *, actor: str
) -> Row[Any]:
    row = await processor(conn, processor_id)
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown processor")
    if status == "APPROVED" and str(row.created_by) == actor:
        raise PrivacyError("PRIVACY_APPROVAL_REQUIRED", "a processor needs an independent approver")
    if status == "APPROVED" and row.role in ("PROCESSOR", "SUBPROCESSOR") and not row.contract_ref:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "a processor needs its DPA/contract reference")
    out = (
        await conn.execute(
            text("""
                UPDATE privacy.processor SET status = :s, revision = revision + 1, updated_at = now(),
                    approved_by = CASE WHEN :s = 'APPROVED' THEN CAST(:a AS uuid) ELSE approved_by END
                WHERE processor_id = :id RETURNING *
            """),
            {"s": status, "a": actor, "id": processor_id},
        )
    ).one()
    await _audit(
        conn,
        tenant_id,
        None,
        "privacy.processor.status_changed",
        actor=actor,
        target_type="processor",
        target_ref=processor_id,
        details={"status": status, "revision": out.revision},
    )
    return out


async def processor(conn: AsyncConnection, processor_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM privacy.processor WHERE processor_id = :id"), {"id": processor_id}
        )
    ).first()


async def processors(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (await conn.execute(text("SELECT * FROM privacy.processor ORDER BY processor_id"))).all()
    )


async def require_processor(
    conn: AsyncConnection, processor_id: str, data_category: str
) -> Row[Any]:
    row = await processor(conn, processor_id)
    if row is None or row.status != "APPROVED":
        raise PrivacyError("PRIVACY_PROCESSOR_NOT_APPROVED", processor_id)
    if data_category not in row.data_categories:
        raise PrivacyError(
            "PRIVACY_PROCESSOR_NOT_APPROVED", f"{processor_id} is not approved for {data_category}"
        )
    return row


# ------------------------------------------------------------------------ automated decisions


async def create_adm_profile(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    decision_class: str,
    purpose_code: str,
    input_data_categories: Sequence[str],
    model_or_rules_ref: str,
    solely_automated: bool,
    significance_class: str,
    explanation_profile: str,
    human_review_route: str,
    created_by: str,
    special_category_use: bool = False,
    jurisdiction_rules: Mapping[str, Any] | None = None,
    risk_assessment_ref: str | None = None,
) -> Row[Any]:
    if significance_class not in SIGNIFICANCE:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown significance class")
    if significance_class != "NONE" and not risk_assessment_ref:
        raise PrivacyError(
            "PRIVACY_DPIA_REQUIRED",
            "a significant automated decision needs a risk assessment (§25)",
        )
    version = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM privacy.automated_decision_profile WHERE decision_class = :d"
                ),
                {"d": decision_class},
            )
        ).scalar_one()
    )
    await conn.execute(
        text(
            "UPDATE privacy.automated_decision_profile SET status = 'RETIRED' WHERE decision_class = :d AND status = 'ACTIVE'"
        ),
        {"d": decision_class},
    )
    return (
        await conn.execute(
            text("""
                INSERT INTO privacy.automated_decision_profile (profile_id, tenant_id, decision_class, version,
                    purpose_code, input_data_categories, model_or_rules_ref, solely_automated, significance_class,
                    special_category_use, explanation_profile, human_review_route, jurisdiction_rules,
                    risk_assessment_ref, created_by)
                VALUES (:id, :t, :d, :v, :p, :c, :m, :sa, :sig, :sc, :e, :h, CAST(:jr AS jsonb), :ra, :by) RETURNING *
            """),
            {
                "id": uuid7(),
                "t": uuid.UUID(tenant_id),
                "d": decision_class,
                "v": version,
                "p": purpose_code,
                "c": sorted({DataCategory(c).value for c in input_data_categories}),
                "m": model_or_rules_ref,
                "sa": solely_automated,
                "sig": significance_class,
                "sc": special_category_use,
                "e": explanation_profile,
                "h": human_review_route,
                "jr": json.dumps(dict(jurisdiction_rules or {})),
                "ra": risk_assessment_ref,
                "by": uuid.UUID(created_by),
            },
        )
    ).one()


async def adm_profile(conn: AsyncConnection, decision_class: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM privacy.automated_decision_profile WHERE decision_class = :d AND status = 'ACTIVE'"
            ),
            {"d": decision_class},
        )
    ).first()


async def adm_profiles(conn: AsyncConnection) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM privacy.automated_decision_profile ORDER BY decision_class, version DESC"
                )
            )
        ).all()
    )


__all__ = [
    "adm_profile",
    "adm_profiles",
    "create_adm_profile",
    "notices",
    "processor",
    "processors",
    "publish_notice",
    "register_processor",
    "require_notice",
    "require_processor",
    "set_processor_status",
]
