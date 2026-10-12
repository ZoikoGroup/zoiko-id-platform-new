"""Mandate ledger domain rules (ZID-MANDATE-001 §9-15, §18; DEC-C5; P0-03).

Core invariant, for every constrained dimension:  committed >= 0, reserved >= 0,
committed + reserved <= configured limit. There is no writable "remaining" field: remaining is
derived at read time. The persistence adapter (Phase C slice 2) runs these exact transitions inside
one transaction under a row lock on the balance; this module owns the arithmetic and the state
machines so they can be property-tested without a database. Pure domain, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

# ---------------------------------------------------------------- mandate lifecycle (§10)


class MandateStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXHAUSTED = "EXHAUSTED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


TERMINAL_MANDATE_STATES = frozenset(
    {MandateStatus.EXHAUSTED, MandateStatus.COMPLETED, MandateStatus.EXPIRED, MandateStatus.REVOKED}
)

_MANDATE_TRANSITIONS: dict[MandateStatus, frozenset[MandateStatus]] = {
    MandateStatus.PENDING_APPROVAL: frozenset(
        {MandateStatus.ACTIVE, MandateStatus.REVOKED, MandateStatus.EXPIRED}
    ),
    MandateStatus.ACTIVE: frozenset(
        {
            MandateStatus.PAUSED,
            MandateStatus.EXHAUSTED,
            MandateStatus.COMPLETED,
            MandateStatus.EXPIRED,
            MandateStatus.REVOKED,
        }
    ),
    MandateStatus.PAUSED: frozenset(
        {MandateStatus.ACTIVE, MandateStatus.EXPIRED, MandateStatus.REVOKED}
    ),
}


class LedgerError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def mandate_transition(
    current: MandateStatus, target: MandateStatus, *, source_valid: bool = True
) -> MandateStatus:
    """Terminal states never move. PAUSED cannot resume while the source grant/root is invalid."""
    if target not in _MANDATE_TRANSITIONS.get(current, frozenset()):
        raise LedgerError("MANDATE_INVALID_TRANSITION", f"{current} -> {target}")
    if current is MandateStatus.PAUSED and target is MandateStatus.ACTIVE and not source_valid:
        raise LedgerError("MANDATE_SOURCE_INVALID")
    return target


# ---------------------------------------------------------------- budget and balance (§11)


@dataclass(frozen=True, slots=True)
class Budget:
    """Configured limits. `None` = explicitly unbounded for that dimension."""

    currency: str | None = None
    value_limit: Decimal | None = None
    count_limit: int | None = None
    data_limit_bytes: int | None = None
    per_transaction_value_limit: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("value_limit", "per_transaction_value_limit"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, Decimal) or not v.is_finite() or v < 0):
                raise LedgerError("MANDATE_BUDGET_INVALID", name)
        for name in ("count_limit", "data_limit_bytes"):
            v = getattr(self, name)
            if v is not None and v < 0:
                raise LedgerError("MANDATE_BUDGET_INVALID", name)
        if (
            self.value_limit is not None or self.per_transaction_value_limit is not None
        ) and not self.currency:
            raise LedgerError("MANDATE_BUDGET_INVALID", "a value limit needs a currency")


@dataclass(frozen=True, slots=True)
class Amounts:
    value: Decimal = Decimal(0)
    count: int = 0
    data_bytes: int = 0

    def __add__(self, other: Amounts) -> Amounts:
        return Amounts(
            self.value + other.value, self.count + other.count, self.data_bytes + other.data_bytes
        )

    def __sub__(self, other: Amounts) -> Amounts:
        return Amounts(
            self.value - other.value, self.count - other.count, self.data_bytes - other.data_bytes
        )

    def non_negative(self) -> bool:
        return self.value >= 0 and self.count >= 0 and self.data_bytes >= 0


@dataclass(frozen=True, slots=True)
class Balance:
    committed: Amounts = Amounts()
    reserved: Amounts = Amounts()
    ledger_version: int = 0


def remaining(budget: Budget, balance: Balance) -> dict[str, Decimal | int | None]:
    """Derived, informational only; never accepted back as authority."""
    used = balance.committed + balance.reserved
    return {
        "value": None if budget.value_limit is None else budget.value_limit - used.value,
        "count": None if budget.count_limit is None else budget.count_limit - used.count,
        "data_bytes": None
        if budget.data_limit_bytes is None
        else budget.data_limit_bytes - used.data_bytes,
    }


def invariant_holds(budget: Budget, balance: Balance) -> bool:
    used = balance.committed + balance.reserved
    return (
        balance.committed.non_negative()
        and balance.reserved.non_negative()
        and (budget.value_limit is None or used.value <= budget.value_limit)
        and (budget.count_limit is None or used.count <= budget.count_limit)
        and (budget.data_limit_bytes is None or used.data_bytes <= budget.data_limit_bytes)
    )


def is_exhausted(budget: Budget, balance: Balance) -> bool:
    """Every configured consumable dimension is fully committed (§10: exhaustion is automatic)."""
    checks: list[bool] = []
    if budget.value_limit is not None:
        checks.append(balance.committed.value >= budget.value_limit)
    if budget.count_limit is not None:
        checks.append(balance.committed.count >= budget.count_limit)
    if budget.data_limit_bytes is not None:
        checks.append(balance.committed.data_bytes >= budget.data_limit_bytes)
    return bool(checks) and all(checks)


# ---------------------------------------------------------------- reservations (§12-15)


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TERMINAL_RESERVATION_STATES = frozenset(
    {ReservationStatus.COMMITTED, ReservationStatus.RELEASED, ReservationStatus.EXPIRED}
)


@dataclass(frozen=True, slots=True)
class ReserveRequest:
    idempotency_key: str
    transaction_context_hash: str
    amounts: Amounts
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    idempotency_key: str
    transaction_context_hash: str
    amounts: Amounts
    status: ReservationStatus
    expires_at: datetime


def check_reserve(
    budget: Budget,
    balance: Balance,
    req: ReserveRequest,
    *,
    mandate_status: MandateStatus,
    now: datetime,
    valid_to: datetime | None,
) -> str | None:
    """Why the reservation can't be made (an error code), or None when every dimension fits."""
    if mandate_status is not MandateStatus.ACTIVE or (valid_to is not None and now >= valid_to):
        return "MANDATE_NOT_ACTIVE"
    a = req.amounts
    if not isinstance(a.value, Decimal) or not a.value.is_finite():
        return "MANDATE_VALUE_INVALID"
    if not a.non_negative():
        return "MANDATE_VALUE_INVALID"  # negative values are rejected, never treated as refunds
    value_bounded = budget.value_limit is not None or budget.per_transaction_value_limit is not None
    if a.value > 0 and value_bounded and req.currency != budget.currency:
        return "MANDATE_CURRENCY_MISMATCH"
    if (
        budget.per_transaction_value_limit is not None
        and a.value > budget.per_transaction_value_limit
    ):
        return "MANDATE_VALUE_LIMIT"
    used = balance.committed + balance.reserved + a
    if budget.value_limit is not None and used.value > budget.value_limit:
        return "MANDATE_VALUE_LIMIT"
    if budget.count_limit is not None and used.count > budget.count_limit:
        return "MANDATE_COUNT_LIMIT"
    if budget.data_limit_bytes is not None and used.data_bytes > budget.data_limit_bytes:
        return "MANDATE_DATA_LIMIT"
    return None


