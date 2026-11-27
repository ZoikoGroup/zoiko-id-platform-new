"""The WF06 screening case: open it, read it, decide it (ZID-DTV-WF-06 S01 to S09, section 07; CR-113).

`open_request` checks eligibility first and refuses, storing nothing, unless the market's signed policy, the region, the lawful basis, a current identity claim and the tenant's right to the class
all hold. It then screens the subject against the ACTIVE corpus of every required official list, stores what each source said with the exact snapshot, and stores what matched as candidates, which
are restricted and never a finding. The four result dimensions are worked out again each time the case is read, so freshness is the corpus's real age at that moment and a stale list reads stale.

`decide` appends one revision to the case's decision chain under the disposition rules (maker and checker, legal-only acts, a stale revision refused). The customer reads a minimal summary; the
subject's details and the candidates are for a reviewer only and appear in no event, audit record or customer view.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.screening.application import licensed
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import ListId, Snapshot
from zid_platform.modules.screening.domain.matching import MatchPolicy, Party, screen_party
from zid_platform.modules.screening.infrastructure.market_policies import MarketPack
from zid_platform.modules.screening.infrastructure.providers import ProviderEntry

OFFICIAL_LISTS = (
    ListId.OFAC_SDN,
    ListId.OFAC_NON_SDN,
    ListId.UK_SANCTIONS,
    ListId.EU_CONSOLIDATED,
    ListId.UN_SC,
)


def _state(sources: Sequence[cov.SourceResult], model: Mapping[str, Any]) -> str:
    if any(s.candidates for s in sources):
        return "REVIEW_REQUIRED"
    if model["coverage_status"] in ("UNAVAILABLE", "SOURCE_STALE"):
        return "SOURCE_UNAVAILABLE"
    return "NO_CANDIDATE"


def _not_eligible(e: elig.Eligibility) -> ScreeningError:
    codes = list(dict.fromkeys([*e.blocked, *(r for c in e.classes for r in c.reasons)]))
    return ScreeningError("SCREENING_NOT_ELIGIBLE", ",".join(codes))


async def _sources_of(conn: AsyncConnection, request_id: str) -> list[cov.SourceResult]:
    rows = (
        await conn.execute(
            text(
                "SELECT source_id, klass, tier, snapshot, screened_at, available, candidates, last_validated_at "
                "FROM verify.screening_request_source WHERE request_id = :r ORDER BY source_id"
            ),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    return [
        cov.SourceResult(
            r.source_id,
            cov.Klass(r.klass),
            cov.Tier(r.tier),
            r.snapshot,
            r.screened_at,
            bool(r.available),
            int(r.candidates),
            r.last_validated_at,
        )
        for r in rows
    ]


def _revision_to_json(r: disp.Revision) -> str:
    return json.dumps(
        {
            "case_id": r.case_id,
            "revision": r.revision,
            "disposition": r.disposition.value,
            "action": r.action.value,
            "maker": r.maker,
            "checker": r.checker,
            "rationale": r.rationale,
            "reason_codes": list(r.reason_codes),
            "evidence_refs": list(r.evidence_refs),
            "policy_version": r.policy_version,
            "legal_citation": r.legal_citation,
            "review_by": r.review_by.isoformat() if r.review_by else None,
            "decided_at": r.decided_at.isoformat(),
            "supersedes": r.supersedes,
            "kind": r.kind,
        }
    )


def _revision_from_json(d: dict[str, Any], case_hash: str) -> disp.Revision:
    return disp.Revision(
        case_id=d["case_id"],
        revision=int(d["revision"]),
        disposition=cov.Disposition(d["disposition"]),
        action=disp.Action(d["action"]),
        maker=d["maker"],
        checker=d["checker"],
        rationale=d["rationale"],
        reason_codes=tuple(d["reason_codes"]),
        evidence_refs=tuple(d["evidence_refs"]),
        policy_version=d["policy_version"],
        legal_citation=d["legal_citation"],
        review_by=datetime.fromisoformat(d["review_by"]) if d["review_by"] else None,
        decided_at=datetime.fromisoformat(d["decided_at"]),
        supersedes=d["supersedes"],
        kind=d["kind"],
        case_hash=case_hash,
    )


async def chain(conn: AsyncConnection, request_id: str) -> list[disp.Revision]:
    rows = (
        await conn.execute(
            text(
                "SELECT body, case_hash FROM verify.screening_request_decision "
                "WHERE request_id = :r ORDER BY revision"
            ),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    return [_revision_from_json(r.body, r.case_hash) for r in rows]


async def _row(conn: AsyncConnection, request_id: str) -> Any:
    try:
        key = uuid.UUID(request_id)
    except ValueError:
        return None
    return (
        await conn.execute(
            text(
                "SELECT request_id, tenant_id, market, region, purpose, subject_type, subject_ref, policy_version, classes, state, "
                "created_at, screened_at, revision FROM verify.screening_request WHERE request_id = :r"
            ),
            {"r": key},
        )
    ).first()


def _reviewed(
    history: Sequence[disp.Revision], sources: Sequence[cov.SourceResult]
) -> tuple[cov.Match | None, bool, list[str]]:
    """What the latest decision says about the match, whether Legal signed a prohibition, and who decided."""
    if not history:
        return None, False, []
    last = history[-1]
    people = [p for p in (last.maker, last.checker) if p]
    had_candidates = any(
        s.candidates
        for s in sources
        if s.klass in (cov.Klass.SANCTIONS, cov.Klass.OWNERSHIP_CONTROL)
    )
    if last.disposition is cov.Disposition.CLEARED_FOR_PURPOSE and had_candidates:
        return cov.Match.FALSE_POSITIVE, False, people
    if last.disposition in (cov.Disposition.RESTRICTED, cov.Disposition.PROHIBITED):
        return cov.Match.CONFIRMED_MATCH, last.disposition is cov.Disposition.PROHIBITED, people
    return None, False, people


async def model_of(
    conn: AsyncConnection, request_id: str, *, now: datetime, max_age: timedelta
) -> dict[str, Any]:
    """The four dimensions as they stand now, from what was stored and the decisions since."""
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    sources = await _sources_of(conn, request_id)
    history = await chain(conn, request_id)
    reviewed, signed, people = _reviewed(history, sources)
    required = frozenset(cov.Klass(k) for k in row.classes)
    expired = bool(history and history[-1].review_by and now >= history[-1].review_by)
    return cov.result_model(
        case_id=str(row.request_id),
        results=sources,
        policy=cov.CoveragePolicy(required, max_age, row.policy_version),
        now=now,
        reviewed_match=reviewed,
        approvers=people,
        legal_signed=signed,
        expired=expired,
        rule_version=row.policy_version,
    )


async def open_request(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    actor: str,
    idempotency_key: str,
    request: elig.Request,
    subject: Party,
    pack: MarketPack | None,
    snapshots: Mapping[ListId, Snapshot],
    max_age: timedelta,
    match_policy: MatchPolicy,
    now: datetime,
    registry: Mapping[str, ProviderEntry] | None = None,
    adapters: Mapping[str, licensed.LicensedScreeningPort] | None = None,
    gated_off: Collection[cov.Klass] = (),
) -> dict[str, Any]:
    """Screen one subject. Refused (nothing stored) unless eligible; the same idempotency key again returns the same case."""
    existing = (
        await conn.execute(
            text(
                "SELECT request_id FROM verify.screening_request WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": uuid.UUID(tenant_id), "k": idempotency_key},
        )
    ).first()
    if existing is not None:
        return await customer_view(conn, str(existing.request_id), now=now, max_age=max_age)
    usable = frozenset({cov.Klass.SANCTIONS}) | licensed.usable_classes(
        registry or {}, adapters or {}
    )
    eligibility = elig.check(
        request,
        pack.policy if pack else None,
        entitled=pack.entitled(tenant_id) if pack else frozenset(),
        usable_sources=usable,
        gated_off=gated_off,
        now=now,
    )
    if not eligibility.eligible:
        raise _not_eligible(eligibility)
    granted = eligibility.available()
    request_id = uuid.uuid4()
    tenant = uuid.UUID(tenant_id)
    await conn.execute(
        text(
            "INSERT INTO verify.screening_request (request_id, tenant_id, market, region, purpose, subject_type, subject_ref, "
            "idempotency_key, policy_version, classes, state, created_by, created_at, screened_at) VALUES (:r, :t, :m, :g, :p, :st, "
            ":sr, :k, :v, CAST(:c AS jsonb), :s, :by, :at, :at)"
        ),
        {
            "r": request_id,
            "t": tenant,
            "m": request.market,
            "g": request.region,
            "p": request.purpose,
            "st": request.subject_type.value,
            "sr": request.subject_ref or "NONE",
            "k": idempotency_key,
            "v": eligibility.policy_version or "",
            "c": json.dumps(sorted(k.value for k in request.requested)),
            "s": "SOURCE_UNAVAILABLE",  # provisional: the state is set when every source has answered
            "by": uuid.UUID(actor),
            "at": now,
        },
    )
    results: list[cov.SourceResult] = []
    candidates: list[tuple[str, Any]] = []
    hits: list[tuple[str, cov.Klass, str, licensed.LicensedHit]] = []
    if cov.Klass.SANCTIONS in granted:
        for list_id in OFFICIAL_LISTS:
            snap = snapshots.get(list_id)
            if snap is None:
                results.append(
                    cov.SourceResult(
                        list_id.value, cov.Klass.SANCTIONS, cov.Tier.OFFICIAL, None, None, False
                    )
                )
                continue
            matches = screen_party(subject, [snap], match_policy)
            results.append(
                cov.SourceResult(
                    list_id.value,
                    cov.Klass.SANCTIONS,
                    cov.Tier.OFFICIAL,
                    f"sha256:{snap.content_hash}",
                    now,
                    True,
                    len(matches),
                    snap.fetched_at,
                )
            )
            candidates.extend((list_id.value, m) for m in matches)
    for klass in (cov.Klass.PEP_RCA, cov.Klass.ADVERSE_MEDIA):
        if klass not in granted:
            continue
        out = await licensed.screen_class(
            conn, tenant_id, request_id=str(request_id), klass=klass, party=subject, market=request.market, region=request.region,
            tenant_regions=[request.region], registry=registry or {}, adapters=adapters or {}, now=now,
        )  # fmt: skip
        results.append(out.result)
        hits.extend((out.provider_id, klass, out.result.snapshot or "", h) for h in out.hits)
    model = cov.result_model(
        case_id=str(request_id),
        results=results,
        policy=cov.CoveragePolicy(
            request.requested, max_age, eligibility.policy_version or ""
        ),  # every class asked for is required: one that could not run is NOT_CONFIGURED, never dropped
        now=now,
        rule_version=eligibility.policy_version or "",
    )
    state = _state(results, model)
    await conn.execute(
        text("UPDATE verify.screening_request SET state = :s WHERE request_id = :r"),
        {"s": state, "r": request_id},
    )
    await notify_outcome(conn, tenant_id, str(request_id), request.market, state, 0)
    assert request.identity is not None  # an eligible request has a current identity claim
    await conn.execute(
        text(
            "INSERT INTO verify.screening_request_subject (request_id, tenant_id, kind, name, birth, countries, identifiers, "
            "expired_identifiers, identity_source, identity_assured_at) VALUES (:r, :t, :k, :n, :b, CAST(:c AS jsonb), "
            "CAST(:i AS jsonb), CAST(:e AS jsonb), :src, :at)"
        ),
        {
            "r": request_id,
            "t": tenant,
            "k": subject.kind.value,
            "n": subject.name,
            "b": subject.birth,
            "c": json.dumps(list(subject.countries)),
            "i": json.dumps(list(subject.identifiers)),
            "e": json.dumps(list(subject.expired_identifiers)),
            "src": request.identity.source,
            "at": request.identity.assured_at,
        },
    )
    for s in results:
        await conn.execute(
            text(
                "INSERT INTO verify.screening_request_source (request_id, tenant_id, source_id, klass, tier, snapshot, screened_at, "
                "available, candidates, last_validated_at) VALUES (:r, :t, :s, :k, :ti, :sn, :sa, :av, :c, :lv)"
            ),
            {
                "r": request_id,
                "t": tenant,
                "s": s.source_id,
                "k": s.klass.value,
                "ti": s.tier.value,
                "sn": s.snapshot,
                "sa": s.screened_at,
                "av": s.available,
                "c": s.candidates,
                "lv": s.last_validated_at,
            },
        )
    for list_id, m in candidates:  # type: ignore[assignment]
        await conn.execute(
            text(
                "INSERT INTO verify.screening_request_candidate (candidate_id, request_id, tenant_id, list_id, list_version, source_ref, "
                "matched_name, score, strength, reasons, programs) VALUES (:c, :r, :t, :l, :v, :sr, :n, :sc, :st, CAST(:re AS jsonb), "
                "CAST(:p AS jsonb)) ON CONFLICT (request_id, list_id, source_ref) DO NOTHING"
            ),
            {
                "c": uuid.uuid4(),
                "r": request_id,
                "t": tenant,
                "l": list_id,
                "v": m.list_version,
                "sr": m.source_ref,
                "n": m.matched_name,
                "sc": m.score,
                "st": m.strength.value,
                "re": json.dumps(list(m.reasons)),
                "p": json.dumps(list(m.programs)),
            },
        )
    for provider_id, klass, version, h in hits:
        row = licensed.hit_row(provider_id, klass, version, h, subject=subject, today=now.date())
        await conn.execute(
            text(
                "INSERT INTO verify.screening_request_candidate (candidate_id, request_id, tenant_id, list_id, list_version, source_ref, "
                "matched_name, score, strength, reasons, programs) VALUES (:c, :r, :t, :l, :v, :sr, :n, :sc, :st, CAST(:re AS jsonb), "
                "CAST(:p AS jsonb)) ON CONFLICT (request_id, list_id, source_ref) DO NOTHING"
            ),
            {"c": uuid.uuid4(), "r": request_id, "t": tenant, "l": row["list_id"], "v": row["list_version"], "sr": row["source_ref"],
             "n": row["matched_name"], "sc": row["score"], "st": row["strength"], "re": row["reasons"], "p": row["programs"]},
        )  # fmt: skip
    return await customer_view(conn, str(request_id), now=now, max_age=max_age)


OUTCOME = {
    "REVIEW_REQUIRED": "IN_REVIEW",
    "SOURCE_UNAVAILABLE": "IN_REVIEW",
    "NO_CANDIDATE": "COMPLETE",
    "CLOSED_CLEAR_FOR_SCOPE": "COMPLETE",
    "CLOSED_RESTRICTED": "COMPLETE",
    "CLOSED_UNRESOLVED": "COMPLETE",
}  # all a webhook says: in review or complete. Which complete, and why, is for the authenticated case view to say


async def notify_outcome(
    conn: AsyncConnection, tenant_id: str, request_id: str, market: str, state: str, revision: int
) -> None:
    """Tell the customer's subscribed endpoints that a case changed, with no name, list, candidate, disposition or reason. A thin notice: the customer then reads the case under its own access."""
    await emit(
        conn,
        name="screening.case.outcome_changed",
        tenant_id=tenant_id,
        aggregate_type="screening_case",
        aggregate_id=request_id,
        aggregate_version=revision,
        data={
            "case_id": request_id,
            "market": market,
            "outcome": OUTCOME.get(state, "IN_REVIEW"),
            "revision": revision,
        },
    )


