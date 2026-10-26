"""Recovery runbooks as enforced step sequences and the failover admission gates (ZID-RESILIENCE-001
§11-20, §21; ZID-TENANCY-001 §15-17; ZID-SRE-001 §17-18; DEC-G1).

Pure rules. What each test proves (and what it leaves to a real cell):
- test_failover_fences_before_it_promotes_and_routes_last      SRE-22/RES-23 (sequence only)
- test_assess_blocks_failover_when_the_destination_cannot_carry_it  RES-29, RES-10
- test_restore_cannot_complete_without_erasure_still_in_force  RES-16/SRE-19 (runbook gate only)
- test_migration_never_has_two_writers_in_the_sequence         TEN-12, TEN-13 (sequence only)
"""

from __future__ import annotations

import pytest
from zid_platform.modules.resilience.domain.runbooks import (
    MIN_HEADROOM,
    PROBES,
    RUNBOOKS,
    TARGETS,
    GateFailure,
    Kind,
    Readiness,
    ResilienceError,
    StepType,
    assess,
    check_probes,
    check_step,
)


def names(kind: Kind) -> list[str]:
    return [s.name for s in RUNBOOKS[kind]]


def before(kind: Kind, first: str, second: str) -> bool:
    n = names(kind)
    return n.index(first) < n.index(second)


def test_failover_fences_before_it_promotes_and_routes_last() -> None:
    # RESILIENCE §15: the old writer is fenced before anything is promoted (no split brain), the
    # target is assessed before data is promoted, epochs advance only after promotion + keys, and
    # traffic is routed only after validation. Mandate reservations are reconciled last.
    for a, b in (
        ("FREEZE_OLD_WRITER", "ASSESS"),
        ("ASSESS", "PROMOTE_DATA"),
        ("PROMOTE_DATA", "ADVANCE_EPOCHS"),
        ("ACTIVATE_KEYS", "ADVANCE_EPOCHS"),
        ("ADVANCE_EPOCHS", "VALIDATE"),
        ("VALIDATE", "ROUTE"),
        ("ROUTE", "RECONCILE"),
    ):
        assert before(Kind.FAILOVER, a, b), (a, b)
    assert names(Kind.FAILOVER)[0] == "FREEZE_OLD_WRITER"

    # failback is a governed migration back: resync verified, recovery writer fenced, THEN new epochs
    for a, b in (
        ("RESYNC_VERIFIED", "FENCE_RECOVERY_WRITER"),
        ("FENCE_RECOVERY_WRITER", "ADVANCE_EPOCHS"),
        ("ADVANCE_EPOCHS", "VALIDATE"),
        ("VALIDATE", "ROUTE"),
    ):
        assert before(Kind.FAILBACK, a, b), (a, b)

    # infrastructure steps carry evidence, platform steps are checks the platform runs itself
    steps = {s.name: s.type for s in RUNBOOKS[Kind.FAILOVER]}
    assert steps["FREEZE_OLD_WRITER"] is StepType.CONFIRM
    assert steps["ADVANCE_EPOCHS"] is StepType.AUTO and steps["ASSESS"] is StepType.AUTO

    # nothing can be skipped or reordered: only the next step is accepted
    for kind in (Kind.FAILOVER, Kind.FAILBACK, Kind.MIGRATION, Kind.RESTORE):
        seq = names(kind)
        for done, requested in enumerate(seq):
            assert check_step(kind, done, requested).name == requested
            skipped = seq[-1] if requested != seq[-1] else seq[0]
            with pytest.raises(ResilienceError) as e:
                check_step(kind, done, skipped)
            assert e.value.code == "RECOVERY_STEP_OUT_OF_ORDER"
        with pytest.raises(ResilienceError) as e:
            check_step(kind, len(seq), seq[0])
        assert e.value.code == "RECOVERY_OPERATION_COMPLETE"  # a finished runbook cannot be re-run

    # a cutover is validated with the full probe set before routing: no partial credit
    assert set(PROBES["VALIDATE"]) >= {
        "authentication",
        "authorization_allow",
        "authorization_deny",
    }
    partial = {p: True for p in PROBES["VALIDATE"]} | {"revocation": False}
    with pytest.raises(GateFailure) as e2:
        check_probes(partial, PROBES["VALIDATE"])
    assert e2.value.code == "RECOVERY_VALIDATION"
    with pytest.raises(GateFailure):
        check_probes(None, PROBES["VALIDATE"])  # silence is not a pass


def ready(**kw: object) -> Readiness:
    base: dict[str, object] = {
        "cell_id": "eu-1-dr",
        "replica_lag_s": 2.0,
        "backup_age_s": 60.0,
        "recovery_keys_ready": True,
        "one_az_loss_headroom": 0.45,
        "status": "ACTIVE",
    }
    return Readiness(**(base | kw))  # type: ignore[arg-type]


