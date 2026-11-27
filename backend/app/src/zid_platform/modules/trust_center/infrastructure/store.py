"""PostgreSQL persistence of the Trust Center's claims register (CR-174). The database enforces the same rules as the domain: the author cannot review or publish,
approvals bind to one content hash, publication needs two approvers, a withdrawn revision stays withdrawn."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.trust_center.domain import claims as cl
from zid_platform.modules.trust_center.domain import evidence as ev


def _claim(r: Any) -> cl.Claim:
    s = r.scope
    return cl.Claim(
        claim_id=str(r.claim_id),
        revision=r.revision,
        exact_text=r.exact_text,
        locale=r.locale,
        claim_type=cl.ClaimType(r.claim_type),
        scope=cl.Scope(
            s["entity_id"], s["service"], tuple(s["markets"]), s.get("environment", "production")
        ),
        channels=tuple(r.channels),
        evidence_digests=tuple(r.evidence_digests),
        valid_until=r.valid_until,
        author=str(r.author),
        status=cl.Status(r.status),
    )


def _artifact(r: Any) -> ev.Artifact:
    return ev.Artifact(
        evidence_id=str(r.evidence_id), evidence_type=ev.EvidenceType(r.evidence_type), scheme=r.scheme, issuer=r.issuer, assessor=r.assessor,
        assessed=ev.Assessed(r.legal_entity, frozenset(r.products), frozenset(r.regions), r.period_start, r.period_end, tuple(r.exclusions)),
        digest=r.digest, issuer_confirmed=r.issuer_confirmed, holder=r.holder, lifecycle=ev.Lifecycle(r.lifecycle), restricted=r.restricted,
    )  # fmt: skip


def _approval(r: Any) -> cl.Approval:
    return cl.Approval(
        approver=str(r.approver), role=cl.Role(r.role), claim_hash=r.claim_hash, evidence_digests=tuple(r.evidence_digests), decided_at=r.decided_at,
        effective_until=r.effective_until, refused=r.refused, reason=r.reason,
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------ evidence


async def insert_evidence(conn: AsyncConnection, *, actor: str, digest: str, evidence_type: str, scheme: str, issuer: str, assessor: str, legal_entity: str, products: Sequence[str], regions: Sequence[str], exclusions: Sequence[str], period_start: date, period_end: date, holder: str, restricted: bool) -> ev.Artifact:  # fmt: skip
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tc_evidence (evidence_id, digest, evidence_type, scheme, issuer, assessor, legal_entity, products, regions, exclusions,
                                                  period_start, period_end, holder, restricted, recorded_by)
                VALUES (:id, :digest, :type, :scheme, :issuer, :assessor, :entity, :products, :regions, :exclusions, :ps, :pe, :holder, :restricted, :actor)
                RETURNING *"""),
            {
                "id": uuid.uuid4(),
                "digest": digest,
                "type": evidence_type,
                "scheme": scheme,
                "issuer": issuer,
                "assessor": assessor,
                "entity": legal_entity,
                "products": list(products),
                "regions": list(regions),
                "exclusions": list(exclusions),
                "ps": period_start,
                "pe": period_end,
                "holder": holder,
                "restricted": restricted,
                "actor": uuid.UUID(actor),
            },
        )
    ).one()
    return _artifact(row)


async def confirm_evidence(
    conn: AsyncConnection, evidence_id: str, *, actor: str, now: datetime
) -> ev.Artifact | None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.tc_evidence SET issuer_confirmed = true, confirmed_by = :a, confirmed_at = :now WHERE evidence_id = :id AND NOT issuer_confirmed RETURNING *"
            ),
            {"a": uuid.UUID(actor), "now": now, "id": uuid.UUID(evidence_id)},
        )
    ).first()
    return _artifact(row) if row else None


async def set_lifecycle(
    conn: AsyncConnection, evidence_id: str, lifecycle: str
) -> ev.Artifact | None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.tc_evidence SET lifecycle = :l WHERE evidence_id = :id RETURNING *"
            ),
            {"l": lifecycle, "id": uuid.UUID(evidence_id)},
        )
    ).first()
    return _artifact(row) if row else None


