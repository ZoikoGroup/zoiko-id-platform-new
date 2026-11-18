"""WF-05 device observations on a case (ZID-DTV-WF-05 DTV-22, §19 POST /v1/device-observations, §22; QA-030 to QA-035; CR-116).

new_nonce   a server nonce for a platform attestation, bound to the case and session, single-use, 5 minutes
observe     one observation: only with DEVICE_RISK cleared for the case (a disabled method writes nothing, QA-030); the nonce is consumed once;
            the attestation is verified by the deployment's adapter (none ships: UNAVAILABLE); the raw device reference becomes a pseudonym and
            is dropped; DEVICE_RISK_OBSERVED is recorded with REVIEW, PASS or INDETERMINATE, never FAIL
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.verification.application import check_cases
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import device_risk as dr
from zid_platform.modules.verification.domain.assertions import Assertion

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
CLIENT_SOURCE = "client-reported"


@dataclass(frozen=True, slots=True)
class Settings:
    rotation_days: int
    platforms: frozenset[dr.Platform]


@lru_cache(maxsize=1)
def settings() -> Settings:
    doc: dict[str, Any] = (
        yaml.safe_load((CONFIG / "verification/device-risk.yaml").read_text(encoding="utf-8")) or {}
    )
    return Settings(
        int(doc.get("rotation_days", 30)),
        frozenset(dr.Platform(p) for p in doc.get("platforms") or ()),
    )


@dataclass(frozen=True, slots=True)
class Verified:
    status: dr.Attestation
    signals: tuple[dr.Signal, ...] = ()
    source: str | None = None  # the adapter, engine@version


class AttestationVerifier(Protocol):
    """Play Integrity or App Attest, checked server side against the nonce, the app id and the audience. The only place a network call happens."""

    async def verify(self, platform: dr.Platform, token: str, nonce: str) -> Verified: ...


async def _keys() -> tuple[bytes, bytes]:
    mac = current_runtime().mac_key
    return await asyncio.gather(
        asyncio.to_thread(mac.mac, b"zid.wf05.device-pseudonym.v1"),
        asyncio.to_thread(mac.mac, b"zid.wf05.device-nonce.v1"),
    )


async def _cleared_case(conn: AsyncConnection, case_id: str) -> cc.Case:
    case = await check_cases.get_case(conn, case_id)
    if case.status is cc.CaseStatus.CLOSED:
        raise cc.CaseError("CASE_CLOSED")
    if cm.CheckMethod.DEVICE_RISK not in case.available:
        raise cc.CaseError(
            "METHOD_NOT_AVAILABLE_FOR_CASE", "DEVICE_RISK was not cleared for this case"
        )
    return case


async def new_nonce(
    conn: AsyncConnection, case_id: str, *, session_ref: str, now: datetime
) -> dict[str, Any]:
    case = await _cleared_case(conn, case_id)
    _, nonce_key = await _keys()
    nonce = dr.new_nonce()
    await conn.execute(
        text(
            "INSERT INTO verify.check_device_nonce (nonce_id, tenant_id, case_id, session_ref, nonce_hash, expires_at, created_at) "
            "VALUES (:id, :t, :c, :s, :h, :ex, :at)"
        ),
        {
            "id": uuid.uuid4(), "t": uuid.UUID(case.tenant_id), "c": uuid.UUID(case_id), "s": session_ref,
            "h": dr.nonce_hash(nonce_key, nonce), "ex": now + dr.NONCE_TTL, "at": now,
        },
    )  # fmt: skip
    return {"nonce": nonce, "expires_at": (now + dr.NONCE_TTL).isoformat()}


async def _consume(
    conn: AsyncConnection, case_id: str, nonce: str, session_ref: str, key: bytes, now: datetime
) -> None:
    row = (
        await conn.execute(
            text(
                "SELECT nonce_id, case_id, session_ref, expires_at, consumed_at FROM verify.check_device_nonce WHERE nonce_hash = :h FOR UPDATE"
            ),
            {"h": dr.nonce_hash(key, nonce)},
        )
    ).first()
    if row is None or str(row.case_id) != case_id:
        raise dr.DeviceError("NONCE_UNKNOWN")
    dr.check_nonce(
        expires_at=row.expires_at,
        consumed=row.consumed_at is not None,
        session_ok=row.session_ref == session_ref,
        now=now,
    )
    await conn.execute(
        text("UPDATE verify.check_device_nonce SET consumed_at = :n WHERE nonce_id = :id"),
        {"n": now, "id": row.nonce_id},
    )


async def observe(
    conn: AsyncConnection,
    case_id: str,
    *,
    session_ref: str,
    platform: dr.Platform,
    device_ref: str,
    reported: tuple[dr.Signal, ...],
    attestation_token: str | None,
    nonce: str | None,
    verifier: AttestationVerifier | None,
    now: datetime,
) -> dict[str, Any]:
    case = await _cleared_case(conn, case_id)
    cfg = settings()
    if platform not in cfg.platforms:
        raise dr.DeviceError("PLATFORM_NOT_PERMITTED")
    pseudonym_key, nonce_key = await _keys()
    status, signals, source = (
        (
            dr.Attestation.NOT_REQUESTED
            if platform is dr.Platform.WEB
            else dr.Attestation.UNAVAILABLE
        ),
        list(reported),
        None,
    )
    if attestation_token:
        if not nonce:
            raise dr.DeviceError("NONCE_REQUIRED")
        await _consume(conn, case_id, nonce, session_ref, nonce_key, now)
        if verifier is not None:
            try:
                v = await verifier.verify(platform, attestation_token, nonce)
                status, source = v.status, v.source
                signals.extend(v.signals)
            except Exception:  # noqa: BLE001 - an adapter that fails is UNAVAILABLE, never a pass or a fail
                status = dr.Attestation.UNAVAILABLE
    ev = dr.evaluate(platform, status, signals)
    observation = dr.pseudonym(
        pseudonym_key, case.tenant_id, device_ref, now=now, rotation_days=cfg.rotation_days
    )
    await conn.execute(
        text(
            "INSERT INTO verify.check_device_observation (observation_id, tenant_id, case_id, pseudonym, platform, attestation, signals, verdict, "
            "reasons, observed_at) VALUES (:id, :t, :c, :p, :pl, :a, :s, :v, :r, :at)"
        ),
        {
            "id": uuid.uuid4(), "t": uuid.UUID(case.tenant_id), "c": uuid.UUID(case_id), "p": observation, "pl": platform.value,
            "a": status.value, "s": sorted({s.value for s in signals}), "v": ev.verdict, "r": list(ev.reasons), "at": now,
        },
    )  # fmt: skip
    await check_cases.record_assertion(
        conn, case_id, assertion=Assertion.DEVICE_RISK_OBSERVED, verdict=cc.Verdict(ev.verdict), method=cm.CheckMethod.DEVICE_RISK,
        source=source or CLIENT_SOURCE, reason_codes=ev.reasons, confidence=None, observed_at=now, expires_at=None, recorded_by="device-risk",
        now=now,
    )  # fmt: skip
    return {
        "outcome": ev.verdict,
        "label": ev.label,
        "reasons": list(ev.reasons),
        "recommendation": ev.recommendation,
        "attestation": status.value,
        "note": "A device observation is risk evidence about this session only. It does not say who the person is, and it is never a finding of fraud.",
    }


__all__ = ["AttestationVerifier", "Settings", "Verified", "new_nonce", "observe", "settings"]
