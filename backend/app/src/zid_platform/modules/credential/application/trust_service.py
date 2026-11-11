"""Credential trust service: eligibility, engagement, trust tiers, consented disclosure, issuer outreach, appeals, monitoring
(ZID-GAP-001 v2.0 GID-041, 047, 048, 049, 053, 054, 058, 059, 060; CR-069).

Everything here reads the subject's CURRENT stored checks and recomputes: no decision is cached across places, services or time,
and no stored check is edited. A status change adds a superseding check; a decision is a new append-only row.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.credential.application import claim_checks
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.domain import portable as pt
from zid_platform.modules.credential.domain import standing as st
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.claims import (
    Authenticity,
    CredentialStatus,
    Eligibility,
    EvidenceTier,
    SourceResult,
    SubjectMatch,
)
from zid_platform.modules.credential.infrastructure import claim_store, trust_store
from zid_platform.modules.verification.ports import person_assurance_current


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def fact_from_row(
    row: Mapping[str, Any],
    *,
    registry: tg.Registry | None,
    target_country: str,
    profession: str,
    on: date,
) -> el.AssertionFact:
    a = row["assertion"]
    recognition: str | None = None
    country = (a.get("jurisdiction") or {}).get("country", "")
    if row["claim_type"] == "ACADEMIC_AWARD" and registry is not None and country:
        recognition = tg.recognition(
            registry,
            award_country=country,
            target_country=target_country,
            profession=profession,
            on=on,
        )[0].value
    elif (
        row["claim_type"] == "ACADEMIC_AWARD"
        and country
        and country.upper() != target_country.upper()
    ):
        recognition = tg.Recognition.UNRECOGNIZED.value  # no registry, no rule: never inferred
    return el.AssertionFact(
        claim_type=row["claim_type"],
        decision=a["decision"],
        credential_status=a["credential_status"],
        jurisdiction=a.get("jurisdiction") or {},
        permitted_services=tuple(a.get("permitted_services") or ()),
        restrictions=tuple(a.get("restrictions") or ()),
        source_checked_at=row["source_checked_at"],
        expires_at=_when(a.get("expires_at")),
        recognition=recognition,
    )


async def _facts(
    conn: AsyncConnection,
    subject: str,
    policy: el.EligibilityPolicy,
    registry: tg.Registry | None,
    now: datetime,
) -> tuple[list[el.AssertionFact], list[Mapping[str, Any]]]:
    rows = await trust_store.live_checks(conn, subject)
    facts = [
        fact_from_row(
            r,
            registry=registry,
            target_country=policy.country,
            profession=policy.profession_code,
            on=now.date(),
        )
        for r in rows
    ]
    return facts, rows


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    decision_id: str
    outcome: el.EligibilityOutcome
    check_ids: tuple[str, ...]


async def evaluate_eligibility(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    policy: el.EligibilityPolicy,
    registry: tg.Registry | None,
    now: datetime,
    consumer: str | None = None,
) -> EligibilityResult:
    facts, rows = await _facts(conn, subject, policy, registry, now)
    outcome = el.evaluate(policy, facts, now=now)
    check_ids = [str(r["check_id"]) for r in rows]
    decision_id = await trust_store.insert_decision(
        conn,
        tenant_id=tenant_id,
        subject_principal_id=subject,
        kind="ELIGIBILITY",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        outcome=outcome.eligibility.value,
        reasons=list(outcome.reasons),
        restrictions=list(outcome.restrictions),
        check_ids=check_ids,
        consumer=consumer,
    )
    await emit(
        conn,
        name="credential.eligibility.decided",
        tenant_id=tenant_id,
        aggregate_type="eligibility_decision",
        aggregate_id=decision_id,
        aggregate_version=1,
        data={
            "decision_id": decision_id,
            "outcome": outcome.eligibility.value,
            "policy": outcome.policy_ref,
        },
    )
    return EligibilityResult(decision_id, outcome, tuple(check_ids))


def insurance_satisfied(
    rows: Sequence[Mapping[str, Any]], *, region: str, work: str, until: date
) -> bool | None:
    """True/False from the subject's live insurance check; None if there is no verified one (not checked)."""
    for r in rows:
        if r["claim_type"] != "INSURANCE_ASSERTION" or r["assertion"]["decision"] != "VERIFIED":
            continue
        facts = r["assertion"].get("insurance_facts") or {}
        fields = {
            "insured_entity": "the-subject" if facts.get("entity_is_subject") else "someone-else",
            "region": facts.get("region") or [],
            "expires_on": facts.get("expires_on"),
            "activities": facts.get("activities") or [],
            "excluded_work": facts.get("excluded_work") or [],
        }
        ok, _ = st.insurance_result(
            fields, st.InsuranceNeed("the-subject", region, work, date.today(), until)
        )
        return ok
    return None