async def evidence_by_id(conn: AsyncConnection, evidence_id: str) -> ev.Artifact | None:
    row = (
        await conn.execute(
            text("SELECT * FROM platform.tc_evidence WHERE evidence_id = :id"),
            {"id": uuid.UUID(evidence_id)},
        )
    ).first()
    return _artifact(row) if row else None


async def evidence_by_digests(
    conn: AsyncConnection, digests: Sequence[str]
) -> dict[str, ev.Artifact]:
    if not digests:
        return {}
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.tc_evidence WHERE digest = ANY(:d)"), {"d": list(digests)}
        )
    ).all()
    return {r.digest: _artifact(r) for r in rows}


async def list_evidence(conn: AsyncConnection, *, limit: int = 200) -> list[ev.Artifact]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.tc_evidence ORDER BY recorded_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_artifact(r) for r in rows]


async def claims_resting_on(conn: AsyncConnection, digest: str) -> list[tuple[str, int]]:
    rows = (
        await conn.execute(
            text(
                "SELECT claim_id, revision FROM platform.tc_claim_revision WHERE :d = ANY(evidence_digests) AND status IN ('PUBLISHED', 'APPROVAL_PENDING')"
            ),
            {"d": digest},
        )
    ).all()
    return [(str(r.claim_id), r.revision) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ claims


async def next_revision(conn: AsyncConnection, claim_id: str) -> int:
    n = (
        await conn.execute(
            text(
                "SELECT coalesce(max(revision), 0) FROM platform.tc_claim_revision WHERE claim_id = :c"
            ),
            {"c": uuid.UUID(claim_id)},
        )
    ).scalar_one()
    return int(n) + 1


async def insert_revision(
    conn: AsyncConnection, c: cl.Claim, *, content_hash: str, status: str
) -> None:
    import json

    await conn.execute(
        text("""
            INSERT INTO platform.tc_claim_revision (claim_id, revision, exact_text, locale, claim_type, scope, channels, evidence_digests, valid_until, author, content_hash, status)
            VALUES (:cid, :rev, :text, :locale, :type, CAST(:scope AS jsonb), :channels, :digests, :vu, :author, :hash, :status)"""),
        {"cid": uuid.UUID(c.claim_id), "rev": c.revision, "text": c.exact_text, "locale": c.locale, "type": str(c.claim_type),
         "scope": json.dumps({"entity_id": c.scope.entity_id, "service": c.scope.service, "markets": list(c.scope.markets), "environment": c.scope.environment}),
         "channels": list(c.channels), "digests": list(c.evidence_digests), "vu": c.valid_until, "author": uuid.UUID(c.author), "hash": content_hash, "status": status},
    )  # fmt: skip


async def load_revision(
    conn: AsyncConnection, claim_id: str, revision: int, *, lock: bool = False
) -> tuple[cl.Claim, str] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_claim_revision WHERE claim_id = :c AND revision = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": uuid.UUID(claim_id), "r": revision},
        )
    ).first()
    return (_claim(row), row.content_hash) if row else None


