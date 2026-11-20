"""Local development delivery of WF-05 one-time codes to a mail sink (Mailpit), wired only with ZID_DEV_SIMULATORS (local/test).

No real challenge sender or mail house ships, so without this a local cell can never finish an email, SMS or postal check. Every code
goes out as a plain email to the local SMTP sink: email codes to the address itself, SMS codes to sms-<digits>@sms.localhost, postal
letters to postal-<challenge>@letters.localhost with the address in the body. Never used in a deployed environment.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from zid_platform.modules.verification.application.address_checks import PostalDispatch
from zid_platform.modules.verification.application.contact_challenges import Delivery

_FROM = "Zoiko iD dev codes <dev-codes@localhost>"


class _Sink:
    def __init__(self, host: str, port: int) -> None:
        self._host, self._port = host, port

    def _mail(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = _FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as s:
            s.send_message(msg)


class DevChallengeSender(_Sink):
    async def send(self, delivery: Delivery) -> None:
        channel = getattr(delivery.channel, "value", str(delivery.channel))
        if channel == "EMAIL":
            to = delivery.destination
        else:
            to = f"sms-{''.join(c for c in delivery.destination if c.isdigit())}@sms.localhost"
        body = f"Channel: {channel}\nTo: {delivery.destination}\nCode: {delivery.code}\nExpires: {delivery.expires_at.isoformat()}\n"
        await asyncio.to_thread(
            self._mail, to, f"[{channel}] Your Zoiko iD code: {delivery.code}", body
        )


class DevPostalSender(_Sink):
    async def send(self, dispatch: PostalDispatch) -> str:
        address = "\n".join(f"{k}: {v}" for k, v in dispatch.address.items())
        body = f"Letter code: {dispatch.code}\nExpires: {dispatch.expires_at.isoformat()}\n\nPosted to:\n{address}\n"
        ref = f"dev-letter-{dispatch.challenge_id[:8]}"
        await asyncio.to_thread(
            self._mail,
            f"postal-{dispatch.challenge_id[:8]}@letters.localhost",
            f"[POSTAL] Letter code: {dispatch.code}",
            body,
        )
        return ref
