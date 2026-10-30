"""Screening service (ZID-ED-04): list ingestion, screening cases, rescreening and reviewer rulings.

Directors, owners and sanctions are separate, auditable steps and one decision. The service loads the lists and the case's
parties and ownership structure, asks the pure rules for a decision, signs it as evidence, and records it append-only with
every alert. It never clears or rejects anyone on its own: an alert, a gap or an unproven authority waits for a person.

Names and birth years are restricted: they are stored for the case and shown to a reviewer, and appear in no event, audit
record, log or customer view. The customer sees the outcome and the next step only.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Decision,
    Role,
    Ruling,
    ScreeningError,
    ScreeningInput,
    ScreeningStatus,
    Subject,
    check_ruling,
    decide,
    effective_status,
    evidence_object,
    safe_view,
)
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Graph
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import ScreeningConfig
from zid_platform.modules.screening.infrastructure.parsers import PARSERS, ListFormatError

Signer = Callable[[dict[str, Any]], str]
CountryGate = Callable[[str], None]
SHRINK_LIMIT = (
    0.5  # a new file smaller than this share of the active one is a truncated or wrong file
)


# ---- lists -----------------------------------------------------------------------------------------------------------


async def ingest_list(
    conn: AsyncConnection,
    list_id: ListId,
    files: Mapping[str, bytes],
    *,
    fetched_at: datetime,
    source_ref: str,
    now: datetime,
    version: str | None = None,
) -> str:
    """Load a list file. A file that does not parse, or is far smaller than the one in use, is refused whole. The same
    file again only refreshes its fetch time, which is what keeps a list from going stale."""
    parser = PARSERS[list_id]
    try:
        if list_id in (ListId.OFAC_SDN, ListId.OFAC_NON_SDN):
            entries = parser(
                files["sdn"], files.get("alt"), files.get("add")
            )  # the consolidated files use the same layout
        else:
            entries = parser(files["file"])
    except KeyError as e:
        raise ScreeningError("LIST_FORMAT_INVALID", "a required file is missing") from e
    except ListFormatError as e:
        raise ScreeningError("LIST_FORMAT_INVALID", e.detail) from e
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(files[name])
    content_hash = digest.hexdigest()
    active = (
        await conn.execute(
            text(
                "SELECT content_hash, entry_count FROM verify.sanction_list_version WHERE list_id = :l AND active"
            ),
            {"l": list_id.value},
        )
    ).first()
    if (
        active is not None
        and active.content_hash != content_hash
        and len(entries) < SHRINK_LIMIT * active.entry_count
    ):
        raise ScreeningError(
            "LIST_SHRUNK", f"{len(entries)} entries against {active.entry_count} in use"
        )
    return await store.put_list(
        conn,
        list_id,
        version=version or content_hash[:12],
        content_hash=content_hash,
        entries=entries,
        source_ref=source_ref,
        fetched_at=fetched_at,
        now=now,
    )


# ---- cases -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseView:
    case_id: str
    status: ScreeningStatus  # effective: a clear outcome past its expiry reads as EXPIRED
    next_action: str
    version: int
    expiry_at: datetime | None


def _view(case: store.CaseRow, now: datetime) -> CaseView:
    status = effective_status(case.status, case.expiry_at, now)
    return CaseView(
        case.case_id,
        status,
        safe_view(status, case.reasons)["next_action"],
        case.current_version,
        case.expiry_at,
    )


def _authority(d: Mapping[str, Any]) -> AuthorityEvidence:
    expires = d.get("expires_at")
    return AuthorityEvidence(
        AuthorityKind(d.get("kind", "NONE")),
        d.get("reference"),
        datetime.fromisoformat(expires) if expires else None,
    )


async def _input(
    conn: AsyncConnection, tenant_id: str, case: store.CaseRow, cfg: ScreeningConfig, now: datetime
) -> ScreeningInput:
    parties = await store.load_parties(conn, tenant_id, case.case_id)
    return ScreeningInput(
        country=case.country,
        subjects=tuple(Subject(role, party) for role, party in parties),
        graph=await store.load_graph(conn, tenant_id, case.case_id),
        authority=_authority(case.authority),
        elevated_risk=case.elevated_risk,
        snapshots=await store.snapshots(conn),
        pack=cfg.pack(case.country),
        policy=cfg.policy,
        now=now,
    )


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    case: store.CaseRow,
    inp: ScreeningInput,
    decision: Decision,
    sign: Signer,
    *,
    version: int,
    actor: str | None,
    decided_by: str | None = None,
    rationale: str | None = None,
    carry: Mapping[str, Any] | None = None,
    now: datetime,
) -> CaseView:
    from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

    body = evidence_object(
        tenant_id=tenant_id,
        case_id=case.case_id,
        inp=inp,
        decision=decision,
        version=version,
        reviewed=decided_by is not None,
        carry=carry,
    )
    await store.append_evidence(
        conn,
        tenant_id,
        case_id=case.case_id,
        version=version,
        status=decision.status,
        reasons=decision.reasons,
        body=body,
        evidence_hash=body["evidence_hash"],
        jws=sign(body),
        decided_by=decided_by,
        rationale=rationale,
        expiry_at=decision.expiry_at,
        matches=decision.matches,
        now=now,
    )
    # ids and codes only: no name, no date of birth, no list entry text
    summary = {
        "case_id": case.case_id,
        "decision_status": decision.status.value,
        "reasons": list(decision.reasons),
        "evidence_hash": body["evidence_hash"],
        "policy_version": inp.policy.version,
        "country": inp.country,
        "alerts": len(decision.matches),
    }
    event_id = await emit(
        conn,
        name="verification.screening.decided",
        tenant_id=tenant_id,
        aggregate_type="screening_case",
        aggregate_id=case.case_id,
        aggregate_version=version,
        data=summary,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="verification.screening.decided",
            actor_type="HUMAN" if actor else "SERVICE",
            action="decided",
            target_type="screening_case",
            target_ref=case.case_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=decision.reasons[0] if decision.reasons else None,
            source_service="screening",
            source_event_id=event_id,
            details={**summary, "version": version},
        ),
    )
    return _view(await store.load_case(conn, tenant_id, case.case_id), now)


async def open_case(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    country: str,
    subjects: Sequence[tuple[Role, Party]],
    graph: Graph | None,
    authority: AuthorityEvidence,
    requested_by: str,
    idempotency_key: str,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
    kyb_case_id: str | None = None,
    elevated_risk: bool = False,
    country_gate: CountryGate | None = None,
) -> CaseView:
    """Open a screening for a verified-status organization and run it. The same key is the same case."""
    if country_gate is not None:
        country_gate(country)
    if len(country) != 2 or not country.isupper():
        raise ScreeningError("COUNTRY_INVALID")
    existing = await store.case_by_key(conn, tenant_id, idempotency_key)
    if existing is not None:
        if existing.country != country:
            raise ScreeningError("IDEMPOTENCY_CONFLICT", "that key was used for another case")
        return _view(existing, now)
    case_id = str(uuid.uuid4())
    created = await store.create_case(
        conn,
        tenant_id,
        case_id=case_id,
        kyb_case_id=kyb_case_id,
        country=country,
        idempotency_key=idempotency_key,
        elevated_risk=elevated_risk,
        authority={
            "kind": authority.kind.value,
            "reference": authority.reference,
            "expires_at": authority.expires_at.isoformat() if authority.expires_at else None,
        },
        requested_by=requested_by,
        now=now,
    )
    if not created:  # a concurrent twin with the same key won
        winner = await store.case_by_key(conn, tenant_id, idempotency_key)
        if winner is None:
            raise ScreeningError("CASE_NOT_FOUND")
        return _view(winner, now)
    await store.put_parties(conn, tenant_id, case_id, list(subjects))
    if graph is not None:
        await store.put_graph(conn, tenant_id, case_id, graph)
    case = await store.load_case(conn, tenant_id, case_id)
    inp = await _input(conn, tenant_id, case, cfg, now)
    return await _record(
        conn, tenant_id, case, inp, decide(inp), sign, version=1, actor=requested_by, now=now
    )


async def rescreen(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
    actor: str | None = None,
) -> CaseView:
    """Screen again against the lists as they are now (a new designation, a changed owner, an expiry). Appends; never edits."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    inp = await _input(conn, tenant_id, case, cfg, now)
    return await _record(
        conn,
        tenant_id,
        case,
        inp,
        decide(inp),
        sign,
        version=case.current_version + 1,
        actor=actor,
        now=now,
    )


