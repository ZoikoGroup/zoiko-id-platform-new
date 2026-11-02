"""SMTP delivery (Communication Estate §03 EMAIL TRANSPORT). The only code that talks to a mail server.

TLS is mandatory: STARTTLS (587) or implicit TLS (465) with the platform's certificate verification, never a
plain login. The password is read once from a file, never from an inline setting, and is not part of any
`repr`. Every answer from the server maps to one `SendResult`, and `send` never raises for a delivery
problem, so the caller always records an outcome:

    SENT                 the server accepted the message for delivery
    RECIPIENT_REJECTED   a permanent (5xx) refusal of this address: a hard bounce, suppress it
    TEMPORARY            a 4xx answer or a network failure: try again later
    PROVIDER_ERROR       a permanent refusal that is not about the recipient (login, sender, policy)
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Protocol

from zid_platform.modules.communication.domain.render import Rendered

log = logging.getLogger("zid.communication.smtp")


@dataclass(frozen=True, slots=True)
class SendResult:
    kind: str  # SENT | RECIPIENT_REJECTED | TEMPORARY | PROVIDER_ERROR
    code: str | None = None
    provider_ref: str | None = None

    @property
    def sent(self) -> bool:
        return self.kind == "SENT"


class EmailSender(Protocol):
    async def send(self, *, to: str, rendered: Rendered, message_id: str) -> SendResult: ...


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    from_name: str
    from_address: str
    reply_to: str | None = None
    implicit_tls: bool = False
    timeout_s: float = 20.0
    insecure_local: bool = False  # a local mail sink: no TLS, never in a deployed environment

    @classmethod
    def load(
        cls,
        *,
        host: str,
        port: int,
        username: str,
        password_file: str,
        from_name: str,
        from_address: str,
        reply_to: str | None = None,
        timeout_s: float = 20.0,
        insecure_local: bool = False,
    ) -> SmtpConfig:
        password = Path(password_file).read_text(encoding="utf-8").strip()
        if not password:
            raise RuntimeError("the SMTP password file is empty")
        if parseaddr(from_address)[1] != from_address or "@" not in from_address:
            raise RuntimeError("the sender address is not a plain address")
        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            from_name=from_name,
            from_address=from_address,
            reply_to=reply_to,
            implicit_tls=port == 465 and not insecure_local,
            timeout_s=timeout_s,
            insecure_local=insecure_local,
        )


def build_message(cfg: SmtpConfig, *, to: str, rendered: Rendered, message_id: str) -> EmailMessage:
    """The wire message: a text and an HTML alternative, with the headers the copybook requires."""
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_address))
    msg["To"] = to
    msg["Subject"] = rendered.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(
        idstring=message_id.replace("-", "")[:20], domain=cfg.from_address.split("@")[1]
    )
    if cfg.reply_to:
        msg["Reply-To"] = cfg.reply_to
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Zoiko-Template"] = f"{rendered.template_id}/{rendered.template_version}"
    if rendered.unsubscribe_url:
        # RFC 8058 one-click unsubscribe, required on marketing mail.
        msg["List-Unsubscribe"] = f"<{rendered.unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(rendered.text)
    msg.add_alternative(rendered.html, subtype="html")
    return msg


class SmtpSender:
    def __init__(self, cfg: SmtpConfig) -> None:
        self._cfg = cfg

    async def send(self, *, to: str, rendered: Rendered, message_id: str) -> SendResult:
        msg = build_message(self._cfg, to=to, rendered=rendered, message_id=message_id)
        try:
            return await asyncio.to_thread(self._deliver, msg, to)
        except Exception:  # a delivery problem is an outcome, never an exception for the caller
            log.exception("smtp delivery crashed template=%s", rendered.template_id)
            return SendResult("TEMPORARY", "SMTP_UNEXPECTED")

    def _deliver(self, msg: EmailMessage, to: str) -> SendResult:
        cfg = self._cfg
        context = ssl.create_default_context()
        try:
            smtp: smtplib.SMTP = (
                smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout_s, context=context)
                if cfg.implicit_tls
                else smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_s)
            )
            with smtp:
                smtp.ehlo()
                if not cfg.implicit_tls and not cfg.insecure_local:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(cfg.username, cfg.password)
                smtp.send_message(msg, from_addr=cfg.from_address, to_addrs=[to])
        except smtplib.SMTPRecipientsRefused as e:
            codes = [c for c, _ in e.recipients.values()]
            return SendResult(
                "RECIPIENT_REJECTED" if all(500 <= c < 600 for c in codes) else "TEMPORARY",
                f"SMTP_{codes[0]}",
            )
        except smtplib.SMTPAuthenticationError:
            return SendResult("PROVIDER_ERROR", "SMTP_AUTH")
        except smtplib.SMTPResponseException as e:
            kind = "TEMPORARY" if 400 <= e.smtp_code < 500 else "PROVIDER_ERROR"
            return SendResult(kind, f"SMTP_{e.smtp_code}")
        except (smtplib.SMTPException, OSError, ssl.SSLError):
            return SendResult("TEMPORARY", "SMTP_CONNECTION")
        return SendResult("SENT", None, str(msg["Message-ID"]).strip("<>"))
