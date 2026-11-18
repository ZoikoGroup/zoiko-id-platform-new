"""WF-05 device risk (ZID-DTV-WF-05 DTV-22, §01, §02, §22, A09; QA-030 to QA-035; CR-116). Pure domain, no I/O.

A device observation is bounded risk evidence about a session: never who the person is, never consent to tracking, never fraud (§02).

- Signals come from a platform attestation (Play Integrity, App Attest; verified by an adapter) or are reported by the tenant's own app. Each is named.
- EMULATOR, ROOTED_OR_JAILBROKEN, APP_INTEGRITY_FAILED and AUTOMATION are REVIEW with a step-up or manual review recommended by policy (QA-031);
  nothing here is ever FAIL, and nothing blocks on its own.
- VPN_OR_PROXY alone is noted and never a reason to refuse (QA-033: privacy features are not risk on their own).
- No usable evidence (no attestation, or an adapter that failed) is INDETERMINATE (§21 DEVICE_SIGNAL_INDETERMINATE), never PASS and never FAIL.
- The device identifier is turned into a pseudonym keyed by tenant and by a rotation period: two tenants never get the same pseudonym for one device
  (QA-034), and the same tenant gets a new one each period. The raw identifier is never kept.
- An attestation answers a server nonce that is single-use, short-lived and bound to the case and session (QA-032 replay).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

NONCE_TTL = timedelta(minutes=5)


class DeviceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Signal(StrEnum):
    EMULATOR = "EMULATOR"
    ROOTED_OR_JAILBROKEN = "ROOTED_OR_JAILBROKEN"
    APP_INTEGRITY_FAILED = "APP_INTEGRITY_FAILED"
    AUTOMATION = "AUTOMATION"
    VPN_OR_PROXY = "VPN_OR_PROXY"


class Platform(StrEnum):
    WEB = "WEB"
    ANDROID = "ANDROID"
    IOS = "IOS"


class Attestation(StrEnum):
    VALID = (
        "VALID"  # the adapter verified the platform's statement for this nonce, app and audience
    )
    INVALID = "INVALID"  # it did not verify: integrity failed
    UNAVAILABLE = "UNAVAILABLE"  # nothing to verify, no adapter, or the adapter failed
    NOT_REQUESTED = "NOT_REQUESTED"  # web: no platform attestation exists


CONCERNING = frozenset(
    {Signal.EMULATOR, Signal.ROOTED_OR_JAILBROKEN, Signal.APP_INTEGRITY_FAILED, Signal.AUTOMATION}
)


@dataclass(frozen=True, slots=True)
class Evaluation:
    verdict: str  # PASS | REVIEW | INDETERMINATE (never FAIL)
    reasons: tuple[str, ...]
    recommendation: str  # NONE | STEP_UP_OR_REVIEW | RETRY_OR_ALTERNATIVE
    label: str  # what the person and the tenant see (A09)


def evaluate(platform: Platform, attestation: Attestation, signals: Iterable[Signal]) -> Evaluation:
    found = set(signals)
    if attestation is Attestation.INVALID:
        found.add(Signal.APP_INTEGRITY_FAILED)
    concerning = sorted(s.value for s in found & CONCERNING)
    noted = ["VPN_OR_PROXY_NOTED"] if Signal.VPN_OR_PROXY in found else []
    if concerning:
        return Evaluation(
            "REVIEW", (*concerning, *noted), "STEP_UP_OR_REVIEW", "Risk review required"
        )
    if platform is not Platform.WEB and attestation is not Attestation.VALID:
        return Evaluation(
            "INDETERMINATE",
            ("DEVICE_SIGNAL_INDETERMINATE", *noted),
            "RETRY_OR_ALTERNATIVE",
            "Device could not be checked",
        )
    return Evaluation("PASS", tuple(noted), "NONE", "No concerning signal")


def pseudonym(
    key: bytes, tenant_id: str, raw_device_ref: str, *, now: datetime, rotation_days: int
) -> str:
    """Keyed by tenant and rotation period; the raw reference is never kept. `dp1.<period>.<hex>`."""
    if not key:
        raise DeviceError("KEY_REQUIRED")
    if not 1 <= rotation_days <= 366:
        raise DeviceError("ROTATION_INVALID")
    if not 8 <= len(raw_device_ref) <= 256:
        raise DeviceError("DEVICE_REF_INVALID")
    period = int(now.timestamp() // (rotation_days * 86400))
    digest = hmac.new(
        key, f"{tenant_id}|{period}|{raw_device_ref}".encode(), hashlib.sha256
    ).hexdigest()
    return f"dp1.{period}.{digest[:40]}"


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def nonce_hash(key: bytes, nonce: str) -> bytes:
    if not key:
        raise DeviceError("KEY_REQUIRED")
    return hmac.new(key, nonce.encode(), hashlib.sha256).digest()


def check_nonce(*, expires_at: datetime, consumed: bool, session_ok: bool, now: datetime) -> None:
    if consumed:
        raise DeviceError("NONCE_REPLAYED")
    if now >= expires_at:
        raise DeviceError("NONCE_EXPIRED")
    if not session_ok:
        raise DeviceError("NONCE_SESSION_MISMATCH")


__all__ = [
    "CONCERNING", "NONCE_TTL", "Attestation", "DeviceError", "Evaluation", "Platform", "Signal", "check_nonce", "evaluate", "new_nonce",
    "nonce_hash", "pseudonym",
]  # fmt: skip