async def due_for_rescreen(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    now: datetime,
    retry_after: timedelta = timedelta(hours=1),
) -> list[str]:
    """Cases whose last decision no longer reflects the lists in use, whose clear outcome has expired, or that could not be
    screened (UNAVAILABLE) and are due another try. A case waiting for information (GATHERING) is not: only new information
    moves it. The retry gap keeps an outage from filling the evidence with identical rows (ED-04 section 07)."""
    current = {lid: (s.version, s.content_hash) for lid, s in (await store.snapshots(conn)).items()}
    rows = (
        await conn.execute(
            text(
                "SELECT c.case_id, c.status, c.expiry_at, e.body, e.created_at FROM verify.screening_case c "
                "JOIN verify.screening_evidence e ON e.case_id = c.case_id AND e.version = c.current_version "
                "WHERE c.tenant_id = :t AND c.status IN ('VERIFIED', 'REVIEW_REQUIRED', 'UNAVAILABLE')"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    due: list[str] = []
    for r in rows:
        seen = {x["list_id"]: (x["version"], x["content_hash"]) for x in r.body.get("lists", [])}
        changed = any(seen.get(lid.value) != cur for lid, cur in current.items())
        expired = r.status == "VERIFIED" and (r.expiry_at is None or r.expiry_at <= now)
        retry = r.status == "UNAVAILABLE" and now - r.created_at >= retry_after
        if changed or expired or retry:
            due.append(str(r.case_id))
    return due


async def review(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    ruling: Ruling,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
) -> CaseView:
    """A compliance reviewer's ruling on a case in review. It keeps the alerts the reviewer saw and records why."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    inp = await _input(conn, tenant_id, case, cfg, now)
    check_ruling(
        case.status,
        ruling,
        requester_id=case.requested_by,
        subject_refs=[s.party.ref for s in inp.subjects],
    )
    last = (await store.history(conn, tenant_id, case_id))[-1]
    validity = cfg.policy.validity_elevated if case.elevated_risk else cfg.policy.validity
    decision = Decision(
        status=ruling.outcome,
        reasons=tuple(dict.fromkeys([*last.reasons, "REVIEWER_RULING"])),
        matches=(),
        ownership=None,
        authority_verified=bool(last.body.get("authority_verified")),
        lists=tuple(
            (x["list_id"], x["version"], x["content_hash"]) for x in last.body.get("lists", [])
        ),
        expiry_at=now + validity if ruling.outcome is ScreeningStatus.VERIFIED else None,
    )
    carry: dict[str, Any] = {k: last.body[k] for k in ("matches", "ownership") if k in last.body}
    return await _record(
        conn,
        tenant_id,
        case,
        inp,
        decision,
        sign,
        version=case.current_version + 1,
        actor=ruling.reviewer_id,
        decided_by=ruling.reviewer_id,
        rationale=ruling.rationale.strip(),
        carry=carry,
        now=now,
    )


async def case_for_kyb(
    conn: AsyncConnection, tenant_id: str, kyb_case_id: str, *, now: datetime
) -> tuple[CaseView, bool] | None:
    """The screening of a company-verification case, if one was opened: its safe view and whether authority was proven."""
    case = await store.case_for_kyb(conn, tenant_id, kyb_case_id)
    if case is None:
        return None
    hist = await store.history(conn, tenant_id, case.case_id)
    return _view(case, now), bool(hist and hist[-1].body.get("authority_verified"))


async def get_case(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, now: datetime
) -> CaseView:
    """The safe view: an outcome and a next step, for a customer administrator."""
    return _view(await store.load_case(conn, tenant_id, case_id), now)


async def restricted_detail(conn: AsyncConnection, tenant_id: str, case_id: str) -> dict[str, Any]:
    """What a designated compliance reviewer sees: the alerts with the listed names. The caller must have authorized this."""
    case = await store.load_case(conn, tenant_id, case_id)
    hist = await store.history(conn, tenant_id, case_id)
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "reasons": list(case.reasons),
        "versions": [
            {"version": h.version, "status": h.decision_status.value, "decided_by": h.decided_by}
            for h in hist
        ],
        "alerts": await store.matches_for(conn, tenant_id, case_id, case.current_version)
        if hist
        else [],
    }


async def history(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[store.EvidenceRow]:
    return await store.history(conn, tenant_id, case_id)