async def customer_view(
    conn: AsyncConnection, request_id: str, *, now: datetime, max_age: timedelta
) -> dict[str, Any]:
    """The minimal status a tenant user may see: per-class coverage in words, one calibrated finding, the time it was last checked, and nothing about a list, a candidate or a reason."""
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    model = await model_of(conn, request_id, now=now, max_age=max_age)
    summary = cov.customer_summary(model, checked_at=row.screened_at)
    return {
        "request_id": str(row.request_id),
        "market": row.market,
        "purpose": row.purpose,
        "subject_type": row.subject_type,
        "created_at": row.created_at.isoformat(),
        **summary,
    }


async def decide(
    conn: AsyncConnection,
    request_id: str,
    proposal: disp.Proposal,
    *,
    checker: str | None,
    checker_role: disp.Role | None,
    now: datetime,
    kind: str = "DECISION",
) -> dict[str, Any]:
    """Append one decision. Must run under the case's own tenant. The new revision cites the one before; the state follows the disposition."""
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    history = await chain(conn, request_id)
    try:
        out = disp.append(
            history, proposal, checker=checker, checker_role=checker_role, at=now, kind=kind
        )
    except disp.DispositionError as e:
        raise ScreeningError(e.code, e.detail) from e
    new = out[-1]
    await conn.execute(
        text(
            "INSERT INTO verify.screening_request_decision (request_id, tenant_id, revision, body, case_hash, decided_at) "
            "VALUES (:r, :t, :n, CAST(:b AS jsonb), :h, :at)"
        ),
        {
            "r": row.request_id,
            "t": row.tenant_id,
            "n": new.revision,
            "b": _revision_to_json(new),
            "h": new.case_hash,
            "at": now,
        },
    )
    state = {
        cov.Disposition.CLEARED_FOR_PURPOSE: "CLOSED_CLEAR_FOR_SCOPE",
        cov.Disposition.RESTRICTED: "CLOSED_RESTRICTED",
        cov.Disposition.PROHIBITED: "CLOSED_RESTRICTED",
        cov.Disposition.UNRESOLVED: "CLOSED_UNRESOLVED",
    }.get(new.disposition, "REVIEW_REQUIRED")
    await conn.execute(
        text("UPDATE verify.screening_request SET revision = :n, state = :s WHERE request_id = :r"),
        {"n": new.revision, "s": state, "r": row.request_id},
    )
    await notify_outcome(
        conn, str(row.tenant_id), str(row.request_id), str(row.market), state, new.revision
    )
    return {
        "request_id": request_id,
        "revision": new.revision,
        "disposition": new.disposition.value,
        "state": state,
        "case_hash": new.case_hash,
    }


