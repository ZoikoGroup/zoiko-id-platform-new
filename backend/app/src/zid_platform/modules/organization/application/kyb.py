"""Company verification service (ZID-ED-03). One provider-neutral flow; Zoiko iD owns the outcome.

`request_verification` validates the identifier, asks the country's first-pass sources, calls an exception supplier only
where the register route is absent or inadequate (never because a source was down), optionally confirms an LEI,
reconciles deterministically, signs the decision as evidence and records it append-only. `refresh` re-reads the sources
and appends a new decision; `review` records a reviewer's ruling with its rationale. Nothing here edits a past decision.

Sources are passed in (`adapters`), so tests and a sovereign cell's egress proxy supply their own transport. A source that
fails for any reason is UNAVAILABLE for that run: never "not found", never success (ED-03 §08).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.organization.domain import dual_review as dual
from zid_platform.modules.organization.domain.kyb import (
    EXCEPTION_TRIGGERS,
    Applicant,
    Candidate,
    Decision,
    KybError,
    KybStatus,
    RegisterRead,
    Review,
    Routing,
    SourceKind,
    SourceOutcome,
    SourceState,
    check_applicant,
    check_review,
    effective_status,
    evidence_object,
    reconcile,
)
from zid_platform.modules.organization.infrastructure import kyb_store as store
from zid_platform.modules.organization.infrastructure.kyb_adapters import KybAdapter

SOURCE_TIMEOUT_S = 10.0
log = logging.getLogger(__name__)
Signer = Callable[[dict[str, Any]], str]
CountryGate = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CaseView:
    case_id: str
    status: KybStatus  # effective: a VERIFIED outcome past its expiry reads as EXPIRED
    review_reason: str | None
    reasons: tuple[str, ...]
    version: int
    evidence_hash: str | None
    expiry_at: datetime | None
    jurisdiction: str
    registration_number: str
    legal_name: str  # the name the applicant gave, never a register's
    requested_by: str  # the principal who made the claim: whose identity check counts for it (internal; never shown to anyone)
    submitted_at: datetime | None = None
    closed_at: datetime | None = None
    closed_reason: str | None = None


def _covers(adapter: KybAdapter, country: str) -> bool:
    countries = adapter.capabilities.countries
    return country in countries or "*" in countries


async def _ask(adapter: KybAdapter, number: str, country: str, now: datetime) -> SourceOutcome:
    """One source, one answer. Anything but a clean answer is UNAVAILABLE; it is never read as 'not found'."""
    try:
        facts = await asyncio.wait_for(adapter.get(number, country, now), SOURCE_TIMEOUT_S)
    except KybError as e:
        return SourceOutcome(
            adapter.adapter_id, adapter.kind, SourceState.UNAVAILABLE, None, e.code
        )
    except Exception as e:  # noqa: BLE001 - fail closed: a crashed or timed-out source is an outage
        return SourceOutcome(
            adapter.adapter_id, adapter.kind, SourceState.UNAVAILABLE, None, type(e).__name__
        )
    if facts is None:
        return SourceOutcome(adapter.adapter_id, adapter.kind, SourceState.NOT_FOUND)
    return SourceOutcome(adapter.adapter_id, adapter.kind, SourceState.FOUND, facts)


async def decide(
    applicant: Applicant,
    *,
    adapters: Mapping[str, KybAdapter],
    routing: Routing,
    now: datetime,
) -> Decision:
    """Ask the sources the country's route names, in order, and reconcile. No source is substituted for another."""
    route = routing.route(applicant.jurisdiction)
    if route is None:
        return reconcile(
            applicant, [], accepted_suppliers=frozenset(), policy=routing.policy, now=now
        )
    accepted = routing.accepted_suppliers(applicant.jurisdiction)
    outcomes: list[SourceOutcome] = []

    async def run(ids: Sequence[str]) -> None:
        for adapter_id in ids:
            adapter = adapters.get(adapter_id)
            if not routing.enabled(adapter_id):
                continue  # switched off by the routing (a contract, a licence or a qualification is outstanding): not called, so nothing is billed (T35)
            if adapter is None or not _covers(adapter, applicant.jurisdiction):
                continue  # not available here: it simply does not answer, and is never replaced by another
            outcomes.append(
                await _ask(adapter, applicant.registration_number, applicant.jurisdiction, now)
            )

    await run(route.first_pass)
    decision = reconcile(
        applicant, outcomes, accepted_suppliers=accepted, policy=routing.policy, now=now
    )
    if route.exception and decision.review_reason in EXCEPTION_TRIGGERS:
        await run(route.exception)
        decision = reconcile(
            applicant, outcomes, accepted_suppliers=accepted, policy=routing.policy, now=now
        )
    if (
        applicant.lei
        and route.lei
        and routing.enabled(route.lei)
        and (adapter := adapters.get(route.lei)) is not None
    ):
        outcomes.append(await _ask(adapter, applicant.lei, applicant.jurisdiction, now))
        decision = reconcile(
            applicant, outcomes, accepted_suppliers=accepted, policy=routing.policy, now=now
        )
    return decision


