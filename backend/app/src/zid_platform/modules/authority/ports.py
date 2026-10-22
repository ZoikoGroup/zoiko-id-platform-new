"""Public interface of the authority module. Other modules import ONLY this."""

from zid_platform.modules.authority.application.cascade import (
    on_person_authority_ended,
    on_root_changed,
)
from zid_platform.modules.authority.application.service import (
    authorize_request,
    issue_grant,
    resolve,
    revoke_grant,
)
from zid_platform.modules.authority.domain.algebra import (
    ANY,
    Authority,
    Request,
    ValueLimit,
    Window,
)
from zid_platform.modules.authority.domain.grants import (
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.domain.grants import Decision as AuthorityDecision
from zid_platform.modules.authority.infrastructure.approval_store import (
    evaluate as evaluate_approval,
)
from zid_platform.modules.authority.infrastructure.grant_store import holds_any_action, load_grant

__all__ = [
    "ANY",
    "Authority",
    "AuthorityDecision",
    "ContinuityMode",
    "Grant",
    "GrantStatus",
    "GrantType",
    "Request",
    "ValueLimit",
    "Window",
    "authorize_request",
    "evaluate_approval",
    "holds_any_action",
    "issue_grant",
    "load_grant",
    "on_person_authority_ended",
    "on_root_changed",
    "resolve",
    "revoke_grant",
]
