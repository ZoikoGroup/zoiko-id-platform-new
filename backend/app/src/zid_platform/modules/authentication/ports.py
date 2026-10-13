"""Public Protocol interface of the authentication module. Other modules import ONLY this."""

from zid_platform.modules.authentication.application.authenticators import revoke_for_security
from zid_platform.modules.authentication.domain.assurance import (
    AAL,
    MappingProfile,
    effective_level,
    map_assurance,
    step_up_needed,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    Link,
    find_active,
    for_principal,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    create_link as create_external_link,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    revoke as revoke_external_link,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    touch as touch_external_link,
)

find_external_link = find_active
external_links_for_principal = for_principal

__all__ = [
    "AAL",
    "Link",
    "MappingProfile",
    "create_external_link",
    "effective_level",
    "external_links_for_principal",
    "find_external_link",
    "map_assurance",
    "revoke_external_link",
    "revoke_for_security",
    "step_up_needed",
    "touch_external_link",
]