async def search(
    query: str,
    jurisdiction: str,
    *,
    adapters: Mapping[str, KybAdapter],
    routing: Routing,
    now: datetime,
) -> list[Candidate]:
    """Ranked candidates for the applicant to choose from; none is ever chosen for them. Suppliers whose coverage is
    not proven are not asked, so an unproven source cannot shape what the applicant sees."""
    route = routing.route(jurisdiction)
    if route is None or len(query.strip()) < 2:
        return []
    out: list[Candidate] = []
    # The country's registers first, then its LEI reference (GLEIF): it lets an applicant find a company in a country whose register is not connected
    # yet. It is reference data, so a company found only there is still decided by `decide` (no registry source: a reviewer, never "verified").
    asked = [*route.first_pass, *([route.lei] if route.lei and route.lei not in route.first_pass else [])]
    for adapter_id in asked:
        adapter = adapters.get(adapter_id)
        if adapter is None or not routing.enabled(adapter_id) or not _covers(adapter, jurisdiction):
            continue
        if adapter.kind is SourceKind.SUPPLIER and adapter_id not in routing.accepted_suppliers(
            jurisdiction
        ):
            continue
        try:
            out.extend(
                await asyncio.wait_for(adapter.search(query, jurisdiction, now), SOURCE_TIMEOUT_S)
            )
        except Exception:  # noqa: BLE001 - an outage gives fewer candidates, never a made-up one
            log.warning("kyb search unavailable: %s", adapter_id)
            continue
    # one company found by two sources is listed once, from the register (the LEI reference only adds its LEI)
    seen: dict[tuple[str, str], Candidate] = {}
    for c in sorted(out, key=lambda c: (c.adapter_id == "gleif", -c.confidence)):
        key = (c.jurisdiction, "".join(ch for ch in c.registration_number if ch.isalnum()).upper())
        if key not in seen:
            seen[key] = c
    return sorted(seen.values(), key=lambda c: (-c.confidence, c.registration_number))


