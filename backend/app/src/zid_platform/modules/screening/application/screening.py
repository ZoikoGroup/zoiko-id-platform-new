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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.screening.domain.decision import (
    APPEAL_OPEN,
    AuthorityEvidence,
    AuthorityKind,
    Decision,
    RegisterEvidence,
    Role,
    Ruling,
    ScreeningError,
    ScreeningInput,
    ScreeningStatus,
    Subject,
    check_appeal,
    check_appeal_reviewer,
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
StorageGate = Callable[
    [], Awaitable[None]
]  # raises unless this cell may hold the tenant's restricted records
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
    tasks: tuple[dict[str, Any], ...] = ()


def _view(case: store.CaseRow, now: datetime) -> CaseView:
    status = effective_status(case.status, case.expiry_at, now)
    return CaseView(
        case.case_id,
        status,
        safe_view(status, case.reasons)["next_action"],
        case.current_version,
        case.expiry_at,
        tuple(safe_view(status, case.reasons)["tasks"]),
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
        register=await store.load_register(conn, tenant_id, case.case_id),
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
    register: RegisterEvidence | None = None,
    register_source: str = "",
    storage_gate: StorageGate | None = None,
) -> CaseView:
    """Open a screening for a verified-status organization and run it. The same key is the same case. `storage_gate` is asked
    before a single name is stored: restricted person records stay in the tenant's approved region (ZID-ED-04 section 06)."""
    if country_gate is not None:
        country_gate(country)
    if len(country) != 2 or not country.isupper():
        raise ScreeningError("COUNTRY_INVALID")
    existing = await store.case_by_key(conn, tenant_id, idempotency_key)
    if existing is not None:
        if existing.country != country:
            raise ScreeningError("IDEMPOTENCY_CONFLICT", "that key was used for another case")
        return _view(existing, now)
    if storage_gate is not None:
        await storage_gate()
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
    if register is not None:
        await store.put_register(
            conn, tenant_id, case_id, register, source=register_source, now=now
        )
    case = await store.load_case(conn, tenant_id, case_id)
    inp = await _input(conn, tenant_id, case, cfg, now)
    return await _record(
        conn, tenant_id, case, inp, decide(inp), sign, version=1, actor=requested_by, now=now
    )


async def amend_case(
    conn: AsyncConnection,
    tenant_id: str,
    kyb_case_id: str,
    *,
    country: str,
    subjects: Sequence[tuple[Role, Party]],
    graph: Graph | None,
    authority: AuthorityEvidence,
    requested_by: str,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
    elevated_risk: bool = False,
    country_gate: CountryGate | None = None,
    register: RegisterEvidence | None = None,
    register_source: str = "",
    storage_gate: StorageGate | None = None,
) -> CaseView:
    """The customer declares again while the case is waiting for details (GATHERING). The earlier declaration and its evidence stay
    as they were; the new one is the case's next screening, so nothing is rewritten."""
    latest = await store.case_for_kyb(conn, tenant_id, kyb_case_id)
    if latest is None or latest.status is not ScreeningStatus.GATHERING:
        raise ScreeningError(
            "AMEND_NOT_AVAILABLE", "only a case that is waiting for details can be declared again"
        )
    n = await store.count_cases_for_kyb(conn, tenant_id, kyb_case_id)
    return await open_case(
        conn, tenant_id, country=country, subjects=subjects, graph=graph, authority=authority, requested_by=requested_by,
        idempotency_key=f"claim-{kyb_case_id}-{n + 1}", cfg=cfg, sign=sign, now=now, kyb_case_id=kyb_case_id,
        elevated_risk=elevated_risk, country_gate=country_gate, register=register, register_source=register_source,
        storage_gate=storage_gate,
    )  # fmt: skip


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
    """Screen again against the lists as they are now (a new designation, a changed owner, an expiry). Appends; never edits. A
    trigger that asked for it (the register changed, a privileged role was granted, a high-risk action) is named in the evidence."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    inp = await _input(conn, tenant_id, case, cfg, now)
    triggers = (await store.open_triggers(conn, tenant_id)).get(case_id, [])
    version = case.current_version + 1
    view = await _record(
        conn, tenant_id, case, inp, decide(inp), sign, version=version, actor=actor,
        carry={"triggers": sorted(set(triggers))} if triggers else None, now=now,
    )  # fmt: skip
    await store.close_triggers(conn, tenant_id, case_id, version)
    return view


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
    triggered = await store.open_triggers(conn, tenant_id)
    due: list[str] = []
    for r in rows:
        seen = {x["list_id"]: (x["version"], x["content_hash"]) for x in r.body.get("lists", [])}
        changed = any(seen.get(lid.value) != cur for lid, cur in current.items())
        expired = r.status == "VERIFIED" and (r.expiry_at is None or r.expiry_at <= now)
        retry = r.status == "UNAVAILABLE" and now - r.created_at >= retry_after
        if changed or expired or retry or str(r.case_id) in triggered:
            due.append(str(r.case_id))
    return due


async def request_rescreen(
    conn: AsyncConnection, tenant_id: str, *, reason: str, now: datetime, case_id: str | None = None
) -> int:
    """Ask for a screening again because something material happened that is not a list change: the register changed, a privileged
    role was granted, a high-risk action was taken (ZID-ED-04 section 07). One case, or every live case of the tenant. The worker
    answers it on its next pass and the evidence names the reason. Returns how many cases were asked for."""
    live = ("VERIFIED", "REVIEW_REQUIRED", "UNAVAILABLE")
    rows = (
        await conn.execute(
            text(
                "SELECT case_id FROM verify.screening_case WHERE tenant_id = :t AND status = ANY(:s)"
                + (" AND case_id = :c" if case_id else "")
            ),
            {
                "t": uuid.UUID(tenant_id),
                "s": list(live),
                **({"c": uuid.UUID(case_id)} if case_id else {}),
            },
        )
    ).all()
    for r in rows:
        await store.add_trigger(conn, tenant_id, str(r.case_id), reason, now)
    return len(rows)


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
    hist = await store.history(conn, tenant_id, case_id)
    last = hist[-1]
    if _appeal_open(case, last):  # an independent second look: not whoever decided before
        check_appeal_reviewer(ruling.reviewer_id, [h.decided_by for h in hist if h.decided_by])
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


def _appeal_open(case: store.CaseRow, last: store.EvidenceRow) -> bool:
    return (
        case.status is ScreeningStatus.REVIEW_REQUIRED
        and last.decided_by is None
        and APPEAL_OPEN in last.reasons
    )


async def live_kyb_cases(conn: AsyncConnection) -> list[tuple[str, str, str, str]]:
    """(tenant, case, company-verification case, country) of each company's newest screening that is still live, for the worker
    (which sees every tenant). A case that is closed or only waiting for the customer is not watched."""
    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id, case_id, kyb_case_id, country FROM ("
                " SELECT DISTINCT ON (kyb_case_id) tenant_id, case_id, kyb_case_id, country, status FROM verify.screening_case"
                " WHERE kyb_case_id IS NOT NULL ORDER BY kyb_case_id, created_at DESC) latest "
                "WHERE status IN ('VERIFIED', 'REVIEW_REQUIRED', 'UNAVAILABLE')"
            )
        )
    ).all()
    return [
        (str(r.tenant_id), str(r.case_id), str(r.kyb_case_id), str(r.country).strip()) for r in rows
    ]


async def record_register(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    ev: RegisterEvidence,
    *,
    source: str,
    now: datetime,
) -> bool:
    """Keep what the register says now as the case's next snapshot when it differs from the last one, and ask for a rescreen.
    True when something was recorded. The first snapshot a case gets is `REGISTER_FIRST_READ`, a later difference `REGISTER_CHANGED`."""
    latest = await store.load_register(conn, tenant_id, case_id)
    if latest is not None and latest.status == ev.status and latest.content_hash == ev.content_hash:
        return False
    await store.put_register(conn, tenant_id, case_id, ev, source=source, now=now)
    reason = "REGISTER_FIRST_READ" if latest is None else "REGISTER_CHANGED"
    await request_rescreen(conn, tenant_id, reason=reason, now=now, case_id=case_id)
    return True


async def request_appeal(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    kind: str,
    statement: str,
    requested_by: str,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
    storage_gate: StorageGate | None = None,
) -> CaseView:
    """The customer appeals a rejection or asks for a correction (ZID-ED-04 section 06, decision fairness). The case goes back to a
    person with the statement; nothing is decided by this call. The statement is a restricted record: only its hash is in the
    evidence, the event and the audit record, and it is shown to a reviewer only."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    hist = await store.history(conn, tenant_id, case_id)
    if not hist:
        raise ScreeningError("APPEAL_NOT_AVAILABLE", case.status.value)
    last = hist[-1]
    check_appeal(case.status, kind, statement, open_appeal=_appeal_open(case, last))
    if storage_gate is not None:
        await storage_gate()
    statement_hash = hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()
    version = case.current_version + 1
    await store.add_appeal(
        conn, tenant_id, case_id, kind=kind, statement=statement.strip(), statement_hash=statement_hash,
        requested_by=requested_by, evidence_version=version, now=now,
    )  # fmt: skip
    inp = await _input(conn, tenant_id, case, cfg, now)
    decision = Decision(
        status=ScreeningStatus.REVIEW_REQUIRED,
        reasons=tuple(
            dict.fromkeys([*last.reasons, APPEAL_OPEN, f"{kind}_STATEMENT:{statement_hash[:16]}"])
        ),
        matches=(),
        ownership=None,
        authority_verified=bool(last.body.get("authority_verified")),
        lists=tuple(
            (x["list_id"], x["version"], x["content_hash"]) for x in last.body.get("lists", [])
        ),
        expiry_at=None,
    )
    carry: dict[str, Any] = {k: last.body[k] for k in ("matches", "ownership") if k in last.body}
    return await _record(
        conn,
        tenant_id,
        case,
        inp,
        decision,
        sign,
        version=version,
        actor=requested_by,
        carry=carry,
        now=now,
    )


async def replay(
    conn: AsyncConnection, tenant_id: str, case_id: str, version: int, *, cfg: ScreeningConfig
) -> dict[str, Any]:
    """Decide again from exactly what a past decision used (the lists it named by content hash, the parties, the register
    snapshot it saw, the policy and the clock it was made at) and compare with the signed evidence (ZID-ED-04 section 08:
    traceable and replayable). A reviewer's ruling is a person's decision and is not replayed. A decision made while a list was
    stale or missing cannot be replayed exactly, because a list's fetch time moves each time it is fetched again; that comes back
    as `matches: False`, never as a quiet pass."""
    case = await store.load_case(conn, tenant_id, case_id)
    row = next(
        (h for h in await store.history(conn, tenant_id, case_id) if h.version == version), None
    )
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", f"no version {version}")
    if row.decided_by is not None or row.body.get("reviewed"):
        return {"replayable": False, "reason": "REVIEWER_RULING", "version": version}
    snaps = {}
    for x in row.body.get("lists", []):
        snap = await store.snapshot_for(conn, ListId(x["list_id"]), x["content_hash"])
        if snap is None:
            return {"replayable": False, "reason": "LIST_VERSION_NOT_KEPT", "version": version}
        snaps[snap.list_id] = snap
    reg = row.body.get("register") or {}
    register = (
        await store.load_register(conn, tenant_id, case_id, seq=reg["seq"])
        if reg.get("seq")
        else None
    )
    screened_at = datetime.fromisoformat(row.body["screened_at"])
    inp = ScreeningInput(
        country=case.country,
        subjects=tuple(
            Subject(role, party)
            for role, party in await store.load_parties(conn, tenant_id, case_id)
        ),
        graph=await store.load_graph(conn, tenant_id, case_id),
        authority=_authority(case.authority),
        elevated_risk=case.elevated_risk,
        snapshots=snaps,
        pack=cfg.pack(case.country),
        policy=cfg.policy,
        now=screened_at,
        register=register,
    )
    decision = decide(inp)
    body = evidence_object(
        tenant_id=tenant_id, case_id=case_id, inp=inp, decision=decision, version=version,
        carry={"triggers": row.body["triggers"]} if "triggers" in row.body else None,
    )  # fmt: skip
    return {
        "replayable": True,
        "version": version,
        "status": decision.status.value,
        "matches": body["evidence_hash"] == row.evidence_hash,
        "evidence_hash": row.evidence_hash,
        "recomputed_hash": body["evidence_hash"],
        "lists": [x["list_id"] + ":" + x["version"] for x in row.body.get("lists", [])],
    }


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
        "alerts": await _current_alerts(conn, tenant_id, case_id, case, hist),
        "appeals": await store.appeals_for(conn, tenant_id, case_id),
        "register": await _register_detail(conn, tenant_id, case_id),
    }


