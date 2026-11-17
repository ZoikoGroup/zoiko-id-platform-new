"""A stored identity proofing case: evidence goes in, the decision is re-derived and stored each time (ZID-DTV-WF-03 sections 6, 10, 18; CR-110).

Nothing here lets a caller state a result. The chip signals are derived from the platform's own passive-authentication verdict, the capture signal from the envelope checked against a server-issued nonce and the
digests already seen, the printed-document signal from the checks' results, and the person signals only from an engine result recorded through `record_engine_result`, which refuses unless the market's biometric
controls are in force. Every change re-runs `decide`, appends a history row, and tells the tenant's webhook subscribers when the decision changed (identifiers and codes only, no personal data)."""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.verification.domain import idv_controls as ctl
from zid_platform.modules.verification.domain import idv_evidence as ev
from zid_platform.modules.verification.domain.idv_decision import (
    ChipAuth,
    Decision,
    FaceMatch,
    Liveness,
    NfcAccess,
    Optical,
    PassiveAuth,
    Result,
    Signals,
    decide,
)
from zid_platform.modules.verification.domain.idv_policy import MarketRules

EVENT = "verification.idv.case_decided"


class CaseError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    applicant: str
    market: str
    route: str
    policy_version: str
    policy_hash: str
    signals: Signals
    decision: Decision
    reasons: tuple[str, ...]
    state: str
    attempts: dict[str, int]
    notice_shown: str | None
    consent_at: datetime | None
    withdrawn_at: datetime | None
    nonce: str | None
    nonce_issued_at: datetime | None
    nonce_used: bool
    printed_salt: str | None
    printed_digests: dict[str, str] | None
    version: int
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------------------------------------------------- (de)serialise


def signals_to_dict(s: Signals) -> dict[str, Any]:
    return {
        "optical": s.optical.value,
        "nfc": s.nfc.value,
        "passive_auth": s.passive_auth.value,
        "chip_auth": s.chip_auth.value,
        "face": s.face.value,
        "liveness": s.liveness.value,
        "mrz_check_digits_ok": s.mrz_check_digits_ok,
        "optical_matches_chip": s.optical_matches_chip,
        "reference_source": s.reference_source,
        "injection_risk": s.injection_risk,
        "document_expired": s.document_expired,
        "web_only_capture": s.web_only_capture,
        "typed_transcription_only": s.typed_transcription_only,
        "route": s.route,
        "route_enabled": s.route_enabled,
        "method_released": s.method_released,
        "reviewers": list(s.reviewers),
        "abandoned": s.abandoned,
    }


def signals_from_dict(d: dict[str, Any]) -> Signals:
    return Signals(
        optical=Optical(d["optical"]),
        nfc=NfcAccess(d["nfc"]),
        passive_auth=PassiveAuth(d["passive_auth"]),
        chip_auth=ChipAuth(d["chip_auth"]),
        face=FaceMatch(d["face"]),
        liveness=Liveness(d["liveness"]),
        mrz_check_digits_ok=d["mrz_check_digits_ok"],
        optical_matches_chip=d["optical_matches_chip"],
        reference_source=d["reference_source"],
        injection_risk=d["injection_risk"],
        document_expired=d["document_expired"],
        web_only_capture=d["web_only_capture"],
        typed_transcription_only=d["typed_transcription_only"],
        route=d["route"],
        route_enabled=d["route_enabled"],
        method_released=d["method_released"],
        reviewers=tuple(d["reviewers"]),
        abandoned=d["abandoned"],
    )