def _view(
    case: store.CaseRow, evidence_hash: str | None, reasons: tuple[str, ...], now: datetime
) -> CaseView:
    return CaseView(
        case_id=case.case_id,
        status=effective_status(case.status, case.expiry_at, now),
        review_reason=case.review_reason,
        reasons=reasons,
        version=case.current_version,
        evidence_hash=evidence_hash,
        expiry_at=case.expiry_at,
        jurisdiction=case.jurisdiction,
        registration_number=case.registration_number,
        legal_name=case.applicant_name,
        requested_by=case.requested_by,
        submitted_at=case.submitted_at,
        closed_at=case.closed_at,
        closed_reason=case.closed_reason,
    )


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    case: store.CaseRow,
    applicant: Applicant,
    decision: Decision,
    routing: Routing,
    sign: Signer,
    *,
    version: int,
    actor: str | None,
    decided_by: str | None,
    rationale: str | None,
    now: datetime,
    carry: Mapping[str, Any] | None = None,
) -> CaseView:
    from zid_platform.modules.commercial.ports import record_vendor_check
    from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

    body = evidence_object(
        tenant_id=tenant_id,
        applicant=applicant,
        decision=decision,
        policy=routing.policy,
        version=version,
        now=now,
        carry=carry,
        reviewed=decided_by is not None,
    )
    jws = sign(body)
    await store.append_evidence(
        conn,
        tenant_id,
        case_id=case.case_id,
        version=version,
        status=decision.status,
        review_reason=decision.review_reason,
        reasons=decision.reasons,
        body=body,
        evidence_hash=body["evidence_hash"],
        jws=jws,
        policy_version=routing.policy.version,
        decided_by=decided_by,
        rationale=rationale,
        observed_at=decision.primary.observed_at if decision.primary else now,
        expiry_at=decision.expiry_at,
        now=now,
    )
    # What each paid supplier lookup cost Zoiko, charged or not, so Finance can measure the cost per completed check
    # (ED-03 section 08). An outage is the supplier's, never chargeable; a recorded cost never changes the decision.
    for o in decision.sources:
        if o.kind is SourceKind.SUPPLIER:
            await record_vendor_check(
                conn,
                tenant_id=tenant_id,
                adapter_id=o.adapter_id,
                supplier_ref=f"{case.case_id}:{o.adapter_id}:v{version}",
                check_type="KYB_LOOKUP",
                country=applicant.jurisdiction,
                decision_status="COMPLETED",
                reasons=["PROVIDER_UNAVAILABLE"] if o.state is SourceState.UNAVAILABLE else [],
                subject_ref=case.case_id,
                at=now,
            )
    event_id = await emit(
        conn,
        name="organization.kyb.decided",
        tenant_id=tenant_id,
        aggregate_type="kyb_case",
        aggregate_id=case.case_id,
        aggregate_version=version,
        data={
            "case_id": case.case_id,
            "decision_status": decision.status.value,
            "reasons": list(decision.reasons),
            "evidence_hash": body["evidence_hash"],
            "policy_version": routing.policy.version,
            "jurisdiction": applicant.jurisdiction,
        },
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.kyb.decided",
            actor_type="HUMAN" if actor else "SERVICE",
            action="decided",
            target_type="kyb_case",
            target_ref=case.case_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=decision.review_reason,
            source_service="organization",
            source_event_id=event_id,
            details={
                "decision_status": decision.status.value,
                "reasons": list(decision.reasons),
                "evidence_hash": body["evidence_hash"],
                "policy_version": routing.policy.version,
                "jurisdiction": applicant.jurisdiction,
                "version": version,
            },
        ),
    )
    if case.status is KybStatus.VERIFIED and decision.status is not KybStatus.VERIFIED:
        await _verification_lost(conn, tenant_id, case, decision, version, actor, now)
    updated = await store.load_case(conn, tenant_id, case.case_id)
    return _view(updated, body["evidence_hash"], decision.reasons, now)


async def _verification_lost(
    conn: AsyncConnection,
    tenant_id: str,
    case: store.CaseRow,
    decision: Decision,
    version: int,
    actor: str | None,
    now: datetime,
) -> None:
    """A company that was verified no longer is (a dissolved register entry, a changed name or form, a withdrawn ruling, a lapse).
    What depends on the company reads the case's status live, so it drops at once; this records why, once, with the evidence
    still on the ledger (WF-13B AT-11)."""
    from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

    data = {
        "case_id": case.case_id,
        "previous_status": KybStatus.VERIFIED.value,
        "decision_status": decision.status.value,
        "reasons": list(decision.reasons),
        "jurisdiction": case.jurisdiction,
        "version": version,
    }
    event_id = await emit(
        conn,
        name="organization.kyb.verification_lost",
        tenant_id=tenant_id,
        aggregate_type="kyb_case",
        aggregate_id=case.case_id,
        aggregate_version=1000 + version,
        data=data,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.kyb.verification_lost",
            actor_type="HUMAN" if actor else "SERVICE",
            action="lost",
            target_type="kyb_case",
            target_ref=case.case_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=decision.review_reason,
            source_service="organization",
            source_event_id=event_id,
            details=data,
        ),
    )


def _applicant(case: store.CaseRow) -> Applicant:
    return Applicant(
        jurisdiction=case.jurisdiction,
        registration_number=case.registration_number,
        legal_name=case.applicant_name,
        entity_form=case.entity_form,
        lei=case.lei,
        register_id=case.register_id,
    )


