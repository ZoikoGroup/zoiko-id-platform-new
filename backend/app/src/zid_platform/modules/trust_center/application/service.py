"""The Trust Center's claims workflow (ZID-CP-WF12 v2.0; CR-174). It joins the pure rules in `domain` to the register in `infrastructure.store`.

Every state change takes the revision row `FOR UPDATE` first, so two reviewers or two publishers cannot interleave. The same rules are in the database as triggers: this layer
produces the explanation, the database is the backstop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.trust_center.domain import claims as cl
from zid_platform.modules.trust_center.domain import disclosure as dz
from zid_platform.modules.trust_center.domain import evidence as ev
from zid_platform.modules.trust_center.infrastructure import store

REVIEW_ROLES = tuple(str(r) for r in cl.Role if r != cl.Role.RESPONDER)
WEAK_FOR_CERTIFICATION = (ev.EvidenceType.SELF_ASSESSMENT, ev.EvidenceType.DECLARATION, ev.EvidenceType.CONTRACT_COMMITMENT)


class TrustError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def evidence_view(a: ev.Artifact) -> dict[str, Any]:
    return {
        "evidence_id": a.evidence_id, "digest": a.digest, "evidence_type": str(a.evidence_type), "scheme": a.scheme, "issuer": a.issuer, "assessor": a.assessor,
        "legal_entity": a.assessed.legal_entity, "products": sorted(a.assessed.products), "regions": sorted(a.assessed.regions), "exclusions": list(a.assessed.exclusions),
        "period_start": a.assessed.period_start.isoformat(), "period_end": a.assessed.period_end.isoformat(), "holder": a.holder, "lifecycle": str(a.lifecycle),
        "issuer_confirmed": a.issuer_confirmed, "restricted": a.restricted,
    }  # fmt: skip


def claim_view(c: cl.Claim, content_hash: str) -> dict[str, Any]:
    return {
        "claim_id": c.claim_id, "revision": c.revision, "exact_text": c.exact_text, "locale": c.locale, "claim_type": str(c.claim_type),
        "effective_class": str(cl.classify(c.exact_text, c.claim_type)), "scope": {"entity_id": c.scope.entity_id, "service": c.scope.service, "markets": list(c.scope.markets), "environment": c.scope.environment},
        "channels": list(c.channels), "evidence_digests": list(c.evidence_digests), "valid_until": c.valid_until.isoformat() if c.valid_until else None,
        "author": c.author, "content_hash": content_hash, "status": str(c.status),
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------ evidence


async def record_evidence(conn: AsyncConnection, *, actor: str, data: dict[str, Any]) -> dict[str, Any]:
    if (msg := ev.scheme_type_error(data["scheme"], ev.EvidenceType(data["evidence_type"]))) is not None:
        raise TrustError("SCHEME_TYPE_MISMATCH", msg)
    if data["period_end"] < data["period_start"]:
        raise TrustError("PERIOD_INVALID", "The period ends before it starts.")
    a = await store.insert_evidence(conn, actor=actor, **data)
    return evidence_view(a)


async def confirm_evidence(conn: AsyncConnection, *, actor: str, evidence_id: str, now: datetime) -> dict[str, Any]:
    cur = await store.evidence_by_id(conn, evidence_id)
    if cur is None:
        raise TrustError("EVIDENCE_NOT_FOUND")
    if cur.issuer_confirmed:
        raise TrustError("ALREADY_CONFIRMED")
    a = await store.confirm_evidence(conn, evidence_id, actor=actor, now=now)
    assert a is not None
    return evidence_view(a)


async def change_lifecycle(conn: AsyncConnection, *, evidence_id: str, lifecycle: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A lifecycle change returns the claims that rest on this evidence, so the caller can re-evaluate them at once."""
    cur = await store.evidence_by_id(conn, evidence_id)
    if cur is None:
        raise TrustError("EVIDENCE_NOT_FOUND")
    if cur.lifecycle == ev.Lifecycle.WITHDRAWN:
        raise TrustError("EVIDENCE_WITHDRAWN", "Withdrawn evidence stays withdrawn.")
    a = await store.set_lifecycle(conn, evidence_id, lifecycle)
    assert a is not None
    hit = await store.claims_resting_on(conn, a.digest)
    return evidence_view(a), [{"claim_id": c, "revision": r} for c, r in hit]


# ------------------------------------------------------------------------------------------------------------------------------ claims