async def history(conn: AsyncConnection, claim_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("""
                SELECT r.revision, r.content_hash, r.status, r.created_at, r.published_at, r.withdrawn_at,
                       EXISTS (SELECT 1 FROM platform.tc_publication p WHERE p.claim_id = r.claim_id AND p.revision = r.revision AND p.kind = 'PUBLISH') AS was_published
                  FROM platform.tc_claim_revision r WHERE r.claim_id = :c ORDER BY r.revision"""),
            {"c": uuid.UUID(claim_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def list_claims(
    conn: AsyncConnection, *, status: str | None, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("""
                SELECT DISTINCT ON (claim_id) claim_id, revision, exact_text, locale, claim_type, status, valid_until, channels, created_at
                  FROM platform.tc_claim_revision WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY claim_id, revision DESC LIMIT :n"""),
            {"s": status, "n": limit},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def set_status(
    conn: AsyncConnection,
    claim_id: str,
    revision: int,
    status: str,
    *,
    published_at: datetime | None = None,
    withdrawn_at: datetime | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tc_claim_revision SET status = :s, published_at = coalesce(:p, published_at), withdrawn_at = coalesce(:w, withdrawn_at) WHERE claim_id = :c AND revision = :r"
        ),
        {
            "s": status,
            "p": published_at,
            "w": withdrawn_at,
            "c": uuid.UUID(claim_id),
            "r": revision,
        },
    )


async def supersede_others(conn: AsyncConnection, claim_id: str, keep: int) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tc_claim_revision SET status = 'SUPERSEDED' WHERE claim_id = :c AND revision <> :r AND status = 'PUBLISHED'"
        ),
        {"c": uuid.UUID(claim_id), "r": keep},
    )


# ------------------------------------------------------------------------------------------------------------------------------ approvals


async def insert_approval(
    conn: AsyncConnection, a: cl.Approval, *, claim_id: str, revision: int
) -> str:
    aid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.tc_approval (approval_id, claim_id, revision, claim_hash, approver, role, evidence_digests, refused, reason, decided_at, effective_until)
            VALUES (:id, :c, :r, :h, :a, :role, :d, :refused, :reason, :at, :until)"""),
        {"id": aid, "c": uuid.UUID(claim_id), "r": revision, "h": a.claim_hash, "a": uuid.UUID(a.approver), "role": str(a.role), "d": list(a.evidence_digests),
         "refused": a.refused, "reason": a.reason, "at": a.decided_at, "until": a.effective_until},
    )  # fmt: skip
    return str(aid)


async def approvals(conn: AsyncConnection, claim_id: str, revision: int) -> list[cl.Approval]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_approval WHERE claim_id = :c AND revision = :r ORDER BY decided_at"
            ),
            {"c": uuid.UUID(claim_id), "r": revision},
        )
    ).all()
    return [_approval(r) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ publication


async def insert_publication(conn: AsyncConnection, *, claim_id: str, revision: int, claim_hash: str, kind: str, rolled_back_from: int | None, channels: Sequence[str], locale: str, manifest_digest: str, actor: str, now: datetime) -> str:  # fmt: skip
    pid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.tc_publication (publication_id, claim_id, revision, claim_hash, kind, rolled_back_from, channels, locale, manifest_digest, published_by, published_at)
            VALUES (:id, :c, :r, :h, :k, :rb, :ch, :l, :m, :a, :at)"""),
        {"id": pid, "c": uuid.UUID(claim_id), "r": revision, "h": claim_hash, "k": kind, "rb": rolled_back_from, "ch": list(channels), "l": locale, "m": manifest_digest, "a": uuid.UUID(actor), "at": now},
    )  # fmt: skip
    return str(pid)


async def live_claims(conn: AsyncConnection) -> list[tuple[cl.Claim, str, datetime]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_claim_revision WHERE status = 'PUBLISHED' ORDER BY claim_id"
            )
        )
    ).all()
    return [(_claim(r), r.content_hash, r.published_at) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ withdrawals


async def insert_withdrawal(conn: AsyncConnection, w: cl.Withdrawal, *, revision: int) -> str:
    wid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO platform.tc_withdrawal (withdrawal_id, claim_id, revision, actor, reason, at, review_due) VALUES (:id, :c, :r, :a, :reason, :at, :due)"
        ),
        {
            "id": wid,
            "c": uuid.UUID(w.claim_id),
            "r": revision,
            "a": uuid.UUID(w.actor),
            "reason": w.reason,
            "at": w.at,
            "due": w.review_due,
        },
    )
    return str(wid)


async def review_withdrawal(
    conn: AsyncConnection, withdrawal_id: str, *, actor: str, note: str, now: datetime
) -> bool:
    r = await conn.execute(
        text(
            "UPDATE platform.tc_withdrawal SET reviewed_by = :a, reviewed_at = :now, review_note = :n WHERE withdrawal_id = :id AND reviewed_by IS NULL"
        ),
        {"a": uuid.UUID(actor), "now": now, "n": note, "id": uuid.UUID(withdrawal_id)},
    )
    return bool(r.rowcount)