async def register_people(
    adapters: Mapping[str, KybAdapter], registration_number: str, country: str, now: datetime
) -> RegisterRead:
    """The directors and persons with significant control the official register lists, or why they could not be read. A country
    with no source is NOT_APPLICABLE (the customer's declaration is then all there is, and a pack may still ask for review); a source
    that fails is UNAVAILABLE and goes to a person, never read as "there is no one"."""
    for adapter in adapters.values():
        reader = getattr(adapter, "register_people", None)
        if reader is None:
            continue
        try:
            read = await asyncio.wait_for(
                reader(registration_number, country, now), SOURCE_TIMEOUT_S * 2
            )
        except (KybError, TimeoutError, Exception):  # noqa: BLE001 - any failure of the source is an outage, not an answer
            return RegisterRead("UNAVAILABLE", source=getattr(adapter, "adapter_id", ""))
        if isinstance(read, RegisterRead) and read.status != "NOT_APPLICABLE":
            return read
    return RegisterRead("NOT_APPLICABLE")


async def request_verification(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    applicant: Applicant,
    requested_by: str,
    idempotency_key: str,
    adapters: Mapping[str, KybAdapter],
    routing: Routing,
    sign: Signer,
    now: datetime,
    country_gate: CountryGate | None = None,
) -> CaseView:
    """Open a case for one selected legal entity and decide it. A repeat with the same key returns the same case."""
    if country_gate is not None:
        country_gate(
            applicant.jurisdiction
        )  # ED-03 §03 / EDR-01: the country must be open for business verification
    applicant = check_applicant(applicant)
    if not applicant.legal_name:
        raise KybError("NAME_REQUIRED", "the applicant must state the company's name")
    existing = await store.case_by_key(conn, tenant_id, idempotency_key)
    if existing is not None:
        same = (
            existing.jurisdiction,
            existing.registration_number,
            existing.register_id,
            existing.applicant_name,
        ) == (
            applicant.jurisdiction,
            applicant.registration_number,
            applicant.register_id,
            applicant.legal_name,
        )
        if not same:
            raise KybError("IDEMPOTENCY_CONFLICT", "that key was used for a different company")
        return await get_case(conn, tenant_id, existing.case_id, now=now)
    case_id = str(uuid.uuid4())
    created = await store.create_case(
        conn,
        tenant_id,
        case_id=case_id,
        jurisdiction=applicant.jurisdiction,
        registration_number=applicant.registration_number,
        applicant_name=applicant.legal_name,
        entity_form=applicant.entity_form,
        lei=applicant.lei,
        register_id=applicant.register_id,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        now=now,
    )
    if (
        not created
    ):  # a concurrent request with the same key won: this one is the same case, not a second
        winner = await store.case_by_key(conn, tenant_id, idempotency_key)
        if winner is None:
            raise KybError("CASE_NOT_FOUND")
        return await get_case(conn, tenant_id, winner.case_id, now=now)
    case = await store.load_case(conn, tenant_id, case_id)
    decision = await decide(applicant, adapters=adapters, routing=routing, now=now)
    return await _record(
        conn, tenant_id, case, applicant, decision, routing, sign,
        version=1, actor=requested_by, decided_by=None, rationale=None, now=now,
    )  # fmt: skip