async def draft(conn: AsyncConnection, *, actor: str, claim_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """A new claim (no id yet) or a new revision of an existing one. Never edits an earlier revision."""
    revision = await store.next_revision(conn, claim_id)
    c = cl.Claim(
        claim_id=claim_id, revision=revision, exact_text=data["exact_text"], locale=data["locale"], claim_type=cl.ClaimType(data["claim_type"]),
        scope=cl.Scope(data["entity_id"], data["service"], tuple(data["markets"]), data.get("environment", "production")), channels=tuple(data["channels"]),
        evidence_digests=tuple(sorted(set(data["evidence_digests"]))), valid_until=data.get("valid_until"), author=actor,
    )  # fmt: skip
    known = await store.evidence_by_digests(conn, c.evidence_digests)
    missing = [d for d in c.evidence_digests if d not in known]
    if missing:
        raise TrustError("EVIDENCE_NOT_FOUND", f"No evidence on record for {missing[0][:12]}.")
    h = cl.content_hash(c)
    await store.insert_revision(conn, c, content_hash=h, status="DRAFT" if not c.evidence_digests else "APPROVAL_PENDING")
    return await detail(conn, claim_id, revision, now=datetime.now(UTC))


def _views(c: cl.Claim, known: dict[str, ev.Artifact], today: date) -> dict[str, cl.EvidenceView]:
    out: dict[str, cl.EvidenceView] = {}
    for d in c.evidence_digests:
        a = known.get(d)
        if a is None:
            continue
        gaps = [bool(ev.covers(a, ev.Subject(c.scope.entity_id, c.scope.service, m, today))) for m in c.scope.markets]
        weak = cl.classify(c.exact_text, c.claim_type) == cl.ClaimType.CERTIFICATION and a.evidence_type in WEAK_FOR_CERTIFICATION
        out[d] = cl.EvidenceView(digest=d, current=ev.is_current(a, today) and not weak, scope_covers=not any(gaps), verified=a.issuer_confirmed, is_supplier=a.holder != "zoiko")
    return out


async def _blockers(conn: AsyncConnection, c: cl.Claim, h: str, approvals: Sequence[cl.Approval], now: datetime) -> list[tuple[str, str]]:
    known = await store.evidence_by_digests(conn, c.evidence_digests)
    out = cl.publish_blockers(c, approvals, _views(c, known, now.date()), expected_hash=h, now=now)
    out += [("DISCLOSURE_UNSAFE", m) for m in dz.safe_for_public(c.exact_text, allow=("EMAIL_ADDRESS",))]
    out += [("VAGUE_SECURITY_LANGUAGE", f"'{w}' cannot be tested by a buyer; state the control and its scope.") for w in dz.vague_security_language(c.exact_text)]
    if cl.classify(c.exact_text, c.claim_type) == cl.ClaimType.CERTIFICATION:
        for d in c.evidence_digests:
            a = known.get(d)
            if a is not None and a.evidence_type in WEAK_FOR_CERTIFICATION:
                out.append(("EVIDENCE_TYPE_TOO_WEAK", f"A {a.evidence_type} cannot support a certification statement."))
            if a is not None and (msg := ev.scheme_type_error(a.scheme, a.evidence_type)):
                out.append(("SCHEME_TYPE_MISMATCH", msg))
    return out


async def detail(conn: AsyncConnection, claim_id: str, revision: int, *, now: datetime) -> dict[str, Any]:
    got = await store.load_revision(conn, claim_id, revision)
    if got is None:
        raise TrustError("CLAIM_NOT_FOUND")
    c, h = got
    appr = await store.approvals(conn, claim_id, revision)
    blockers = await _blockers(conn, c, h, appr, now)
    _status, missing = cl.standing(c, appr, now=now)
    view = claim_view(c, h)
    view["approvals"] = [{"approver": a.approver, "role": str(a.role), "refused": a.refused, "reason": a.reason, "decided_at": a.decided_at.isoformat(), "this_version": a.claim_hash == h} for a in appr]
    view["still_needed"] = missing
    view["blockers"] = [{"code": k, "detail": d} for k, d in blockers]
    view["ready_to_publish"] = not blockers and c.status != cl.Status.EMERGENCY_WITHDRAWN
    view["history"] = [{**r, "created_at": r["created_at"].isoformat(), "published_at": r["published_at"].isoformat() if r["published_at"] else None, "withdrawn_at": r["withdrawn_at"].isoformat() if r["withdrawn_at"] else None} for r in await store.history(conn, claim_id)]
    return view


async def review(conn: AsyncConnection, *, actor: str, claim_id: str, revision: int, role: str, approve: bool, reason: str, evidence_digests: Sequence[str], now: datetime, effective_until: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    got = await store.load_revision(conn, claim_id, revision, lock=True)
    if got is None:
        raise TrustError("CLAIM_NOT_FOUND")
    c, _ = got
    if c.status in (cl.Status.EMERGENCY_WITHDRAWN, cl.Status.EXPIRED, cl.Status.SUPERSEDED):
        raise TrustError("REVISION_CLOSED", f"This revision is {c.status.value.lower()}.")
    existing = await store.approvals(conn, claim_id, revision)
    try:
        a = (
            cl.approve(c, approver=actor, role=cl.Role(role), existing=existing, evidence_digests=evidence_digests, now=now, effective_until=effective_until)
            if approve
            else cl.refuse(c, approver=actor, role=cl.Role(role), reason=reason, now=now)
        )
    except cl.ApprovalError as e:
        raise TrustError(e.code, e.detail) from e
    await store.insert_approval(conn, a, claim_id=claim_id, revision=revision)
    return await detail(conn, claim_id, revision, now=now)


def manifest_digest(c: cl.Claim, h: str) -> str:
    """The signed-bundle manifest of one publication: exactly what goes where. A pipeline recomputes it and refuses a bundle that differs."""
    body = {"claim_id": c.claim_id, "revision": c.revision, "hash": h, "locale": c.locale, "channels": sorted(c.channels), "evidence": sorted(c.evidence_digests), "valid_until": c.valid_until.isoformat() if c.valid_until else None}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def publish(conn: AsyncConnection, *, actor: str, claim_id: str, revision: int, expected_hash: str, now: datetime) -> dict[str, Any]:
    got = await store.load_revision(conn, claim_id, revision, lock=True)
    if got is None:
        raise TrustError("CLAIM_NOT_FOUND")
    c, h = got
    if c.status == cl.Status.EMERGENCY_WITHDRAWN:
        raise TrustError("REVISION_WITHDRAWN", "A withdrawn revision cannot be published; submit a new revision.")
    if c.status == cl.Status.PUBLISHED:
        raise TrustError("ALREADY_PUBLISHED")
    appr = await store.approvals(conn, claim_id, revision)
    blockers = await _blockers(conn, c, expected_hash, appr, now)
    if blockers:
        raise TrustError("PUBLISH_BLOCKED", "; ".join(f"{k}: {d}" for k, d in blockers))
    m = manifest_digest(c, h)
    await store.insert_publication(conn, claim_id=claim_id, revision=revision, claim_hash=h, kind="PUBLISH", rolled_back_from=None, channels=c.channels, locale=c.locale, manifest_digest=m, actor=actor, now=now)
    await store.supersede_others(conn, claim_id, revision)
    await store.set_status(conn, claim_id, revision, "PUBLISHED", published_at=now)
    out = await detail(conn, claim_id, revision, now=now)
    out["manifest_digest"] = m
    return out


async def rollback(conn: AsyncConnection, *, actor: str, claim_id: str, now: datetime) -> dict[str, Any]:
    """Back to the latest earlier revision that went through the gate and was never withdrawn, re-checked against today's evidence."""
    hist = await store.history(conn, claim_id)
    if not hist:
        raise TrustError("CLAIM_NOT_FOUND")
    current = next((r["revision"] for r in reversed(hist) if r["status"] == "PUBLISHED"), hist[-1]["revision"])
    revs = [cl.Revision(r["revision"], r["content_hash"], approved_validly=bool(r["was_published"]), withdrawn=r["status"] == "EMERGENCY_WITHDRAWN") for r in hist]
    target = cl.rollback_target(revs, current=current)
    if target is None:
        raise TrustError("NO_ROLLBACK_TARGET", "No earlier revision went through the gate and stayed live.")
    got = await store.load_revision(conn, claim_id, target.revision, lock=True)
    assert got is not None
    c, h = got
    appr = await store.approvals(conn, claim_id, target.revision)
    blockers = await _blockers(conn, c, h, appr, now)
    if blockers:
        raise TrustError("ROLLBACK_BLOCKED", "; ".join(f"{k}: {d}" for k, d in blockers))
    m = manifest_digest(c, h)
    await store.insert_publication(conn, claim_id=claim_id, revision=target.revision, claim_hash=h, kind="ROLLBACK", rolled_back_from=current, channels=c.channels, locale=c.locale, manifest_digest=m, actor=actor, now=now)
    await store.supersede_others(conn, claim_id, target.revision)
    await store.set_status(conn, claim_id, target.revision, "PUBLISHED", published_at=now)
    out = await detail(conn, claim_id, target.revision, now=now)
    out["manifest_digest"] = m
    out["rolled_back_from"] = current
    return out


async def withdraw(conn: AsyncConnection, *, actor: str, claim_id: str, revision: int | None, reason: str, now: datetime, review_hours: int = 72) -> dict[str, Any]:
    """Emergency withdrawal: one responder, no approver, takes effect in this transaction. The live revision is the default target."""
    hist = await store.history(conn, claim_id)
    if not hist:
        raise TrustError("CLAIM_NOT_FOUND")
    rev = revision if revision is not None else next((r["revision"] for r in reversed(hist) if r["status"] == "PUBLISHED"), None)
    if rev is None:
        raise TrustError("NOTHING_LIVE", "No revision of this claim is live.")
    got = await store.load_revision(conn, claim_id, rev, lock=True)
    if got is None:
        raise TrustError("CLAIM_NOT_FOUND")
    c, _ = got
    if c.status == cl.Status.EMERGENCY_WITHDRAWN:
        raise TrustError("ALREADY_WITHDRAWN")
    try:
        w = cl.emergency_withdraw(c, actor=actor, actor_roles=[cl.Role.RESPONDER], reason=reason, now=now, review_within_hours=review_hours)
    except cl.WithdrawError as e:
        raise TrustError(e.code, e.detail) from e
    wid = await store.insert_withdrawal(conn, w, revision=rev)
    await store.set_status(conn, claim_id, rev, "EMERGENCY_WITHDRAWN", withdrawn_at=now)
    return {"withdrawal_id": wid, "claim_id": claim_id, "revision": rev, "channels": list(w.channels), "review_due": w.review_due.isoformat(), "purge_required": w.purge_required}


async def review_withdrawal(conn: AsyncConnection, *, actor: str, withdrawal_id: str, note: str, now: datetime) -> dict[str, Any]:
    if not note.strip():
        raise TrustError("REASON_REQUIRED", "A review says what it found.")
    if not await store.review_withdrawal(conn, withdrawal_id, actor=actor, note=note, now=now):
        raise TrustError("WITHDRAWAL_NOT_OPEN", "No such withdrawal, or it was already reviewed.")
    return {"withdrawal_id": withdrawal_id, "reviewed_by": actor, "reviewed_at": now.isoformat()}


async def expire_due(conn: AsyncConnection, *, now: datetime) -> list[dict[str, Any]]:
    """Take live claims out of service when their own date has passed or any evidence under them is no longer current. Idempotent; run by the worker and before every public read."""
    out: list[dict[str, Any]] = []
    for c, h, _ in await store.live_claims(conn):
        known = await store.evidence_by_digests(conn, c.evidence_digests)
        views = _views(c, known, now.date())
        why = None
        if cl.expired_by(c, today=now.date()):
            why = "CLAIM_EXPIRED"
        elif not c.evidence_digests or any(d not in views or not views[d].current or not views[d].scope_covers or not views[d].verified for d in c.evidence_digests):
            why = "EVIDENCE_NO_LONGER_CURRENT"
        if why:
            await store.set_status(conn, c.claim_id, c.revision, "EXPIRED")
            out.append({"claim_id": c.claim_id, "revision": c.revision, "reason": why, "hash": h})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ availability


def _snapshot_view(r: dict[str, Any]) -> dict[str, Any]:
    from zid_platform.modules.trust_center.domain import availability as av

    s = av.Snapshot(r["service"], r["region"], r["window_label"], r["numerator"], r["denominator"], r["exclusions"], r["validated_by"] is not None)
    reading = av.read(s)
    return {"snapshot_id": str(r["snapshot_id"]), "service": r["service"], "region": r["region"], "window": r["window_label"], "state": str(reading.state), "percent": reading.percent, "note": reading.note, "source_ref": r["source_ref"], "validated": r["validated_by"] is not None}


async def record_snapshot(conn: AsyncConnection, *, actor: str, data: dict[str, Any]) -> dict[str, Any]:
    from zid_platform.modules.trust_center.domain import availability as av

    if data["numerator"] > data["denominator"]:
        raise TrustError("FIGURES_DO_NOT_RECONCILE", "Good cannot exceed eligible.")
    raw_total, raw_good = data.pop("raw_total", None), data.pop("raw_good", None)
    if raw_total is not None and raw_good is not None and not av.reconciles(av.Snapshot(data["service"], data["region"], data["window_label"], data["numerator"], data["denominator"], data["exclusions"], False), raw_good, raw_total):
        raise TrustError("FIGURES_DO_NOT_RECONCILE", "The figure cannot be reproduced from the raw telemetry given.")
    return _snapshot_view(await store.insert_snapshot(conn, actor=actor, **data))


async def validate_snapshot(conn: AsyncConnection, *, actor: str, snapshot_id: str, now: datetime) -> dict[str, Any]:
    row = await store.validate_snapshot(conn, snapshot_id, actor=actor, now=now)
    if row is None:
        raise TrustError("SNAPSHOT_NOT_OPEN", "No such figure, or it was already validated.")
    return _snapshot_view(row)


async def public_availability(conn: AsyncConnection, services: Sequence[str], *, now: datetime) -> dict[str, Any]:
    """Every service named in the catalogue is listed; one with no validated figure says 'not yet reported', never 100%."""
    rows = await store.snapshots(conn, validated_only=True)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:  # newest window first
        latest.setdefault((r["service"], r["region"]), r)
    listed = [_snapshot_view(r) for r in latest.values()]
    seen = {v["service"] for v in listed}
    for s in services:
        if s not in seen:
            listed.append({"service": s, "region": None, "window": None, "state": "METRIC_UNAVAILABLE", "percent": None, "note": "Not yet reported.", "validated": False})
    return {
        "as_of": now.isoformat(), "services": sorted(listed, key=lambda v: (v["service"], v["region"] or "")),
        "definition": "Availability is good requests divided by eligible requests in the counting window, excluding stated maintenance. A figure appears only after a second person validated it.",
        "sla": "Service level targets apply only under a signed plan for a covered tier. They are targets, not measured results, and sandbox and free use have none.",
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------ advisories


def _version_view(v: dict[str, Any]) -> dict[str, Any]:
    return {"seq": v["seq"], "kind": v["kind"], "title": v["title"], "body": v["body"], "correction_reason": v["reason"] if v["kind"] == "CORRECTION" else None, "published": v["approved_by"] is not None, "created_at": v["created_at"].isoformat(), "approved_at": v["approved_at"].isoformat() if v["approved_at"] else None}


async def draft_advisory(conn: AsyncConnection, *, actor: str, incident_ref: str, kind: str, title: str, body: str, reason: str) -> dict[str, Any]:
    from zid_platform.modules.operations.ports import incident_summary
    from zid_platform.modules.trust_center.domain import reports as rp

    inc = await incident_summary(conn, incident_ref)
    if inc is None:
        raise TrustError("INCIDENT_NOT_FOUND", "A public advisory is written about an incident in the operators' registry.")
    aid = await store.advisory_for_incident(conn, incident_ref)
    existing = await store.versions(conn, aid) if aid else []
    prev = None
    if existing:
        p = existing[-1]
        prev = rp.AdvisoryVersion(p["seq"], rp.AdvisoryKind(p["kind"]), p["title"], p["body"], p["reason"], str(p["author"]), None if p["approved_by"] is None else str(p["approved_by"]))
    k = rp.AdvisoryKind(kind)
    if existing and k == rp.AdvisoryKind.PUBLISH:
        k = rp.AdvisoryKind.UPDATE
    if not existing:
        k = rp.AdvisoryKind.PUBLISH
    v = rp.AdvisoryVersion(len(existing) + 1, k, title, body, reason, actor, "approver-pending")  # judged on text; approval comes later and is checked by the database
    problems = [p for p in rp.advisory_problems(v, prev) if not p.startswith("Security and Legal must")]
    if problems:
        raise TrustError("ADVISORY_REFUSED", " ".join(problems))
    if aid is None:
        aid = await store.create_advisory(conn, incident_ref=incident_ref, actor=actor)
    seq = await store.add_version(conn, advisory_id=aid, kind=str(k), title=title, body=body, reason=reason, author=actor)
    return {"advisory_id": aid, "seq": seq, "kind": str(k), "published": False, "incident": inc}


async def approve_advisory(conn: AsyncConnection, *, actor: str, advisory_id: str, seq: int, now: datetime) -> dict[str, Any]:
    vs = await store.versions(conn, advisory_id)
    v = next((x for x in vs if x["seq"] == seq), None)
    if v is None:
        raise TrustError("ADVISORY_NOT_FOUND")
    if str(v["author"]) == actor:
        raise TrustError("SELF_APPROVAL", "The author of an advisory cannot approve it.")
    if not await store.approve_version(conn, advisory_id, seq, actor=actor, now=now):
        raise TrustError("ALREADY_APPROVED")
    return {"advisory_id": advisory_id, "seq": seq, "published": True, "approved_at": now.isoformat()}


async def public_advisories(conn: AsyncConnection) -> dict[str, Any]:
    out = []
    for a in await store.advisories(conn, public=True):
        out.append({"advisory_id": a["advisory_id"], "incident": a["incident_ref"], "versions": [_version_view(v) for v in a["versions"]], "latest_title": a["versions"][-1]["title"]})
    return {"items": out}


# ------------------------------------------------------------------------------------------------------------------------------ vulnerability reports


async def receive_report(conn: AsyncConnection, *, summary: str, affected: str, severity: str, contact: str | None, source_hash: str, now: datetime, per_source_hour: int = 5, per_hour: int = 200) -> dict[str, str]:  # fmt: skip
    import secrets
    from datetime import timedelta

    from zid_platform.modules.trust_center.domain import reports as rp

    bad = rp.intake_problems(summary, affected, contact=contact)
    if bad:
        raise TrustError("REPORT_REFUSED", " ".join(bad))
    since = now - timedelta(hours=1)
    if await store.reports_from_source_since(conn, source_hash, since) >= per_source_hour or await store.reports_since(conn, since) >= per_hour:
        raise TrustError("RATE_LIMITED", "Too many reports right now; try again later or email the security address.")
    ref = "VR-" + "".join(secrets.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10))
    await store.insert_report(conn, reference=ref, summary=summary, affected=affected, severity=severity, contact=contact, source_hash=source_hash, ack_due=rp.ack_due(now, severity), received=now)
    return rp.receipt(ref, now, severity)


async def triage_report(conn: AsyncConnection, *, actor: str, report_id: str, state: str | None, assignee: str | None, severity: str | None, note: str, now: datetime) -> dict[str, Any]:  # fmt: skip
    from zid_platform.modules.trust_center.domain import reports as rp

    cur = await store.report_detail(conn, report_id)
    if cur is None:
        raise TrustError("REPORT_NOT_FOUND")
    if state and not rp.can_move(rp.ReportState(cur["state"]), rp.ReportState(state)):
        raise TrustError("REPORT_STATE_INVALID", f"A report in {cur['state']} cannot move to {state}.")
    if severity and severity not in rp.SEVERITIES:
        raise TrustError("REPORT_STATE_INVALID", "Unknown severity.")
    await store.move_report(conn, report_id, state=state, assignee=assignee, severity=severity, actor=actor, note=note, now=now)
    got = await store.report_detail(conn, report_id)
    assert got is not None
    return report_view(got, with_text=False)


def report_view(r: dict[str, Any], *, with_text: bool) -> dict[str, Any]:
    out = {"report_id": str(r["report_id"]), "reference": r["reference"], "severity": r["severity"], "state": r["state"], "received_at": r["received_at"].isoformat(), "ack_due": r["ack_due"].isoformat(), "acknowledged_at": r["acknowledged_at"].isoformat() if r.get("acknowledged_at") else None, "assignee": str(r["assignee"]) if r.get("assignee") else None}
    if with_text:
        out |= {"summary": r["summary"], "affected": r["affected"], "contact": r["contact"], "timeline": [{"seq": e["seq"], "kind": e["kind"], "actor": str(e["actor"]) if e["actor"] else None, "note": e["note"], "at": e["at"].isoformat()} for e in r["timeline"]]}
    return out


# ------------------------------------------------------------------------------------------------------------------------------ public reads


def _live_standing(c: cl.Claim, known: dict[str, ev.Artifact], today: date) -> bool:
    """A claim is shown only while its own date holds and every piece of evidence under it is still current, in scope and confirmed."""
    if cl.expired_by(c, today=today) or not c.evidence_digests:
        return False
    views = _views(c, known, today)
    return all(d in views and views[d].current and views[d].scope_covers and views[d].verified for d in c.evidence_digests)


async def public_claims(conn: AsyncConnection, *, now: datetime) -> dict[str, Any]:
    """The approved, current, public projection. Read-only: a claim whose support lapsed is simply not listed (a worker sweep records it)."""
    items = []
    for c, h, published_at in await store.live_claims(conn):
        known = await store.evidence_by_digests(conn, c.evidence_digests)
        if not _live_standing(c, known, now.date()):
            continue
        p = cl.project(replace(c, status=cl.Status.PUBLISHED), published=True, as_of=published_at, today=now.date())
        if p is None:
            continue
        items.append({
            "claim_id": p.claim_id, "text": p.text, "locale": p.locale, "scope": p.scope, "effective_as_of": p.effective_as_of.isoformat(),
            "valid_until": p.valid_until.isoformat() if p.valid_until else None, "channels": list(c.channels),
            "evidence": [{"scheme": a.scheme, "type": str(a.evidence_type), "issuer": a.issuer, "period_end": a.assessed.period_end.isoformat(), "held_by": a.holder, "restricted": a.restricted} for d in c.evidence_digests if (a := known.get(d)) is not None],
        })  # fmt: skip
    return {"as_of": now.isoformat(), "items": items, "note": "Every statement here is approved by two reviewers and rests on evidence that is current today. A statement with no current evidence is not listed."}


_TYPE_LABEL = {
    "report": "Independent audit report", "attestation": "Attestation", "certificate": "Certificate", "assessment": "Assessment", "registration": "Registration",
    "self_assessment": "Self-assessment (not independent)", "declaration": "Declaration (not independent)", "contract_commitment": "Contractual commitment", "program_listing": "Official program listing",
}  # fmt: skip


async def public_certifications(conn: AsyncConnection, *, now: datetime) -> dict[str, Any]:
    """What was assessed, by whom, for which entity and product and period. A report is never called a certification, a supplier's is attributed to the supplier, and an
    expired one is shown as history. Evidence nobody at the issuer confirmed is not shown at all."""
    today = now.date()
    cur: list[dict[str, Any]] = []
    hist: list[dict[str, Any]] = []
    for a in await store.list_evidence(conn):
        if not a.issuer_confirmed:
            continue
        life = ev.lifecycle_on(a, today)
        row = {
            "scheme": a.scheme, "kind": _TYPE_LABEL[str(a.evidence_type)], "issuer": a.issuer, "assessor": a.assessor or None, "issued_to": a.holder,
            "is_zoiko": a.holder == "zoiko", "legal_entity": a.assessed.legal_entity, "products": sorted(a.assessed.products), "regions": sorted(a.assessed.regions),
            "exclusions": list(a.assessed.exclusions), "period_start": a.assessed.period_start.isoformat(), "period_end": a.assessed.period_end.isoformat(),
            "state": str(life), "full_report": "Available on request under NDA." if a.restricted else None,
        }  # fmt: skip
        (cur if life == ev.Lifecycle.ACTIVE and ev.is_current(a, today) else hist).append(row)
    return {"as_of": now.isoformat(), "current": cur, "history": hist, "note": "Each entry covers only the entity, products, regions and period shown. Supplier entries are the supplier's and do not extend to Zoiko iD's own service."}


# ------------------------------------------------------------------------------------------------------------------------------ privacy disclosures


def _disclosure_view(d: dict[str, Any]) -> dict[str, Any]:
    return {"disclosure_id": str(d["disclosure_id"]), "kind": d["kind"], "subject": d["subject"], "market": d["market"], "revision": d["revision"], "payload": d["payload"], "effective_from": d["effective_from"].isoformat(), "status": d["status"], "approved": d["approved_by"] is not None}


def _strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _strings(x)]
    return []


async def draft_disclosure(conn: AsyncConnection, *, actor: str, kind: str, subject: str, market: str, payload: dict[str, Any], effective_from: date) -> dict[str, Any]:
    from zid_platform.modules.trust_center.domain import privacy as pv

    bad = pv.problems(kind, subject, payload)
    for s in _strings(payload):
        bad += pv.text_problems(s) + dz.safe_for_public(s, allow=("EMAIL_ADDRESS",))
    if bad:
        raise TrustError("DISCLOSURE_REFUSED", " ".join(bad))
    return _disclosure_view(await store.insert_disclosure(conn, kind=kind, subject=subject, market=market, payload=payload, effective_from=effective_from, author=actor))


async def approve_disclosure(conn: AsyncConnection, *, actor: str, disclosure_id: str, now: datetime) -> dict[str, Any]:
    d = await store.load_disclosure(conn, disclosure_id)
    if d is None:
        raise TrustError("DISCLOSURE_NOT_FOUND")
    if str(d["author"]) == actor:
        raise TrustError("SELF_APPROVAL", "The author of a disclosure cannot approve it.")
    if d["status"] != "DRAFT":
        raise TrustError("DISCLOSURE_NOT_OPEN", f"This disclosure is {d['status'].lower()}.")
    await store.activate_disclosure(conn, d, actor=actor, now=now)
    got = await store.load_disclosure(conn, disclosure_id)
    assert got is not None
    return _disclosure_view(got)


async def withdraw_disclosure(conn: AsyncConnection, *, disclosure_id: str) -> dict[str, Any]:
    d = await store.load_disclosure(conn, disclosure_id)
    if d is None:
        raise TrustError("DISCLOSURE_NOT_FOUND")
    await store.withdraw_disclosure(conn, disclosure_id)
    return {"disclosure_id": disclosure_id, "status": "WITHDRAWN"}


async def public_privacy(conn: AsyncConnection, *, market: str | None, now: datetime) -> dict[str, Any]:
    """Only approved, active records for the market (and the global ones). A kind with nothing says so; no number is ever filled in."""
    rows = await store.disclosures(conn, market=market, active_only=True)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ("subprocessor", "retention", "biometric", "transfer")}
    for r in rows:
        v = _disclosure_view(r)
        out[r["kind"]].append({"subject": v["subject"], "market": v["market"], "revision": v["revision"], "effective_from": v["effective_from"], **v["payload"]})
    return {"as_of": now.isoformat(), "market": market, **{k: v for k, v in out.items()}, "empty": {k: "Not yet published for this market." for k, v in out.items() if not v}}