async def withdrawals(conn: AsyncConnection, *, open_only: bool) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_withdrawal"
                + (" WHERE reviewed_by IS NULL" if open_only else "")
                + " ORDER BY at DESC LIMIT 200"
            )
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ availability snapshots


async def insert_snapshot(conn: AsyncConnection, *, actor: str, service: str, region: str, window_label: str, numerator: int, denominator: int, exclusions: str, source_ref: str) -> dict[str, Any]:  # fmt: skip
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tc_sli_snapshot (snapshot_id, service, region, window_label, numerator, denominator, exclusions, source_ref, recorded_by)
                VALUES (:id, :s, :r, :w, :n, :d, :e, :src, :a) RETURNING *"""),
            {
                "id": uuid.uuid4(),
                "s": service,
                "r": region,
                "w": window_label,
                "n": numerator,
                "d": denominator,
                "e": exclusions,
                "src": source_ref,
                "a": uuid.UUID(actor),
            },
        )
    ).one()
    return dict(row._mapping)


async def validate_snapshot(
    conn: AsyncConnection, snapshot_id: str, *, actor: str, now: datetime
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.tc_sli_snapshot SET validated_by = :a, validated_at = :now WHERE snapshot_id = :id AND validated_by IS NULL RETURNING *"
            ),
            {"a": uuid.UUID(actor), "now": now, "id": uuid.UUID(snapshot_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def snapshots(conn: AsyncConnection, *, validated_only: bool) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_sli_snapshot"
                + (" WHERE validated_by IS NOT NULL" if validated_only else "")
                + " ORDER BY window_label DESC, service, region LIMIT 500"
            )
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ advisories


async def advisory_for_incident(conn: AsyncConnection, incident_ref: str) -> str | None:
    r = (
        await conn.execute(
            text("SELECT advisory_id FROM platform.tc_advisory WHERE incident_ref = :i"),
            {"i": uuid.UUID(incident_ref)},
        )
    ).first()
    return str(r.advisory_id) if r else None


async def create_advisory(conn: AsyncConnection, *, incident_ref: str, actor: str) -> str:
    aid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO platform.tc_advisory (advisory_id, incident_ref, created_by) VALUES (:a, :i, :by)"
        ),
        {"a": aid, "i": uuid.UUID(incident_ref), "by": uuid.UUID(actor)},
    )
    return str(aid)


async def add_version(
    conn: AsyncConnection,
    *,
    advisory_id: str,
    kind: str,
    title: str,
    body: str,
    reason: str,
    author: str,
) -> int:
    seq = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(seq), 0) + 1 FROM platform.tc_advisory_version WHERE advisory_id = :a"
                ),
                {"a": uuid.UUID(advisory_id)},
            )
        ).scalar_one()
    )
    await conn.execute(
        text(
            "INSERT INTO platform.tc_advisory_version (advisory_id, seq, kind, title, body, reason, author) VALUES (:a, :q, :k, :t, :b, :r, :au)"
        ),
        {
            "a": uuid.UUID(advisory_id),
            "q": seq,
            "k": kind,
            "t": title,
            "b": body,
            "r": reason,
            "au": uuid.UUID(author),
        },
    )
    return seq


async def approve_version(
    conn: AsyncConnection, advisory_id: str, seq: int, *, actor: str, now: datetime
) -> bool:
    r = await conn.execute(
        text(
            "UPDATE platform.tc_advisory_version SET approved_by = :a, approved_at = :now WHERE advisory_id = :i AND seq = :q AND approved_by IS NULL"
        ),
        {"a": uuid.UUID(actor), "now": now, "i": uuid.UUID(advisory_id), "q": seq},
    )
    return bool(r.rowcount)


async def versions(conn: AsyncConnection, advisory_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.tc_advisory_version WHERE advisory_id = :a ORDER BY seq"),
            {"a": uuid.UUID(advisory_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def advisories(conn: AsyncConnection, *, public: bool) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT a.advisory_id, a.incident_ref, a.created_at FROM platform.tc_advisory a ORDER BY a.created_at DESC LIMIT 100"
            )
        )
    ).all()
    out = []
    for r in rows:
        vs = await versions(conn, str(r.advisory_id))
        if public:
            vs = [v for v in vs if v["approved_by"] is not None]
            if not vs:
                continue
        out.append(
            {
                "advisory_id": str(r.advisory_id),
                "incident_ref": str(r.incident_ref),
                "created_at": r.created_at,
                "versions": vs,
            }
        )
    return out


# ------------------------------------------------------------------------------------------------------------------------------ vulnerability reports


async def reports_from_source_since(
    conn: AsyncConnection, source_hash: str, since: datetime
) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.tc_report WHERE source_hash = :h AND received_at > :s"
                ),
                {"h": source_hash, "s": since},
            )
        ).scalar_one()
    )


async def reports_since(conn: AsyncConnection, since: datetime) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM platform.tc_report WHERE received_at > :s"), {"s": since}
            )
        ).scalar_one()
    )


async def insert_report(conn: AsyncConnection, *, reference: str, summary: str, affected: str, severity: str, contact: str | None, source_hash: str, ack_due: datetime, received: datetime) -> str:  # fmt: skip
    rid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.tc_report (report_id, reference, received_at, summary, affected, severity, contact, source_hash, ack_due)
            VALUES (:id, :ref, :at, :s, :a, :sev, :c, :h, :due)"""),
        {
            "id": rid,
            "ref": reference,
            "at": received,
            "s": summary,
            "a": affected,
            "sev": severity,
            "c": contact,
            "h": source_hash,
            "due": ack_due,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO platform.tc_report_event (report_id, seq, kind, actor, note, at) VALUES (:r, 1, 'RECEIVED', NULL, '', :at)"
        ),
        {"r": rid, "at": received},
    )
    return str(rid)