async def refresh(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    adapters: Mapping[str, KybAdapter],
    routing: Routing,
    sign: Signer,
    now: datetime,
    actor: str | None = None,
    country_gate: CountryGate | None = None,
    keep_valid_on_outage: bool = False,
) -> CaseView:
    """Re-read the sources and append a new decision. A source outage appends UNAVAILABLE; the earlier evidence stays.
    A scheduled refresh sets `keep_valid_on_outage`: a verification that still stands is not replaced by an outage, which says
    nothing about the company; the next pass asks again, and the verification lapses on its own date if the sources stay down."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    if case.closed_at is not None:  # a withdrawn or abandoned claim is never read again
        raise KybError("CLAIM_CLOSED", case.closed_reason or "")
    if country_gate is not None:
        country_gate(case.jurisdiction)
    applicant = _applicant(case)
    decision = await decide(applicant, adapters=adapters, routing=routing, now=now)
    if (
        keep_valid_on_outage
        and decision.status is KybStatus.UNAVAILABLE
        and effective_status(case.status, case.expiry_at, now) is KybStatus.VERIFIED
    ):
        return await get_case(conn, tenant_id, case_id, now=now)
    return await _record(
        conn, tenant_id, case, applicant, decision, routing, sign,
        version=case.current_version + 1, actor=actor, decided_by=None, rationale=None, now=now,
    )  # fmt: skip


async def review(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    ruling: Review,
    routing: Routing,
    sign: Signer,
    now: datetime,
    escalation_authorized: bool = False,
) -> CaseView:
    """A reviewer's ruling on a case in review: a new signed evidence row with the rationale; history is kept. A case no authoritative source backs needs two independent
    reviewers to be approved (domain/dual_review.py): the first approval is recorded and the case stays in review."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    if case.closed_at is not None:
        raise KybError("CLAIM_CLOSED", case.closed_reason or "")
    check_review(case.status, ruling, requester_id=case.requested_by)
    history = await store.evidence_history(conn, tenant_id, case_id)
    last = history[-1]
    applicant = _applicant(case)
    prior = last.body
    step = dual.next_step(
        [dual.Row(h.decided_by, h.decision_status, h.reasons) for h in history],
        reviewer=ruling.reviewer_id,
        outcome=ruling.outcome,
        dual_required=dual.requires_dual(prior),
        escalation_authorized=escalation_authorized,
    )
    carried = [
        r for r in last.reasons if r not in dual.POSITION_MARKERS
    ]  # where the review stands is restated by each step, never inherited
    # the ruling carries the facts the reviewer saw; the sources are those of the decision it answers
    decision = Decision(
        status=step.outcome,
        reasons=tuple(dict.fromkeys([*carried, *ruling.reasons, "REVIEWER_RULING", *step.reasons])),
        review_reason=last.review_reason if step.outcome is KybStatus.NEEDS_REVIEW else None,
        match_confidence=prior.get("match_confidence"),
        sources=(),
        primary=None,
        expiry_at=now + routing.policy.validity if step.outcome is KybStatus.VERIFIED else None,
    )
    return await _record(
        conn, tenant_id, case, applicant, decision, routing, sign,
        version=case.current_version + 1, actor=ruling.reviewer_id, decided_by=ruling.reviewer_id,
        rationale=ruling.rationale.strip(), now=now, carry=prior,
    )  # fmt: skip


async def awaiting_compliance(conn: AsyncConnection, tenant_id: str, case_id: str) -> bool:
    """Whether the case was escalated after two reviewers disagreed, so that a ruling on it needs the escalation permission."""
    rows = await store.evidence_history(conn, tenant_id, case_id)
    return dual.is_escalated([dual.Row(h.decided_by, h.decision_status, h.reasons) for h in rows])


async def get_case(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, now: datetime
) -> CaseView:
    case = await store.load_case(conn, tenant_id, case_id)
    history = await store.evidence_history(conn, tenant_id, case_id)
    last = history[-1] if history else None
    return _view(case, last.evidence_hash if last else None, last.reasons if last else (), now)


async def history(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[store.EvidenceRow]:
    return await store.evidence_history(conn, tenant_id, case_id)


async def submit_claim(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, now: datetime
) -> tuple[CaseView, bool]:
    """The applicant says the claim is complete and ready for review. Returns (the claim, whether this call submitted it): a repeat is
    the same answer and changes nothing. A closed claim cannot be submitted."""
    case = await store.load_case(conn, tenant_id, case_id, lock=True)
    if case.closed_at is not None:
        raise KybError("CLAIM_CLOSED", case.closed_reason or "")
    done = await store.mark_submitted(conn, tenant_id, case_id, now)
    return await get_case(conn, tenant_id, case_id, now=now), done


async def close_claim(
    conn: AsyncConnection, tenant_id: str, case_id: str, reason: str, *, now: datetime
) -> tuple[CaseView, bool]:
    """WITHDRAWN (the applicant) or ABANDONED (a draft nobody submitted in time). Returns (the claim, whether this call closed it). The
    history stays; nothing is deleted. Whether a claim may be withdrawn at all (a verified one may not) is the caller's rule."""
    if reason not in ("WITHDRAWN", "ABANDONED"):
        raise KybError("CLOSE_REASON_INVALID", reason)
    await store.load_case(conn, tenant_id, case_id, lock=True)
    done = await store.close_case(conn, tenant_id, case_id, reason, now)
    return await get_case(conn, tenant_id, case_id, now=now), done


async def open_claims(conn: AsyncConnection) -> list[tuple[str, str]]:
    return await store.open_cases(conn)


async def drafts_before(conn: AsyncConnection, cutoff: datetime) -> list[tuple[str, str]]:
    return await store.draft_cases_before(conn, cutoff)
