"""Public Protocol interface of the privacy module. Other modules import ONLY this.

- `check_processing(conn, tenant, activity_code=..., purpose_code=..., data_category=...,
  subject_principal_id=...)` at every processing boundary that can vary by purpose (PRIV-001 §3)
- `register_personal_data(...)` records where a subject's personal data lives (the data map), and
  `register_store(store, disposer=..., exporter=...)` how that store deletes and exports it
- `pack_transfer_mechanisms(pack_id)` the transfer mechanisms a jurisdiction pack supports
- `release_gate(...)` / `capture_paused_breaches(conn, country)` the DPIA release gate and the breach containment a biometric method obeys, and
  `missing_biometric_retention(conn, method, purpose)` the asset classes with no retention period (CR-107, CR-108)
- `privacy_pass` the worker's retention sweep and deadline monitor
"""

from __future__ import annotations

from zid_platform.modules.privacy.application import applicant_session
from zid_platform.modules.privacy.application.biometric_retention import (
    missing_biometric_retention,
    use_case_problems,
)
from zid_platform.modules.privacy.application.control import (
    acknowledge_notice,
    capture_paused_breaches,
    release_gate,
    retry_failed_erasures,
)
from zid_platform.modules.privacy.application.erasure_retry import erasure_retry_pass
from zid_platform.modules.privacy.application.model_governance import (
    active_model_version,
    method_evidence,
)
from zid_platform.modules.privacy.application.reauthorization import reauthorization_pass
from zid_platform.modules.privacy.application.release_attestation import (
    record_release_attestation,
    release_attestation_for,
)
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
    register_decision_hook,
    register_erasure_hook,
    register_personal_data,
    register_rectification_source,
    register_store,
)
from zid_platform.modules.privacy.application.sessions import cancel as cancel_session
from zid_platform.modules.privacy.application.sessions import (
    record_consent as record_session_consent,
)
from zid_platform.modules.privacy.application.sessions import status as session_status
from zid_platform.modules.privacy.domain.model import PrivacyError, ProcessingDecision
from zid_platform.modules.privacy.domain.outcome_routing import route as route_outcome

__all__ = [
    "PrivacyError",
    "ProcessingDecision",
    "Reconciliation",
    "acknowledge_notice",
    "active_model_version",
    "applicant_session",
    "cancel_session",
    "capture_paused_breaches",
    "check_processing",
    "configure_enforcement",
    "discover_subject",
    "enforce_processing",
    "erasure_retry_pass",
    "mark_restored",
    "method_evidence",
    "missing_biometric_retention",
    "pack_transfer_mechanisms",
    "privacy_pass",
    "reauthorization_pass",
    "reconcile_after_restore",
    "record_release_attestation",
    "record_session_consent",
    "register_decision_hook",
    "register_erasure_hook",
    "register_personal_data",
    "register_rectification_source",
    "register_store",
    "release_attestation_for",
    "release_gate",
    "retry_failed_erasures",
    "route_outcome",
    "session_status",
    "use_case_problems",
]
