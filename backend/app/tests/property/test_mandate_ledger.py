"""Property tests for the mandate ledger (MAND-08..12, MAND-14, MAND-24; DEC-C5; P0-03).

Hypothesis drives random sequences of reserve / commit / release / expiry / reconciliation against
one mandate and checks, after every step, that committed + reserved never exceeds any limit,
nothing goes negative, and retries never consume twice. The database adapter must run these same
transitions under a row lock; the 10k-concurrency test (AT-04) proves that part against Postgres.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Balance,
    Budget,
    LedgerError,
    MandateStatus,
    Reservation,
    ReservationStatus,
    ReserveRequest,
    SideEffect,
    commit,
    expire_or_reconcile,
    invariant_holds,
    is_exhausted,
    release,
    reserve,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
BUDGET = Budget(
    currency="GBP",
    value_limit=Decimal("1000.00"),
    count_limit=20,
    data_limit_bytes=5000,
    per_transaction_value_limit=Decimal("300.00"),
)

amounts = st.builds(
    Amounts,
    value=st.decimals(min_value=0, max_value=400, places=2, allow_nan=False, allow_infinity=False),
    count=st.integers(0, 3),
    data_bytes=st.integers(0, 1500),
)
ops = st.lists(
    st.one_of(
        st.tuples(st.just("reserve"), amounts, st.integers(0, 15)),  # key index: reuse = retry
        st.tuples(st.just("commit"), st.integers(0, 40), st.booleans()),  # recheck passed?
        st.tuples(st.just("release"), st.integers(0, 40)),
        st.tuples(
            st.just("sweep"), st.integers(0, 40), st.sampled_from(list(SideEffect)), st.booleans()
        ),
    ),
    max_size=60,
)


def _reserve(balance: Balance, by_key: dict[str, Reservation], a: Amounts, k: int) -> Balance:
    key = f"k{k}"
    req = ReserveRequest(key, f"ctx-{key}-{a}", a, currency="GBP")
    try:
        balance, res = reserve(
            BUDGET,
            balance,
            req,
            existing=by_key.get(key),
            new_reservation_id=f"r{len(by_key)}",
            mandate_status=MandateStatus.ACTIVE,
            now=NOW,
            valid_to=None,
            expires_at=NOW + timedelta(minutes=5),
        )
        by_key[key] = res
    except LedgerError:
        pass
    return balance


@settings(max_examples=500, deadline=None)
@given(ops)
def test_ledger_invariant_survives_any_operation_sequence(sequence: list[tuple]) -> None:  # type: ignore[type-arg]
    balance = Balance()
    by_key: dict[str, Reservation] = {}
    for op in sequence:
        ids = list(by_key)
        if op[0] == "reserve":
            balance = _reserve(balance, by_key, op[1], op[2])
        elif ids:
            key = ids[op[1] % len(ids)]
            res = by_key[key]
            try:
                if op[0] == "commit":
                    balance, res = commit(balance, res, now=NOW, recheck_passed=op[2])
                elif op[0] == "release":
                    balance, res = release(balance, res)
                else:
                    balance, res = expire_or_reconcile(
                        balance,
                        res,
                        now=NOW + timedelta(minutes=10),
                        side_effect=op[2],
                        high_risk=op[3],
                    )
            except LedgerError:
                pass
            by_key[key] = res
        assert invariant_holds(BUDGET, balance), balance
        # reserved always equals the sum of open reservations: nothing leaks, nothing is double-counted
        open_res = [
            r.amounts
            for r in by_key.values()
            if r.status in (ReservationStatus.RESERVED, ReservationStatus.RECONCILIATION_REQUIRED)
        ]
        assert balance.reserved == sum(open_res, Amounts())
        committed = [r.amounts for r in by_key.values() if r.status is ReservationStatus.COMMITTED]
        assert balance.committed == sum(committed, Amounts())


def test_same_key_retries_create_one_reservation() -> None:
    """MAND-10: 100 same-key retries create one reservation; a changed payload conflicts."""
    balance, by_key = Balance(), {}  # type: ignore[var-annotated]
    a = Amounts(Decimal("10.00"), 1, 0)
    for _ in range(100):
        balance = _reserve(balance, by_key, a, 1)
    assert len(by_key) == 1 and balance.reserved == a
    with pytest.raises(LedgerError) as e:
        reserve(
            BUDGET,
            balance,
            ReserveRequest("k1", "different", Amounts(Decimal("11.00"), 1, 0), "GBP"),
            existing=by_key["k1"],
            new_reservation_id="x",
            mandate_status=MandateStatus.ACTIVE,
            now=NOW,
            valid_to=None,
            expires_at=NOW,
        )
    assert e.value.code == "RESERVATION_CONFLICT"


def test_commit_is_idempotent() -> None:
    """MAND-11: repeated commit changes the balance once."""
    balance, by_key = Balance(), {}  # type: ignore[var-annotated]
    balance = _reserve(balance, by_key, Amounts(Decimal("50.00"), 1, 10), 1)
    res = by_key["k1"]
    once, res = commit(balance, res, now=NOW, recheck_passed=True)
    twice, res = commit(once, res, now=NOW, recheck_passed=True)
    assert once == twice and twice.committed == Amounts(Decimal("50.00"), 1, 10)


def test_revocation_between_reserve_and_commit_blocks_commit() -> None:
    """MAND-15 / AUTHZ-14: a failed commit recheck never consumes."""
    balance, by_key = Balance(), {}  # type: ignore[var-annotated]
    balance = _reserve(balance, by_key, Amounts(Decimal("50.00"), 1, 0), 1)
    with pytest.raises(LedgerError) as e:
        commit(balance, by_key["k1"], now=NOW, recheck_passed=False)
    assert e.value.code == "AUTHORITY_REVOKED_DURING_EXECUTION"


def test_unknown_high_risk_outcome_keeps_capacity_blocked() -> None:
    """MAND-14: lease expiry alone never frees an uncertain high-risk reservation."""
    balance, by_key = Balance(), {}  # type: ignore[var-annotated]
    balance = _reserve(balance, by_key, Amounts(Decimal("200.00"), 1, 0), 1)
    later = NOW + timedelta(hours=1)
    b2, r2 = expire_or_reconcile(
        balance, by_key["k1"], now=later, side_effect=SideEffect.UNKNOWN, high_risk=True
    )
    assert (
        r2.status is ReservationStatus.RECONCILIATION_REQUIRED and b2.reserved == balance.reserved
    )
    b3, r3 = expire_or_reconcile(
        b2, r2, now=later, side_effect=SideEffect.SUCCEEDED, high_risk=True
    )
    assert r3.status is ReservationStatus.COMMITTED and b3.committed.value == Decimal("200.00")


def test_expired_unexecuted_reservation_releases_exactly_once() -> None:
    """MAND-12."""
    balance, by_key = Balance(), {}  # type: ignore[var-annotated]
    balance = _reserve(balance, by_key, Amounts(Decimal("20.00"), 1, 0), 1)
    later = NOW + timedelta(hours=1)
    b2, r2 = expire_or_reconcile(
        balance, by_key["k1"], now=later, side_effect=SideEffect.NOT_EXECUTED, high_risk=True
    )
    b3, r3 = expire_or_reconcile(
        b2, r2, now=later, side_effect=SideEffect.NOT_EXECUTED, high_risk=True
    )
    assert r3.status is ReservationStatus.EXPIRED and b2 == b3 and b3.reserved == Amounts()


@pytest.mark.parametrize(
    ("a", "currency", "code"),
    [
        (Amounts(Decimal(-1), 0, 0), "GBP", "MANDATE_VALUE_INVALID"),  # MAND-24 negative
        (Amounts(Decimal(0), -1, 0), "GBP", "MANDATE_VALUE_INVALID"),
        (Amounts(Decimal("NaN"), 0, 0), "GBP", "MANDATE_VALUE_INVALID"),
        (Amounts(Decimal("Infinity"), 0, 0), "GBP", "MANDATE_VALUE_INVALID"),
        (Amounts(Decimal(10), 0, 0), "EUR", "MANDATE_CURRENCY_MISMATCH"),  # MAND-23
        (Amounts(Decimal("300.01"), 0, 0), "GBP", "MANDATE_VALUE_LIMIT"),  # per-transaction ceiling
        (Amounts(Decimal(0), 21, 0), "GBP", "MANDATE_COUNT_LIMIT"),
        (Amounts(Decimal(0), 0, 5001), "GBP", "MANDATE_DATA_LIMIT"),
    ],
)
def test_bad_or_oversized_requests_are_rejected(a: Amounts, currency: str, code: str) -> None:
    with pytest.raises(LedgerError) as e:
        reserve(
            BUDGET,
            Balance(),
            ReserveRequest("k", "h", a, currency),
            existing=None,
            new_reservation_id="r",
            mandate_status=MandateStatus.ACTIVE,
            now=NOW,
            valid_to=None,
            expires_at=NOW,
        )
    assert e.value.code == code


def test_one_dimension_cannot_compensate_for_another() -> None:
    """§11.1: value fits, count does not -> refused."""
    budget = Budget(currency="GBP", value_limit=Decimal(1000), count_limit=1)
    balance = Balance(committed=Amounts(Decimal(0), 1, 0))
    with pytest.raises(LedgerError) as e:
        reserve(
            budget,
            balance,
            ReserveRequest("k", "h", Amounts(Decimal(1), 1, 0), "GBP"),
            existing=None,
            new_reservation_id="r",
            mandate_status=MandateStatus.ACTIVE,
            now=NOW,
            valid_to=None,
            expires_at=NOW,
        )
    assert e.value.code == "MANDATE_COUNT_LIMIT"


def test_inactive_or_expired_mandate_cannot_reserve() -> None:
    for status, valid_to in ((MandateStatus.PAUSED, None), (MandateStatus.ACTIVE, NOW)):
        with pytest.raises(LedgerError) as e:
            reserve(
                BUDGET,
                Balance(),
                ReserveRequest("k", "h", Amounts(), "GBP"),
                existing=None,
                new_reservation_id="r",
                mandate_status=status,
                now=NOW,
                valid_to=valid_to,
                expires_at=NOW,
            )
        assert e.value.code == "MANDATE_NOT_ACTIVE"


def test_exhaustion_needs_every_bounded_dimension_spent() -> None:
    budget = Budget(currency="GBP", value_limit=Decimal(100), count_limit=2)
    assert not is_exhausted(budget, Balance(committed=Amounts(Decimal(100), 1, 0)))
    assert is_exhausted(budget, Balance(committed=Amounts(Decimal(100), 2, 0)))
    assert not is_exhausted(Budget(), Balance())