@dataclass(frozen=True, slots=True)
class EngagementResult:
    decision_id: str
    outcome: el.EngagementOutcome
    tier: el.Tier
    tier_gaps: tuple[str, ...]
    dimensions: Mapping[str, str]


async def evaluate_engagement(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    request: el.EngagementRequest,
    policy: el.EligibilityPolicy,
    rule: el.TierRule,
    registry: tg.Registry | None,
    now: datetime,
    consumer: str,
    engagement_days: int = 365,
) -> EngagementResult:
    facts, rows = await _facts(conn, subject, policy, registry, now)
    identity = await person_assurance_current(conn, subject, now)
    ins = insurance_satisfied(
        rows,
        region=request.country,
        work=request.service_code,
        until=(now + timedelta(days=engagement_days)).date(),
    )
    findings = [
        r["assertion"].get("disciplinary")
        for r in rows
        if r["claim_type"] == "DISCIPLINARY_FINDING"
    ]
    outcome = el.engagement_check(
        request,
        policy,
        facts,
        identity_verified=identity,
        insurance_satisfied=ins,
        restricted="ADVERSE_FINDING"
        in findings,  # only a matched, confirmed, final, un-appealed finding restricts
        now=now,
    )
    if outcome.engagement is el.Engagement.READY and {"ADVERSE_UNDER_APPEAL", "REVIEW"} & set(
        findings
    ):
        # a finding under appeal, or a name match not yet resolved, holds the engagement for a person: never a silent pass
        outcome = replace(
            outcome, engagement=el.Engagement.HOLD, reasons=("DISCIPLINARY_REVIEW_PENDING",)
        )
    elig = (
        el.evaluate(policy, facts, now=now)
        if outcome.reasons != ("POLICY_DOES_NOT_MATCH_ENGAGEMENT",)
        else None
    )
    dims = dimensions(
        rows,
        identity=identity,
        eligibility=elig.eligibility if elig else Eligibility.NOT_EVALUATED,
        now=now,
    )
    tier, gaps = el.tier_of(rule, dims)
    decision_id = await trust_store.insert_decision(
        conn,
        tenant_id=tenant_id,
        subject_principal_id=subject,
        kind="ENGAGEMENT",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        outcome=outcome.engagement.value,
        reasons=list(outcome.reasons),
        restrictions=[],
        check_ids=[str(r["check_id"]) for r in rows],
        consumer=consumer,
    )
    await emit(
        conn,
        name="credential.eligibility.decided",
        tenant_id=tenant_id,
        aggregate_type="eligibility_decision",
        aggregate_id=decision_id,
        aggregate_version=1,
        data={"decision_id": decision_id, "outcome": outcome.engagement.value, "tier": tier.value},
    )
    return EngagementResult(
        decision_id, outcome, tier, gaps, {d.value: s.value for d, s in dims.items()}
    )


