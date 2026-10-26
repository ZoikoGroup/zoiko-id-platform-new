"""A horizontal path toward 1,000,000 decisions/s without redesigning the domain (RES-25).

Not a load test: it shows that the structure lets capacity be added by adding cells. Three things have to be true.

1. Nothing on the decision path is shared between tenants: every table a decision reads or writes is tenant-scoped,
   and the global plane is off the hot path. If so, tenants can be spread over cells freely.
2. Capacity adds linearly: the cells needed for a target follow the admission rules, double the target and the cells
   double, and the Stage C design point (200,000/s per cell, 1,000,000/s overall) needs a small, finite number.
3. A skewed population of tenants fits: placing a realistic long-tailed set under the real admission rules works,
   and the only tenant that cannot be placed is one bigger than a cell, which is named, not hidden.

The honest limit is today's code: one process carries about 40 decisions/s (tests/load/decision-gate/RESULTS.md), so
the processes per cell are computed from that, with and without the planned tuning.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import yaml
from zid_platform.modules.tenant.domain.capacity_plan import (
    cells_required,
    place_tenants,
    processes_per_cell,
    usable_per_cell,
)
from zid_platform.modules.tenant.domain.placement import CellCapacity

REPO = Path(__file__).resolve().parents[4]
CAPACITY = yaml.safe_load(
    (REPO / "contracts" / "baseline" / "capacity.yaml").read_text(encoding="utf-8")
)
OWNERSHIP = yaml.safe_load(
    (REPO / "backend" / "database" / "table-ownership.yaml").read_text(encoding="utf-8")
)
CELLS = yaml.safe_load(
    (REPO / "backend" / "config" / "cells" / "cells.yaml").read_text(encoding="utf-8")
)

STAGE_C = CAPACITY["stages"]["C"]
# Design points are what a cell is planned to carry; the raw capacity behind it is design / (1 - headroom) at least.
A_CELL = CellCapacity(
    capacity_per_s=STAGE_C["authz_decisions_per_s_per_cell"] / 0.5,
    availability_zones=3,
    saturation_threshold=0.8,
)

# Every table the decision path reads or writes, by owning schema.table (authentication, placement, security state,
# authority chain, relationships, mandates, receipts).
DECISION_PATH_TABLES = [
    "authn.session",
    "authn.authenticator",
    "core.principal",
    "authz.authority_grant",
    "org.authority_root_assertion",
    "authz.approval_request",
    "signals.security_state",
    "agent.mandate",
    "agent.mandate_reservation",
    "agent.mandate_balance",
    "evidence.decision_receipt",
    "evidence.ledger_entry",
    "platform.tenant_residency",
]


def test_the_decision_path_shares_no_state_between_tenants() -> None:
    """Every table a decision touches is tenant-scoped (so one tenant's decisions can run in any cell that holds that
    tenant), and the global plane is not on the hot path."""
    tables = OWNERSHIP["tables"]
    missing = [t for t in DECISION_PATH_TABLES if t not in tables]
    assert not missing, f"decision-path tables not in the ownership registry: {missing}"
    shared = [t for t in DECISION_PATH_TABLES if not tables[t].get("tenant_scoped")]
    assert not shared, f"decision-path tables shared between tenants: {shared}"
    assert CELLS["global_plane"]["hot_path_dependency"] is False


def test_the_stage_c_envelope_needs_a_small_finite_number_of_cells() -> None:
    """1,000,000 decisions/s at 200,000/s design points per cell: the cells follow from the admission rules."""
    n = cells_required(STAGE_C["authz_decisions_per_s_global"], A_CELL)
    assert n * usable_per_cell(A_CELL) >= STAGE_C["authz_decisions_per_s_global"]
    assert (n - 1) * usable_per_cell(A_CELL) < STAGE_C[
        "authz_decisions_per_s_global"
    ]  # not one more than needed
    assert 4 <= n <= 20, (
        n
    )  # of the order the stage table implies (5 at design points, a few more with headroom)


def test_capacity_adds_linearly_with_cells_and_never_exceeds_the_headroom_rules() -> None:
    per = usable_per_cell(A_CELL)
    assert per < A_CELL.capacity_per_s * 0.7 + 1e-9  # the 30% headroom is never planned away
    assert (
        per <= A_CELL.after_one_az_loss() * A_CELL.saturation_threshold + 1e-9
    )  # one AZ can be lost
    for target in (1_000, 50_000, 250_000, 1_000_000, 2_000_000):
        a, b = cells_required(target, A_CELL), cells_required(target * 2, A_CELL)
        assert b <= 2 * a and b >= a  # doubling the load at most doubles the cells


def test_a_skewed_population_of_tenants_fits_and_only_a_giant_does_not() -> None:
    """RES-25 / qualification 'skewed_tenant_traffic': a long-tailed set of tenants is placed under the real
    admission rules, and the one tenant bigger than a cell is refused by name (it must be sharded below tenant)."""
    rng = random.Random(25)
    cells = (
        cells_required(STAGE_C["authz_decisions_per_s_global"], A_CELL) + 2
    )  # a little slack for fragmentation
    # 20,000 tenants with a Pareto tail, scaled so the total is 85% of what the cells can carry
    raw = [rng.paretovariate(1.2) for _ in range(20_000)]
    scale = 0.85 * cells * usable_per_cell(A_CELL) / sum(raw)
    peaks = [
        min(x * scale, usable_per_cell(A_CELL) * 0.6) for x in raw
    ]  # the largest is big, but smaller than a cell
    result = place_tenants(peaks, A_CELL, cells)
    assert result.refused == [], f"{len(result.refused)} tenants did not fit"
    assert result.peak_utilisation <= 1.0

    giant = [usable_per_cell(A_CELL) * 1.5]
    assert place_tenants(giant, A_CELL, cells).refused == [
        0
    ]  # no number of cells helps; it is named, not hidden


def test_processes_per_cell_follow_from_the_measured_ceiling_and_the_tuning_plan() -> None:
    """Today's measured ceiling and the planned speedups give the processes a cell needs; the model reads the figure
    from the results file so it cannot drift from the measurement."""
    results = (REPO / "tests" / "load" / "decision-gate" / "RESULTS.md").read_text(encoding="utf-8")
    m = re.search(r"near \*\*(\d+) decisions/s\*\*", results)
    assert m, "the measured per-process ceiling is no longer in RESULTS.md"
    per_process = float(m.group(1))
    cell = STAGE_C["authz_decisions_per_s_per_cell"]
    today = processes_per_cell(cell, per_process)
    tuned = processes_per_cell(
        cell, per_process, speedup=2.0 * 4.0
    )  # 2x from cheaper statements, 4x from using cores
    assert today == -(-cell // int(per_process))
    assert tuned < today and tuned >= today // 8
