"""Model inventory, independent evaluation, promotion and drift (ZID-PRI-004 PR-16). Runs inside the caller's tenant transaction.

A version is never promoted by default: promotion reads the latest INDEPENDENT evaluation of exactly that version. The version in service is
pinned; a different version reported by a provider or deployment holds the model until the new version has its own evidence.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.privacy.application import service as v1
from zid_platform.modules.privacy.domain import models as rules
from zid_platform.modules.privacy.domain.model import PrivacyError


def _u(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _view(r: Any) -> dict[str, Any]:
    return {
        "model_id": str(r.model_id),
        "model_ref": r.model_ref,
        "version": r.version,
        "kind": r.kind,
        "provider_service_ref": r.provider_service_ref,
        "live_features": list(r.live_features),
        "state": r.state,
        "state_reason": r.state_reason,
        "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None,
    }


async def _get(conn: AsyncConnection, model_ref: str, version: str) -> Any:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.model_version WHERE model_ref = :r AND version = :v FOR UPDATE"
            ),
            {"r": model_ref, "v": version},
        )
    ).first()
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown model version")
    return row


async def register(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    model_ref: str,
    version: str,
    kind: str,
    provider_service_ref: str | None,
    artifact_sha256: str | None,
    live_features: list[str],
) -> dict[str, Any]:
    if kind not in rules.KINDS:
        raise PrivacyError("MODEL_KIND_UNKNOWN", kind)
    banned = rules.protected_features(live_features)
    if banned:  # AT-017: a protected characteristic or proxy is never a live input
        raise PrivacyError("PROTECTED_FEATURE_IN_LIVE_USE", ", ".join(banned))
    dup = (
        await conn.execute(
            text("SELECT 1 FROM privacy.model_version WHERE model_ref = :r AND version = :v"),
            {"r": model_ref, "v": version},
        )
    ).first()
    if dup:
        raise PrivacyError("MODEL_VERSION_EXISTS", "a version is never re-registered")
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.model_version (model_id, tenant_id, model_ref, version, kind, provider_service_ref, artifact_sha256,
                    live_features, state, created_by) VALUES (:id, :t, :r, :v, :k, :ps, :sha, :f, 'REGISTERED', :by) RETURNING *
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "r": model_ref,
                "v": version,
                "k": kind,
                "ps": provider_service_ref,
                "sha": artifact_sha256,
                "f": sorted(set(live_features)),
                "by": _u(actor),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.model.registered",
        actor=actor,
        target_type="model_version",
        target_ref=f"{model_ref}@{version}",
    )
    return _view(row)


async def record_evaluation(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    model_ref: str,
    version: str,
    evaluator_ref: str,
    independent: bool,
    cohorts: int,
    metrics: dict[str, Any],
    accessible_alternative_confirmed: bool,
    audit_attributes: list[str],
    report_ref: str | None,
) -> dict[str, Any]:
    m = await _get(conn, model_ref, version)
    problems = rules.evaluation_problems(m.kind, metrics)
    # Audit attributes are kept apart from live inputs: they may name a protected trait for a lawful fairness audit, and never reach the model.
    clash = sorted(set(audit_attributes) & set(m.live_features))
    if clash:
        raise PrivacyError("AUDIT_ATTRIBUTE_IS_A_LIVE_FEATURE", ", ".join(clash))
    row = (
        await conn.execute(
            text("""
                INSERT INTO privacy.model_evaluation (evaluation_id, tenant_id, model_id, evaluator_ref, independent, cohorts, metrics,
                    accessible_alternative_confirmed, audit_attributes, report_ref, recorded_by)
                VALUES (:id, :t, :m, :e, :i, :c, CAST(:mx AS jsonb), :a, :aa, :rr, :by) RETURNING evaluation_id, created_at
            """),
            {
                "id": uuid7(),
                "t": _u(tenant_id),
                "m": m.model_id,
                "e": evaluator_ref,
                "i": independent,
                "c": cohorts,
                "mx": json.dumps(metrics),
                "a": accessible_alternative_confirmed,
                "aa": sorted(set(audit_attributes)),
                "rr": report_ref,
                "by": _u(actor),
            },
        )
    ).one()
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.model.evaluated",
        actor=actor,
        target_type="model_version",
        target_ref=f"{model_ref}@{version}",
        details={"independent": independent},
    )
    return {
        "evaluation_id": str(row.evaluation_id),
        "model_ref": model_ref,
        "version": version,
        "problems": problems,
        "usable_for_promotion": not problems and independent,
    }


async def _latest_evaluation(conn: AsyncConnection, model_id: Any) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT independent, cohorts, metrics, accessible_alternative_confirmed, created_at "
                "FROM privacy.model_evaluation WHERE model_id = :m ORDER BY created_at DESC LIMIT 1"
            ),
            {"m": model_id},
        )
    ).first()
    return dict(row._mapping) if row else None


async def promote(
    conn: AsyncConnection, tenant_id: str, actor: str, model_ref: str, version: str
) -> dict[str, Any]:
    m = await _get(conn, model_ref, version)
    blockers = rules.promotion_blockers(
        kind=m.kind,
        state=m.state,
        evaluation=await _latest_evaluation(conn, m.model_id),
        now=datetime.now(UTC),
    )
    if blockers:
        raise PrivacyError("PROMOTION_BLOCKED", ", ".join(blockers))
    if str(m.created_by) == actor:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "the registrant cannot promote their own model"
        )
    await conn.execute(  # the previous version stops serving: rollback target stays RETIRED-but-listed, never deleted
        text(
            "UPDATE privacy.model_version SET state = 'RETIRED', state_reason = 'superseded' "
            "WHERE model_ref = :r AND state = 'ACTIVE'"
        ),
        {"r": model_ref},
    )
    await conn.execute(
        text(
            "UPDATE privacy.model_version SET state = 'ACTIVE', state_reason = NULL, promoted_by = :by, promoted_at = now() "
            "WHERE model_id = :m"
        ),
        {"by": _u(actor), "m": m.model_id},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.model.promoted",
        actor=actor,
        target_type="model_version",
        target_ref=f"{model_ref}@{version}",
    )
    return await get(conn, model_ref, version)


async def observe(
    conn: AsyncConnection, tenant_id: str, actor: str, model_ref: str, seen_version: str
) -> dict[str, Any]:
    active = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.model_version WHERE model_ref = :r AND state = 'ACTIVE' FOR UPDATE"
            ),
            {"r": model_ref},
        )
    ).first()
    if active is None:
        raise PrivacyError("NOT_FOUND", "no version of this model is in service")
    verdict = rules.observe(active.version, seen_version)
    await conn.execute(
        text("""
            INSERT INTO privacy.model_observation (observation_id, tenant_id, model_ref, pinned_version, seen_version, verdict, observed_by)
            VALUES (:id, :t, :r, :p, :s, :v, :by)
        """),
        {
            "id": uuid7(),
            "t": _u(tenant_id),
            "r": model_ref,
            "p": active.version,
            "s": seen_version,
            "v": verdict,
            "by": _u(actor),
        },
    )
    if (
        verdict == "DRIFT"
    ):  # rollout blocked: nothing new is decided with a model that is not the one evaluated
        await conn.execute(
            text(
                "UPDATE privacy.model_version SET state = 'HELD', state_reason = :why WHERE model_id = :m"
            ),
            {
                "why": f"version drift: pinned {active.version}, saw {seen_version}",
                "m": active.model_id,
            },
        )
        await v1._audit(
            conn,
            tenant_id,
            None,
            "privacy.model.drift_held",
            actor=actor,
            target_type="model_version",
            target_ref=f"{model_ref}@{active.version}",
            details={"seen": seen_version},
        )
    return {
        "model_ref": model_ref,
        "pinned_version": active.version,
        "seen_version": seen_version,
        "verdict": verdict,
        "held": verdict == "DRIFT",
    }


async def rollback(
    conn: AsyncConnection, tenant_id: str, actor: str, model_ref: str, to_version: str
) -> dict[str, Any]:
    """Put a previously evaluated version back in service. It needs no new evidence only if its own independent evaluation is still current;
    the same gate applies, so a rollback can never bypass the evidence a promotion needs. A held model is released only this way."""
    m = await _get(conn, model_ref, to_version)
    if m.state not in ("RETIRED", "HELD"):
        raise PrivacyError("MODEL_STATE_NOT_PROMOTABLE", m.state)
    blockers = rules.promotion_blockers(
        kind=m.kind,
        state="REGISTERED",
        evaluation=await _latest_evaluation(conn, m.model_id),
        now=datetime.now(UTC),
    )
    if blockers:
        raise PrivacyError("ROLLBACK_BLOCKED", ", ".join(blockers))
    await conn.execute(
        text(
            "UPDATE privacy.model_version SET state = 'RETIRED', state_reason = 'rolled back from' "
            "WHERE model_ref = :r AND state IN ('ACTIVE', 'HELD') AND model_id <> :m"
        ),
        {"r": model_ref, "m": m.model_id},
    )
    await conn.execute(
        text(
            "UPDATE privacy.model_version SET state = 'ACTIVE', state_reason = 'rolled back', promoted_by = :by, promoted_at = now() "
            "WHERE model_id = :m"
        ),
        {"by": _u(actor), "m": m.model_id},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.model.rolled_back",
        actor=actor,
        target_type="model_version",
        target_ref=f"{model_ref}@{to_version}",
    )
    return await get(conn, model_ref, to_version)


async def get(conn: AsyncConnection, model_ref: str, version: str) -> dict[str, Any]:
    m = await _get(conn, model_ref, version)
    out = _view(m)
    ev = await _latest_evaluation(conn, m.model_id)
    out["latest_evaluation"] = (
        None
        if ev is None
        else {
            "independent": ev["independent"],
            "cohorts": ev["cohorts"],
            "created_at": ev["created_at"].isoformat(),
        }
    )
    out["promotion_blockers"] = (
        rules.promotion_blockers(kind=m.kind, state=m.state, evaluation=ev, now=datetime.now(UTC))
        if m.state in ("REGISTERED", "HELD")
        else []
    )
    return out


async def blockers_for(conn: AsyncConnection, model_version: str | None) -> list[str]:
    """What a DPIA package or release gate adds for the model it names: `model_ref@version` must be the one ACTIVE in service."""
    if not model_version:
        return []
    ref, _, version = model_version.partition("@")
    row = (
        await conn.execute(
            text("SELECT state FROM privacy.model_version WHERE model_ref = :r AND version = :v"),
            {"r": ref, "v": version},
        )
    ).first()
    if row is None:
        return ["MODEL_NOT_IN_INVENTORY"]
    return rules.release_blockers([{"state": row.state}])


# PROVISIONAL mapping (CR-109): which model kind backs which biometric method.
METHOD_KIND = {"FACE_MATCH": "face_match", "LIVENESS": "pad"}


async def method_evidence(
    conn: AsyncConnection, method: str, model_ref: str, version: str
) -> dict[str, Any]:
    """The independent evaluation a biometric method rests on, read live for the caller's tenant (WF-04 BIO-10/11, QA-015; CR-109).

    The engine `model_ref@version` must be in this tenant's model inventory with the kind the method needs, ACTIVE (promoted on its evidence and
    pinned), and its LATEST evaluation must still meet the promotion rules: independent, at least two cohorts compared, the accessible alternative
    confirmed, every measure with its confidence interval, and not stale. A reference string alone counts for nothing."""
    kind = METHOD_KIND.get(method)
    row = (
        await conn.execute(
            text(
                "SELECT model_id, kind, state FROM privacy.model_version WHERE model_ref = :r AND version = :v"
            ),
            {"r": model_ref, "v": version},
        )
    ).first()
    if row is None:
        return {"ready": False, "blockers": ["MODEL_NOT_IN_INVENTORY"], "evaluation_id": None}
    blockers: list[str] = []
    if row.kind != kind:
        blockers.append("MODEL_KIND_MISMATCH")
    if row.state != "ACTIVE":
        blockers.append(f"MODEL_{row.state}")
    latest = (
        await conn.execute(
            text(
                "SELECT evaluation_id, independent, cohorts, metrics, accessible_alternative_confirmed, created_at "
                "FROM privacy.model_evaluation WHERE model_id = :m ORDER BY created_at DESC LIMIT 1"
            ),
            {"m": row.model_id},
        )
    ).first()
    evaluation = dict(latest._mapping) if latest else None
    blockers += [
        b
        for b in rules.promotion_blockers(
            kind=row.kind, state="REGISTERED", evaluation=evaluation, now=datetime.now(UTC)
        )
        if b != "MODEL_STATE_NOT_PROMOTABLE"
    ]
    return {
        "ready": not blockers,
        "blockers": blockers,
        "evaluation_id": str(latest.evaluation_id) if latest else None,
    }


async def active_model_version(conn: AsyncConnection, model_ref: str, method: str) -> str | None:
    """The version of this tenant's ACTIVE (pinned) model `model_ref` of the kind a biometric method needs, or None (CR-110). For a provider-hosted
    method this IS the engine version: a provider that reports another version is drift, and `observe` holds the model."""
    row = (
        await conn.execute(
            text(
                "SELECT version FROM privacy.model_version WHERE model_ref = :r AND kind = :k AND state = 'ACTIVE'"
            ),
            {"r": model_ref, "k": METHOD_KIND.get(method, "")},
        )
    ).first()
    return str(row.version) if row else None


async def list_models(conn: AsyncConnection, *, limit: int = 200) -> list[dict[str, Any]]:
    """The tenant's model inventory with each version's latest evaluation and its fairness state (WF-04 B07; CR-110). Aggregate measures only:
    no person, image or template is stored or returned."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.model_version ORDER BY model_ref, created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    now = datetime.now(UTC)
    out = []
    for r in rows:
        latest = (
            await conn.execute(
                text(
                    "SELECT evaluation_id, evaluator_ref, independent, cohorts, metrics, accessible_alternative_confirmed, report_ref, created_at "
                    "FROM privacy.model_evaluation WHERE model_id = :m ORDER BY created_at DESC LIMIT 1"
                ),
                {"m": r.model_id},
            )
        ).first()
        evaluation = dict(latest._mapping) if latest else None
        state, reasons = rules.fairness_state(r.kind, evaluation, now=now)
        view = _view(r)
        view["fairness"] = {"state": state, "reasons": reasons}
        view["latest_evaluation"] = (
            {
                **{k: v for k, v in evaluation.items() if k != "created_at"},
                "evaluation_id": str(evaluation["evaluation_id"]),
                "created_at": evaluation["created_at"].isoformat(),
            }
            if evaluation
            else None
        )
        out.append(view)
    return out