def dimensions(
    rows: Sequence[Mapping[str, Any]], *, identity: bool, eligibility: Eligibility, now: datetime
) -> dict[el.Dimension, el.DimStatus]:
    """One status per Zoikorum dimension, from the live checks. A dimension with no verified check is NOT_VERIFIED."""
    dims: dict[el.Dimension, el.DimStatus] = {
        el.Dimension.IDENTITY: el.DimStatus.VERIFIED if identity else el.DimStatus.NOT_VERIFIED,
        el.Dimension.ELIGIBILITY: (
            el.DimStatus.VERIFIED
            if eligibility is Eligibility.ELIGIBLE
            else el.DimStatus.NOT_VERIFIED
        ),
        el.Dimension.CREDENTIAL: el.DimStatus.NOT_VERIFIED,
        el.Dimension.INSURANCE: el.DimStatus.NOT_VERIFIED,
        el.Dimension.RESTRICTIONS: el.DimStatus.NOT_REQUIRED,
    }
    for r in rows:
        a = r["assertion"]
        if r["claim_type"] == "DISCIPLINARY_FINDING":
            # restrictions: an adverse finding (or one under appeal) is NOT_VERIFIED; a clean answer from the source is
            # VERIFIED for that source only; no check at all stays NOT_REQUIRED
            outcome = a.get("disciplinary")
            if outcome in ("ADVERSE_FINDING", "ADVERSE_UNDER_APPEAL", "REVIEW"):
                dims[el.Dimension.RESTRICTIONS] = el.DimStatus.NOT_VERIFIED
            elif (
                outcome == "NO_ADVERSE_FINDING"
                and dims[el.Dimension.RESTRICTIONS] is el.DimStatus.NOT_REQUIRED
            ):
                dims[el.Dimension.RESTRICTIONS] = el.DimStatus.VERIFIED
            continue
        dim = lc.CLAIM_DIMENSION.get(r["claim_type"])
        if (
            dim not in (el.Dimension.CREDENTIAL, el.Dimension.INSURANCE)
            or a["decision"] != "VERIFIED"
        ):
            continue
        expires = _when(a.get("expires_at"))
        status = (
            el.DimStatus.EXPIRED
            if (expires and now >= expires) or a["credential_status"] == "EXPIRED"
            else lc.dimension_status(r["claim_type"], CredentialStatus(a["credential_status"]))
        )
        if dims[dim] is not el.DimStatus.VERIFIED:
            dims[dim] = status
    return dims


# --------------------------------------------------------------------------------------------- consented disclosure


