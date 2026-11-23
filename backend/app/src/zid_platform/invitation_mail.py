"""Sends the fixed team-invitation email (ZID-EM-004, "Invitation to join an organization").

This file is where the tenant module and the communication module meet, so it sits at the root with the other composition code (`main.py` plugs it into the tenant module at start-up).
Neither module imports the other: the tenant module only calls the hook it was given.

The email carries ONE token, `<workspace>_<ticket>` (a workspace name has no underscore), which the web app's /join page turns into the passkey setup address. It works once, as the ticket does.
Sending never raises: the outcome is returned, and the inviter still holds the link, so a mail problem can never lose an invitation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from zid_platform.api_auth import request_engine
from zid_platform.modules.communication.ports import (
    ContactError,
    Recipient,
    address_hash,
    normalize_address,
    send_email,
)

INVITATION_TEMPLATE = "ZID-EM-004"  # fixed words; only the merge fields below change
ROLE_WORDS = {
    "OWNER": "an Owner",
    "ADMIN": "an Admin",
    "APPROVER": "an Approver",
    "OPERATOR": "an Operator",
    "VIEWER": "a Viewer",
    "MEMBER": "a member",
}
_log = logging.getLogger("zid.invitation_mail")


def masked(address: str) -> str:
    """a***@example.com: enough for the sender to see which inbox, never the whole address in a response or a log."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


async def send_invitation_email(
    request: Any,
    *,
    tenant_id: str,
    address: str,
    ticket: str,
    expires_at: datetime,
    slug: str,
    organization: str,
    inviter: str,
    role: str,
) -> dict[str, str]:
    """Send the invitation. Returns {"status": SENT | NOT_CONFIGURED | INVALID_ADDRESS | FAILED | <the communication module's own outcome>, "to": masked address}."""
    cfg = getattr(request.app.state, "email_config", None)
    sender = getattr(request.app.state, "email_sender", None)
    if cfg is None or sender is None:
        return {"status": "NOT_CONFIGURED"}
    try:
        to = normalize_address(address)
    except ContactError:
        return {"status": "INVALID_ADDRESS"}
    try:
        report = await send_email(
            request_engine(request),
            cfg,
            sender,
            cell_id=str(getattr(request.app.state, "home_cell", None) or ""),
            template_id=INVITATION_TEMPLATE,
            event_id=str(uuid.uuid4()),  # every send has its own ticket, so every send is its own event
            event_type="tenant.member.invited",
            tenant_id=tenant_id,
            recipient=Recipient(to, await address_hash(to), None, "UTC", "en-US"),
            merge={
                "inviter_name": inviter,
                "organization_name": organization,
                "invited_role_label": ROLE_WORDS.get(role, "a member"),
                "expires_at_local": expires_at,
                "single_use_invitation_url": f"{slug}_{ticket}",
            },
        )
    except Exception:  # noqa: BLE001 - delivery must never change the invitation the person already has
        _log.warning("invitation email not sent", exc_info=False)
        return {"status": "FAILED", "to": masked(to)}
    return {"status": str(report.outcome), "to": masked(to)}