async def restricted_detail(
    conn: AsyncConnection, request_id: str, *, now: datetime, max_age: timedelta
) -> dict[str, Any]:
    """For a screening reviewer only, under the case's own tenant: the subject's details, every candidate with its reasons, the sources, the four dimensions and the decision chain."""
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    key = uuid.UUID(request_id)
    subject = (
        await conn.execute(
            text(
                "SELECT kind, name, birth, countries, identifiers, expired_identifiers, identity_source, identity_assured_at "
                "FROM verify.screening_request_subject WHERE request_id = :r"
            ),
            {"r": key},
        )
    ).first()
    cands = (
        await conn.execute(
            text(
                "SELECT candidate_id, list_id, list_version, source_ref, matched_name, score, strength, reasons, programs "
                "FROM verify.screening_request_candidate WHERE request_id = :r ORDER BY strength, score DESC, source_ref"
            ),
            {"r": key},
        )
    ).all()
    history = await chain(conn, request_id)
    return {
        "request_id": request_id,
        "tenant_id": str(row.tenant_id),
        "state": row.state,
        "revision": int(row.revision),
        "market": row.market,
        "purpose": row.purpose,
        "subject": None
        if subject is None
        else {
            "kind": subject.kind,
            "name": subject.name,
            "birth": subject.birth,
            "countries": subject.countries,
            "identifiers": subject.identifiers,
            "expired_identifiers": subject.expired_identifiers,
            "identity_source": subject.identity_source,
            "identity_assured_at": subject.identity_assured_at.isoformat(),
        },
        "candidates": [
            {
                "candidate_id": str(c.candidate_id),
                "list_id": c.list_id,
                "list_version": c.list_version,
                "source_ref": c.source_ref,
                "matched_name": c.matched_name,
                "score": float(c.score),
                "strength": c.strength,
                "reasons": c.reasons,
                "programs": c.programs,
            }
            for c in cands
        ],
        "model": await model_of(conn, request_id, now=now, max_age=max_age),
        "decisions": [
            {
                "revision": h.revision,
                "disposition": h.disposition.value,
                "action": h.action.value,
                "maker": h.maker,
                "checker": h.checker,
                "rationale": h.rationale,
                "reason_codes": list(h.reason_codes),
                "kind": h.kind,
                "decided_at": h.decided_at.isoformat(),
                "case_hash": h.case_hash,
            }
            for h in history
        ],
        "chain_intact": disp.verify_chain(history),
        "proposals": await pending(conn, request_id),
    }


