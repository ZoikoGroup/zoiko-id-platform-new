"""Cell capacity admission for tenant placement (DEC-B2 §3; ZID-REQ-001 NFR-SCL-005).

A cell keeps at least 30% compute headroom at normal peak and must survive the loss of one AZ.
It stops accepting new tenant placements *before* projected one-AZ-loss capacity passes the
saturation threshold. Pure domain rules, no I/O; capacity numbers come from cell telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MIN_COMPUTE_HEADROOM = 0.30  # contracts/baseline/capacity.yaml admission.min_compute_headroom


class AdmissionRefusal(StrEnum):
    HEADROOM = "HEADROOM"  # normal peak would eat into the 30% headroom
    AZ_LOSS = "AZ_LOSS"  # losing one AZ would push the cell past saturation
    SINGLE_AZ = "SINGLE_AZ"  # a cell needs >= 2 AZs to survive an AZ loss


@dataclass(frozen=True, slots=True)
class CellCapacity:
    capacity_per_s: float  # sustainable decisions/s of the whole cell (all AZs healthy)
    availability_zones: int
    # Fraction of capacity at which latency SLOs start to break (set by SRE from load tests).
    saturation_threshold: float

    def after_one_az_loss(self) -> float:
        if self.availability_zones < 2:
            return 0.0
        return self.capacity_per_s * (self.availability_zones - 1) / self.availability_zones


def admission_refusals(
    cell: CellCapacity, current_peak_per_s: float, tenant_peak_per_s: float
) -> list[AdmissionRefusal]:
    """Why a new tenant can't be placed in this cell; an empty list means it can."""
    projected = current_peak_per_s + tenant_peak_per_s
    refusals: list[AdmissionRefusal] = []
    if cell.availability_zones < 2:
        refusals.append(AdmissionRefusal.SINGLE_AZ)
    if projected > cell.capacity_per_s * (1 - MIN_COMPUTE_HEADROOM):
        refusals.append(AdmissionRefusal.HEADROOM)
    if projected > cell.after_one_az_loss() * cell.saturation_threshold:
        refusals.append(AdmissionRefusal.AZ_LOSS)
    return refusals
