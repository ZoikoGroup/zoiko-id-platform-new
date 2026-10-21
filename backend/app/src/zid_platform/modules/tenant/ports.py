"""Public Protocol interface of the tenant module. Other modules import ONLY this.

Residency (F-W5; ZID-TENANCY-001):
- `authorize_transfer(conn, tenant, data_class=..., destination_cell_id=..., purpose_code=...)`
  the cross-cell transfer gateway decision (default deny for restricted classes, evidenced);
- `check_category_transfer` the same for a PRIV-001 data category (D0-D4);
- `effective_operator_jurisdictions(conn, tenant, configured)` SRP-2/3 operator restrictions.
"""

from __future__ import annotations

from zid_platform.modules.tenant.application.residency import (
    authorize_transfer,
    check_category_transfer,
    effective_operator_jurisdictions,
)
from zid_platform.modules.tenant.domain.residency import (
    CATEGORY_CLASS,
    ResidencyError,
    TransferDecision,
    minimize,
)

__all__ = [
    "CATEGORY_CLASS",
    "ResidencyError",
    "TransferDecision",
    "authorize_transfer",
    "check_category_transfer",
    "effective_operator_jurisdictions",
    "minimize",
]