async def region_check(conn: AsyncConnection, region: str) -> dict[str, Any]:
    from zid_platform.modules.trust_center.domain import privacy as pv

    rows = await store.disclosures(conn, market=None, active_only=True)
    subs = [{"name": r["subject"], **r["payload"]} for r in rows if r["kind"] == "subprocessor"]
    trans = [r["payload"] for r in rows if r["kind"] == "transfer"]
    why = pv.region_claim_blockers(region, subs, trans)
    return {"region": region, "may_claim_data_stays": not why, "blockers": why}


# ------------------------------------------------------------------------------------------------------------------------------ evidence room

ROOM_TERMS_VERSION = "UNAPPROVED-DRAFT"  # default only; the deployment sets ZID_TRUST_ROOM_TERMS_VERSION to the version Legal approved (DEC-11), recorded with every request
ALLOWED_CONTENT = ("application/pdf", "text/plain", "text/csv", "application/json")


async def upload_blob(conn: AsyncConnection, *, actor: str, evidence_id: str, content: bytes, content_type: str, scanner: Any) -> dict[str, Any]:
    from zid_platform.modules.evidence.ports import scan_content

    a = await store.evidence_by_id(conn, evidence_id)
    if a is None:
        raise TrustError("EVIDENCE_NOT_FOUND")
    if content_type not in ALLOWED_CONTENT:
        raise TrustError("CONTENT_TYPE_REFUSED", "Only PDF, plain text, CSV or JSON.")
    if not content or len(content) > 20_000_000:
        raise TrustError("CONTENT_SIZE_REFUSED", "Between 1 byte and 20 MB.")
    if hashlib.sha256(content).hexdigest() != a.digest:
        raise TrustError("DIGEST_MISMATCH", "The file is not the artifact this record describes.")
    verdict = await scan_content(scanner, content)
    if verdict.state == "INFECTED":
        raise TrustError("CONTENT_REFUSED", "The file was refused by the malware scan.")
    if verdict.state != "CLEAN":
        raise TrustError("SCAN_UNAVAILABLE", "The file could not be scanned right now; nothing was stored.")
    if await store.has_blob(conn, evidence_id):
        raise TrustError("ALREADY_STORED")
    await store.insert_blob(conn, evidence_id=evidence_id, content=content, content_type=content_type, actor=actor)
    return {"evidence_id": evidence_id, "size_bytes": len(content), "content_type": content_type, "scan": "CLEAN"}