def _json(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


_COLS = (
    "case_id, applicant, market, route, policy_version, policy_hash, signals, decision, reasons, state, attempts, "
    "notice_shown, consent_at, withdrawn_at, nonce, nonce_issued_at, nonce_used, printed_salt, printed_digests, version, created_at, updated_at"
)


def _case(r: Any) -> Case:
    m = r._mapping
    return Case(
        str(m["case_id"]), str(m["applicant"]), m["market"], m["route"], m["policy_version"], m["policy_hash"],
        signals_from_dict(m["signals"]), Decision(m["decision"]), tuple(m["reasons"]), m["state"], dict(m["attempts"]),
        m["notice_shown"], m["consent_at"], m["withdrawn_at"], m["nonce"], m["nonce_issued_at"], m["nonce_used"], m["printed_salt"], m["printed_digests"],
        m["version"], m["created_at"], m["updated_at"],
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------- reads


async def get(conn: AsyncConnection, case_id: str) -> Case | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM verify.idv_case WHERE case_id = :c"),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    return _case(row) if row else None


async def list_cases(conn: AsyncConnection, *, limit: int = 50) -> list[Case]:
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM verify.idv_case ORDER BY created_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_case(r) for r in rows]


async def history(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT version, decision, reasons, claims, allowed, policy_version, trigger, decided_at "
                "FROM verify.idv_case_decision WHERE case_id = :c ORDER BY version"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------------------------------------------------ writes


async def _require_open(conn: AsyncConnection, case_id: str) -> Case:
    c = await get(conn, case_id)
    if c is None:
        raise CaseError("CASE_NOT_FOUND")
    if c.state == "CLOSED":
        raise CaseError(
            "CASE_CLOSED", "This case reached a final decision and takes no more evidence."
        )
    return c


def _biometrics(c: Case, rules: MarketRules) -> tuple[bool, tuple[str, ...]]:
    return ctl.biometrics_allowed(
        rules.basis, self_hosted=rules.self_hosted_engine, notice_shown_version=c.notice_shown,
        consent_at=c.consent_at, withdrawn_at=c.withdrawn_at,
    )  # fmt: skip


async def _commit(
    conn: AsyncConnection, c: Case, signals: Signals, rules: MarketRules, *, trigger: str, now: datetime,
    attempts: dict[str, int] | None = None, extra_reasons: tuple[str, ...] = (), **cols: Any,
) -> Case:  # fmt: skip
    """Re-derive the decision from the signals, store it with the new signals, append the history row, and say so when the decision changed."""
    allowed, why = _biometrics(c if not cols else replace(c, **cols), rules)
    if not allowed and (
        signals.face is not FaceMatch.NOT_RUN or signals.liveness is not Liveness.NOT_RUN
    ):
        signals = replace(
            signals, face=FaceMatch.NOT_RUN, liveness=Liveness.NOT_RUN, reference_source="NONE"
        )  # nothing biometric survives a missing control
    result: Result = decide(signals, rules.policy)
    off = (
        ()
        if allowed or rules.policy.face.value == "NOT_USED"
        else tuple(f"BIOMETRICS_OFF:{w}" for w in why)
    )
    reasons = tuple(dict.fromkeys(result.reasons + extra_reasons + off))
    version = c.version + 1
    state = (
        "CLOSED"
        if result.decision in (Decision.APPROVED, Decision.REJECTED, Decision.ABANDONED)
        else "OPEN"
    )
    sets = {"signals": _json(signals_to_dict(signals)), "decision": result.decision.value, "reasons": _json(list(reasons)), "state": state,
            "attempts": _json(attempts if attempts is not None else c.attempts), "version": version, "updated_at": now}  # fmt: skip
    after = replace(c, **cols)  # the few columns a step may change; the statement itself is fixed
    params: dict[str, Any] = {
        "c": uuid.UUID(c.case_id),
        **sets,
        "notice": after.notice_shown,
        "consent": after.consent_at,
        "withdrawn": after.withdrawn_at,
        "nonce_used": after.nonce_used,
        "salt": after.printed_salt,
        "digests": _json(after.printed_digests) if after.printed_digests is not None else None,
    }
    await conn.execute(
        text(
            "UPDATE verify.idv_case SET signals = CAST(:signals AS jsonb), decision = :decision, reasons = CAST(:reasons AS jsonb), "
            "state = :state, attempts = CAST(:attempts AS jsonb), version = :version, updated_at = :updated_at, "
            "notice_shown = :notice, consent_at = :consent, withdrawn_at = :withdrawn, nonce_used = :nonce_used, printed_salt = :salt, printed_digests = CAST(:digests AS jsonb) WHERE case_id = :c"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO verify.idv_case_decision (decision_id, tenant_id, case_id, version, decision, reasons, claims, allowed, policy_version, policy_hash, trigger, decided_at) "
            "SELECT :id, tenant_id, :c, :v, :d, CAST(:r AS jsonb), CAST(:cl AS jsonb), CAST(:al AS jsonb), :pv, :ph, :tr, :now FROM verify.idv_case WHERE case_id = :c"
        ),
        {
            "id": uuid.uuid4(), "c": uuid.UUID(c.case_id), "v": version, "d": result.decision.value, "r": _json(list(reasons)),
            "cl": _json(result.claims), "al": _json(list(result.allowed_assertions)), "pv": c.policy_version, "ph": c.policy_hash,
            "tr": trigger[:80], "now": now,
        },
    )  # fmt: skip
    if result.decision is not c.decision:
        tenant = (
            await conn.execute(
                text("SELECT tenant_id FROM verify.idv_case WHERE case_id = :c"),
                {"c": uuid.UUID(c.case_id)},
            )
        ).scalar_one()
        await emit(
            conn,
            name=EVENT,
            tenant_id=str(tenant),
            aggregate_type="idv_case",
            aggregate_id=c.case_id,
            aggregate_version=version,
            data={
                "case_id": c.case_id, "decision": result.decision.value, "previous": c.decision.value, "reasons": list(reasons),
                "claims": result.claims, "assertions": list(result.allowed_assertions), "policy_version": c.policy_version,
                "policy_hash": c.policy_hash, "version": version,
            },
        )  # fmt: skip
    latest = await get(conn, c.case_id)
    assert latest is not None
    return latest


async def open_case(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    applicant: str,
    route: str,
    rules: MarketRules,
    now: datetime,
) -> Case:
    if route != "CHIP" and all(a.route_id != route for a in rules.policy.alternates):
        raise CaseError(
            "ROUTE_NOT_ALLOWED_BY_POLICY",
            "That route is not one this market's signed policy names.",
        )
    signals = Signals(route=route)
    result = decide(signals, rules.policy)
    case_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.idv_case (case_id, tenant_id, applicant, market, route, policy_version, policy_hash, signals, decision, reasons, created_at, updated_at) "
            "VALUES (:c, :t, :a, :m, :r, :pv, :ph, CAST(:s AS jsonb), :d, CAST(:rs AS jsonb), :now, :now)"
        ),
        {
            "c": case_id, "t": uuid.UUID(tenant_id), "a": uuid.UUID(applicant), "m": rules.market, "r": route,
            "pv": rules.policy.version, "ph": rules.policy_hash, "s": _json(signals_to_dict(signals)),
            "d": result.decision.value, "rs": _json(list(result.reasons)), "now": now,
        },
    )  # fmt: skip
    await conn.execute(
        text(
            "INSERT INTO verify.idv_case_decision (decision_id, tenant_id, case_id, version, decision, reasons, claims, allowed, policy_version, policy_hash, trigger, decided_at) "
            "VALUES (:id, :t, :c, 1, :d, CAST(:r AS jsonb), CAST(:cl AS jsonb), CAST('[]' AS jsonb), :pv, :ph, 'opened', :now)"
        ),
        {
            "id": uuid.uuid4(), "t": uuid.UUID(tenant_id), "c": case_id, "d": result.decision.value,
            "r": _json(list(result.reasons)), "cl": _json(result.claims), "pv": rules.policy.version, "ph": rules.policy_hash, "now": now,
        },
    )  # fmt: skip
    got = await get(conn, str(case_id))
    assert got is not None
    return got


