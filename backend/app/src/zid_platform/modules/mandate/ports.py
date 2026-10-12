"""Public interface of the mandate module. Other modules import ONLY this."""

from zid_platform.modules.mandate.domain.subagent import CascadeEffect
from zid_platform.modules.mandate.infrastructure.mandate_store import pause_or_revoke_for_grants

__all__ = ["CascadeEffect", "pause_or_revoke_for_grants"]
