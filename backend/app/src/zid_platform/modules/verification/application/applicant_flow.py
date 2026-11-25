"""The hosted applicant's journey over a verification session and its proofing case (ZID-CP-WF01 sections 05, 09, S08, S10).

The session (privacy) holds why capture may happen and the person's marks; the proofing case (here) holds the evidence and the decision. This module reads both
and answers two audiences: the person, who sees only a state, a reference and one safe message; and the business, which reads the normalized decision and is
told through the outbox when a submitted session reaches a final state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.privacy.ports import applicant_session
from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.domain import applicant as ap
from zid_platform.modules.verification.domain.countries import SANDBOX_ENVIRONMENTS

FINALIZED = "verification.session.finalized"

UsageSink = Callable[[AsyncConnection, str, str, datetime], Awaitable[None]]
_usage_sink: UsageSink | None = None


def configure_usage_sink(sink: UsageSink | None) -> None:
    """Wired by the composition root: where a submitted session is recorded as usage (commercial's IDV_SUBMISSION meter). Verification never imports commercial."""
    global _usage_sink
    _usage_sink = sink


async def record_submission(conn: AsyncConnection, tenant_id: str, session_ref: str, at: datetime) -> None:
    """Once per session (the meter's key is the session): the first submission only calls this, and a repeat would be the same event anyway (Q33)."""
    if _usage_sink is not None:
        await _usage_sink(conn, tenant_id, session_ref, at)


def idv_coverage(source: object | None = None) -> list[dict[str, object]]:
    """Each market with a published proofing policy, its routes and whether a face comparison is authorized there (ZID-CPS-WF-02 07.06). A market
    without a policy is absent, never shown as available. `source` is the app's policy loader when one is configured (tests), else the file."""
    from zid_platform.modules.verification.domain.idv_policy import PolicyError
    from zid_platform.modules.verification.infrastructure import idv_policy_file

    try:
        table = source() if callable(source) else idv_policy_file.load()
    except PolicyError:
        return []
    out: list[dict[str, object]] = []
    for market, r in sorted(table.items()):
        routes = [{"route": "CHIP", "kind": "CHIP_AND_DOCUMENT"}] + [
            {"route": a.route_id, "kind": "DOCUMENT_AND_FACE" if a.face_match else "DOCUMENT_AUTOMATIC" if a.automatic else "DOCUMENT_REVIEWED"}
            for a in r.policy.alternates
        ]
        out.append({"market": market, "policy_version": r.policy.version, "routes": routes,
                    "face_comparison_authorized": bool(r.basis.lawful_basis and r.basis.impact_assessment_ref), "review_sla_hours": r.review_sla_hours})  # fmt: skip
    return out


def phase_of(s: Any, case: svc.Case | None, now: datetime) -> str:
    return ap.phase(
        canceled=s.state == "CANCELED", expires_at=s.expires_at, now=now, presented=s.presented_at is not None,
        acknowledged=s.acknowledged_at is not None, submitted=s.submitted_at is not None,
        case=None if case is None else {"decision": case.decision.value, "reasons": case.reasons, "attempts": case.attempts},
    )  # fmt: skip


def applicant_view(s: Any, case: svc.Case | None, now: datetime) -> dict[str, Any]:
    """What the person may see: no case id, no signal, no reason code, no score (WF-01 S08 to S10, Q34, Q35)."""
    p = phase_of(s, case, now)
    out: dict[str, Any] = {
        "session_ref": s.session_ref,
        "reference": ap.reference(s.session_ref),
        "state": p,
        "acknowledged": s.acknowledged_at is not None,
        "biometric_consent": s.biometric_consent_at is not None,
        "method": case.route if case else None,
        "submitted": s.submitted_at is not None,
        "final": p in ap.TERMINAL,
        "action": ap.message(
            ap.action(p, case.signals if case else None, submitted=s.submitted_at is not None)
        ),
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "mode": "sandbox" if s.environment in SANDBOX_ENVIRONMENTS else "production",
    }
    if p in ap.TERMINAL and s.return_url:
        out["return_url"] = (
            s.return_url
        )  # validated against the business's verified domains when the session opened
        out["return_state"] = s.return_state
    return out


async def decision(conn: AsyncConnection, case: svc.Case) -> dict[str, Any]:
    """The normalized result the business reads (WF-01 §08): the outcome, its version, the claim it supports and a digest of the decision record. No image,
    no document field, no score. `decision_digest` lets the business show later that the record it holds is the one Zoiko iD made."""
    hist = await svc.history(conn, case.case_id)
    last = hist[-1] if hist else {}
    record = {"case_id": case.case_id, "version": case.version, "decision": case.decision.value, "reasons": list(case.reasons),
              "policy_version": case.policy_version, "policy_hash": case.policy_hash, "decided_at": str(last.get("decided_at", ""))}  # fmt: skip
    return {
        "case_id": case.case_id,
        "outcome": case.decision.value,
        "decision_version": case.version,
        "reasons": list(case.reasons),
        "assertions": list(last.get("allowed") or []),
        "policy_version": case.policy_version,
        "policy_hash": case.policy_hash,
        "decided_at": record["decided_at"] or None,
        "decision_digest": hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


async def finalize_if_due(
    conn: AsyncConnection, tenant_id: str, case: svc.Case, now: datetime, *, review_sla_hours: int | None = None
) -> None:
    """Tell the business once a submitted session's state is final, and again if a later decision supersedes it (each with its decision version)."""
    s = await applicant_session.session_for_case(conn, case.case_id)
    if s is None or s.submitted_at is None:
        return
    p = phase_of(s, case, now)
    if p not in ap.TERMINAL and p != "MANUAL_REVIEW":
        return
    d = await decision(conn, case)
    await emit(
        conn, name=FINALIZED if p in ap.TERMINAL else "verification.session.review_required", tenant_id=tenant_id,
        aggregate_type="verification_session", aggregate_id=s.session_ref,
        aggregate_version=await applicant_session.next_event_version(conn, s.session_ref),
        data={"session_ref": s.session_ref, "state": p, "outcome": d["outcome"], "decision_version": d["decision_version"],
              "policy_version": d["policy_version"], "decision_digest": d["decision_digest"], "environment": s.environment,
              # WF-02 M05, M09, M10: who decided (any reviewer signature makes it human), a reviewer's defined finding, and the review clock
              "decided_by": "REVIEWER" if (case.signals.reviewers or case.signals.reject_code) else "AUTOMATED",
              "finding_code": case.signals.reject_code, "market": s.market,
              "due_at": (now + timedelta(hours=review_sla_hours)).isoformat() if p == "MANUAL_REVIEW" and review_sla_hours else None},
    )  # fmt: skip