def gate(target: Readiness | None, **kw: object) -> list[dict[str, object]]:
    args: dict[str, object] = {
        "target": target,
        "permitted_targets": ("eu-1-dr",),
        "prohibited": (),
        "target_cell": "eu-1-dr",
    }
    return assess(**(args | kw))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("target", "kw", "code"),
    [
        (ready(one_az_loss_headroom=MIN_HEADROOM - 0.01), {}, "CAPACITY_FAILOVER_BLOCKED"),
        (ready(one_az_loss_headroom=None), {}, "CAPACITY_FAILOVER_BLOCKED"),  # unknown = blocked
        (ready(status="QUARANTINED"), {}, "CELL_UNAVAILABLE"),
        (ready(replica_lag_s=31.0), {}, "RECOVERY_REPLICA_TOO_STALE"),
        (ready(replica_lag_s=None), {}, "RECOVERY_REPLICA_TOO_STALE"),
        (ready(recovery_keys_ready=False), {}, "RECOVERY_KEY_UNAVAILABLE"),
        (None, {}, "RECOVERY_REPLICA_TOO_STALE"),  # no readiness report at all
        (ready(), {"permitted_targets": ()}, "RESIDENCY_FAILOVER_BLOCKED"),
        (ready(), {"prohibited": ("eu-1-dr",)}, "RESIDENCY_FAILOVER_BLOCKED"),
    ],
)
def test_assess_blocks_failover_when_the_destination_cannot_carry_it(
    target: Readiness | None, kw: dict[str, object], code: str
) -> None:
    """RES-29: failover is blocked (a controlled DENY, never a promotion) when the destination
    cannot safely carry the promoted traffic with the redundancy floor; RES-10 residency gate."""
    with pytest.raises(GateFailure) as e:
        gate(target, **kw)
    assert e.value.code == code
    assert any(g["status"] == "FAIL" and g["code"] == code for g in e.value.report)


def test_assess_passes_only_with_every_gate_green() -> None:
    report = gate(ready(one_az_loss_headroom=MIN_HEADROOM))  # exactly at the floor is enough
    assert {g["gate_id"] for g in report} >= {
        "residency.target_approved",
        "target.not_quarantined",
        "replica.lag_within_rpo",
        "keys.recovery_ready",
        "capacity.one_az_loss",
    }
    assert all(g["status"] == "PASS" for g in report)
    # a planned migration copies data instead of promoting a replica: no replica gate, but the
    # capacity and residency gates stay
    gate(ready(replica_lag_s=None, recovery_keys_ready=False), needs_replica=False)
    with pytest.raises(GateFailure):
        gate(ready(replica_lag_s=None, one_az_loss_headroom=0.1), needs_replica=False)


def test_restore_cannot_complete_without_erasure_still_in_force() -> None:
    # RESTORE ends only after integrity verification proving erasure is preserved (RESILIENCE §20)
    assert before(Kind.RESTORE, "RESTORE_ISOLATED", "VERIFY_INTEGRITY")
    assert before(Kind.RESTORE, "VERIFY_INTEGRITY", "PROBES")
    assert "erasure_preserved" in PROBES["VERIFY_INTEGRITY"]
    assert "evidence_chain" in PROBES["VERIFY_INTEGRITY"]
    ok = {p: True for p in PROBES["VERIFY_INTEGRITY"]}
    check_probes(ok, PROBES["VERIFY_INTEGRITY"])
    with pytest.raises(GateFailure):
        check_probes({**ok, "erasure_preserved": False}, PROBES["VERIFY_INTEGRITY"])


def test_migration_never_has_two_writers_in_the_sequence() -> None:
    n = names(Kind.MIGRATION)
    # sensitive writes are drained before the destination becomes writer, and the destination is
    # validated before routing; the old copy is only retired after routing
    assert n.index("COPY_VERIFIED") < n.index("QUIESCE") < n.index("ADVANCE_EPOCHS")
    assert (
        n.index("ADVANCE_EPOCHS")
        < n.index("VALIDATE")
        < n.index("ROUTE")
        < n.index("RETIRE_OLD_COPY")
    )
    assert n.count("ADVANCE_EPOCHS") == 1  # exactly one cutover per operation
    for kind in (Kind.FAILOVER, Kind.FAILBACK):
        assert names(kind).count("ADVANCE_EPOCHS") == 1
    # recovery objectives exist for every kind and a migration/failback may lose nothing
    assert set(TARGETS) == set(Kind)
    assert TARGETS[Kind.MIGRATION].rpo_s == 0.0 and TARGETS[Kind.FAILBACK].rpo_s == 0.0