async def reports(conn: AsyncConnection, *, open_only: bool) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT report_id, reference, received_at, severity, state, assignee, ack_due, acknowledged_at FROM platform.tc_report"
                + (
                    " WHERE state NOT IN ('CLOSED', 'OUT_OF_SCOPE', 'DISCLOSED')"
                    if open_only
                    else ""
                )
                + " ORDER BY received_at DESC LIMIT 200"
            )
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def report_detail(conn: AsyncConnection, report_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text("SELECT * FROM platform.tc_report WHERE report_id = :i FOR UPDATE"),
            {"i": uuid.UUID(report_id)},
        )
    ).first()
    if r is None:
        return None
    ev = (
        await conn.execute(
            text(
                "SELECT seq, kind, actor, note, at FROM platform.tc_report_event WHERE report_id = :i ORDER BY seq"
            ),
            {"i": uuid.UUID(report_id)},
        )
    ).all()
    return {**dict(r._mapping), "timeline": [dict(e._mapping) for e in ev]}


async def move_report(conn: AsyncConnection, report_id: str, *, state: str | None, assignee: str | None, severity: str | None, actor: str, note: str, now: datetime) -> None:  # fmt: skip
    rid = uuid.UUID(report_id)
    seq = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(seq), 0) + 1 FROM platform.tc_report_event WHERE report_id = :i"
                ),
                {"i": rid},
            )
        ).scalar_one()
    )
    if state:
        await conn.execute(
            text(
                "UPDATE platform.tc_report SET state = :s, acknowledged_at = CASE WHEN :s = 'ACKNOWLEDGED' THEN :now ELSE acknowledged_at END WHERE report_id = :i"
            ),
            {"s": state, "now": now, "i": rid},
        )
    if assignee:
        await conn.execute(
            text("UPDATE platform.tc_report SET assignee = :a WHERE report_id = :i"),
            {"a": uuid.UUID(assignee), "i": rid},
        )
    if severity:
        await conn.execute(
            text("UPDATE platform.tc_report SET severity = :s WHERE report_id = :i"),
            {"s": severity, "i": rid},
        )
    kind = "STATE" if state else "ASSIGNED" if assignee else "SEVERITY" if severity else "NOTE"
    await conn.execute(
        text(
            "INSERT INTO platform.tc_report_event (report_id, seq, kind, actor, note, at) VALUES (:i, :q, :k, :a, :n, :at)"
        ),
        {
            "i": rid,
            "q": seq,
            "k": kind,
            "a": uuid.UUID(actor),
            "n": (f"{state or severity or assignee or ''} {note}").strip()[:4000],
            "at": now,
        },
    )


