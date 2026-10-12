"""Public interface of the principal module. Other modules import ONLY this."""

from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import load_principal

__all__ = ["Principal", "PrincipalStatus", "PrincipalType", "load_principal"]