async def _current_alerts(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    case: store.CaseRow,
    hist: Sequence[store.EvidenceRow],
) -> list[dict[str, Any]]:
    """The alerts of the current decision. A ruling or an appeal carries the alerts it answers (the evidence body names them) but
    only the decision that found them stored the rows, so a carried decision reads them from the latest one that did."""
    if not hist:
        return []
    alerts = await store.matches_for(conn, tenant_id, case_id, case.current_version)
    if alerts or not hist[-1].body.get("matches"):
        return alerts
    for h in reversed(hist[:-1]):
        alerts = await store.matches_for(conn, tenant_id, case_id, h.version)
        if alerts:
            return alerts
    return []


async def _register_detail(
    conn: AsyncConnection, tenant_id: str, case_id: str
) -> dict[str, Any] | None:
    """What the official register listed, for the reviewer to set beside what the customer declared."""
    reg = await store.load_register(conn, tenant_id, case_id)
    if reg is None:
        return None
    return {
        "status": reg.status,
        "seq": reg.seq,
        "people": [
            {
                "ref": p.ref,
                "kind": p.kind,
                "name": p.party.name,
                "natures": list(p.natures),
                "ceased": p.ceased,
            }
            for p in reg.people
        ],
    }


async def history(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[store.EvidenceRow]:
    return await store.history(conn, tenant_id, case_id)


async def case_countries(conn: AsyncConnection, case_ids: list[str]) -> dict[str, str]:
    """The country each case was opened for (row-level security scopes the read to the tenant in force)."""
    if not case_ids:
        return {}
    rows = (
        await conn.execute(
            text("SELECT case_id, country FROM verify.screening_case WHERE case_id = ANY(:ids)"),
            {"ids": [uuid.UUID(c) for c in case_ids]},
        )
    ).all()
    return {str(r.case_id): str(r.country).strip() for r in rows}
