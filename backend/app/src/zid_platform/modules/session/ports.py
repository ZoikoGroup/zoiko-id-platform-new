"""Public Protocol interface of the session module. Other modules import ONLY this."""

from zid_platform.modules.session.application.authenticate import (
    Authenticated,
    TokenSettings,
    authenticate,
)
from zid_platform.modules.session.application.client_auth import (
    cert_thumbprint,
    parse_client_cert,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.application.oauth_server import ServerSettings
from zid_platform.modules.session.domain.session import Session, SessionPolicy, SessionStatus
from zid_platform.modules.session.domain.tokens import TokenError
from zid_platform.modules.session.infrastructure.oauth_store import client_tenant
from zid_platform.modules.session.infrastructure.session_store import (
    create_session,
    issue_handle,
    load_session,
    load_session_by_handle,
    revoke_principal_sessions,
)
from zid_platform.modules.session.infrastructure.session_store import (
    set_status as set_session_status,
)

__all__ = [
    "Authenticated",
    "ServerSettings",
    "Session",
    "SessionPolicy",
    "SessionStatus",
    "TokenError",
    "TokenSettings",
    "authenticate",
    "cert_thumbprint",
    "client_tenant",
    "create_session",
    "issue_access_token",
    "issue_handle",
    "load_session",
    "load_session_by_handle",
    "parse_client_cert",
    "revoke_principal_sessions",
    "set_session_status",
]
