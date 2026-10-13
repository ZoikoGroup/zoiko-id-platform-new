"""Identity verification orchestration (ZID-VERIFY-001 §4-5, §9-10, §16-18, §22; FR-VER-001..008).

    start()            session from an ACTIVE profile -> server-side adapter choice -> submit
                       (failover only inside the adapter's equivalence group; circuit breaker)
    provider_callback  signed (pinned keys) + mapping-version pinned + deduplicated -> normalized
                       facts -> deterministic decision -> assertions / review case / failure
    review()           reason-coded, one decision per reviewer, dual control per profile, never
                       the subject themselves; no other way to reach VERIFIED
    rely()             explicit reuse decision, recorded and evented
    assurance()        assurance facts for policy, never evidence (FR-VER-008)

Provider payloads end at the adapter boundary: only canonical facts reach the rest of Zoiko.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_crypto import CryptoError, verify_external_jws
from zid_db import tenant_transaction
from zid_outbox import emit

from zid_platform.modules.signals.ports import CanonicalSignal, ingest
from zid_platform.modules.verification.domain.verification import (
    IAL_RANK,
    SESSION_TTL,
    ClaimResult,
    Decision,
    Profile,
    ProviderResult,
    SessionStatus,
    VerificationError,
    decide,
    failover_candidates,
    reliance,
    select_profile,
    transition,
)
from zid_platform.modules.verification.infrastructure import verification_store as store

CALLBACK_TYP = "verification-result+jwt"


async def _event(
    conn: AsyncConnection, name: str, s: store.VSession, version: int, **data: Any
) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=s.tenant_id,
        aggregate_type="verification_session",
        aggregate_id=s.session_id,
        aggregate_version=version,
        data={"session_id": s.session_id, "subject_principal_id": s.subject_principal_id, **data},
    )


@dataclass(frozen=True, slots=True)
class Started:
    session_id: str
    status: str
    adapter_id: str | None
    outcome: str | None


async def start(
    engine: AsyncEngine,
    *,
    ctx: RequestContext,
    profile_id: str,
    subject_principal_id: str,
    purpose: str,
    jurisdiction: str,
    floor: str | None,
    transport: httpx.AsyncBaseTransport | None,
    now: datetime,
    supersedes: str | None = None,
) -> Started:
    async with tenant_transaction(engine, ctx) as conn:
        profile = await store.load_profile(conn, profile_id)
        select_profile(profile, floor=floor, jurisdiction=jurisdiction)
        s = store.VSession(
            session_id=str(uuid.uuid4()),
            tenant_id=ctx.tenant_id or "",
            profile_id=profile.profile_id,
            subject_principal_id=subject_principal_id,
            purpose=purpose,
            jurisdiction=jurisdiction,
            status=SessionStatus.CREATED,
            adapter_id=None,
            provider_transaction_ref=None,
            attempted_adapters=(),
            outcome_code=None,
            pending_result=None,
            row_version=1,
            created_at=now,
            expires_at=now + SESSION_TTL,
        )
        await store.create_session(conn, s, supersedes)
        await _event(conn, "verification.created", s, 1, profile_id=profile.profile_id)
        s = await store.update_session(
            conn, s, status=transition(s.status, SessionStatus.IN_PROGRESS), now=now
        )
        await _event(conn, "verification.started", s, 2)
        adapters = [
            a
            for a in await store.list_adapters(conn)
            if jurisdiction in a["jurisdictions"]
            and set(a["supported_evidence"]) & profile.accepted_evidence
        ]
    groups = sorted({a["equivalence_group"] for a in adapters})
    attempted: list[str] = []
    for group in groups[:1]:  # the profile's method is the first eligible group; never swap groups
        for adapter_id in failover_candidates(adapters, group=group, attempted=attempted, now=now):
            adapter = next(a for a in adapters if a["adapter_id"] == adapter_id)
            attempted.append(adapter_id)
            ref = await _submit(adapter, s, profile, transport)
            async with tenant_transaction(engine, ctx) as conn:
                if ref is None:
                    await store.adapter_failed(conn, adapter_id, now)
                    continue
                await store.adapter_succeeded(conn, adapter_id)
                current = await store.load_session(conn, s.session_id, lock=True)
                done = await store.update_session(
                    conn,
                    current,
                    status=transition(current.status, SessionStatus.PENDING_EXTERNAL),
                    now=now,
                    adapter_id=adapter_id,
                    ref=ref,
                    attempted=tuple(attempted),
                )
                await _event(
                    conn, "verification.provider_submitted", done, 3, adapter_id=adapter_id
                )
            return Started(s.session_id, done.status.value, adapter_id, None)
    async with tenant_transaction(
        engine, ctx
    ) as conn:  # no equivalent provider: fail, never downgrade
        current = await store.load_session(conn, s.session_id, lock=True)
        failed = await store.update_session(
            conn,
            current,
            status=transition(current.status, SessionStatus.FAILED),
            now=now,
            attempted=tuple(attempted),
            outcome="PROVIDER_UNAVAILABLE",
        )
        await _event(conn, "verification.completed", failed, 3, outcome="PROVIDER_UNAVAILABLE")
    return Started(s.session_id, "FAILED", None, "PROVIDER_UNAVAILABLE")


async def _submit(
    adapter: dict[str, Any],
    s: store.VSession,
    profile: Profile,
    transport: httpx.AsyncBaseTransport | None,
) -> str | None:
    """Only opaque references leave Zoiko; the provider collects evidence from the applicant."""
    try:
        async with httpx.AsyncClient(transport=transport, timeout=5.0, follow_redirects=False) as c:
            r = await c.post(
                adapter["submit_endpoint"],
                json={
                    "session_ref": s.session_id,
                    "profile": f"{profile.name}@{profile.version}",
                    "required_claims": list(profile.required_claims),
                    "jurisdiction": s.jurisdiction,
                    "mapping_version": adapter["mapping_version"],
                },
            )
    except httpx.HTTPError:
        return None
    if r.status_code not in (200, 201, 202):
        return None
    ref = r.json().get("transaction_ref") if r.content else None
    return ref if isinstance(ref, str) and 1 <= len(ref) <= 256 else None


def _result(payload: dict[str, Any]) -> ProviderResult:
    try:
        claims = tuple(
            ClaimResult(
                claim=str(c["claim"]),
                verified=bool(c["verified"]),
                value=str(c.get("value", "")),
                method=str(c["method"]),
                evidence=tuple(str(e) for e in c.get("evidence", ())),
            )
            for c in payload.get("claims", ())
        )
        return ProviderResult(
            status=str(payload["status"]),
            claims=claims,
            binding=payload.get("binding"),
            liveness_passed=payload.get("liveness_passed"),
            fraud_signals=tuple(str(f) for f in payload.get("fraud_signals", ())),
            reason_codes=tuple(str(r) for r in payload.get("reason_codes", ())),
            evidence_commitment=payload.get("evidence_commitment"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise VerificationError("CALLBACK_SCHEMA_INVALID") from e


async def provider_callback(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    adapter_id: str,
    body: bytes,
    base_ctx: RequestContext,
    now: datetime,
) -> str:
    ctx = base_ctx.with_identity(tenant_id, None)
    async with tenant_transaction(engine, ctx) as conn:
        adapter = await store.load_adapter(conn, adapter_id)
        if adapter is None or adapter["status"] in ("QUARANTINED", "RETIRED"):
            raise VerificationError("PROVIDER_UNTRUSTED")
        try:
            _, claims = verify_external_jws(
                body.decode("ascii").strip(),
                keys=adapter["callback_jwks"]["keys"],
                algorithms=adapter["callback_algorithms"],
                typ=CALLBACK_TYP,
            )
        except (CryptoError, UnicodeDecodeError) as e:
            raise VerificationError("CALLBACK_SIGNATURE_INVALID") from e
        if (
            claims.get("iss") != adapter_id
            or claims.get("mapping_version") != adapter["mapping_version"]
        ):
            raise VerificationError("CALLBACK_SCHEMA_INVALID", "issuer or mapping version")
        event_id, ref = claims.get("event_id"), claims.get("transaction_ref")
        if not isinstance(event_id, str) or not isinstance(ref, str):
            raise VerificationError("CALLBACK_SCHEMA_INVALID")
        s = await store.session_by_ref(conn, adapter_id, ref)
        seen = await store.record_callback(
            conn,
            tenant_id=tenant_id,
            adapter_id=adapter_id,
            event_id=event_id,
            body=body,
            outcome="QUARANTINED_UNKNOWN_TRANSACTION" if s is None else "ACCEPTED",
        )
        if seen == "CONFLICT":
            raise VerificationError("CALLBACK_CONFLICT")
        if seen == "DUPLICATE" or s is None:
            return "DUPLICATE" if seen == "DUPLICATE" else "QUARANTINED"
        if s.status not in (SessionStatus.PENDING_EXTERNAL, SessionStatus.IN_PROGRESS):
            return "IGNORED_TERMINAL"  # provider status never regresses a decided session
        profile = await store.load_profile(conn, s.profile_id)
        result = _result(claims.get("result") or {})
        decision = decide(profile, result, jurisdiction=s.jurisdiction)
        await _apply(conn, s, profile, decision, adapter_id, result, now)
        return decision.status.value


async def _apply(
    conn: AsyncConnection,
    s: store.VSession,
    profile: Profile,
    d: Decision,
    adapter_id: str,
    result: ProviderResult,
    now: datetime,
) -> None:
    if d.status is SessionStatus.PENDING_EXTERNAL:
        return
    if "FRAUD_SUSPECTED" in d.reasons:
        await _event(conn, "verification.fraud_signal", s, 50, signals=list(result.fraud_signals))
        await ingest(
            conn,
            CanonicalSignal(
                tenant_id=s.tenant_id,
                subject_type="PRINCIPAL",
                subject_ref=s.subject_principal_id,
                source_id=f"verification:{adapter_id}",
                source_event_id=f"{s.session_id}:fraud",
                source_event_type="verification.fraud_signal",
                event_type="risk.elevated",
                effective_at=now,
                observed_at=now,
                confidence="HIGH",
            ),
            now=now,
        )
    if d.status is SessionStatus.MANUAL_REVIEW:
        pending = {
            "decision": d.outcome,
            "claims": [asdict(c) for c in d.verified_claims],
            "adapter_id": adapter_id,
            "evidence_commitment": result.evidence_commitment,
        }
        moved = await store.update_session(
            conn,
            s,
            status=transition(s.status, SessionStatus.MANUAL_REVIEW),
            now=now,
            outcome=d.outcome,
            pending=pending,
        )
        await store.open_case(conn, moved, approvals=profile.review_approvals, reasons=d.reasons)
        await _event(conn, "verification.manual_review_required", moved, 4, reasons=list(d.reasons))
        return
    await _finish(
        conn,
        s,
        profile,
        d.status,
        d.outcome,
        d.verified_claims,
        adapter_id,
        d.reasons,
        result.evidence_commitment,
        now,
    )


async def _finish(
    conn: AsyncConnection,
    s: store.VSession,
    profile: Profile,
    status: SessionStatus,
    outcome: str,
    claims: tuple[ClaimResult, ...],
    adapter_id: str,
    reasons: tuple[str, ...],
    evidence_commitment: str | None,
    now: datetime,
) -> list[str]:
    done = await store.update_session(
        conn, s, status=transition(s.status, status), now=now, outcome=outcome
    )
    ids: list[str] = []
    if status is SessionStatus.VERIFIED:
        ids = await store.insert_assertions(
            conn,
            done,
            profile,
            claims,
            adapter_id=adapter_id,
            reasons=reasons,
            evidence_commitment=evidence_commitment,
            now=now,
        )
        for aid, c in zip(ids, claims, strict=True):
            await emit(
                conn,
                name="verification.assertion.created",
                tenant_id=s.tenant_id,
                aggregate_type="verification_assertion",
                aggregate_id=aid,
                aggregate_version=1,
                data={
                    "assertion_id": aid,
                    "subject_principal_id": s.subject_principal_id,
                    "claim_type": c.claim,
                    "assurance_level": profile.level,
                },
            )
    await _event(conn, "verification.completed", done, 10, outcome=outcome, assertions=ids)
    return ids


async def review(
    conn: AsyncConnection,
    *,
    case_id: str,
    reviewer: str,
    decision: str,
    reasons: list[str],
    now: datetime,
) -> str:
    case = await store.load_case(conn, case_id)
    if case["status"] != "OPEN":
        raise VerificationError("REVIEW_CASE_CLOSED")
    s = await store.load_session(conn, str(case["session_id"]), lock=True)
    if reviewer == s.subject_principal_id:
        raise VerificationError("REVIEWER_IS_SUBJECT")  # no self-approval
    decisions = await store.add_decision(
        conn, case=case, reviewer=reviewer, decision=decision, reasons=reasons, now=now
    )
    profile = await store.load_profile(conn, s.profile_id)
    pending = s.pending_result or {}
    claims = tuple(ClaimResult(**c) for c in pending.get("claims", ()))
    if decision == "REJECT":
        await store.close_case(conn, case["case_id"], "REJECTED", now)
        await _finish(
            conn,
            s,
            profile,
            SessionStatus.FAILED,
            "MANUAL_REVIEW_REJECTED",
            (),
            str(pending.get("adapter_id")),
            tuple(reasons),
            None,
            now,
        )
        return "REJECTED"
    if decision == "ESCALATE":
        await store.close_case(conn, case["case_id"], "ESCALATED", now)
        return "ESCALATED"
    approvals = decisions.count("APPROVE")
    if approvals < int(case["required_approvals"]):
        return "PENDING_SECOND_REVIEWER" if decision == "APPROVE" else "PENDING"
    missing = [c for c in profile.required_claims if c not in {x.claim for x in claims}]
    if missing:  # a reviewer cannot invent evidence the provider never verified
        await store.close_case(conn, case["case_id"], "REJECTED", now)
        await _finish(
            conn,
            s,
            profile,
            SessionStatus.FAILED,
            "INSUFFICIENT_EVIDENCE",
            (),
            str(pending.get("adapter_id")),
            ("INSUFFICIENT_EVIDENCE",),
            None,
            now,
        )
        return "REJECTED"
    await store.close_case(conn, case["case_id"], "APPROVED", now)
    await _finish(
        conn,
        s,
        profile,
        SessionStatus.VERIFIED,
        "VERIFIED",
        claims,
        str(pending.get("adapter_id")),
        ("MANUAL_REVIEW_APPROVED", *reasons),
        pending.get("evidence_commitment"),
        now,
    )
    return "APPROVED"


async def rely(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    client_id: str,
    purpose: str,
    required_level: str,
    claims: list[str],
    jurisdiction: str | None,
    now: datetime,
) -> dict[str, Any]:
    rows = store.held(await store.assertions_for(conn, principal_id))
    outcome, reasons, used = reliance(
        rows,
        required_level=required_level,
        claims=claims,
        purpose=purpose,
        jurisdiction=jurisdiction,
        now=now,
    )
    did = await store.record_reliance(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        client_id=client_id,
        purpose=purpose,
        required_level=required_level,
        jurisdiction=jurisdiction,
        assertion_ids=used,
        outcome=outcome,
        reasons=reasons,
        now=now,
    )
    await emit(
        conn,
        name="verification.reliance.allowed"
        if outcome == "REUSE_ALLOWED"
        else "verification.reliance.denied",
        tenant_id=tenant_id,
        aggregate_type="reliance_decision",
        aggregate_id=did,
        aggregate_version=1,
        data={
            "reliance_decision_id": did,
            "subject_principal_id": principal_id,
            "outcome": outcome,
            "purpose": purpose,
        },
    )
    return {
        "reliance_decision_id": did,
        "outcome": outcome,
        "reason_codes": list(reasons),
        "assertion_ids": list(used),
    }


async def assurance(conn: AsyncConnection, principal_id: str, now: datetime) -> dict[str, Any]:
    """FR-VER-008: levels, claims and freshness only - never values, evidence or provider data."""
    claims: dict[str, dict[str, Any]] = {}
    for r in await store.assertions_for(conn, principal_id):
        if r["status"] != "VALID" or (r["valid_until"] and r["valid_until"] <= now):
            continue
        current = claims.get(r["claim_type"])
        if current is None or IAL_RANK[r["assurance_level"]] > IAL_RANK[current["level"]]:
            claims[r["claim_type"]] = {
                "level": r["assurance_level"],
                "method": r["verification_method"],
                "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
            }
    ial = max((IAL_RANK[c["level"]] for c in claims.values()), default=0)
    return {
        "principal_id": principal_id,
        "identity_proofing": {"level": f"IAL-Z{ial}", "claims": claims},
    }


async def revoke(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str | None,
    assertion_id: str | None,
    now: datetime,
) -> list[str]:
    ids = await store.revoke_assertions(
        conn, principal_id=principal_id, assertion_id=assertion_id, now=now
    )
    for aid in ids:
        await emit(
            conn,
            name="verification.assertion.revoked",
            tenant_id=tenant_id,
            aggregate_type="verification_assertion",
            aggregate_id=aid,
            aggregate_version=2,
            data={"assertion_id": aid},
        )
    return ids


async def fresh_reproof(
    conn: AsyncConnection, principal_id: str, *, min_level: str, since: datetime, now: datetime
) -> str | None:
    """For recovery (AUTH §14): a VERIFIED identity session at >= level completed after `since`."""
    for r in await store.assertions_for(conn, principal_id):
        if (
            r["status"] == "VALID"
            and r["performed_at"] >= since
            and (r["valid_until"] is None or r["valid_until"] > now)
            and IAL_RANK[r["assurance_level"]] >= IAL_RANK[min_level]
        ):
            return str(r["assertion_id"])
    return None


async def record_presentation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_principal_id: str,
    profile_id: str,
    claims: dict[str, Any],
    holder_bound: bool,
    reference: str,
    jurisdiction: str,
    now: datetime,
) -> tuple[str, list[str]]:
    """VERIFY §12: a verified credential presentation is DIGITAL_CREDENTIAL evidence, decided by the
    same deterministic profile rules as any provider result. Returns (outcome, assertion ids)."""
    profile = await store.load_profile(conn, profile_id)
    select_profile(profile, floor=None, jurisdiction=jurisdiction)
    s = store.VSession(
        session_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        profile_id=profile_id,
        subject_principal_id=subject_principal_id,
        purpose="credential_presentation",
        jurisdiction=jurisdiction,
        status=SessionStatus.CREATED,
        adapter_id=None,
        provider_transaction_ref=None,
        attempted_adapters=(),
        outcome_code=None,
        pending_result=None,
        row_version=1,
        created_at=now,
        expires_at=now + SESSION_TTL,
    )
    await store.create_session(conn, s)
    s = await store.update_session(
        conn,
        s,
        status=transition(s.status, SessionStatus.IN_PROGRESS),
        now=now,
        adapter_id="oid4vp",
        ref=reference,
    )
    result = ProviderResult(
        status="COMPLETED",
        claims=tuple(
            ClaimResult(name, True, str(value), "digital_credential", ("DIGITAL_CREDENTIAL",))
            for name, value in claims.items()
        ),
        binding="NON_BIOMETRIC" if holder_bound else None,
        liveness_passed=None,
        fraud_signals=(),
    )
    decision = decide(profile, result, jurisdiction=jurisdiction)
    if decision.status is SessionStatus.MANUAL_REVIEW:
        decision = Decision(
            SessionStatus.FAILED, decision.outcome, decision.verified_claims, decision.reasons
        )
    ids = await _finish(
        conn,
        s,
        profile,
        decision.status,
        decision.outcome,
        decision.verified_claims,
        "oid4vp",
        decision.reasons,
        None,
        now,
    )
    return decision.outcome, ids
