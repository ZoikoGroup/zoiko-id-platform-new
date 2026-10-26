"""Public Protocol interface of the onboarding module. Other modules import ONLY this.

- `apply_overlay(conn, tenant_id=..., tenant_status=..., client_id=..., ...)`: the overlay verdict
  on an AuthZEN decision - PEP obligation and context checks, pilot scope, ENFORCE vs EVALUATE_ONLY,
  and the shadow observation (ZID-OVERLAY-001 §12, §18-19).
"""

from __future__ import annotations

from zid_platform.modules.onboarding.application.deletion import (
    DeletionError,
    deletion_pass,
    deletion_status,
    request_deletion,
)
from zid_platform.modules.onboarding.application.offboarding import offboarding_pass
from zid_platform.modules.onboarding.application.organization import (
    development_verification,
    seed_administrator_grant,
)
from zid_platform.modules.onboarding.application.overlay import apply_overlay
from zid_platform.modules.onboarding.domain.plan import Enforcement, Verdict

__all__ = [
    "DeletionError",
    "Enforcement",
    "Verdict",
    "apply_overlay",
    "deletion_pass",
    "deletion_status",
    "development_verification",
    "offboarding_pass",
    "request_deletion",
    "seed_administrator_grant",
]