async def queue(
    conn: AsyncConnection,
    *,
    state: str | None = None,
    limit: int = 50,
    after: tuple[datetime, str] | None = None,
) -> dict[str, Any]:
    """The restricted queue, across tenants, for a screening reviewer. The session must say it is a reviewer's (`app.screening_reviewer`); the rows carry a case alias, the subject type, the
    market, the state and the age, never a name. Cursor pagination, oldest first."""
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        rows = (
            await conn.execute(
                text(
                    "SELECT r.request_id, r.tenant_id, r.market, r.purpose, r.subject_type, r.state, r.created_at, r.revision, "
                    "(SELECT count(*) FROM verify.screening_request_source s WHERE s.request_id = r.request_id AND s.candidates > 0) AS sources_with_candidates "
                    "FROM verify.screening_request r "
                    "WHERE (CAST(:st AS text) IS NULL OR r.state = :st) "
                    "AND (CAST(:ca AS timestamptz) IS NULL OR (r.created_at, r.request_id) > (CAST(:ca AS timestamptz), CAST(:ci AS uuid))) "
                    "ORDER BY r.created_at, r.request_id LIMIT :n"
                ),
                {
                    "st": state,
                    "ca": after[0] if after else None,
                    "ci": after[1] if after else None,
                    "n": limit + 1,
                },
            )
        ).all()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    page = rows[:limit]
    items = [
        {
            "request_id": str(r.request_id),
            "tenant_id": str(r.tenant_id),
            "market": r.market,
            "purpose": r.purpose,
            "subject_type": r.subject_type,
            "state": r.state,
            "created_at": r.created_at.isoformat(),
            "revision": int(r.revision),
            "sources_with_candidates": int(r.sources_with_candidates),
        }
        for r in page
    ]
    more = len(rows) > limit
    cursor = f"{page[-1].created_at.isoformat()}|{page[-1].request_id}" if more and page else None
    return {"items": items, "next_cursor": cursor}


