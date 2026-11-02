"""Public interface of the communication module. Other modules import ONLY this.

- `EmailConfig.from_settings(settings)` the release switches (§07): off, nothing enabled, no unrouted link.
- `build_sender(settings)` the SMTP sender, or None when email is off; it fails fast when email is on but
  the transport is not fully configured.
- `send_email(engine, cfg, sender, ...)` one templated email, idempotent per (event, template, recipient).
- `email_handler` / `email_topics`: the event-consumer side (events in, emails out).
- Contacts: `set_principal_contact`, `add_role_contact`, `resolve_*`, kept in the vault (see `contacts`).
- `catalogue()` the 160 templates, `coverage()` which of them an emitted event can trigger today.
"""

from __future__ import annotations

from typing import Any

from zid_platform.modules.communication.application import contacts
from zid_platform.modules.communication.application.config import EmailConfig, sendable
from zid_platform.modules.communication.application.contacts import (
    ContactError,
    Recipient,
    add_role_contact,
    address_hash,
    normalize_address,
    set_principal_contact,
)
from zid_platform.modules.communication.application.dispatch import EVENT_ROUTES, coverage
from zid_platform.modules.communication.application.handler import email_handler, email_topics
from zid_platform.modules.communication.application.service import Report, send_email
from zid_platform.modules.communication.infrastructure.catalogue_file import load as catalogue
from zid_platform.modules.communication.infrastructure.smtp import (
    EmailSender,
    SmtpConfig,
    SmtpSender,
)


def build_sender(settings: Any) -> EmailSender | None:
    """The SMTP transport from settings. None when email is switched off; an error when it is on but a
    required part (host, login, password file, sender address) is missing, so a misconfigured cell stops at
    start-up instead of silently sending nothing."""
    if not settings.email_enabled:
        return None
    missing = [
        name
        for name, value in (
            ("ZID_SMTP_HOST", settings.smtp_host),
            ("ZID_SMTP_USERNAME", settings.smtp_username),
            ("ZID_SMTP_PASSWORD_FILE", settings.smtp_password_file),
            ("ZID_EMAIL_FROM", settings.email_from),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"email is enabled but {', '.join(missing)} is not set")
    from email.utils import parseaddr

    insecure = bool(getattr(settings, "smtp_insecure_local", False))
    if insecure and settings.environment not in ("local", "test"):
        raise RuntimeError("ZID_SMTP_INSECURE_LOCAL is for local/test only")
    name, address = parseaddr(settings.email_from)
    return SmtpSender(
        SmtpConfig.load(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password_file=settings.smtp_password_file,
            from_name=name or "Zoiko iD",
            from_address=address,
            reply_to=settings.email_reply_to,
            insecure_local=insecure,
        )
    )


__all__ = [
    "EVENT_ROUTES",
    "ContactError",
    "EmailConfig",
    "EmailSender",
    "Recipient",
    "Report",
    "SmtpConfig",
    "SmtpSender",
    "add_role_contact",
    "address_hash",
    "build_sender",
    "catalogue",
    "contacts",
    "coverage",
    "email_handler",
    "email_topics",
    "normalize_address",
    "send_email",
    "sendable",
    "set_principal_contact",
]