def _spend(c: Case, kind: str, rules: MarketRules) -> dict[str, int]:
    ok, why = ctl.retry_left(rules.retry, kind=kind, used=c.attempts.get(kind, 0))
    if not ok:
        raise CaseError(why, "No further attempts are allowed for this case.")
    return {**c.attempts, kind: c.attempts.get(kind, 0) + 1}


async def submit_chip(
    conn: AsyncConnection, case_id: str, rules: MarketRules, *, status: str, reasons: tuple[str, ...],
    document: dict[str, str] | None, chip_auth: ChipAuth, now: datetime,
) -> Case:  # fmt: skip
    """The platform's own passive-authentication verdict (status and reason codes) and the chip's DG1 fields when it read them. What is printed was read by the platform from the document photograph earlier
    (`submit_document`) and is compared here by digest; the digests are then cleared. Nothing the applicant types or sends as "printed" data is ever compared."""
    c = await _require_open(conn, case_id)
    if c.route != "CHIP":
        raise CaseError(
            "ROUTE_HAS_NO_CHIP",
            "This case is on an alternative route that carries no chip evidence.",
        )
    attempts = _spend(c, "NFC", rules)
    sig = ev.chip_signals(status, reasons)
    compare, _differ = (
        ev.compare_digests(c.printed_digests, document, c.printed_salt or "")
        if c.printed_digests and document
        else ("UNKNOWN", ())
    )
    s = replace(
        c.signals, nfc=sig.nfc, passive_auth=sig.passive_auth, chip_auth=chip_auth, mrz_check_digits_ok=sig.mrz_check_digits_ok,
        document_expired=sig.document_expired, optical_matches_chip=compare, web_only_capture=False,
        reference_source="CHIP_DG2_TRUSTED" if sig.passive_auth is PassiveAuth.TRUSTED_PASS else "CHIP_DG2_UNTRUSTED",
    )  # fmt: skip
    return await _commit(
        conn, c, s, rules, trigger="chip", now=now, attempts=attempts,
        **({"printed_digests": None, "printed_salt": None} if document else {}),
    )  # fmt: skip