def reserve(
    budget: Budget,
    balance: Balance,
    req: ReserveRequest,
    *,
    existing: Reservation | None,
    new_reservation_id: str,
    mandate_status: MandateStatus,
    now: datetime,
    valid_to: datetime | None,
    expires_at: datetime,
) -> tuple[Balance, Reservation]:
    """Atomic reserve (§12.1). `existing` is the reservation already stored under this key, if any.

    Same key + same request returns the original reservation and leaves the balance unchanged;
    same key + different request is a conflict and never creates a second reservation (§15).
    """
    if existing is not None:
        same = (
            existing.transaction_context_hash == req.transaction_context_hash
            and existing.amounts == req.amounts
        )
        if not same:
            raise LedgerError(
                "RESERVATION_CONFLICT", "idempotency key reused for a different request"
            )
        return balance, existing
    problem = check_reserve(
        budget, balance, req, mandate_status=mandate_status, now=now, valid_to=valid_to
    )
    if problem:
        raise LedgerError(problem)
    res = Reservation(
        new_reservation_id,
        req.idempotency_key,
        req.transaction_context_hash,
        req.amounts,
        ReservationStatus.RESERVED,
        expires_at,
    )
    new = replace(
        balance, reserved=balance.reserved + req.amounts, ledger_version=balance.ledger_version + 1
    )
    return new, res


