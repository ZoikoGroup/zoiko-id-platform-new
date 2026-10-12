"""Public Protocol interface of the session module. Other modules import ONLY this."""

from zid_platform.modules.session.application.authenticate import (
    Authenticated,
    TokenSettings,
    authenticate,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.domain.session import Session, SessionPolicy, SessionStatus
from zid_platform.modules.session.domain.tokens import TokenError
from zid_platform.modules.session.infrastructure.session_store import (
    create_session,
    revoke_principal_sessions,
)

__all__ = [
    "Authenticated",
    "Session",
    "SessionPolicy",
    "SessionStatus",
    "TokenError",
    "TokenSettings",
    "authenticate",
    "create_session",
    "issue_access_token",
    "revoke_principal_sessions",
]
