"""Capacity planning toward the target envelope (ZID-REQ-001 NFR-SCL-001..005; DEC-B2; RES-25). Pure domain.

The decision path is sharded by tenant: each tenant lives in one home cell and a decision touches only that cell's
data (row-level security per tenant, no global component on the hot path). That makes capacity additive: the platform
adds cells, it does not redesign the domain. This module is the arithmetic of that claim, using the same admission
rules that stop a real cell filling past its headroom.

It is a model, not a load test. The measured per-process ceiling (tests/load/decision-gate/RESULTS.md) is an input,
and every figure it produces is only as good as that measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from zid_platform.modules.tenant.domain.placement import CellCapacity, admission_refusals


def usable_per_cell(cell: CellCapacity) -> float:
    """Sustained decisions/s a cell may be *planned* to carry: the tighter of normal-peak headroom and one-AZ loss."""
    from zid_platform.modules.tenant.domain.placement import MIN_COMPUTE_HEADROOM

    return min(
        cell.capacity_per_s * (1 - MIN_COMPUTE_HEADROOM),
        cell.after_one_az_loss() * cell.saturation_threshold,
    )


def cells_required(target_per_s: float, cell: CellCapacity) -> int:
    """How many identical cells carry `target_per_s` while each keeps its headroom and survives one AZ loss."""
    per = usable_per_cell(cell)
    if per <= 0:
        raise ValueError("a cell with no usable capacity cannot carry anything")
    return math.ceil(target_per_s / per)


def processes_per_cell(
    cell_target_per_s: float, per_process_per_s: float, *, speedup: float = 1.0
) -> int:
    """API processes one cell needs for `cell_target_per_s`, given a measured per-process ceiling and the speedup the
    planned tuning steps are expected to bring (1.0 = today's code)."""
    if per_process_per_s <= 0 or speedup <= 0:
        raise ValueError("rates must be positive")
    return math.ceil(cell_target_per_s / (per_process_per_s * speedup))


@dataclass(frozen=True, slots=True)
class Placement:
    cells: int
    placed: int
    refused: list[
        int
    ]  # indexes of tenants no cell could take (they must be split, not squeezed in)
    peak_utilisation: float  # highest cell load as a fraction of its planned capacity


def place_tenants(tenant_peaks: Sequence[float], cell: CellCapacity, cells: int) -> Placement:
    """Place tenants (largest first, least-loaded cell) under the real admission rules, returning what fitted.

    A tenant whose own peak exceeds one cell's planned capacity is refused outright: no number of cells helps it, so
    it has to be sharded below the tenant (that is the one place the plan can break)."""
    load = [0.0] * cells
    refused: list[int] = []
    placed = 0
    for idx in sorted(range(len(tenant_peaks)), key=lambda i: tenant_peaks[i], reverse=True):
        peak = tenant_peaks[idx]
        target = min(range(cells), key=load.__getitem__)
        if admission_refusals(cell, load[target], peak):
            refused.append(idx)
            continue
        load[target] += peak
        placed += 1
    per = usable_per_cell(cell)
    return Placement(cells, placed, refused, max(load) / per if per else 0.0)
