"""SMTP delivery (Communication Estate §03 EMAIL TRANSPORT): TLS first, login, headers, and every server answer
mapped to one outcome. `smtplib` is replaced by a recorder, so nothing touches a network."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest
from logcapture import capture
from zid_platform.modules.communication.domain import catalogue as cat
from zid_platform.modules.communication.domain.render import LinkPolicy, RenderContext, render
from zid_platform.modules.communication.infrastructure import smtp as smtp_module
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue
from zid_platform.modules.communication.infrastructure.smtp import (
    SmtpConfig,
    SmtpSender,
    build_message,
)
from zid_platform.modules.communication.ports import build_sender

CATALOGUE = load_catalogue()
CTX = RenderContext(
    "Zoiko iD",
    LinkPolicy("https://id.example.com", {f: f"/r/{f}" for t in CATALOGUE for f in t.url_fields}),
    "https://id.example.com/help",
    marketing_basis="you asked for updates",
    preferences_url="https://id.example.com/p",
    unsubscribe_url="https://id.example.com/email/unsubscribe?r=a&l=b&s=c",
    legal_entity="Zoiko Tech Inc.",
    mailing_address="1 Example Street",
)


def rendered(template_id: str = "ZID-EM-130"):  # type: ignore[no-untyped-def]
    t = CATALOGUE.get(template_id)
    merge: dict[str, object] = {}
    for f in t.fields:
        k = cat.field_kind(f)
        merge[f] = (
            datetime(2026, 10, 25, tzinfo=UTC)
            if k is cat.FieldKind.DATETIME
            else None
            if k is cat.FieldKind.URL_ROUTE
            else "Sample"
        )
        if k is cat.FieldKind.MONEY:
            from zid_platform.modules.communication.domain.render import Money

            merge[f] = Money(Decimal(10), "USD")
    return render(t, merge, CTX)


CFG = SmtpConfig(
    "smtp.example.com", 587, "sender@example.com", "s3cret-pass", "Zoiko iD", "sender@example.com"
)


class FakeSMTP:
    """Records the conversation; `script` decides how it ends."""

    calls: ClassVar[list[tuple[str, Any]]] = []
    fail_on: ClassVar[dict[str, Exception]] = {}

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        FakeSMTP.calls.append(("connect", (host, port, timeout, context is not None)))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def _do(self, name: str, arg: Any = None) -> None:
        FakeSMTP.calls.append((name, arg))
        if name in FakeSMTP.fail_on:
            raise FakeSMTP.fail_on[name]

    def ehlo(self) -> None:
        self._do("ehlo")

    def starttls(self, context: ssl.SSLContext) -> None:
        self._do("starttls", context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname)

    def login(self, user: str, password: str) -> None:
        self._do("login", (user, password))

    def send_message(self, msg: Any, from_addr: str, to_addrs: list[str]) -> dict[str, Any]:
        self._do("send", (from_addr, to_addrs, msg["Subject"]))
        return {}


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.calls = []
    FakeSMTP.fail_on = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


def send(cfg: SmtpConfig = CFG, to: str = "to@example.com"):  # type: ignore[no-untyped-def]
    return asyncio.run(SmtpSender(cfg).send(to=to, rendered=rendered(), message_id="mid-1"))


def test_a_message_is_sent_over_verified_starttls_before_the_login() -> None:
    result = send()
    assert result.sent and result.provider_ref and "@example.com" in result.provider_ref
    order = [name for name, _ in FakeSMTP.calls]
    assert order == ["connect", "ehlo", "starttls", "ehlo", "login", "send"]
    assert dict(FakeSMTP.calls)["starttls"] is True  # certificate and hostname verification are on
    assert dict(FakeSMTP.calls)["send"][:2] == ("sender@example.com", ["to@example.com"])


def test_port_465_uses_implicit_tls_and_never_a_plain_login() -> None:
    result = send(
        SmtpConfig(
            "smtp.example.com",
            465,
            "u@example.com",
            "pw",
            "Zoiko iD",
            "u@example.com",
            implicit_tls=True,
        )
    )
    assert result.sent
    assert "starttls" not in [name for name, _ in FakeSMTP.calls]


def test_every_server_answer_maps_to_one_outcome_and_none_raises() -> None:
    cases: list[tuple[str, Exception, str, str]] = [
        (
            "send",
            smtplib.SMTPRecipientsRefused({"to@example.com": (550, b"no such user")}),
            "RECIPIENT_REJECTED",
            "SMTP_550",
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"to@example.com": (450, b"mailbox busy")}),
            "TEMPORARY",
            "SMTP_450",
        ),
        (
            "login",
            smtplib.SMTPAuthenticationError(535, b"bad credentials"),
            "PROVIDER_ERROR",
            "SMTP_AUTH",
        ),
        (
            "send",
            smtplib.SMTPSenderRefused(553, b"sender not allowed", "x"),
            "PROVIDER_ERROR",
            "SMTP_553",
        ),
        ("send", smtplib.SMTPDataError(421, b"try later"), "TEMPORARY", "SMTP_421"),
        ("starttls", smtplib.SMTPException("tls failed"), "TEMPORARY", "SMTP_CONNECTION"),
        ("send", ConnectionResetError("reset"), "TEMPORARY", "SMTP_CONNECTION"),
        ("send", ssl.SSLError("bad cert"), "TEMPORARY", "SMTP_CONNECTION"),
        ("send", RuntimeError("anything else"), "TEMPORARY", "SMTP_UNEXPECTED"),
    ]
    for step, error, kind, code in cases:
        FakeSMTP.calls, FakeSMTP.fail_on = [], {step: error}
        result = send()
        assert (result.kind, result.code) == (kind, code), (step, error)
        assert not result.sent


def test_the_wire_message_has_the_headers_the_copybook_requires() -> None:
    msg = build_message(
        CFG, to="to@example.com", rendered=rendered("ZID-EM-130"), message_id="9f1c-aaaa"
    )
    assert msg["From"] == "Zoiko iD <sender@example.com>" and msg["To"] == "to@example.com"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["X-Zoiko-Template"].startswith("ZID-EM-130/2.0.")
    assert (
        msg["Message-ID"].endswith("@example.com>") and msg["List-Unsubscribe"] is None
    )  # not marketing
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    marketing = build_message(
        CFG, to="to@example.com", rendered=rendered("ZID-EM-142"), message_id="mid"
    )
    assert marketing["List-Unsubscribe"] == "<https://id.example.com/email/unsubscribe?r=a&l=b&s=c>"
    assert marketing["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"  # RFC 8058
    reply = build_message(
        SmtpConfig(
            "h",
            587,
            "u@example.com",
            "pw",
            "Zoiko iD",
            "u@example.com",
            reply_to="help@example.com",
        ),
        to="to@example.com",
        rendered=rendered(),
        message_id="mid",
    )
    assert reply["Reply-To"] == "help@example.com"


def test_the_password_comes_from_a_file_and_is_never_shown() -> None:
    assert "s3cret-pass" not in repr(CFG) and "s3cret-pass" not in str(SmtpSender(CFG).__dict__)


def test_config_loading_checks_the_password_file_and_the_sender(tmp_path: Path) -> None:
    pw = tmp_path / "pw"
    pw.write_text("  hunter2  \n", encoding="utf-8")
    cfg = SmtpConfig.load(
        host="h",
        port=587,
        username="u@example.com",
        password_file=str(pw),
        from_name="Zoiko iD",
        from_address="u@example.com",
    )
    assert cfg.password == "hunter2" and not cfg.implicit_tls
    assert SmtpConfig.load(
        host="h",
        port=465,
        username="u",
        password_file=str(pw),
        from_name="n",
        from_address="u@example.com",
    ).implicit_tls
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        SmtpConfig.load(
            host="h",
            port=587,
            username="u",
            password_file=str(empty),
            from_name="n",
            from_address="u@example.com",
        )
    for bad in ("Name <u@example.com>", "no-at-sign"):
        with pytest.raises(RuntimeError, match="plain address"):
            SmtpConfig.load(
                host="h",
                port=587,
                username="u",
                password_file=str(pw),
                from_name="n",
                from_address=bad,
            )


class S:
    """Just the settings `build_sender` reads."""

    def __init__(self, **kw: Any) -> None:
        self.email_enabled = True
        self.smtp_host = "smtp.example.com"
        self.smtp_port = 587
        self.smtp_username = "u@example.com"
        self.smtp_password_file = None
        self.email_from = "Zoiko iD <u@example.com>"
        self.email_reply_to = None
        self.__dict__.update(kw)


def test_the_sender_is_off_unless_enabled_and_fails_fast_when_half_configured(
    tmp_path: Path,
) -> None:
    assert build_sender(S(email_enabled=False)) is None
    with pytest.raises(RuntimeError, match="ZID_SMTP_PASSWORD_FILE"):
        build_sender(S())
    with pytest.raises(RuntimeError, match="ZID_SMTP_HOST"):
        build_sender(S(smtp_host=None, smtp_password_file="x"))
    pw = tmp_path / "pw"
    pw.write_text("pw", encoding="utf-8")
    sender = build_sender(S(smtp_password_file=str(pw)))
    assert isinstance(sender, SmtpSender)


def test_the_module_never_logs_the_password() -> None:
    FakeSMTP.fail_on = {"send": RuntimeError("boom")}
    with capture("DEBUG") as logs:
        send()
    assert "s3cret-pass" not in logs.text
    assert smtp_module.log.name == "zid.communication.smtp"