async def room_catalogue(conn: AsyncConnection) -> dict[str, Any]:
    """What a business may ask for: restricted evidence that exists, is confirmed and has its file. Names the scope, never the file."""
    out = []
    for a in await store.list_evidence(conn):
        if a.restricted and a.issuer_confirmed and await store.has_blob(conn, a.evidence_id):
            out.append({"evidence_id": a.evidence_id, "scheme": a.scheme, "kind": _TYPE_LABEL[str(a.evidence_type)], "legal_entity": a.assessed.legal_entity, "products": sorted(a.assessed.products), "period_end": a.assessed.period_end.isoformat(), "state": str(ev.lifecycle_on(a, datetime.now(UTC).date()))})
    return {"items": out, "terms_version": ROOM_TERMS_VERSION}


async def request_access(conn: AsyncConnection, *, tenant: str, requester: str, organization: str, purpose: str, evidence_id: str, accept_terms: bool, now: datetime, terms_version: str = ROOM_TERMS_VERSION) -> dict[str, Any]:
    if not accept_terms:
        raise TrustError("TERMS_REQUIRED", "Accept the confidentiality terms to request this document.")
    a = await store.evidence_by_id(conn, evidence_id)
    if a is None or not a.restricted or not a.issuer_confirmed or not await store.has_blob(conn, evidence_id):
        raise TrustError("EVIDENCE_NOT_AVAILABLE", "That document is not available through the request room.")
    r = await store.insert_request(conn, tenant=tenant, requester=requester, organization=organization, purpose=purpose, evidence_id=evidence_id, terms_version=terms_version, now=now)
    return _request_view(r)