# ---- a decision made by two people in two requests --------------------------------------------------------------------------


async def reopen_for_delta(
    conn: AsyncConnection,
    list_id: str,
    refs: Collection[str],
    *,
    release_id: str,
    now: datetime,
    code: str = "ENTRY_DELISTED_OR_CHANGED",
) -> int:
    """Hold again every case that relied on a candidate whose list entry was delisted or changed in a new release. A case already waiting for a person is left as it is; one that was cleared,
    restricted or closed goes back to a hold as a new revision by the system. Runs for a screening reviewer: the candidates are read across tenants, and each case is written under its own."""
    if not refs:
        return 0
    from zid_db import in_tenant

    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT c.request_id, c.tenant_id FROM verify.screening_request_candidate c "
                    "JOIN verify.screening_request r ON r.request_id = c.request_id "
                    "WHERE c.list_id = :l AND c.source_ref = ANY(:refs) AND r.state <> 'REVIEW_REQUIRED'"
                ),
                {"l": list_id, "refs": sorted(refs)},
            )
        ).all()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    reopened = 0
    for row in rows:
        async with in_tenant(conn, str(row.tenant_id)):
            history = await chain(conn, str(row.request_id))
            case = await _row(conn, str(row.request_id))
            if case is None:
                continue
            out = disp.reopen(
                history,
                case_id=str(row.request_id),
                reason_codes=(code,),
                evidence_ref=release_id,
                policy_version=str(case.policy_version),
                at=now,
            )
            new = out[-1]
            await conn.execute(
                text(
                    "INSERT INTO verify.screening_request_decision (request_id, tenant_id, revision, body, case_hash, decided_at) "
                    "VALUES (:r, :t, :n, CAST(:b AS jsonb), :h, :at)"
                ),
                {
                    "r": row.request_id,
                    "t": row.tenant_id,
                    "n": new.revision,
                    "b": _revision_to_json(new),
                    "h": new.case_hash,
                    "at": now,
                },
            )
            await conn.execute(
                text(
                    "UPDATE verify.screening_request SET revision = :n, state = 'REVIEW_REQUIRED' WHERE request_id = :r"
                ),
                {"n": new.revision, "r": row.request_id},
            )
            await notify_outcome(
                conn,
                str(row.tenant_id),
                str(row.request_id),
                str(case.market),
                "REVIEW_REQUIRED",
                new.revision,
            )
            reopened += 1
    return reopened