def _grant(row: Mapping[str, Any]) -> pt.Grant:
    return pt.Grant(
        grant_id=str(row["grant_id"]),
        subject=str(row["subject_principal_id"]),
        recipient=row["recipient"],
        consumer=row["consumer"],
        purpose=row["purpose"],
        fields=frozenset(row["fields"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        withdrawn_at=row["withdrawn_at"],
        parent_id=str(row["parent_id"]) if row["parent_id"] else None,
    )


async def create_grant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    recipient: str,
    consumer: str,
    purpose: str,
    fields: Sequence[str],
    days: int,
    parent_id: str | None,
    now: datetime,
) -> pt.Grant:
    parent = None
    if parent_id:
        chain = await trust_store.load_grants(conn, parent_id)
        if parent_id not in chain:
            raise pt.DisclosureError("PARENT_GRANT_NOT_FOUND")
        parent = _grant(chain[parent_id])
        if not pt.in_force(parent, {k: _grant(v) for k, v in chain.items()}, now):
            raise pt.DisclosureError("GRANT_NOT_IN_FORCE")
    grant = pt.new_grant(
        grant_id=str(uuid.uuid4()),
        subject=subject,
        recipient=recipient,
        consumer=consumer,
        purpose=purpose,
        fields=fields,
        created_at=now,
        expires_at=now + timedelta(days=days),
        parent=parent,
    )
    await trust_store.insert_grant(conn, tenant_id, grant)
    return grant


async def withdraw_grant(
    conn: AsyncConnection, *, grant_id: str, subject: str, now: datetime
) -> None:
    chain = await trust_store.load_grants(conn, grant_id)
    row = chain.get(grant_id)
    if row is None or str(row["subject_principal_id"]) != subject:
        raise pt.DisclosureError("GRANT_NOT_FOUND")  # only the person it belongs to can withdraw it
    await trust_store.withdraw_grant(conn, grant_id, now)


FLAT = (
    "claim_type",
    "decision",
    "credential_status",
    "assurance_tier",
    "issuer_ref",
    "source_checked_at",
    "expires_at",
)


async def disclose_check(
    conn: AsyncConnection,
    *,
    grant_id: str,
    check_id: str,
    recipient: str,
    consumer: str,
    purpose: str,
    now: datetime,
) -> dict[str, Any]:
    chain = await trust_store.load_grants(conn, grant_id)
    if grant_id not in chain:
        raise pt.DisclosureError("GRANT_NOT_FOUND")
    grants = {k: _grant(v) for k, v in chain.items()}
    row = await claim_store.load_check(conn, check_id)
    if row is None or str(row["subject_principal_id"]) != grants[grant_id].subject:
        raise pt.DisclosureError("CHECK_NOT_FOUND")  # a grant never reaches another person's check
    if row["superseded_at"] is not None:
        raise pt.DisclosureError("CHECK_SUPERSEDED")  # a stale assertion is not disclosed
    flat = {k: row["assertion"].get(k) for k in FLAT}
    return pt.disclose(
        grants[grant_id],
        grants,
        flat,
        recipient=recipient,
        consumer=consumer,
        purpose=purpose,
        now=now,
    )


async def public_assertion(
    conn: AsyncConnection, *, grant_id: str, check_id: str, now: datetime
) -> dict[str, Any]:
    """The bounded verifier view, available only under a grant for the PUBLIC_VERIFIER consumer that is still in force."""
    chain = await trust_store.load_grants(conn, grant_id)
    if grant_id not in chain:
        raise pt.DisclosureError("GRANT_NOT_FOUND")
    grants = {k: _grant(v) for k, v in chain.items()}
    g = grants[grant_id]
    if g.consumer != "PUBLIC_VERIFIER" or not pt.in_force(g, grants, now):
        raise pt.DisclosureError("GRANT_NOT_IN_FORCE")
    row = await claim_store.load_check(conn, check_id)
    if row is None or str(row["subject_principal_id"]) != g.subject:
        raise pt.DisclosureError("CHECK_NOT_FOUND")
    view = pt.public_view(row["assertion"], revoked=row["superseded_at"] is not None)
    return {k: v for k, v in view.items() if k in g.fields or k in ("claim_type", "decision")}


# ----------------------------------------------------------------------------------------------- issuer outreach


def _request(row: Mapping[str, Any]) -> oc.Request:
    return oc.Request(
        request_id=str(row["request_id"]),
        issuer_id=row["issuer_id"],
        claim_type=row["claim_type"],
        nonce_hash=bytes(row["nonce_hash"]),
        requested_by=str(row["requested_by"]),
        created_at=row["created_at"],
        deadline=row["deadline"],
        state=oc.State(row["state"]),
        response_digest=bytes(row["response_digest"]) if row["response_digest"] else None,
        outcome=row["outcome"],
    )


async def open_outreach(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    issuer_id: str,
    claim_type: str,
    requested_by: str,
    contacts: Sequence[str],
    days: int,
    now: datetime,
    subject: str | None = None,
    claim: dict[str, Any] | None = None,
) -> tuple[oc.Request, str]:
    nonce = uuid.uuid4().hex
    req = oc.open_request(
        request_id=str(uuid.uuid4()),
        issuer_id=issuer_id,
        claim_type=claim_type,
        nonce=nonce,
        requested_by=requested_by,
        now=now,
        deadline=now + timedelta(days=days),
        registered_contacts=contacts,
    )
    await trust_store.insert_outreach(conn, tenant_id, req, subject, claim)
    return req, nonce  # the nonce goes to the issuer once; only its hash is kept


async def record_response(
    conn: AsyncConnection,
    *,
    request_id: str,
    response: oc.Response,
    contacts: Sequence[str],
    verify: oc.Verifier,
    now: datetime,
) -> oc.Request:
    row = await trust_store.load_outreach(conn, request_id)
    if row is None:
        raise oc.OutreachError("REQUEST_NOT_FOUND")
    req = oc.expire(_request(row), now)
    if req.state is oc.State.EXPIRED:
        await trust_store.save_outreach(
            conn,
            request_id,
            state="EXPIRED",
            response_digest=None,
            outcome=None,
            reviews=row["reviews"],
        )
        raise oc.OutreachError("REQUEST_EXPIRED")
    done = oc.receive(req, response, registered_contacts=contacts, verify=verify)
    await trust_store.save_outreach(
        conn,
        request_id,
        state=done.state.value,
        response_digest=done.response_digest,
        outcome=done.outcome,
        reviews=row["reviews"],
    )
    return done


async def review_outreach(
    conn: AsyncConnection, *, request_id: str, reviewer: str, approve: bool
) -> oc.Request:
    row = await trust_store.load_outreach(conn, request_id)
    if row is None:
        raise oc.OutreachError("REQUEST_NOT_FOUND")
    req = _request(row)
    reviews = {**dict(row["reviews"]), reviewer: approve}
    try:
        decided = oc.review(
            req, reviews
        )  # two different reviewers, neither the requester; any rejection rejects
    except oc.OutreachError as e:
        if e.code != "SECOND_REVIEWER_REQUIRED":
            raise
        decided = req  # one approval so far: it waits for a second person
    await trust_store.save_outreach(
        conn,
        request_id,
        state=decided.state.value,
        response_digest=req.response_digest,
        outcome=req.outcome,
        reviews=reviews,
    )
    return decided


class _OutreachSource:
    """The issuer's reviewed answer, presented to the claim pipeline as a source. Not a connector: it only replays one answer."""

    def __init__(self, request_id: str, answer: claim_checks.SourceAnswer) -> None:
        self.source_id = f"outreach:{request_id[:8]}"
        self._answer = answer

    async def lookup(self, *_: Any) -> claim_checks.SourceAnswer:
        return self._answer


async def conclude_outreach(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
    packs: Any,
    registry: tg.Registry | None,
    environment: str,
    now: datetime,
) -> str | None:
    """Turn a finished outreach into a claim check, once. An issuer's confirmation (after dual review) is a primary-source
    answer; a denial or "not held" is a reachable source with no record; silence or a rejected answer is UNVERIFIABLE, never a
    finding about the person. The claimed values are dropped as soon as the check exists. Returns the check id, or None when
    there is nothing to conclude (no claim attached, still waiting, or already concluded)."""
    row = await trust_store.load_outreach(conn, request_id)
    if row is None or row["claim"] is None or row["concluded_at"] is not None:
        return None
    req = oc.expire(_request(row), now)
    if req.state is not _request(row).state:
        await trust_store.save_outreach(
            conn,
            request_id,
            state=req.state.value,
            response_digest=None,
            outcome=None,
            reviews=row["reviews"],
        )
    if req.state in (oc.State.PENDING, oc.State.RESPONDED):
        return None
    verdict, _ = oc.verdict(req)
    claim = dict(row["claim"])
    fields = dict(claim.get("fields") or {})
    if verdict == "CONFIRMED":
        answer = claim_checks.SourceAnswer(
            source_id="outreach",
            result=SourceResult.RESPONDED,
            checked_at=now,
            fields=fields,
            evidence=EvidenceTier.PRIMARY_SOURCE_CONFIRMED,
            authenticity=Authenticity.CONFIRMED,
            subject=SubjectMatch.MATCHED,
            reference=f"outreach:{request_id}",
        )
    else:
        answer = claim_checks.SourceAnswer(
            source_id="outreach",
            result=SourceResult.NO_RECORD,
            checked_at=now,
            fields={},
            obtainable=verdict
            == "NOT_CONFIRMED",  # silence or a rejected answer: nobody can confirm it
            reference=f"outreach:{request_id}",
        )
    result = await claim_checks.run_check(
        conn,
        tenant_id=tenant_id,
        subject_principal_id=str(row["subject_principal_id"]),
        kind=row["claim_type"],
        claimed=fields,
        jurisdiction=claim.get("jurisdiction") or {},
        profession=claim.get("profession"),
        packs=packs,
        source=_OutreachSource(request_id, answer),
        now=now,
        environment=environment,
        gate=claim_checks.Gate(registry=registry),
    )
    await trust_store.conclude_outreach(conn, request_id, result.check_id, now)
    return result.check_id


# ------------------------------------------------------------------------------------------------------- appeals


async def raise_appeal(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    check_id: str,
    raised_by: str,
    reason: str,
    correction: dict[str, Any],
) -> str:
    row = await claim_store.load_check(conn, check_id)
    if row is None:
        raise LookupError("CHECK_NOT_FOUND")
    appeal_id = await trust_store.insert_appeal(
        conn,
        tenant_id=tenant_id,
        check_id=check_id,
        raised_by=raised_by,
        reason=reason,
        correction=correction,
    )
    await emit(
        conn,
        name="credential.claim.appealed",
        tenant_id=tenant_id,
        aggregate_type="claim_appeal",
        aggregate_id=appeal_id,
        aggregate_version=1,
        data={"appeal_id": appeal_id, "check_id": check_id},
    )
    return appeal_id


# ----------------------------------------------------------------------------------------------------- monitoring


async def apply_source_event(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    check_id: str,
    event: lc.SourceEvent,
    now: datetime,
) -> tuple[bool, str]:
    """Apply a source's status event to a check. Returns (changed, effective_status). The newest valid observation wins; an older
    one is only recorded. A real change adds a superseding check (the old one is never edited) and emits the event."""
    mon = await trust_store.load_monitor(conn, check_id)
    row = await claim_store.load_check(conn, check_id)
    if mon is None or row is None:
        raise LookupError("CHECK_NOT_FOUND")
    if row["superseded_at"] is not None:
        raise LookupError("CHECK_SUPERSEDED")
    state = lc.LifecycleState(
        claim_ref=check_id,
        status=CredentialStatus(mon["status"]),
        sequence=mon["last_source_seq"],
        observed_at=mon["last_observed_at"],
        seen=frozenset(mon["seen_events"] or ()),
    )
    applied = lc.apply_event(state, event)
    await trust_store.save_monitor(
        conn,
        check_id=check_id,
        status=applied.state.status.value,
        seq=applied.state.sequence,
        observed_at=applied.state.observed_at,
        seen=sorted(applied.state.seen),
        next_check_at=mon["next_check_at"],
    )
    if not applied.changed:
        return False, applied.state.status.value
    old = dict(row["assertion"])
    new_status = applied.state.status
    new = {**old, "credential_status": new_status.value}
    if new_status is not CredentialStatus.ACTIVE:
        new["decision"] = "NOT_VERIFIED"
        new["reason_codes"] = [f"CREDENTIAL_{new_status.value}"]
    new["professional_eligibility"] = "NOT_EVALUATED"
    new_id = str(uuid.uuid4())
    await claim_store.insert_check(
        conn,
        check_id=new_id,
        tenant_id=tenant_id,
        subject_principal_id=str(row["subject_principal_id"]),
        claim_type=row["claim_type"],
        decision=new["decision"],
        assurance_tier=old["assurance_tier"],
        pack_id=row["pack_id"],
        pack_version=row["pack_version"],
        jurisdiction=old.get("jurisdiction") or {},
        claimed_digest=hashlib.sha256(
            f"status-change:{check_id}:{event.event_id}".encode()
        ).digest(),
        source_id=row["source_id"],
        source_reference=f"event:{event.event_id}",
        source_result="RESPONDED",
        source_checked_at=event.observed_at,
        assertion=new,
    )
    await trust_store.insert_monitor(
        conn,
        check_id=new_id,
        tenant_id=tenant_id,
        claim_type=row["claim_type"],
        status=new_status.value,
        expires_at=_when(old.get("expires_at")),
        next_check_at=now + timedelta(days=1),
    )
    await emit(
        conn,
        name="credential.claim.status.changed",
        tenant_id=tenant_id,
        aggregate_type="claim_check",
        aggregate_id=new_id,
        aggregate_version=2,
        data={
            "check_id": new_id,
            "previous_check_id": check_id,
            "claim_type": row["claim_type"],
            "status": new_status.value,
        },
    )
    return True, new_status.value


async def recheck_due(
    conn: AsyncConnection, now: datetime, push: timedelta = timedelta(days=1)
) -> int:
    """Tell consumers which CURRENT checks need a fresh look at their source, once per day each. Runs across tenants (the
    worker role); each announcement carries its own tenant. Returns how many were announced."""
    due = await trust_store.due_monitors(conn, now)
    for m in due:
        cid = str(m["check_id"])
        reason = "EXPIRED" if m["expires_at"] and m["expires_at"] <= now else "DUE"
        await emit(
            conn,
            name="credential.claim.recheck.due",
            tenant_id=str(m["tenant_id"]),
            aggregate_type="claim_check",
            aggregate_id=cid,
            aggregate_version=await trust_store.next_event_version(conn, "claim_check", cid),
            data={"check_id": cid, "claim_type": m["claim_type"], "reason": reason},
        )
        await conn.execute(
            text("UPDATE cred.claim_monitor SET next_check_at = :n WHERE check_id = :c"),
            {"n": now + push, "c": m["check_id"]},
        )
    return len(due)