def _request_view(r: dict[str, Any]) -> dict[str, Any]:
    return {"request_id": str(r["request_id"]), "evidence_id": str(r["evidence_id"]), "organization": r["organization"], "purpose": r["purpose"], "status": r["status"], "created_at": r["created_at"].isoformat(), "decision_reason": r.get("decision_reason", ""), "grant_expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None, "downloads_left": (r["max_downloads"] - r["downloads"]) if r.get("max_downloads") is not None else None, "revoked": bool(r.get("revoked_at"))}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def decide_access(conn: AsyncConnection, *, actor: str, request_id: str, approve: bool, reason: str, hours: int, max_downloads: int, now: datetime) -> dict[str, Any]:
    import secrets
    from datetime import timedelta

    r = await store.load_request(conn, request_id)
    if r is None:
        raise TrustError("REQUEST_NOT_FOUND")
    if r["status"] != "REQUESTED":
        raise TrustError("REQUEST_NOT_OPEN", f"This request is {r['status'].lower()}.")
    if not approve:
        if not reason.strip():
            raise TrustError("REASON_REQUIRED", "A refusal says why.")
        await store.decide_request(conn, request_id, status="DENIED", actor=actor, reason=reason, now=now)
        return {"request_id": request_id, "status": "DENIED"}
    token = "zrg_" + secrets.token_urlsafe(32)
    mark = f"Provided to {r['organization']} for {r['purpose'][:60]} on {now:%Y-%m-%d}. Confidential. Request {request_id[:8]}."
    await store.decide_request(conn, request_id, status="APPROVED", actor=actor, reason=reason, now=now)
    gid = await store.insert_grant(conn, request_id=request_id, token_hash=hash_token(token), expires_at=now + timedelta(hours=hours), max_downloads=max_downloads, watermark=mark)
    return {"request_id": request_id, "status": "APPROVED", "grant_id": gid, "access_token": token, "expires_at": (now + timedelta(hours=hours)).isoformat(), "max_downloads": max_downloads, "note": "The token is shown once and works only for the person who asked."}


class RoomDenied(Exception):
    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


async def room_download(conn: AsyncConnection, *, token: str, tenant: str, principal: str, now: datetime, source_hash: str) -> tuple[bytes, str, str]:
    """A download needs the token AND the signed-in person it was issued to: a forwarded link fails. Every attempt, allowed or not, is recorded."""
    g = await store.grant_by_hash(conn, hash_token(token))
    if g is None:
        raise RoomDenied("UNKNOWN_TOKEN")

    async def refuse(outcome: str) -> None:
        await store.record_access(conn, grant_id=str(g["grant_id"]), principal=principal, action="DOWNLOAD", outcome=outcome, source_hash=source_hash, now=now)
        raise RoomDenied(outcome)

    if str(g["requester"]) != principal or str(g["tenant_id"]) != tenant:
        await refuse("NOT_THE_REQUESTER")
    if g["revoked_at"] is not None or g["request_status"] != "APPROVED":
        await refuse("REVOKED")
    if g["expires_at"] <= now:
        await refuse("EXPIRED")
    if g["downloads"] >= g["max_downloads"]:
        await refuse("DOWNLOAD_LIMIT_REACHED")
    blob = await store.load_blob(conn, str(g["evidence_id"]))
    art = await store.evidence_by_id(conn, str(g["evidence_id"]))
    if blob is None or art is None:
        await refuse("NOT_AVAILABLE")
    assert blob is not None and art is not None
    if ev.lifecycle_on(art, now.date()) == ev.Lifecycle.WITHDRAWN:
        await refuse("EVIDENCE_WITHDRAWN")
    if hashlib.sha256(blob[0]).hexdigest() != art.digest:
        await refuse("INTEGRITY_FAILED")
    await store.bump_download(conn, str(g["grant_id"]))
    await store.record_access(conn, grant_id=str(g["grant_id"]), principal=principal, action="DOWNLOAD", outcome="ALLOWED", source_hash=source_hash, now=now)
    body = blob[0]
    if blob[1] == "text/plain":
        body = body + f"\n\n--\n{g['watermark']}\n".encode()  # text carries its watermark in the body; other types carry it in a header and the access record
    return body, blob[1], str(g["watermark"])


async def revoke_access(conn: AsyncConnection, *, actor: str, request_id: str, now: datetime) -> dict[str, Any]:
    g = await store.grant_for_request(conn, request_id)
    if g is None:
        raise TrustError("REQUEST_NOT_OPEN", "There is no grant for that request.")
    await store.revoke_grant(conn, str(g["grant_id"]), actor=actor, now=now)
    return {"request_id": request_id, "grant_id": str(g["grant_id"]), "revoked_at": now.isoformat(), "history": [{"at": h["at"].isoformat(), "action": h["action"], "outcome": h["outcome"]} for h in await store.access_history(conn, str(g["grant_id"]))]}


# ------------------------------------------------------------------------------------------------------------------------------ monitoring and the audit dashboard


async def monitoring_report(conn: AsyncConnection, *, now: datetime) -> dict[str, Any]:
    from zid_platform.modules.trust_center.domain import monitoring as mon

    n = await store.counts(conn, now)
    unsupported = 0
    for c, _, _ in await store.live_claims(conn):
        known = await store.evidence_by_digests(conn, c.evidence_digests)
        if not _live_standing(c, known, now.date()):
            unsupported += 1
    watch = mon.expiry_watch(await store.live_with_evidence_end(conn), today=now.date())
    ok, why = mon.release_gate(unsupported_live=unsupported, overdue_withdrawal_reviews=n["withdrawals_overdue_review"], unacknowledged_reports=n["reports_past_target"])
    return {
        "as_of": now.isoformat(), "counts": n, "unsupported_live_claims": unsupported, "median_hours_published_to_withdrawn": await store.median_hours_to_withdraw(conn),
        "expiring": [{"claim_id": w.claim_id, "revision": w.revision, "reason": w.reason, "days_left": w.days_left} for w in watch],
        "release_gate": {"open": ok, "reasons": why},
    }  # fmt: skip


async def scan_pages(conn: AsyncConnection, pages: Sequence[dict[str, str]]) -> dict[str, Any]:
    """The caller fetched the pages (this service never fetches arbitrary addresses); each is read for claim-like statements and reconciled to the register."""
    from zid_platform.modules.trust_center.domain import monitoring as mon

    live, gone = await store.texts_by_state(conn)
    seen = [o for p in pages for o in mon.observe(p["url"], p["content"])]
    found = mon.reconcile(seen, live, gone)
    return {"pages": len(pages), "claim_like_statements": len(seen), "findings": [{"url": f.url, "sentence": f.sentence[:300], "code": f.code, "detail": f.detail} for f in found], "clean": not found}
