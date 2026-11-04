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

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_app.resilience import budget_timeout
from zid_context import RequestContext
from zid_crypto import CryptoError, verify_external_jws
from zid_db import in_tenant, tenant_transaction
from zid_outbox import emit

from zid_platform.modules.commercial.ports import record_vendor_check
from zid_platform.modules.privacy.ports import enforce_processing
from zid_platform.modules.signals.ports import CanonicalSignal, ingest
from zid_platform.modules.verification.domain.providers import (
    ProviderKind,
    sumsub_digest_ok,
    sumsub_event_id,
    sumsub_needs_applicant,
    sumsub_ref,
    sumsub_result,
    veriff_event_id,
    veriff_ref,
    veriff_result,
    veriff_signature_ok,
)
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
    decision_basis,
    failover_candidates,
    reconstruct,
    reliance,
    select_profile,
    transition,
)
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.providers import SumsubClient, VeriffClient

Secrets = Callable[[str], str | None]

CALLBACK_TYP = "verification-result+jwt"
CALLBACK_MAX_AGE = (
    600  # seconds: a signed result older than this (or from the future) is not acted on
)


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
    handoff: dict[str, str] | None = (
        None  # the applicant's short-lived link or token: returned once, never stored
    )


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
    country_registry_version: str | None = None,
    secrets: Secrets | None = None,
) -> Started:
    async with tenant_transaction(engine, ctx) as conn:
        profile = await store.load_profile(conn, profile_id)
        select_profile(profile, floor=floor, jurisdiction=jurisdiction)
        # Identity evidence is D3: only for a purpose the tenant registered (PRIV-01/05)
        await enforce_processing(
            conn,
            ctx.tenant_id or "",
            activity_code="IDENTITY_VERIFICATION",
            purpose=purpose,
            data_category="D3",
            subject_principal_id=subject_principal_id,
        )
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
        await _event(
            conn,
            "verification.created",
            s,
            1,
            profile_id=profile.profile_id,
            country=jurisdiction,  # EDR-001: which country rule and registry version allowed this
            country_registry_version=country_registry_version,
        )
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
            submitted = await _submit(adapter, s, profile, transport, secrets)
            ref = submitted[0] if submitted else None
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
            return Started(
                s.session_id,
                done.status.value,
                adapter_id,
                None,
                submitted[1] if submitted else None,
            )
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


def _provider_client(
    adapter: Mapping[str, Any], secrets: Secrets | None, transport: httpx.AsyncBaseTransport | None
) -> VeriffClient | SumsubClient | None:
    """The client for a Veriff or Sumsub adapter. No secrets source, or a missing setting, gives None: nothing is attempted."""
    if secrets is None:
        return None
    cfg = adapter.get("provider_config") or {}
    kind = adapter.get("provider_kind", "GENERIC")
    try:
        if kind == ProviderKind.VERIFF.value:
            return VeriffClient(
                adapter["submit_endpoint"],
                secrets,
                cfg["api_key_ref"],
                cfg["shared_secret_ref"],
                transport,
            )
        if kind == ProviderKind.SUMSUB.value:
            return SumsubClient(
                adapter["submit_endpoint"],
                secrets,
                cfg["app_token_ref"],
                cfg["secret_key_ref"],
                cfg["level_name"],
                transport,
            )
    except KeyError:
        return None
    return None