def commit(
    balance: Balance, res: Reservation, *, now: datetime, recheck_passed: bool
) -> tuple[Balance, Reservation]:
    """Move a reservation's amounts from reserved to committed (§13). Repeats are no-ops.

    `recheck_passed` is the R2/R3 commit recheck (grant/root, security revision, mandate state,
    approval, transaction context). Commit after a side effect that already happened goes through
    `reconcile` instead, which records consumption even if revocation arrived late (§18.1).
    """
    if res.status is ReservationStatus.COMMITTED:
        return balance, res  # idempotent: terminal state returned, no second balance change
    if res.status is not ReservationStatus.RESERVED:
        raise LedgerError("RESERVATION_ALREADY_TERMINAL", str(res.status))
    if now >= res.expires_at:
        raise LedgerError("RESERVATION_EXPIRED")
    if not recheck_passed:
        raise LedgerError("AUTHORITY_REVOKED_DURING_EXECUTION")
    return _settle(balance, res, ReservationStatus.COMMITTED)


def release(balance: Balance, res: Reservation) -> tuple[Balance, Reservation]:
    """Caller abandons before the side effect. Repeats are no-ops."""
    if res.status in (ReservationStatus.RELEASED, ReservationStatus.EXPIRED):
        return balance, res
    if res.status is not ReservationStatus.RESERVED:
        raise LedgerError("RESERVATION_ALREADY_TERMINAL", str(res.status))
    return _settle(balance, res, ReservationStatus.RELEASED)


class SideEffect(StrEnum):
    """What the target system reports for the stable execution ID (§13-14)."""

    SUCCEEDED = "SUCCEEDED"
    NOT_EXECUTED = "NOT_EXECUTED"
    UNKNOWN = "UNKNOWN"


def expire_or_reconcile(
    balance: Balance, res: Reservation, *, now: datetime, side_effect: SideEffect, high_risk: bool
) -> tuple[Balance, Reservation]:
    """Lease sweeper and crash reconciliation (§14).

    Commit wins if the side effect happened; release wins if it provably did not. An unknown
    outcome on a high-risk/value reservation stays blocked: capacity is never released just
    because the lease ran out.
    """
    if res.status in TERMINAL_RESERVATION_STATES:
        return balance, res
    if side_effect is SideEffect.SUCCEEDED:
        return _settle(balance, res, ReservationStatus.COMMITTED)
    if res.status is ReservationStatus.RESERVED and now < res.expires_at:
        return balance, res  # lease still running; nothing to do yet
    if side_effect is SideEffect.NOT_EXECUTED:
        return _settle(balance, res, ReservationStatus.EXPIRED)
    if high_risk:
        if res.status is ReservationStatus.RECONCILIATION_REQUIRED:
            return balance, res
        return replace(balance, ledger_version=balance.ledger_version + 1), replace(
            res, status=ReservationStatus.RECONCILIATION_REQUIRED
        )
    return _settle(balance, res, ReservationStatus.EXPIRED)


def _settle(
    balance: Balance, res: Reservation, outcome: ReservationStatus
) -> tuple[Balance, Reservation]:
    reserved = balance.reserved - res.amounts
    committed = (
        balance.committed + res.amounts
        if outcome is ReservationStatus.COMMITTED
        else balance.committed
    )
    if not reserved.non_negative():
        raise LedgerError("LEDGER_CORRUPTION", "reserved would go negative")
    new = Balance(committed=committed, reserved=reserved, ledger_version=balance.ledger_version + 1)
    return new, replace(res, status=outcome)