# ------------------------------------------------------------------------------------------------------------------------------ privacy disclosures


async def insert_disclosure(
    conn: AsyncConnection,
    *,
    kind: str,
    subject: str,
    market: str,
    payload: dict[str, Any],
    effective_from: date,
    author: str,
) -> dict[str, Any]:
    import json

    rev = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(revision), 0) + 1 FROM platform.tc_disclosure WHERE kind = :k AND subject = :s AND market = :m"
                ),
                {"k": kind, "s": subject, "m": market},
            )
        ).scalar_one()
    )
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tc_disclosure (disclosure_id, kind, subject, market, revision, payload, effective_from, author)
                VALUES (:id, :k, :s, :m, :r, CAST(:p AS jsonb), :ef, :a) RETURNING *"""),
            {
                "id": uuid.uuid4(),
                "k": kind,
                "s": subject,
                "m": market,
                "r": rev,
                "p": json.dumps(payload, sort_keys=True),
                "ef": effective_from,
                "a": uuid.UUID(author),
            },
        )
    ).one()
    return dict(row._mapping)


async def load_disclosure(conn: AsyncConnection, disclosure_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text("SELECT * FROM platform.tc_disclosure WHERE disclosure_id = :i FOR UPDATE"),
            {"i": uuid.UUID(disclosure_id)},
        )
    ).first()
    return dict(r._mapping) if r else None


async def activate_disclosure(
    conn: AsyncConnection, d: dict[str, Any], *, actor: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tc_disclosure SET status = 'SUPERSEDED' WHERE kind = :k AND subject = :s AND market = :m AND status = 'ACTIVE'"
        ),
        {"k": d["kind"], "s": d["subject"], "m": d["market"]},
    )
    await conn.execute(
        text(
            "UPDATE platform.tc_disclosure SET approved_by = :a, approved_at = :now, status = 'ACTIVE' WHERE disclosure_id = :i"
        ),
        {"a": uuid.UUID(actor), "now": now, "i": d["disclosure_id"]},
    )


async def withdraw_disclosure(conn: AsyncConnection, disclosure_id: str) -> None:
    await conn.execute(
        text("UPDATE platform.tc_disclosure SET status = 'WITHDRAWN' WHERE disclosure_id = :i"),
        {"i": uuid.UUID(disclosure_id)},
    )


async def disclosures(
    conn: AsyncConnection, *, market: str | None, active_only: bool
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tc_disclosure WHERE (CAST(:m AS text) IS NULL OR market IN (:m, 'GLOBAL')) AND (NOT :act OR status = 'ACTIVE') ORDER BY kind, subject, revision DESC LIMIT 500"
            ),
            {"m": market, "act": active_only},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ evidence room


async def insert_blob(
    conn: AsyncConnection, *, evidence_id: str, content: bytes, content_type: str, actor: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.tc_evidence_blob (evidence_id, content, content_type, size_bytes, scan_state, uploaded_by) VALUES (:e, :c, :t, :n, 'CLEAN', :a)"
        ),
        {
            "e": uuid.UUID(evidence_id),
            "c": content,
            "t": content_type,
            "n": len(content),
            "a": uuid.UUID(actor),
        },
    )


async def load_blob(conn: AsyncConnection, evidence_id: str) -> tuple[bytes, str] | None:
    r = (
        await conn.execute(
            text(
                "SELECT content, content_type FROM platform.tc_evidence_blob WHERE evidence_id = :e"
            ),
            {"e": uuid.UUID(evidence_id)},
        )
    ).first()
    return (bytes(r.content), r.content_type) if r else None


async def has_blob(conn: AsyncConnection, evidence_id: str) -> bool:
    return (
        await conn.execute(
            text("SELECT 1 FROM platform.tc_evidence_blob WHERE evidence_id = :e"),
            {"e": uuid.UUID(evidence_id)},
        )
    ).first() is not None


async def insert_request(conn: AsyncConnection, *, tenant: str, requester: str, organization: str, purpose: str, evidence_id: str, terms_version: str, now: datetime) -> dict[str, Any]:  # fmt: skip
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tc_room_request (request_id, tenant_id, requester, organization, purpose, evidence_id, terms_accepted_at, terms_version)
                VALUES (:id, :t, :r, :o, :p, :e, :now, :v) RETURNING *"""),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant),
                "r": uuid.UUID(requester),
                "o": organization,
                "p": purpose,
                "e": uuid.UUID(evidence_id),
                "now": now,
                "v": terms_version,
            },
        )
    ).one()
    return dict(row._mapping)


