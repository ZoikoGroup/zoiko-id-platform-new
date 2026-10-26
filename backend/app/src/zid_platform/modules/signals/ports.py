"""Public Protocol interface of the signals module. Other modules import ONLY this."""

from zid_platform.modules.signals.application.ingest import CanonicalSignal, IngestResult, ingest
from zid_platform.modules.signals.application.state import pdp_security_state
from zid_platform.modules.signals.application.state_cache import (
    CACHE,
    SecurityStateCache,
    StateView,
    security_view,
)


def forget_subject(tenant_id: str, subject_id: str) -> bool:
    """Erasure: nothing cached about this subject outlives the request (PRIV-10)."""
    return CACHE.forget(tenant_id, subject_id)


def forget_tenant(tenant_id: str) -> int:
    """Tenant deletion: no security state cached for the company outlives it (TEN-25)."""
    return CACHE.forget_tenant(tenant_id)


__all__ = [
    "CACHE",
    "CanonicalSignal",
    "IngestResult",
    "SecurityStateCache",
    "StateView",
    "forget_subject",
    "forget_tenant",
    "ingest",
    "pdp_security_state",
    "security_view",
]