async def store_printed(conn: AsyncConnection, case_id: str, fields: dict[str, str]) -> None:
    """Keep only salted digests of the printed holder data, for one later comparison with the chip."""
    salt = secrets.token_hex(16)
    await conn.execute(
        text(
            "UPDATE verify.idv_case SET printed_salt = :s, printed_digests = CAST(:d AS jsonb) WHERE case_id = :c"
        ),
        {"s": salt, "d": _json(ev.printed_digests(fields, salt)), "c": uuid.UUID(case_id)},
    )


async def submit_document(
    conn: AsyncConnection, case_id: str, rules: MarketRules, envelope: ev.Envelope, *, checks: dict[str, str],
    printed: dict[str, str] | None, now: datetime, web: bool = False,
) -> Case:  # fmt: skip
    """A document photograph: the capture's envelope is checked (the digest is the one the platform computed from the bytes, never the client's), the image checks become the printed-document signal, and the printed
    machine-readable data, which the platform read itself, is kept as digests for the chip comparison."""
    await submit_capture(conn, case_id, rules, envelope, now=now, web=web)
    if printed:
        await store_printed(conn, case_id, printed)
    return await submit_optical(conn, case_id, rules, checks, now=now)


async def chip_not_read(
    conn: AsyncConnection, case_id: str, rules: MarketRules, *, access: NfcAccess, now: datetime
) -> Case:
    """The applicant's phone has no NFC, they declined, or the read failed: each is its own state and none is a pass."""
    if access in (NfcAccess.READ_OK, NfcAccess.NOT_STARTED):
        raise CaseError(
            "INVALID_ACCESS_STATE",
            "Only UNSUPPORTED, USER_DECLINED or READ_FAILED can be recorded here.",
        )
    c = await _require_open(conn, case_id)
    attempts = _spend(c, "NFC", rules) if access is NfcAccess.READ_FAILED else None
    return await _commit(
        conn,
        c,
        replace(c.signals, nfc=access, passive_auth=PassiveAuth.NOT_TESTED),
        rules,
        trigger=f"nfc_{access.value.lower()}",
        now=now,
        attempts=attempts,
    )


async def issue_nonce(conn: AsyncConnection, case_id: str, now: datetime) -> tuple[str, Case]:
    await _require_open(conn, case_id)
    nonce = secrets.token_urlsafe(24)
    await conn.execute(
        text(
            "UPDATE verify.idv_case SET nonce = :n, nonce_issued_at = :t, nonce_used = false WHERE case_id = :c"
        ),
        {"n": nonce, "t": now, "c": uuid.UUID(case_id)},
    )
    got = await get(conn, case_id)
    assert got is not None
    return nonce, got


async def submit_capture(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    envelope: ev.Envelope,
    *,
    now: datetime,
    web: bool = False,
) -> Case:
    """Check the capture's envelope against the nonce this case issued and the digests already seen. A finding is sticky: a later clean capture does not clear it."""
    c = await _require_open(conn, case_id)
    attempts = _spend(c, "CAPTURE", rules)
    issued = (
        ev.IssuedNonce(c.nonce, c.case_id, c.nonce_issued_at, c.nonce_used)
        if c.nonce and c.nonce_issued_at
        else None
    )
    tenant = (
        await conn.execute(
            text("SELECT tenant_id FROM verify.idv_case WHERE case_id = :c"),
            {"c": uuid.UUID(case_id)},
        )
    ).scalar_one()
    seen = False
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO verify.idv_media_hash (tenant_id, media_sha256, case_id, seen_at) VALUES (:t, :h, :c, :now)"
                ),
                {"t": tenant, "h": envelope.media_sha256, "c": uuid.UUID(case_id), "now": now},
            )
    except IntegrityError:
        seen = True
    v = ev.capture_integrity(
        envelope,
        issued,
        case_id=c.case_id,
        media_seen_before=seen,
        now=now,
        attestation_required=rules.attestation_required,
    )
    risk = (
        True if (c.signals.injection_risk is True or v.injection_risk is True) else v.injection_risk
    )
    s = replace(c.signals, injection_risk=risk, web_only_capture=web or c.signals.web_only_capture)
    return await _commit(
        conn,
        c,
        s,
        rules,
        trigger="capture",
        now=now,
        attempts=attempts,
        extra_reasons=v.reasons,
        nonce_used=True,
    )


