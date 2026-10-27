"""Public Protocol interface of the privacy module. Other modules import ONLY this.

- `check_processing(conn, tenant, activity_code=..., purpose_code=..., data_category=...,
  subject_principal_id=...)` at every processing boundary that can vary by purpose (PRIV-001 §3)
- `register_personal_data(...)` records where a subject's personal data lives (the data map), and
  `register_store(store, disposer=..., exporter=...)` how that store deletes and exports it
- `pack_transfer_mechanisms(pack_id)` the transfer mechanisms a jurisdiction pack supports
- `privacy_pass` the worker's retention sweep and deadline monitor
"""

from __future__ import annotations

from zid_platform.modules.privacy.application.restore import (
    Reconciliation,
    mark_restored,
    reconcile_after_restore,
)
from zid_platform.modules.privacy.application.service import (
    check_processing,
    configure_enforcement,
    discover_subject,
    enforce_processing,
    pack_transfer_mechanisms,
    privacy_pass,
    register_erasure_hook,
    register_personal_data,
    register_rectification_source,
    register_store,
)
from zid_platform.modules.privacy.domain.model import PrivacyError, ProcessingDecision

__all__ = [
    "PrivacyError",
    "ProcessingDecision",
    "Reconciliation",
    "check_processing",
    "configure_enforcement",
    "discover_subject",
    "enforce_processing",
    "mark_restored",
    "pack_transfer_mechanisms",
    "privacy_pass",
    "reconcile_after_restore",
    "register_erasure_hook",
    "register_personal_data",
    "register_rectification_source",
    "register_store",
]
