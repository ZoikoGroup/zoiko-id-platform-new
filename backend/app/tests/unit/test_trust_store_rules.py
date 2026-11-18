"""The governed trust-store lifecycle as pure rules (ZID-DTV-WF-01 section 13, O2; CR-107). T06 and T26 of WF01 at the rule level."""

from __future__ import annotations

import pytest
from zid_platform.modules.evidence.domain import trust_store as ts
from zid_platform.modules.evidence.domain.trust_store import PackageRef, State, TrustStoreError


def ref(pid: str, seq: int, state: State, by: str = "stager", at: str | None = None) -> PackageRef:
    return PackageRef(pid, seq, state, by, at)


def test_a_list_that_did_not_verify_is_never_a_candidate() -> None:
    assert ts.after_validation("VERIFIED", 3) is State.VALIDATED
    assert ts.after_validation("NOT_VERIFIED", 3) is State.REJECTED
    assert (
        ts.after_validation("INDETERMINATE", 3) is State.REJECTED
    )  # an anchor that does not issue the signer is not a pass
    assert ts.after_validation("VERIFIED", 0) is State.REJECTED  # nothing to trust


def test_only_a_staged_package_can_be_validated() -> None:
    ts.require_validate(State.STAGED)
    for state in State:
        if state is not State.STAGED:
            with pytest.raises(TrustStoreError) as e:
                ts.require_validate(state)
            assert e.value.code == "INVALID_STATE"


def test_the_stager_can_neither_approve_nor_activate() -> None:
    with pytest.raises(TrustStoreError) as e:
        ts.require_approve(State.VALIDATED, staged_by="a", actor="a")
    assert e.value.code == "SELF_APPROVAL"
    with pytest.raises(TrustStoreError) as e2:
        ts.require_activate(State.APPROVED, staged_by="a", actor="a")
    assert e2.value.code == "SELF_ACTIVATION"
    ts.require_approve(State.VALIDATED, staged_by="a", actor="b")
    ts.require_activate(State.APPROVED, staged_by="a", actor="b")


def test_no_step_can_be_skipped() -> None:
    for state in (State.STAGED, State.REJECTED, State.APPROVED, State.ACTIVE, State.SUPERSEDED):
        with pytest.raises(TrustStoreError):
            ts.require_approve(
                state, staged_by="a", actor="b"
            )  # only a VALIDATED package is approved
    for state in (State.STAGED, State.VALIDATED, State.REJECTED, State.ACTIVE, State.SUPERSEDED):
        with pytest.raises(TrustStoreError):
            ts.require_activate(
                state, staged_by="a", actor="b"
            )  # an import is never activated before it is approved


def test_a_rejection_and_a_retirement_need_a_reason() -> None:
    for bad in (None, "", "  ", "ab"):
        with pytest.raises(TrustStoreError) as e:
            ts.require_reject(State.VALIDATED, bad)
        assert e.value.code == "REASON_REQUIRED"
        with pytest.raises(TrustStoreError):
            ts.require_retire(State.ACTIVE, bad)
    assert ts.require_reject(State.STAGED, " list is stale ") == "list is stale"
    with pytest.raises(TrustStoreError):
        ts.require_reject(
            State.ACTIVE, "too late"
        )  # an active package is rolled back or retired, not rejected
    with pytest.raises(TrustStoreError) as e3:
        ts.require_retire(State.APPROVED, "not active")
    assert e3.value.code == "NOTHING_TO_RETIRE"


def test_the_manifest_digest_is_the_same_for_the_same_set_in_any_order() -> None:
    a = {"country": "DE", "fingerprint": "aa"}
    b = {"country": "FR", "fingerprint": "bb"}
    c = {"country": "DE", "fingerprint": "cc"}
    assert ts.manifest_digest([a, b, c]) == ts.manifest_digest([c, b, a])
    assert ts.manifest_digest([a, b]) != ts.manifest_digest([a, b, c])
    assert ts.manifest_digest([a, a, b]) == ts.manifest_digest(
        [a, b]
    )  # a duplicate is not a different set
    assert ts.manifest_digest([{"country": "DE", "fingerprint": "aa"}]) != ts.manifest_digest(
        [{"country": "FR", "fingerprint": "aa"}]
    )  # the country is part of what is trusted


def test_the_store_governs_once_any_package_was_ever_activated() -> None:
    assert not ts.governs([])
    assert not ts.governs(
        [State.STAGED, State.VALIDATED, State.APPROVED, State.REJECTED]
    )  # an import alone changes nothing
    for state in (State.ACTIVE, State.SUPERSEDED, State.ROLLED_BACK, State.RETIRED):
        assert ts.governs([State.STAGED, state])


def test_rollback_restores_the_most_recently_active_superseded_package() -> None:
    packages = [
        ref("p1", 1, State.SUPERSEDED, at="2026-11-01T00:00:00"),
        ref("p2", 2, State.SUPERSEDED, at="2026-11-10T00:00:00"),
        ref("p3", 3, State.ACTIVE, at="2026-11-15T00:00:00"),
        ref("p4", 4, State.VALIDATED),
    ]
    current, target, why = ts.plan_rollback(packages, to_package_id=None, reason="revocation seen")
    assert (current.package_id, target.package_id, why) == ("p3", "p2", "revocation seen")
    _, named, _ = ts.plan_rollback(packages, to_package_id="p1", reason="back to the first")
    assert named.package_id == "p1"


def test_rollback_refuses_what_was_never_safe_or_never_active() -> None:
    packages = [
        ref("p1", 1, State.ROLLED_BACK, at="2026-11-01T00:00:00"),
        ref("p2", 2, State.REJECTED),
        ref("p3", 3, State.APPROVED),
        ref("p4", 4, State.ACTIVE, at="2026-11-15T00:00:00"),
    ]
    for target in ("p1", "p2", "p3", "p4", "missing"):
        with pytest.raises(TrustStoreError) as e:
            ts.plan_rollback(packages, to_package_id=target, reason="try")
        assert e.value.code == "ROLLBACK_TARGET_INVALID"
    with pytest.raises(TrustStoreError) as e2:
        ts.plan_rollback(
            packages, to_package_id=None, reason="try"
        )  # nothing earlier was ever safely active
    assert e2.value.code == "NO_PREVIOUS_PACKAGE"
    with pytest.raises(TrustStoreError) as e3:
        ts.plan_rollback(
            [ref("p1", 1, State.SUPERSEDED, at="2026-11-01T00:00:00")],
            to_package_id=None,
            reason="try",
        )
    assert e3.value.code == "NOTHING_TO_ROLL_BACK"
    with pytest.raises(TrustStoreError) as e4:
        ts.plan_rollback(packages, to_package_id=None, reason=" ")
    assert e4.value.code == "REASON_REQUIRED"
