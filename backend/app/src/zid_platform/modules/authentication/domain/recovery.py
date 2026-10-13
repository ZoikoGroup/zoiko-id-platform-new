"""Account recovery classes and gates (ZID-AUTH-001 §4, §14; AUTH-13). Pure domain, no I/O.

    RC-1  ordinary account, recovery code      -> ready at once (low risk)
    RC-2  no trusted factor                    -> identity re-proofing + delay
    RC-3  privileged administrator             -> re-proofing + second administrator + cooling-off
    RC-4  sovereign / lockbox account          -> IAL-Z3 re-proofing + k >= 2 of the account's
                                                  *designated* approvers + cooling-off >= 7 days

A recovery code never recovers a privileged administrator on its own, and no single operator can
complete a recovery: support may only *open* a case; the gates are re-proofing (the applicant),
an approval by a different administrator, and time. Notifications are evidence that the owner was
told, never proof of identity (§14.2).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

RECOVERY_TICKET_TTL = timedelta(minutes=15)  # §6.4 "Recovery token/session <= 15 minutes"
CASE_TTL = timedelta(days=7)
LOCKBOX_CASE_TTL = timedelta(days=35)  # >= cooling-off + time to re-proof and gather approvals
COOLING_OFF = {"RC-2": timedelta(hours=24), "RC-3": timedelta(hours=72), "RC-4": timedelta(days=7)}
MIN_LOCKBOX_COOLING_OFF = timedelta(days=7)
MAX_LOCKBOX_COOLING_OFF = timedelta(days=30)
REPROOF_LEVEL = "IAL-Z2"
REPROOF_LEVELS = {"RC-2": "IAL-Z2", "RC-3": "IAL-Z2", "RC-4": "IAL-Z3"}
CODES_PER_BATCH = 10
# Holding authority for any of these makes an account privileged (RC-3).
PRIVILEGED_ACTIONS = [
    "tenant.identity_provider.change",
    "tenant.mfa_policy.change",
    "tenant.owner.change",
    "provisioning.source.manage",
    "signals.stream.manage",
    "security_state.override",
    "verification.profile.manage",
    "verification.adapter.manage",
    "authentication.recovery.approve",
    "operator.jit.elevate",
]
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion


class RecoveryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def new_code() -> str:
    """100 bits of entropy in 4 groups of 5 (one-time; stored only as a hash)."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(20))
    return "-".join(raw[i : i + 5] for i in range(0, 20, 5))


def normalize_code(code: str) -> str:
    raw = code.replace("-", "").replace(" ", "").upper()
    return "-".join(raw[i : i + 5] for i in range(0, 20, 5)) if len(raw) == 20 else raw


@dataclass(frozen=True, slots=True)
class LockboxPolicy:
    """RC-4: who may approve this account's recovery, how many must, and how long it waits."""

    principal_id: str
    approvers: tuple[str, ...]
    k: int
    cooling_off: timedelta
    revision: int = 1

    def __post_init__(self) -> None:
        if not 2 <= len(set(self.approvers)) == len(self.approvers) <= 5:
            raise RecoveryError("LOCKBOX_POLICY_INVALID", "2-5 distinct approvers")
        if self.principal_id in self.approvers:
            raise RecoveryError("LOCKBOX_POLICY_INVALID", "never one's own approver")
        if not 2 <= self.k <= len(self.approvers):
            raise RecoveryError("LOCKBOX_POLICY_INVALID", "2 <= k <= approvers")
        if not MIN_LOCKBOX_COOLING_OFF <= self.cooling_off <= MAX_LOCKBOX_COOLING_OFF:
            raise RecoveryError("LOCKBOX_POLICY_INVALID", "cooling-off 7-30 days")

    def quorum(self, counted: Sequence[str]) -> bool:
        """Approved only by enough *designated* approvers: other administrators do not count."""
        return len(set(counted) & set(self.approvers)) >= self.k


def classify(*, privileged: bool, with_code: bool, lockbox: bool = False) -> tuple[str, str]:
    """(recovery class, method)."""
    if lockbox:
        return "RC-4", "REPROOF"  # neither a code nor a single administrator is ever enough
    if privileged:
        return "RC-3", "REPROOF"  # AUTH-13: a code alone never recovers an administrator
    if with_code:
        return "RC-1", "RECOVERY_CODE"
    return "RC-2", "REPROOF"


@dataclass(frozen=True, slots=True)
class Gates:
    status: str  # PENDING_REPROOF | PENDING_APPROVAL | COOLING_OFF | READY
    cooling_off_until: datetime | None
    missing: tuple[str, ...]


def evaluate(
    recovery_class: str,
    *,
    reproof_at: datetime | None,
    approval_state: str | None,
    cooling_off_until: datetime | None,
    now: datetime,
    cooling_off: timedelta | None = None,
) -> Gates:
    if recovery_class == "RC-1":
        return Gates("READY", None, ())
    if reproof_at is None:
        return Gates("PENDING_REPROOF", None, ("REPROOF_REQUIRED",))
    if recovery_class == "RC-3" and approval_state != "APPROVED":
        return Gates("PENDING_APPROVAL", None, ("SECOND_ADMIN_APPROVAL_REQUIRED",))
    if recovery_class == "RC-4" and approval_state != "APPROVED":
        return Gates("PENDING_APPROVAL", None, ("DESIGNATED_APPROVERS_REQUIRED",))
    wait = cooling_off or COOLING_OFF[recovery_class]
    until = cooling_off_until or now + wait  # the clock starts once gated
    if now < until:
        return Gates("COOLING_OFF", until, ("COOLING_OFF",))
    return Gates("READY", until, ())


def transaction_context(recovery_id: str) -> str:
    """What the RC-3 approval is bound to (the protected helper's canonical form)."""
    return f"authentication.recovery.approve|recovery_case:{recovery_id}|complete"