async def requests(
    conn: AsyncConnection, *, tenant: str | None, open_only: bool = False
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT r.*, g.grant_id, g.expires_at, g.revoked_at, g.downloads, g.max_downloads FROM platform.tc_room_request r LEFT JOIN platform.tc_room_grant g ON g.request_id = r.request_id WHERE (CAST(:t AS uuid) IS NULL OR r.tenant_id = :t) AND (NOT :o OR r.status = 'REQUESTED') ORDER BY r.created_at DESC LIMIT 200"
            ),
            {"t": uuid.UUID(tenant) if tenant else None, "o": open_only},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def load_request(conn: AsyncConnection, request_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text("SELECT * FROM platform.tc_room_request WHERE request_id = :i FOR UPDATE"),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    return dict(r._mapping) if r else None


async def decide_request(
    conn: AsyncConnection, request_id: str, *, status: str, actor: str, reason: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tc_room_request SET status = :s, decided_by = :a, decided_at = :now, decision_reason = :r WHERE request_id = :i"
        ),
        {"s": status, "a": uuid.UUID(actor), "now": now, "r": reason, "i": uuid.UUID(request_id)},
    )


async def insert_grant(
    conn: AsyncConnection,
    *,
    request_id: str,
    token_hash: str,
    expires_at: datetime,
    max_downloads: int,
    watermark: str,
) -> str:
    gid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO platform.tc_room_grant (grant_id, request_id, token_hash, expires_at, max_downloads, watermark) VALUES (:g, :r, :h, :e, :m, :w)"
        ),
        {
            "g": gid,
            "r": uuid.UUID(request_id),
            "h": token_hash,
            "e": expires_at,
            "m": max_downloads,
            "w": watermark,
        },
    )
    return str(gid)


async def grant_by_hash(conn: AsyncConnection, token_hash: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text(
                "SELECT g.*, r.tenant_id, r.requester, r.evidence_id, r.status AS request_status FROM platform.tc_room_grant g JOIN platform.tc_room_request r ON r.request_id = g.request_id WHERE g.token_hash = :h FOR UPDATE OF g"
            ),
            {"h": token_hash},
        )
    ).first()
    return dict(r._mapping) if r else None


async def grant_for_request(conn: AsyncConnection, request_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text("SELECT * FROM platform.tc_room_grant WHERE request_id = :i FOR UPDATE"),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    return dict(r._mapping) if r else None


async def bump_download(conn: AsyncConnection, grant_id: str) -> None:
    await conn.execute(
        text("UPDATE platform.tc_room_grant SET downloads = downloads + 1 WHERE grant_id = :g"),
        {"g": uuid.UUID(grant_id)},
    )


async def revoke_grant(conn: AsyncConnection, grant_id: str, *, actor: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tc_room_grant SET revoked_at = :now, revoked_by = :a WHERE grant_id = :g AND revoked_at IS NULL"
        ),
        {"now": now, "a": uuid.UUID(actor), "g": uuid.UUID(grant_id)},
    )
    await conn.execute(
        text(
            "UPDATE platform.tc_room_request SET status = 'REVOKED' WHERE request_id = (SELECT request_id FROM platform.tc_room_grant WHERE grant_id = :g) AND status = 'APPROVED'"
        ),
        {"g": uuid.UUID(grant_id)},
    )


