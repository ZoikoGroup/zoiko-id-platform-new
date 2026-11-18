"""Evidence packages for a screening case (ZID-DTV-WF-06 S13, ENG-16; CR-113).

A package is built from the case's own records by `domain.evidence_export`: the open kinds (what was checked and decided) always, and the sensitive kinds (the subject's details, the candidates) only
when a second person who is not the requester has approved, and then only as hashes of their personal fields. The bundle is stored already redacted, with its manifest hash, so what is held is what an
auditor is handed; it expires, and when it does it is cleared. Every function runs under the case's own tenant.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.application import screening_requests as requests
from zid_platform.modules.screening.domain import evidence_export as ex
from zid_platform.modules.screening.domain.decision import ScreeningError


async def _artifacts(
    conn: AsyncConnection, request_id: str, *, now: datetime, max_age: timedelta
) -> tuple[list[ex.Artifact], list[str]]:
    detail = await requests.restricted_detail(conn, request_id, now=now, max_age=max_age)
    model = detail["model"]
    subject = detail["subject"] or {}
    artifacts = [
        ex.Artifact(
            "REQUEST_AUTHORISATION",
            "request",
            {
                "market": detail["market"],
                "purpose": detail["purpose"],
                "state": detail["state"],
                "revision": detail["revision"],
            },
        ),
        ex.Artifact("POLICY", "rule", {"rule_version": model["decision"]["rule_version"]}),
        ex.Artifact(
            "COVERAGE",
            "coverage",
            {
                k: model[k]
                for k in (
                    "coverage_status",
                    "sanctions",
                    "pep_rca",
                    "adverse_media",
                    "ownership_control",
                    "match_status",
                    "review",
                )
            },
        ),
        ex.Artifact("DISPOSITION", "decision", {"decision": model["decision"]}),
        *(
            ex.Artifact(
                "SOURCE_SNAPSHOT",
                s["source_id"],
                {k: s[k] for k in ("source_id", "snapshot", "match_status", "screened_at")},
            )
            for s in model["source_results"]
        ),
        ex.Artifact(
            "RAW_INPUT",
            "subject",
            {
                "name": subject.get("name"),
                "birth": subject.get("birth"),
                "countries": subject.get("countries"),
                "identifiers": subject.get("identifiers"),
                "identity_source": subject.get("identity_source"),
            },
        ),
        *(
            ex.Artifact(
                "MATCH_CANDIDATE",
                c["candidate_id"],
                {
                    "name": c["matched_name"],
                    "list_id": c["list_id"],
                    "list_version": c["list_version"],
                    "score": c["score"],
                    "strength": c["strength"],
                    "reasons": c["reasons"],
                },
            )
            for c in detail["candidates"]
        ),
    ]
    chain = [d["case_hash"] for d in detail["decisions"]]
    return artifacts, chain


def _bundle_json(b: ex.Bundle) -> str:
    return json.dumps(
        {
            "case_id": b.case_id,
            "purpose": b.purpose,
            "requester": b.requester,
            "approver": b.approver,
            "policy_version": b.policy_version,
            "created_at": b.created_at.isoformat(),
            "expires_at": b.expires_at.isoformat(),
            "watermark": b.watermark,
            "included": list(b.included),
            "excluded": list(b.excluded),
            "chain": list(b.chain),
            "manifest_hash": b.manifest_hash,
        },
        default=str,
    )


async def _build(
    conn: AsyncConnection,
    request_id: str,
    *,
    purpose: str,
    requester: str,
    approver: str | None,
    include_sensitive: bool,
    ttl: timedelta,
    now: datetime,
    max_age: timedelta,
) -> ex.Bundle:
    artifacts, chain = await _artifacts(conn, request_id, now=now, max_age=max_age)
    policy = await requests.case_policy_version(conn, request_id)
    try:
        return ex.build(
            request_id,
            artifacts,
            purpose=purpose,
            requester=requester,
            approver=approver,
            policy_version=policy,
            chain=chain,
            now=now,
            ttl=ttl,
            include_sensitive=include_sensitive,
        )
    except ex.ExportError as e:
        raise ScreeningError(e.code, e.detail) from e


async def request_export(
    conn: AsyncConnection,
    request_id: str,
    *,
    requester: str,
    purpose: str,
    include_sensitive: bool,
    ttl: timedelta,
    now: datetime,
    max_age: timedelta,
) -> dict[str, Any]:
    """Ask for a package. Without sensitive material it is built now; with it, it waits for a second approver."""
    row = (
        await conn.execute(
            text("SELECT tenant_id FROM verify.screening_request WHERE request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    ).first()
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    export_id = uuid.uuid4()
    bundle: ex.Bundle | None = None
    if not include_sensitive:
        bundle = await _build(
            conn,
            request_id,
            purpose=purpose,
            requester=requester,
            approver=None,
            include_sensitive=False,
            ttl=ttl,
            now=now,
            max_age=max_age,
        )
    elif len(purpose.strip()) < 10:
        raise ScreeningError("PURPOSE_REQUIRED", "say what the export is for")
    await conn.execute(
        text(
            "INSERT INTO verify.screening_evidence_export (export_id, request_id, tenant_id, purpose, requester, include_sensitive, state, bundle, manifest_hash, ttl_seconds, created_at, expires_at) "
            "VALUES (:e, :r, :t, :p, :q, :s, :st, CAST(:b AS jsonb), :h, :ttl, :at, :exp)"
        ),
        {
            "e": export_id,
            "r": uuid.UUID(request_id),
            "t": row.tenant_id,
            "p": purpose.strip(),
            "q": requester,
            "s": include_sensitive,
            "st": "READY" if bundle else "PENDING",
            "b": _bundle_json(bundle) if bundle else None,
            "h": bundle.manifest_hash if bundle else None,
            "ttl": int(ttl.total_seconds()),
            "at": now,
            "exp": bundle.expires_at if bundle else None,
        },
    )
    return {
        "export_id": str(export_id),
        "state": "READY" if bundle else "PENDING",
        "manifest_hash": bundle.manifest_hash if bundle else None,
    }


async def export_tenant(conn: AsyncConnection, export_id: str) -> tuple[str, str] | None:
    """(tenant id, case id) of a package, for a screening reviewer (the reviewer's view across tenants, set only for this read)."""
    try:
        key = uuid.UUID(export_id)
    except ValueError:
        return None
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        row = (
            await conn.execute(
                text(
                    "SELECT tenant_id, request_id FROM verify.screening_evidence_export WHERE export_id = :e"
                ),
                {"e": key},
            )
        ).first()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    return None if row is None else (str(row.tenant_id), str(row.request_id))


async def requester_of(conn: AsyncConnection, export_id: str) -> str | None:
    row = (
        await conn.execute(
            text("SELECT requester FROM verify.screening_evidence_export WHERE export_id = :e"),
            {"e": uuid.UUID(export_id)},
        )
    ).first()
    return None if row is None else str(row.requester)


async def approve_export(
    conn: AsyncConnection, export_id: str, *, approver: str, now: datetime, max_age: timedelta
) -> dict[str, Any]:
    """A second person who is not the requester approves a package with sensitive material; it is then built, redacted to hashes."""
    row = (
        await conn.execute(
            text(
                "SELECT request_id, purpose, requester, include_sensitive, state, ttl_seconds FROM verify.screening_evidence_export WHERE export_id = :e FOR UPDATE"
            ),
            {"e": uuid.UUID(export_id)},
        )
    ).first()
    if row is None:
        raise ScreeningError("EXPORT_NOT_FOUND", export_id)
    if row.state != "PENDING" or not row.include_sensitive:
        raise ScreeningError("EXPORT_NOT_PENDING", row.state)
    bundle = await _build(
        conn,
        str(row.request_id),
        purpose=row.purpose,
        requester=row.requester,
        approver=approver,
        include_sensitive=True,
        ttl=timedelta(seconds=int(row.ttl_seconds)),
        now=now,
        max_age=max_age,
    )
    await conn.execute(
        text(
            "UPDATE verify.screening_evidence_export SET state = 'READY', approver = :a, bundle = CAST(:b AS jsonb), manifest_hash = :h, expires_at = :x WHERE export_id = :e"
        ),
        {
            "a": approver,
            "b": _bundle_json(bundle),
            "h": bundle.manifest_hash,
            "x": bundle.expires_at,
            "e": uuid.UUID(export_id),
        },
    )
    return {"export_id": export_id, "state": "READY", "manifest_hash": bundle.manifest_hash}


async def fetch(conn: AsyncConnection, export_id: str, *, now: datetime) -> dict[str, Any]:
    """The package, while it is READY and unexpired. An expired package is cleared and says so."""
    row = (
        await conn.execute(
            text(
                "SELECT state, bundle, manifest_hash, expires_at, purpose, requester, approver FROM verify.screening_evidence_export WHERE export_id = :e FOR UPDATE"
            ),
            {"e": uuid.UUID(export_id)},
        )
    ).first()
    if row is None:
        raise ScreeningError("EXPORT_NOT_FOUND", export_id)
    if row.state == "READY" and row.expires_at is not None and now >= row.expires_at:
        await conn.execute(
            text(
                "UPDATE verify.screening_evidence_export SET state = 'EXPIRED', bundle = NULL WHERE export_id = :e"
            ),
            {"e": uuid.UUID(export_id)},
        )
        raise ScreeningError("EXPORT_EXPIRED", export_id)
    if row.state == "EXPIRED":
        raise ScreeningError("EXPORT_EXPIRED", export_id)
    if row.state != "READY":
        raise ScreeningError("EXPORT_NOT_READY", row.state)
    return {"export_id": export_id, "manifest_hash": row.manifest_hash, "bundle": row.bundle}


def verify_stored(bundle: dict[str, Any], *, now: datetime) -> list[str]:
    """What is wrong with a fetched package, as an auditor would check it. Empty means it is intact and in date."""
    b = ex.Bundle(
        case_id=bundle["case_id"],
        purpose=bundle["purpose"],
        requester=bundle["requester"],
        approver=bundle["approver"],
        policy_version=bundle["policy_version"],
        created_at=datetime.fromisoformat(bundle["created_at"]),
        expires_at=datetime.fromisoformat(bundle["expires_at"]),
        watermark=bundle["watermark"],
        included=tuple(bundle["included"]),
        excluded=tuple(bundle["excluded"]),
        chain=tuple(bundle["chain"]),
        manifest_hash=bundle["manifest_hash"],
    )
    return ex.verify(b, now=now) + [f"PERSONAL_FIELD:{p}" for p in ex.leaks_personal_data(b)]


_ = asdict  # kept for callers that serialise a bundle directly