async def locate(conn: AsyncConnection, request_id: str) -> str | None:
    """The tenant a case belongs to, for a screening reviewer, read with the reviewer's view across tenants. The caller then works under that tenant's own scope."""
    try:
        key = uuid.UUID(request_id)
    except ValueError:
        return None
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        row = (
            await conn.execute(
                text("SELECT tenant_id FROM verify.screening_request WHERE request_id = :r"),
                {"r": key},
            )
        ).first()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    return None if row is None else str(row.tenant_id)


async def proposal_tenant(conn: AsyncConnection, proposal_id: str) -> str | None:
    """The tenant a proposal's case belongs to, for a screening reviewer (the reviewer's view across tenants, set only for this read)."""
    try:
        key = uuid.UUID(proposal_id)
    except ValueError:
        return None
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        row = (
            await conn.execute(
                text(
                    "SELECT tenant_id FROM verify.screening_request_proposal WHERE proposal_id = :p"
                ),
                {"p": key},
            )
        ).first()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    return None if row is None else str(row.tenant_id)


async def case_policy_version(conn: AsyncConnection, request_id: str) -> str:
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    return str(row.policy_version)


def _proposal_to_json(p: disp.Proposal) -> str:
    return json.dumps(
        {
            "case_id": p.case_id,
            "expected_revision": p.expected_revision,
            "disposition": p.disposition.value,
            "action": p.action.value,
            "maker": p.maker,
            "maker_role": p.maker_role.value,
            "rationale": p.rationale,
            "reason_codes": list(p.reason_codes),
            "evidence_refs": list(p.evidence_refs),
            "policy_version": p.policy_version,
            "legal_citation": p.legal_citation,
            "review_by": p.review_by.isoformat() if p.review_by else None,
        }
    )


