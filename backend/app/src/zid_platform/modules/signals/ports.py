"""Public Protocol interface of the signals module. Other modules import ONLY this."""

from zid_platform.modules.signals.application.ingest import CanonicalSignal, IngestResult, ingest
from zid_platform.modules.signals.application.state import pdp_security_state
from zid_platform.modules.signals.application.state_cache import (
    CACHE,
    SecurityStateCache,
    StateView,
    security_view,
)

__all__ = [
    "CACHE",
    "CanonicalSignal",
    "IngestResult",
    "SecurityStateCache",
    "StateView",
    "ingest",
    "pdp_security_state",
    "security_view",
]
