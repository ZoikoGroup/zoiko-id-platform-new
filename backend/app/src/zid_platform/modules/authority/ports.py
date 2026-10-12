"""Public interface of the authority module. Other modules import ONLY this."""

from zid_platform.modules.authority.application.service import (
    authorize_request,
    issue_grant,
    resolve,
    revoke_grant,
)
from zid_platform.modules.authority.domain.algebra import Request, ValueLimit
from zid_platform.modules.authority.domain.grants import Decision as AuthorityDecision

__all__ = [
    "AuthorityDecision",
    "Request",
    "ValueLimit",
    "authorize_request",
    "issue_grant",
    "resolve",
    "revoke_grant",
]