def _proposal_from_json(d: dict[str, Any]) -> disp.Proposal:
    return disp.Proposal(
        case_id=d["case_id"],
        expected_revision=int(d["expected_revision"]),
        disposition=cov.Disposition(d["disposition"]),
        action=disp.Action(d["action"]),
        maker=d["maker"],
        maker_role=disp.Role(d["maker_role"]),
        rationale=d["rationale"],
        reason_codes=tuple(d["reason_codes"]),
        evidence_refs=tuple(d["evidence_refs"]),
        policy_version=d["policy_version"],
        legal_citation=d["legal_citation"],
        review_by=datetime.fromisoformat(d["review_by"]) if d["review_by"] else None,
    )


async def propose(
    conn: AsyncConnection, request_id: str, proposal: disp.Proposal, *, now: datetime
) -> dict[str, Any]:
    """One person's proposal. A decision that needs no second person (a hold, a request for evidence) is made at once; any other waits, stored, for a checker who is a different person."""
    row = await _row(conn, request_id)
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    history = await chain(conn, request_id)
    try:
        disp.check_proposal(proposal, current_revision=history[-1].revision if history else 0)
    except disp.DispositionError as e:
        raise ScreeningError(e.code, e.detail) from e
    if not disp.needs_checker(proposal):
        done = await decide(conn, request_id, proposal, checker=None, checker_role=None, now=now)
        return {"proposal_id": None, **done, "state": "DECIDED", "case_state": done["state"]}
    proposal_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.screening_request_proposal (proposal_id, request_id, tenant_id, maker, maker_role, body, state, created_at) "
            "VALUES (:p, :r, :t, :m, :mr, CAST(:b AS jsonb), 'PENDING', :at)"
        ),
        {
            "p": proposal_id,
            "r": row.request_id,
            "t": row.tenant_id,
            "m": proposal.maker,
            "mr": proposal.maker_role.value,
            "b": _proposal_to_json(proposal),
            "at": now,
        },
    )
    return {
        "proposal_id": str(proposal_id),
        "state": "AWAITING_CHECKER",
        "request_id": request_id,
        "disposition": proposal.disposition.value,
    }