async def _submit(
    adapter: dict[str, Any],
    s: store.VSession,
    profile: Profile,
    transport: httpx.AsyncBaseTransport | None,
    secrets: Secrets | None = None,
) -> tuple[str, dict[str, str] | None] | None:
    """Only opaque references leave Zoiko; the provider collects evidence from the applicant. Returns the provider's reference
    for the session and, for Veriff and Sumsub, the applicant's short-lived link or token."""
    if adapter.get("provider_kind", "GENERIC") != ProviderKind.GENERIC.value:
        client = _provider_client(adapter, secrets, transport)
        if client is None:
            return None
        made = await client.create_session(
            s.session_id, (adapter.get("provider_config") or {}).get("callback_url")
        )
        if made is None:
            return None
        handoff = {k: v for k, v in (("url", made.url), ("token", made.token)) if v}
        return made.provider_ref, handoff or None
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=budget_timeout(5.0), follow_redirects=False
        ) as c:
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
    return (ref, None) if isinstance(ref, str) and 1 <= len(ref) <= 256 else None


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
            fraud_check=payload.get("fraud_check"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise VerificationError("CALLBACK_SCHEMA_INVALID") from e


async def _normalize(
    adapter: Mapping[str, Any],
    adapter_id: str,
    body: bytes,
    headers: Mapping[str, str],
    secrets: Secrets | None,
    transport: httpx.AsyncBaseTransport | None,
    now: datetime,
) -> tuple[str, str, ProviderResult]:
    """Authenticate a provider callback on its raw bytes, then turn it into (event id, provider reference, normalized result).

    Whatever the provider, an unauthenticated body decides nothing and an unreadable one is refused; Veriff and Sumsub
    authenticate with a shared-secret HMAC, Zoiko's own protocol with pinned public keys."""
    kind = ProviderKind(adapter.get("provider_kind", "GENERIC"))
    if kind is ProviderKind.GENERIC:
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
        issued = claims.get("iat")
        if (
            not isinstance(issued, int | float)
            or isinstance(issued, bool)
            or abs(now.timestamp() - issued) > CALLBACK_MAX_AGE
        ):
            raise VerificationError("CALLBACK_STALE", "a signed result must be fresh")
        event_id, ref = claims.get("event_id"), claims.get("transaction_ref")
        if not isinstance(event_id, str) or not isinstance(ref, str):
            raise VerificationError("CALLBACK_SCHEMA_INVALID")
        return event_id, ref, _result(claims.get("result") or {})
    cfg = adapter.get("provider_config") or {}
    get = secrets or (lambda _name: None)
    if kind is ProviderKind.VERIFF:
        if not veriff_signature_ok(
            body,
            headers,
            api_key=get(str(cfg.get("api_key_ref", ""))) or "",
            shared_secret=get(str(cfg.get("shared_secret_ref", ""))) or "",
        ):
            raise VerificationError("CALLBACK_SIGNATURE_INVALID")
    elif not sumsub_digest_ok(
        body, headers, webhook_secret=get(str(cfg.get("webhook_secret_ref", ""))) or ""
    ):
        raise VerificationError("CALLBACK_SIGNATURE_INVALID")
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise VerificationError("CALLBACK_SCHEMA_INVALID", "not JSON") from e
    if not isinstance(payload, dict):
        raise VerificationError("CALLBACK_SCHEMA_INVALID", "not an object")
    if kind is ProviderKind.VERIFF:
        return (
            veriff_event_id(payload),
            veriff_ref(payload),
            veriff_result(payload, uk_certified=bool(cfg.get("uk_certified"))),
        )
    info = None
    if sumsub_needs_applicant(payload):
        client = _provider_client(adapter, secrets, transport)
        info = (
            await client.applicant_info(sumsub_ref(payload))
            if isinstance(client, SumsubClient)
            else None
        )
        if (
            info is None
        ):  # nothing is recorded, so the provider's retry can try again; never an approval without the data
            raise VerificationError("APPLICANT_DATA_UNAVAILABLE")
    return (
        sumsub_event_id(payload),
        sumsub_ref(payload),
        sumsub_result(payload, info=info, biometric_level=bool(cfg.get("biometric_level"))),
    )


_AWAITING = (SessionStatus.PENDING_EXTERNAL, SessionStatus.IN_PROGRESS, SessionStatus.RESUBMISSION_REQUIRED)  # a provider result can still change these


async def provider_callback(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    adapter_id: str,
    body: bytes,
    base_ctx: RequestContext,
    now: datetime,
    country_check: Callable[[str], None] | None = None,
    headers: Mapping[str, str] | None = None,
    secrets: Secrets | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    ctx = base_ctx.with_identity(tenant_id, None)
    async with tenant_transaction(engine, ctx) as conn:
        adapter = await store.load_adapter(conn, adapter_id)
        if adapter is None or adapter["status"] in ("QUARANTINED", "RETIRED"):
            raise VerificationError("PROVIDER_UNTRUSTED")
        event_id, ref, result = await _normalize(
            adapter, adapter_id, body, headers or {}, secrets, transport, now
        )
        return await _ingest(
            conn, tenant_id=tenant_id, adapter_id=adapter_id, adapter=adapter, event_id=event_id,
            ref=ref, result=result, body=body, country_check=country_check, now=now,
        )  # fmt: skip


async def _ingest(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    adapter_id: str,
    adapter: Mapping[str, Any],
    event_id: str,
    ref: str,
    result: ProviderResult,
    body: bytes,
    country_check: Callable[[str], None] | None,
    now: datetime,
) -> str:
    """Apply one provider result to its session. Shared by the signed callback and by the poll that reconciles a delayed one."""
    s = await store.session_by_ref(conn, adapter_id, ref)
    if s is not None and country_check is not None:
        # EDR-001: a disabled country applies no result and records nothing
        country_check(s.jurisdiction)
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
    if s.status not in _AWAITING:
        return "IGNORED_TERMINAL"  # provider status never regresses a decided session
    profile = await store.load_profile(conn, s.profile_id)
    decision = decide(profile, result, jurisdiction=s.jurisdiction)
    basis = decision_basis(
        result,
        adapter_id=adapter_id,
        mapping_version=str(adapter["mapping_version"]),
        salt=s.session_id,
    )
    await _apply(conn, s, profile, decision, adapter_id, result, now, basis)
    # what this provider attempt cost Zoiko, charged to the customer or not (CR-029); never raises
    await record_vendor_check(
        conn,
        tenant_id=tenant_id,
        adapter_id=adapter_id,
        supplier_ref=event_id,
        check_type="IDENTITY_VERIFICATION",
        country=s.jurisdiction,
        decision_status=decision.status.value,
        reasons=list(decision.reasons),
        subject_ref=s.session_id,
        at=now,
    )
    return decision.status.value


RECONCILE_AFTER = timedelta(minutes=10)  # a webhook that has not arrived by now is worth asking the provider about
RECONCILE_EVERY = timedelta(minutes=10)  # how often one session is asked about
_last_polled: dict[str, datetime] = {}


async def reconcile_delayed(
    conn: AsyncConnection,
    now: datetime,
    *,
    secrets: Secrets | None,
    transport: httpx.AsyncBaseTransport | None = None,
    country_check: Callable[[str], None] | None = None,
    limit: int = 25,
    only_tenant: str | None = None,
) -> dict[str, int]:
    """The polling channel that reconciles a delayed webhook (ZID-EDR-005): for sessions still waiting on a Veriff decision after `RECONCILE_AFTER`,
    ask the provider for it and apply it exactly as a signed callback would be. Nothing is recorded when the provider has no decision yet, a failed
    call is never read as a pass, and a result already applied by the webhook is a duplicate or an ignored terminal. Sumsub is polled through the applicant
    call the platform already makes; its response shape is not yet confirmed against a live account, so anything unrecognised is no decision."""
    out = {"asked": 0, "applied": 0, "no_decision": 0, "unsupported": 0}
    if secrets is None:
        return out
    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id, adapter_id, provider_transaction_ref FROM verify.verification_session "
                "WHERE status IN ('PENDING_EXTERNAL', 'IN_PROGRESS', 'RESUBMISSION_REQUIRED') AND provider_transaction_ref IS NOT NULL "
                "AND adapter_id IS NOT NULL AND updated_at <= :cut AND expires_at > :now AND (CAST(:only AS uuid) IS NULL OR tenant_id = CAST(:only AS uuid)) "
                "ORDER BY updated_at LIMIT :n"
            ),
            {"cut": now - RECONCILE_AFTER, "now": now, "n": limit, "only": only_tenant},
        )
    ).all()
    for row in rows:
        key = f"{row.adapter_id}:{row.provider_transaction_ref}"
        if (last := _last_polled.get(key)) is not None and now - last < RECONCILE_EVERY:
            continue
        _last_polled[key] = now
        tenant = str(row.tenant_id)
        async with in_tenant(conn, tenant):
            adapter = await store.load_adapter(conn, row.adapter_id)
            if adapter is None or adapter["status"] in ("QUARANTINED", "RETIRED"):
                continue
            client = _provider_client(adapter, secrets, transport)
            cfg = adapter.get("provider_config") or {}
            if isinstance(client, VeriffClient):
                out["asked"] += 1
                doc = await client.decision(row.provider_transaction_ref)
                if doc is None or veriff_ref(doc) != row.provider_transaction_ref:
                    out["no_decision"] += 1
                    continue
                event_id, result = f"poll:{veriff_event_id(doc)}", veriff_result(doc, uk_certified=bool(cfg.get("uk_certified")))
            elif isinstance(client, SumsubClient):
                out["asked"] += 1
                applicant = await client.applicant_doc(row.provider_transaction_ref)
                review = (applicant or {}).get("review")
                if not isinstance(review, dict) or not review.get("reviewStatus"):
                    out["no_decision"] += 1  # no review on record, or a shape we do not recognise: never read as a pass
                    continue
                doc = {
                    "type": "applicantReviewed", "applicantId": row.provider_transaction_ref, "correlationId": "poll",
                    "reviewStatus": review.get("reviewStatus"), "reviewResult": review.get("reviewResult") or {},
                }  # fmt: skip
                info = (applicant or {}).get("info")
                event_id = f"poll:{sumsub_event_id(doc)}"
                result = sumsub_result(doc, info=info if isinstance(info, dict) else None, biometric_level=bool(cfg.get("biometric_level")))
            else:
                out["unsupported"] += 1
                continue
            body = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
            await _ingest(
                conn, tenant_id=tenant, adapter_id=row.adapter_id, adapter=adapter, event_id=event_id,
                ref=row.provider_transaction_ref, result=result, body=body, country_check=country_check, now=now,
            )  # fmt: skip
            out["applied"] += 1
    return out


