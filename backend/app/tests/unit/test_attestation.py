"""Official-register attestations (ZID-RCG-003 RCG-G02): certified means a fresh, in-force, exact-scope snapshot, never a stale or missing one;
history is never rewritten; losing one certification touches only its own scope."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain.attestation import (
    DEFAULT_STALE_AFTER,
    AttestationError,
    Decision,
    Outcome,
    Scope,
    Snapshot,
    changes,
    check_snapshot,
    decide,
)
from zid_platform.modules.verification.domain.certification import CertStatus

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
SCOPE = Scope("PARTNER-A", "UK_DVS", "GB", "RTR")


def _snap(**kw: Any) -> Snapshot:
    base: dict[str, Any] = {
        "snapshot_id": "s1",
        "scope": SCOPE,
        "scheme_version": "2026.1",
        "certificate_ref": "CERT-1",
        "component_only": False,
        "status": CertStatus.ACTIVE,
        "valid_from": date(2026, 1, 1),
        "valid_until": date(2027, 1, 1),
        "register_url": "https://register.example.test/entry/1",
        "register_entry_id": "ENTRY-1",
        "cab": "CAB-X",
        "fetched_at": T0,
        "proof_hash": "a" * 64,
        "evidence_locator": "vault:attestations/s1",
        "recorded_by": "ravi",
        "recorded_at": T0,
    }
    base.update(kw)
    return Snapshot(**base)


def _why(d: Decision) -> tuple[Outcome, str]:
    return d.outcome, d.reason


# ---- certified means fresh, in force and exact ------------------------------------------------------------------------------------


def test_a_fresh_active_in_force_snapshot_of_the_exact_scope_is_certified() -> None:
    d = decide([_snap()], SCOPE, at=T0 + timedelta(hours=1))
    assert (d.certified, d.reason, d.snapshot) == (True, "attested", _snap())


def test_no_snapshot_is_hold_never_certified() -> None:
    assert _why(decide([], SCOPE, at=T0)) == (Outcome.HOLD, "no_attestation")


@pytest.mark.parametrize(
    "other",
    [
        Scope(
            "PARTNER-B", "UK_DVS", "GB", "RTR"
        ),  # another entity (T05: the certified company is not the one asked about)
        Scope("PARTNER-A", "UK_DVS", "GB", "IDV"),  # another service of the same company
        Scope("PARTNER-A", "UK_DVS", "IE", "RTR"),  # the same service in another country (T09)
        Scope("PARTNER-A", "EU_QTSP", "GB", "RTR"),  # another scheme
    ],
)
def test_a_neighbouring_scope_is_never_covered(other: Scope) -> None:
    assert _why(decide([_snap()], other, at=T0)) == (Outcome.HOLD, "no_attestation")


def test_t07_a_snapshot_older_than_the_limit_is_hold_so_an_unreachable_register_cannot_keep_a_certification_alive() -> (
    None
):
    assert decide(
        [_snap()], SCOPE, at=T0 + DEFAULT_STALE_AFTER
    ).certified  # exactly at the limit is still fresh
    assert _why(decide([_snap()], SCOPE, at=T0 + DEFAULT_STALE_AFTER + timedelta(seconds=1))) == (
        Outcome.HOLD,
        "attestation_stale",
    )


def test_the_limit_can_be_set_per_scheme() -> None:
    limits = {"UK_DVS": timedelta(hours=1)}
    assert not decide([_snap()], SCOPE, at=T0 + timedelta(hours=2), stale_after=limits).certified
    assert (
        decide(
            [_snap()], Scope("PARTNER-A", "EU_QTSP", "GB", "RTR"), at=T0, stale_after=limits
        ).reason
        == "no_attestation"
    )
    assert decide(
        [_snap()], SCOPE, at=T0 + timedelta(hours=2), stale_after=timedelta(days=1)
    ).certified


def test_a_component_of_a_service_never_covers_the_service() -> None:
    d = decide([_snap(component_only=True)], SCOPE, at=T0)
    assert _why(d) == (Outcome.HOLD, "component_only")
    assert decide(
        [_snap(component_only=True)], SCOPE, at=T0, full_service=False
    ).certified  # asked about the component itself


@pytest.mark.parametrize("status", [CertStatus.SUSPENDED, CertStatus.WITHDRAWN, CertStatus.EXPIRED])
def test_a_suspended_withdrawn_or_expired_certification_is_hold_with_its_own_reason(
    status: CertStatus,
) -> None:
    assert _why(decide([_snap(status=status)], SCOPE, at=T0)) == (
        Outcome.HOLD,
        status.value.lower(),
    )


def test_a_certification_outside_its_validity_dates_is_hold() -> None:
    assert _why(decide([_snap(valid_from=date(2026, 12, 1))], SCOPE, at=T0)) == (
        Outcome.HOLD,
        "not_in_force",
    )
    assert _why(decide([_snap(valid_until=date(2026, 10, 31))], SCOPE, at=T0)) == (
        Outcome.HOLD,
        "not_in_force",
    )


# ---- history is never rewritten -----------------------------------------------------------------------------------------------------


def test_t08_a_later_withdrawal_does_not_change_what_was_true_at_an_earlier_check() -> None:
    first = _snap(snapshot_id="s1", fetched_at=T0)
    later = replace(
        first,
        snapshot_id="s2",
        status=CertStatus.WITHDRAWN,
        fetched_at=T0 + timedelta(days=3),
        recorded_at=T0 + timedelta(days=3),
    )
    history = [first, later]
    assert decide(
        history, SCOPE, at=T0 + timedelta(days=1)
    ).certified  # a check made before the withdrawal stays valid
    assert _why(decide(history, SCOPE, at=T0 + timedelta(days=3, hours=1))) == (
        Outcome.HOLD,
        "withdrawn",
    )
    assert _why(decide(history, SCOPE, at=T0 - timedelta(days=1))) == (
        Outcome.HOLD,
        "no_attestation",
    )  # before anyone looked


def test_t46_the_snapshot_that_answered_a_past_check_can_be_produced_again() -> None:
    first = _snap(snapshot_id="s1", fetched_at=T0)
    second = _snap(
        snapshot_id="s2",
        fetched_at=T0 + timedelta(days=2),
        recorded_at=T0 + timedelta(days=2),
        proof_hash="b" * 64,
    )
    answered = decide([first, second], SCOPE, at=T0 + timedelta(days=1))
    assert answered.snapshot is not None and (
        answered.snapshot.snapshot_id,
        answered.snapshot.proof_hash,
    ) == ("s1", "a" * 64)


def test_the_latest_snapshot_decides_and_a_fresh_one_revives_a_lapsed_scope() -> None:
    old = _snap(snapshot_id="s1", fetched_at=T0)
    fresh = _snap(
        snapshot_id="s2", fetched_at=T0 + timedelta(days=10), recorded_at=T0 + timedelta(days=10)
    )
    assert not decide([old], SCOPE, at=T0 + timedelta(days=10)).certified
    assert decide([old, fresh], SCOPE, at=T0 + timedelta(days=10, hours=1)).snapshot == fresh


# ---- recording a snapshot -----------------------------------------------------------------------------------------------------------


def test_a_well_formed_snapshot_is_accepted() -> None:
    check_snapshot(_snap(), now=T0 + timedelta(minutes=1))


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"register_url": "http://register.example.test/x"}, "ATTESTATION_REGISTER_URL_INVALID"),
        ({"register_url": "register.example.test"}, "ATTESTATION_REGISTER_URL_INVALID"),
        ({"register_entry_id": "  "}, "ATTESTATION_ENTRY_REQUIRED"),
        ({"proof_hash": "abc"}, "ATTESTATION_PROOF_REQUIRED"),
        ({"proof_hash": "G" * 64}, "ATTESTATION_PROOF_REQUIRED"),
        ({"evidence_locator": ""}, "ATTESTATION_EVIDENCE_REQUIRED"),
        ({"recorded_by": " "}, "ATTESTATION_RECORDER_REQUIRED"),
        ({"fetched_at": datetime(2026, 11, 1, 9, 0)}, "ATTESTATION_TIME_INVALID"),
        (
            {"valid_from": date(2026, 6, 1), "valid_until": date(2026, 5, 1)},
            "ATTESTATION_DATES_INVALID",
        ),
    ],
)
def test_a_malformed_snapshot_is_refused_with_its_reason(change: dict[str, Any], code: str) -> None:
    with pytest.raises(AttestationError) as e:
        check_snapshot(_snap(**change), now=T0 + timedelta(minutes=1))
    assert e.value.code == code


def test_a_snapshot_from_the_future_or_out_of_order_is_refused() -> None:
    with pytest.raises(AttestationError) as e:
        check_snapshot(_snap(fetched_at=T0 + timedelta(hours=1)), now=T0)
    assert e.value.code == "ATTESTATION_FROM_THE_FUTURE"
    check_snapshot(
        _snap(fetched_at=T0 + timedelta(minutes=4)), now=T0
    )  # within the clock skew: accepted
    prior = _snap(snapshot_id="s1", fetched_at=T0)
    for same_or_older in (T0, T0 - timedelta(hours=1)):
        with pytest.raises(AttestationError) as e2:
            check_snapshot(
                _snap(snapshot_id="s2", fetched_at=same_or_older),
                now=T0 + timedelta(days=1),
                previous=[prior],
            )
        assert e2.value.code == "ATTESTATION_OUT_OF_ORDER"
    check_snapshot(
        _snap(snapshot_id="s2", fetched_at=T0 + timedelta(hours=1)),
        now=T0 + timedelta(days=1),
        previous=[prior],
    )


# ---- what changed -------------------------------------------------------------------------------------------------------------------


def test_losing_one_certification_changes_only_its_own_scope() -> None:
    other = Scope("PARTNER-A", "UK_DVS", "GB", "IDV")
    s1, s2 = _snap(), _snap(snapshot_id="o1", scope=other)
    before = {SCOPE: decide([s1, s2], SCOPE, at=T0), other: decide([s1, s2], other, at=T0)}
    lost = replace(
        s1,
        snapshot_id="s3",
        status=CertStatus.SUSPENDED,
        fetched_at=T0 + timedelta(hours=2),
        recorded_at=T0 + timedelta(hours=2),
    )
    at = T0 + timedelta(hours=3)
    after = {
        SCOPE: decide([s1, lost, s2], SCOPE, at=at),
        other: decide([s1, lost, s2], other, at=at),
    }
    assert changes(before, after) == {SCOPE: (Outcome.CERTIFIED, Outcome.HOLD)}
    assert changes(after, after) == {}
