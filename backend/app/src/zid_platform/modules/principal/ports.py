"""Public interface of the principal module. Other modules import ONLY this."""

from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    bump_security_revision,
    create_principal,
    load_principal,
    set_status,
)

__all__ = [
    "Principal",
    "PrincipalStatus",
    "PrincipalType",
    "bump_security_revision",
    "create_principal",
    "load_principal",
    "set_status",
]