async def submit_optical(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    results: dict[str, str],
    *,
    now: datetime,
    typed_transcription: bool = False,
) -> Case:
    c = await _require_open(conn, case_id)
    state, _why = ev.optical_state(results, rules.optical_required)
    s = replace(c.signals, optical=state, typed_transcription_only=typed_transcription)
    return await _commit(conn, c, s, rules, trigger="optical", now=now)


async def record_notice_and_consent(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    *,
    notice_version: str,
    consented: bool,
    now: datetime,
) -> Case:
    c = await _require_open(conn, case_id)
    s = c.signals
    if consented:
        return await _commit(
            conn,
            c,
            s,
            rules,
            trigger="consent",
            now=now,
            notice_shown=notice_version,
            consent_at=now,
        )
    return await _commit(conn, c, s, rules, trigger="notice", now=now, notice_shown=notice_version)


async def withdraw_consent(
    conn: AsyncConnection, case_id: str, rules: MarketRules, *, now: datetime
) -> Case:
    c = await _require_open(conn, case_id)
    return await _commit(
        conn, c, c.signals, rules, trigger="consent_withdrawn", now=now, withdrawn_at=now
    )


async def record_engine_result(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    *,
    liveness: Liveness,
    face: FaceMatch,
    now: datetime,
) -> Case:
    """A presentation-attack and face-match result from an approved engine. Refused unless the market's biometric controls are in force; the reference is the chip portrait only when the chip chained to a root we trust."""
    c = await _require_open(conn, case_id)
    ok, why = _biometrics(c, rules)
    if not ok:
        raise CaseError("BIOMETRICS_OFF", ", ".join(why))
    if c.signals.reference_source == "NONE":
        raise CaseError(
            "NO_REFERENCE_PORTRAIT",
            "There is no reference portrait to compare the live face with yet.",
        )
    return await _commit(
        conn, c, replace(c.signals, liveness=liveness, face=face), rules, trigger="engine", now=now
    )


async def add_reviewer(
    conn: AsyncConnection, case_id: str, rules: MarketRules, *, reviewer: str, now: datetime
) -> Case:
    c = await _require_open(conn, case_id)
    if reviewer == c.applicant:
        raise CaseError("REVIEWER_IS_THE_APPLICANT", "A person cannot review their own case.")
    if reviewer in c.signals.reviewers:
        raise CaseError("ALREADY_REVIEWED", "This reviewer already signed the case.")
    s = replace(c.signals, reviewers=(*c.signals.reviewers, reviewer))
    return await _commit(conn, c, s, rules, trigger="reviewer", now=now)


async def abandon(
    conn: AsyncConnection, case_id: str, rules: MarketRules, *, now: datetime
) -> Case:
    c = await _require_open(conn, case_id)
    return await _commit(
        conn, c, replace(c.signals, abandoned=True), rules, trigger="abandoned", now=now
    )


async def apply_engine(
    conn: AsyncConnection,
    case_id: str,
    rules: MarketRules,
    *,
    engine: Any,
    gate: Any,
    probe: bytes,
    media: bytes,
    reference: bytes | None,
    now: datetime,
) -> Case:
    """Run an APPROVED engine (`gate` is what `biometric_engine.engine_gate` produced, or None) on a live probe and a liveness capture against the reference portrait, and record the result. Refused unless the engine is approved for this
    market and the market's biometric controls are in force. The liveness check runs first and a face is never matched against a presentation that failed. The media is held for this call only and never stored."""
    c = await _require_open(conn, case_id)
    if gate is None or engine is None or not gate.allows(rules.market):
        raise CaseError("ENGINE_NOT_APPROVED", "No biometric engine is approved for this market.")
    if reference is None:
        raise CaseError(
            "NO_REFERENCE_PORTRAIT",
            "The chip's portrait is not available from a signed data group.",
        )
    ok, why = _biometrics(c, rules)
    if not ok:
        raise CaseError("BIOMETRICS_OFF", ", ".join(why))
    try:
        live: float | None = engine.liveness(media).score
    except Exception:  # noqa: BLE001 - an engine that cannot decide is never a pass
        live = None
    face: float | None = None
    if live is not None and live >= gate.liveness_threshold:
        try:
            face = engine.match(probe, reference).score
        except Exception:  # noqa: BLE001
            face = None
    lv, fm = ev.engine_signals(
        live, face, liveness_min=gate.liveness_threshold, face_min=gate.face_threshold
    )
    return await record_engine_result(
        conn, case_id, rules, liveness=Liveness(lv), face=FaceMatch(fm), now=now
    )