async def _apply(
    conn: AsyncConnection,
    s: store.VSession,
    profile: Profile,
    d: Decision,
    adapter_id: str,
    result: ProviderResult,
    now: datetime,
    basis: dict[str, Any],
) -> None:
    if d.status is SessionStatus.PENDING_EXTERNAL:
        return
    if d.status is SessionStatus.RESUBMISSION_REQUIRED:
        if s.status is not SessionStatus.RESUBMISSION_REQUIRED:  # a repeat of the same request changes nothing
            await store.update_session(
                conn, s, status=transition(s.status, d.status), now=now, outcome=d.outcome, basis=basis
            )
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
            basis=basis,
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
        basis,
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
    basis: dict[str, Any] | None = None,
) -> list[str]:
    done = await store.update_session(
        conn, s, status=transition(s.status, status), now=now, outcome=outcome, basis=basis
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


async def reconstruct_decision(conn: AsyncConnection, session_id: str) -> dict[str, Any]:
    """Audit (VERIFY-29): re-derive a recorded decision from the pinned profile and the recorded
    normalized facts, and say whether it equals what was recorded. Never reads raw evidence."""
    s = await store.load_session(conn, session_id)
    basis = await store.decision_basis(conn, session_id)
    if basis is None:
        raise VerificationError("DECISION_BASIS_NOT_RECORDED")
    profile = await store.load_profile(conn, s.profile_id)
    again = reconstruct(profile, basis, jurisdiction=s.jurisdiction)
    return {
        "session_id": s.session_id,
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "adapter_id": basis["adapter_id"],
        "mapping_version": basis["mapping_version"],
        "evidence_commitment": basis["evidence_commitment"],
        "recorded_outcome": s.outcome_code,
        "reconstructed_outcome": again.outcome,
        # a reviewer's decision is a human act: facts alone only reconstruct the route into review
        "manual_review": again.status is SessionStatus.MANUAL_REVIEW,
        "matches": None
        if again.status is SessionStatus.MANUAL_REVIEW
        else again.outcome == s.outcome_code,
    }


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


async def verified_claims(
    conn: AsyncConnection,
    principal_id: str,
    *,
    purpose: str,
    requested: list[str],
    now: datetime,
    min_level: str = "IAL-Z1",
) -> dict[str, Any]:
    """VERIFY-25: the OpenID IDA `verified_claims` for exactly what was asked for and may be relied on."""
    from zid_platform.modules.verification.domain.ida import project

    rows = await store.assertions_for(conn, principal_id)
    return {
        "verified_claims": project(
            rows, purpose=purpose, requested=requested, now=now, min_level=min_level
        )
    }


SHARED_ASSERTION_CLASS = "DC2"  # a commitment about an identifiable person

# The tenant module's cross-cell transfer gateway (tenant.ports.authorize_transfer), handed over by main.py so the
# verification module does not import tenant.
_TRANSFER_GATE: Callable[..., Awaitable[Any]] | None = None


def configure_transfer_gate(gate: Callable[..., Awaitable[Any]] | None) -> None:
    global _TRANSFER_GATE
    _TRANSFER_GATE = gate


async def shared_assertions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    home_cell_id: str,
    relying_cell_id: str,
    purpose: str,
    requested: list[str],
    now: datetime,
    min_level: str = "IAL-Z1",
) -> dict[str, Any]:
    """TEN-24: what a relying tenant in another cell may be told about a reusable identity held here. The transfer
    gateway decides whether the destination may receive it at all (and which fields an approved rule allows); what is
    sent is the approved assertion/commitment only, never the source profile."""
    from zid_platform.modules.verification.domain.shared_identity import (
        SharedIdentityError,
        shareable,
    )

    if _TRANSFER_GATE is None:
        raise SharedIdentityError(
            "TRANSFER_GATE_UNAVAILABLE", "the cross-cell transfer gateway is not configured"
        )
    decision = await _TRANSFER_GATE(
        conn,
        tenant_id,
        data_class=SHARED_ASSERTION_CLASS,
        destination_cell_id=relying_cell_id,
        purpose_code=purpose,
        now=now,
    )
    if not decision.allowed:
        raise SharedIdentityError(decision.code, f"{relying_cell_id} may not receive this identity")
    rows = await store.assertions_for(conn, principal_id)
    return {
        "home_cell_id": home_cell_id,
        "assertions": shareable(
            rows,
            home_cell_id=home_cell_id,
            purpose=purpose,
            requested=requested,
            now=now,
            min_level=min_level,
            allowlist=decision.field_allowlist,
        ),
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
        decision_basis(result, adapter_id="oid4vp", mapping_version=None, salt=s.session_id),
    )
    return decision.outcome, ids


async def claim_matches(
    conn: AsyncConnection,
    principal_id: str,
    *,
    claims: tuple[str, ...],
    candidates: tuple[str, ...],
    now: datetime,
    min_level: str = "IAL-Z2",
) -> bool:
    """Does a VALID, unexpired assertion (>= min_level) of one of `claims` equal one candidate?

    Equality against the stored commitment only (no value is ever read back): the caller supplies a
    finite list of exact renderings, e.g. a registered officer name (ROOT-001 §6.1 holder binding)."""
    rows = (
        await conn.execute(
            text(
                "SELECT assertion_id, claim_type, claim_value_commitment, assurance_level "
                "FROM verify.verification_assertion WHERE subject_principal_id = :s "
                "AND status = 'VALID' AND (valid_until IS NULL OR valid_until > :now) "
                "AND claim_type = ANY(:claims)"
            ),
            {"s": uuid.UUID(principal_id), "now": now, "claims": list(claims)},
        )
    ).all()
    for r in rows:
        if IAL_RANK.get(r.assurance_level, 0) < IAL_RANK[min_level]:
            continue
        stored = bytes(r.claim_value_commitment)
        for value in candidates:
            if store.commitment(str(r.assertion_id), r.claim_type, value) == stored:
                return True
    return False