async def pending(conn: AsyncConnection, request_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT proposal_id, maker, maker_role, body, created_at FROM verify.screening_request_proposal "
                "WHERE request_id = :r AND state = 'PENDING' ORDER BY created_at"
            ),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    return [
        {
            "proposal_id": str(r.proposal_id),
            "maker": r.maker,
            "maker_role": r.maker_role,
            "disposition": r.body["disposition"],
            "action": r.body["action"],
            "rationale": r.body["rationale"],
            "reason_codes": r.body["reason_codes"],
            "evidence_refs": r.body["evidence_refs"],
            "expected_revision": r.body["expected_revision"],
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def finalise(
    conn: AsyncConnection, proposal_id: str, *, checker: str, checker_role: disp.Role, now: datetime
) -> dict[str, Any]:
    """A second person finalises a stored proposal. The checker is not the maker and has the authority the decision needs; a case that has moved on since the proposal is refused (409)."""
    try:
        key = uuid.UUID(proposal_id)
    except ValueError as e:
        raise ScreeningError("PROPOSAL_NOT_FOUND", proposal_id) from e
    row = (
        await conn.execute(
            text(
                "SELECT request_id, body, state FROM verify.screening_request_proposal WHERE proposal_id = :p FOR UPDATE"
            ),
            {"p": key},
        )
    ).first()
    if row is None:
        raise ScreeningError("PROPOSAL_NOT_FOUND", proposal_id)
    if row.state != "PENDING":
        raise ScreeningError("PROPOSAL_NOT_PENDING", row.state)
    proposal = _proposal_from_json(row.body)
    done = await decide(
        conn, str(row.request_id), proposal, checker=checker, checker_role=checker_role, now=now
    )
    await conn.execute(
        text(
            "UPDATE verify.screening_request_proposal SET state = 'FINALISED', closed_at = :at WHERE proposal_id = :p"
        ),
        {"at": now, "p": key},
    )
    return {"proposal_id": proposal_id, **done, "state": "FINALISED", "case_state": done["state"]}


async def withdraw(
    conn: AsyncConnection, proposal_id: str, *, by: str, now: datetime
) -> dict[str, Any]:
    """Only the maker withdraws a proposal that has not been finalised."""
    try:
        key = uuid.UUID(proposal_id)
    except ValueError as e:
        raise ScreeningError("PROPOSAL_NOT_FOUND", proposal_id) from e
    row = (
        await conn.execute(
            text(
                "SELECT maker, state FROM verify.screening_request_proposal WHERE proposal_id = :p FOR UPDATE"
            ),
            {"p": key},
        )
    ).first()
    if row is None:
        raise ScreeningError("PROPOSAL_NOT_FOUND", proposal_id)
    if row.state != "PENDING":
        raise ScreeningError("PROPOSAL_NOT_PENDING", row.state)
    if row.maker != by:
        raise ScreeningError("ONLY_THE_MAKER_WITHDRAWS")
    await conn.execute(
        text(
            "UPDATE verify.screening_request_proposal SET state = 'WITHDRAWN', closed_at = :at WHERE proposal_id = :p"
        ),
        {"at": now, "p": key},
    )
    return {"proposal_id": proposal_id, "state": "WITHDRAWN"}