async def record_access(
    conn: AsyncConnection,
    *,
    grant_id: str,
    principal: str | None,
    action: str,
    outcome: str,
    source_hash: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.tc_room_access (access_id, grant_id, at, principal, action, outcome, source_hash) VALUES (:i, :g, :at, :p, :a, :o, :h)"
        ),
        {
            "i": uuid.uuid4(),
            "g": uuid.UUID(grant_id),
            "at": now,
            "p": uuid.UUID(principal) if principal else None,
            "a": action,
            "o": outcome,
            "h": source_hash,
        },
    )


async def access_history(conn: AsyncConnection, grant_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT at, principal, action, outcome FROM platform.tc_room_access WHERE grant_id = :g ORDER BY at"
            ),
            {"g": uuid.UUID(grant_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ monitoring


async def texts_by_state(conn: AsyncConnection) -> tuple[list[str], list[str]]:
    live = (
        (
            await conn.execute(
                text("SELECT exact_text FROM platform.tc_claim_revision WHERE status = 'PUBLISHED'")
            )
        )
        .scalars()
        .all()
    )
    gone = (
        (
            await conn.execute(
                text(
                    "SELECT exact_text FROM platform.tc_claim_revision WHERE status = 'EMERGENCY_WITHDRAWN'"
                )
            )
        )
        .scalars()
        .all()
    )
    return list(live), list(gone)


async def counts(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    q = {
        "live_claims": "SELECT count(*) FROM platform.tc_claim_revision WHERE status = 'PUBLISHED'",
        "pending_claims": "SELECT count(*) FROM platform.tc_claim_revision WHERE status IN ('DRAFT', 'APPROVAL_PENDING')",
        "withdrawals_total": "SELECT count(*) FROM platform.tc_withdrawal",
        "withdrawals_overdue_review": "SELECT count(*) FROM platform.tc_withdrawal WHERE reviewed_by IS NULL AND review_due < CAST(:now AS timestamptz)",
        "reports_open": "SELECT count(*) FROM platform.tc_report WHERE state NOT IN ('CLOSED', 'OUT_OF_SCOPE', 'DISCLOSED')",
        "reports_past_target": "SELECT count(*) FROM platform.tc_report WHERE acknowledged_at IS NULL AND ack_due < CAST(:now AS timestamptz) AND state = 'RECEIVED'",
        "figures_unvalidated": "SELECT count(*) FROM platform.tc_sli_snapshot WHERE validated_by IS NULL",
        "room_requests_open": "SELECT count(*) FROM platform.tc_room_request WHERE status = 'REQUESTED'",
        "room_downloads_30d": "SELECT count(*) FROM platform.tc_room_access WHERE outcome = 'ALLOWED' AND at > CAST(:now AS timestamptz) - interval '30 days'",
        "room_denied_30d": "SELECT count(*) FROM platform.tc_room_access WHERE outcome <> 'ALLOWED' AND at > CAST(:now AS timestamptz) - interval '30 days'",
        "disclosures_pending": "SELECT count(*) FROM platform.tc_disclosure WHERE status = 'DRAFT'",
    }
    return {
        k: int((await conn.execute(text(v), {"now": now} if ":now" in v else {})).scalar_one())
        for k, v in q.items()
    }


async def median_hours_to_withdraw(conn: AsyncConnection) -> float | None:
    """From the first publication of a revision to its withdrawal, for withdrawn revisions."""
    r = (
        await conn.execute(
            text("""
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (r.withdrawn_at - r.published_at)) / 3600.0)
                  FROM platform.tc_claim_revision r WHERE r.status = 'EMERGENCY_WITHDRAWN' AND r.published_at IS NOT NULL AND r.withdrawn_at IS NOT NULL""")
        )
    ).scalar_one()
    return float(r) if r is not None else None


async def live_with_evidence_end(conn: AsyncConnection) -> list[tuple[cl.Claim, date | None]]:
    out = []
    for c, _, _ in await live_claims(conn):
        known = await evidence_by_digests(conn, c.evidence_digests)
        ends = [a.assessed.period_end for a in known.values()]
        out.append((c, min(ends) if ends else None))
    return out
