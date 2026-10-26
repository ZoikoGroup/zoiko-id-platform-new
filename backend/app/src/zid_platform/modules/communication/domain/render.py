"""Rendering one email from one template (Communication Estate §03, §04). Pure domain, no I/O.

Fails closed: a missing, unknown, over-long or malformed merge value, an unrouted link or a stray merge tag
raises `RenderError` and nothing is sent (§03 "reject unresolved mandatory placeholders"). Output is the
subject, preheader, an equivalent plain-text part and a responsive, accessible HTML part, built from the
copybook's exact wrapper: greeting, event copy, the one labelled action with its destination spelled out,
sign-off, and the service or marketing footer.

Links are never taken from the caller. A route field is resolved from an allowlist of first-party HTTPS
paths; a token field is that allowlisted path plus an opaque single-use token. Tokens and one-time codes
are rendered into the message and nowhere else (`Rendered` has no `__repr__` that shows them).
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zid_platform.modules.communication.domain.catalogue import (
    PLACEHOLDER,
    FieldKind,
    MessageClass,
    Template,
    field_kind,
    max_length,
)

SIGN_OFF = ("Thank you,", "The Zoiko iD Team", "Zoiko iD is a Zoiko Tech Inc. platform.")
SERVICE_FOOTER = (
    "This is an account, security or service message from Zoiko iD. Never send us passwords, "
    "recovery codes or private keys by email. Help: {support_url}."
)
MARKETING_FOOTER = (
    "You are receiving this because {basis}. Manage preferences: {preferences_url}. "
    "Unsubscribe: {unsubscribe_url}. {entity} • {address}."
)
_REFERENCE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_CODE = re.compile(r"^[0-9A-Z]{6,10}$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{16,512}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
_ZERO_DECIMAL = frozenset(
    [
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    ]
)
# A header must be one short line, and a high-risk subject must not be a vehicle for a link or an address.
_SUBJECT_MAX = 150
_NO_LINKS_IN_HEADERS = re.compile(r"(?:https?://|www\.|@)", re.IGNORECASE)


class RenderError(ValueError):
    """The message cannot be made safely. `code` is stable and carries no value that was supplied."""

    def __init__(self, code: str, field_name: str | None = None) -> None:
        super().__init__(f"{code}:{field_name}" if field_name else code)
        self.code = code
        self.field = field_name


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class LinkPolicy:
    """The signed first-party allowlist (§03 SAFE LINKS): one HTTPS origin and the paths links may take."""

    base_url: str
    routes: Mapping[str, str]
    allow_localhost_http: bool = False

    def __post_init__(self) -> None:
        base = self.base_url
        ok = base.startswith("https://") or (
            self.allow_localhost_http
            and re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$", base)
        )
        if not ok or any(c in base for c in ("?", "#", "@", " ")) or base.endswith("/"):
            raise ValueError("the link base must be a bare HTTPS origin")
        for name, path in self.routes.items():
            if (
                not path.startswith("/")
                or "//" in path
                or ".." in path
                or "?" in path
                or "#" in path
            ):
                raise ValueError(f"route {name} is not a plain absolute path")

    def routed(self, field_name: str) -> bool:
        return field_name in self.routes

    def url_for(self, field_name: str, supplied: object) -> str:
        path = self.routes.get(field_name)
        if path is None:
            raise RenderError("ROUTE_UNCONFIGURED", field_name)
        kind = field_kind(field_name)
        if kind is FieldKind.URL_TOKEN:
            if not isinstance(supplied, str) or not _TOKEN.match(supplied):
                raise RenderError("MERGE_INVALID", field_name)
            return f"{self.base_url}{path}?t={quote(supplied, safe='')}"
        if supplied is None:
            return f"{self.base_url}{path}"
        if not isinstance(supplied, str) or not _OBJECT_ID.match(supplied):
            raise RenderError("MERGE_INVALID", field_name)
        return f"{self.base_url}{path}/{supplied}"


@dataclass(frozen=True, slots=True)
class RenderContext:
    """What the wrapper needs besides the event copy. All of it is server configuration."""

    sender_display_name: str
    links: LinkPolicy
    support_url: str
    locale: str = "en-US"
    timezone: str = "UTC"
    recipient_display_name: str | None = None
    # marketing footer and one-click unsubscribe (RFC 8058)
    marketing_basis: str | None = None
    preferences_url: str | None = None
    unsubscribe_url: str | None = None
    legal_entity: str | None = None
    mailing_address: str | None = None


@dataclass(frozen=True, slots=True)
class Rendered:
    template_id: str
    template_version: str
    subject: str
    preheader: str
    text: str = field(repr=False)
    html: str = field(repr=False)
    message_class: MessageClass
    action_label: str | None = None
    unsubscribe_url: str | None = None


def _single_line(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())


def format_datetime(value: datetime, tz_name: str) -> str:
    """US-English master format in the recipient's zone: `Oct 25, 2026, 2:30 PM UTC`."""
    if value.tzinfo is None:
        raise RenderError("MERGE_INVALID", "datetime_not_aware")
    zone: tzinfo
    if tz_name.upper() == "UTC":
        zone = UTC  # no tz database needed (Windows has none unless `tzdata` is installed)
    else:
        try:
            zone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC  # a safe fallback, never a failure for a bad preference
    local = value.astimezone(zone)
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {local.year}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} {local.tzname()}"


def format_money(value: Money) -> str:
    cur = value.currency.upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise RenderError("MERGE_INVALID", "currency")
    places = 0 if cur in _ZERO_DECIMAL else 2
    quantized = value.amount.quantize(Decimal(1).scaleb(-places))
    return f"{cur} {quantized:,.{places}f}"


def _value(template: Template, name: str, raw: object, ctx: RenderContext) -> str:
    kind = field_kind(name)
    if kind is FieldKind.DATETIME:
        if not isinstance(raw, datetime):
            raise RenderError("MERGE_INVALID", name)
        return format_datetime(raw, ctx.timezone)
    if kind is FieldKind.MONEY:
        if not isinstance(raw, Money):
            raise RenderError("MERGE_INVALID", name)
        return format_money(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise RenderError("MERGE_MISSING", name)
    text = _single_line(raw)
    if "{{" in text or "}}" in text:
        raise RenderError("MERGE_INVALID", name)
    if len(text) > max_length(name):
        raise RenderError("MERGE_TOO_LONG", name)
    if kind is FieldKind.CODE and not _CODE.match(text):
        raise RenderError("MERGE_INVALID", name)
    if kind is FieldKind.REFERENCE and not _REFERENCE.match(text):
        raise RenderError("MERGE_INVALID", name)
    return text


def _substitute(
    text: str, values: Mapping[str, str], *, escape: bool, urls: Mapping[str, str] | None = None
) -> str:
    """Fill `{{field}}` placeholders. A link field inside the copy is the allowlisted URL, as a link in HTML."""
    urls = urls or {}

    def one(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in urls:
            u = html.escape(urls[name], quote=True)
            return f'<a href="{u}">{u}</a>' if escape else urls[name]
        return html.escape(values[name]) if escape else values[name]

    return PLACEHOLDER.sub(one, text)


def _header_text(text: str, template: Template) -> str:
    out = _single_line(text)
    high_risk = template.message_class in (
        MessageClass.SECURITY,
        MessageClass.INCIDENT,
        MessageClass.PRIVACY,
    )
    # §03 "high-risk messages omit user-entered arbitrary content": at least no link or address in a header.
    if high_risk and _NO_LINKS_IN_HEADERS.search(out):
        raise RenderError("HEADER_UNSAFE")
    return out


def render(template: Template, merge: Mapping[str, object], ctx: RenderContext) -> Rendered:
    """Render `template` with `merge`. Raises `RenderError`; never returns a half-filled message."""
    expected = template.fields
    extra = set(merge) - expected - {"recipient_display_name"}
    if extra:
        raise RenderError("MERGE_UNKNOWN", min(extra))

    values: dict[str, str] = {}
    urls: dict[str, str] = {}
    for name in sorted(expected):
        kind = field_kind(name)
        if kind in (FieldKind.URL_ROUTE, FieldKind.URL_TOKEN):
            urls[name] = ctx.links.url_for(name, merge.get(name))
        elif name not in merge:
            raise RenderError("MERGE_MISSING", name)
        else:
            values[name] = _value(template, name, merge[name], ctx)

    subject = _header_text(_substitute(template.subject, values, escape=False), template)
    preheader = _header_text(_substitute(template.preheader, values, escape=False), template)
    if not subject or len(subject) > _SUBJECT_MAX:
        raise RenderError("HEADER_UNSAFE")

    display = merge.get("recipient_display_name", ctx.recipient_display_name)
    greeting = "Hello,"
    if isinstance(display, str) and display.strip():
        shown = _single_line(display)
        if len(shown) <= 100 and "{{" not in shown:
            greeting = f"Hello {shown},"

    body_text = [_substitute(p, values, escape=False, urls=urls) for p in template.paragraphs]
    body_html = [_substitute(p, values, escape=True, urls=urls) for p in template.paragraphs]

    action_url = urls.get(template.action.url_field) if template.action else None

    if template.message_class is MessageClass.MARKETING:
        needed = (
            ctx.marketing_basis,
            ctx.preferences_url,
            ctx.unsubscribe_url,
            ctx.legal_entity,
            ctx.mailing_address,
        )
        if not all(needed):
            raise RenderError("MARKETING_FOOTER_INCOMPLETE")
        footer = MARKETING_FOOTER.format(
            basis=ctx.marketing_basis,
            preferences_url=ctx.preferences_url,
            unsubscribe_url=ctx.unsubscribe_url,
            entity=ctx.legal_entity,
            address=ctx.mailing_address,
        )
    else:
        footer = SERVICE_FOOTER.format(support_url=ctx.support_url)

    text_parts = [greeting, "", *[x for p in body_text for x in (p, "")]]
    if action_url and template.action:
        text_parts += [f"{template.action.label}: {action_url}", ""]
    text_parts += [SIGN_OFF[0], SIGN_OFF[1], SIGN_OFF[2], "", "--", footer, ""]
    text = "\n".join(text_parts)

    html_body = _html(template, subject, preheader, greeting, body_html, action_url, footer, ctx)
    return Rendered(
        template_id=template.id,
        template_version=template.version,
        subject=subject,
        preheader=preheader,
        text=text,
        html=html_body,
        message_class=template.message_class,
        action_label=template.action.label if template.action else None,
        unsubscribe_url=ctx.unsubscribe_url
        if template.message_class is MessageClass.MARKETING
        else None,
    )


def _html(
    template: Template,
    subject: str,
    preheader: str,
    greeting: str,
    body: list[str],
    action_url: str | None,
    footer: str,
    ctx: RenderContext,
) -> str:
    e = html.escape
    lang = e(ctx.locale.split("-")[0] if ctx.locale else "en")
    paragraphs = "".join(f'<p style="margin:0 0 16px 0">{p}</p>' for p in body)
    cta = ""
    if action_url and template.action:
        label = e(template.action.label)
        url = e(action_url, quote=True)
        cta = (
            f'<p style="margin:24px 0"><a href="{url}" class="btn" style="background:#0e7490;color:#ffffff;'
            f"text-decoration:none;padding:12px 20px;border-radius:6px;display:inline-block;font-weight:600"
            f'">{label}</a></p>'
            f'<p style="margin:0 0 16px 0;font-size:14px">If the button does not work, copy this address into '
            f'your browser: <a href="{url}">{url}</a></p>'
        )
    return (
        "<!doctype html>"
        f'<html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f"<title>{e(subject)}</title>"
        "<style>@media (prefers-color-scheme: dark){body,.wrap{background:#0b1220!important;color:#e5e7eb!important}"
        "a{color:#67e8f9!important}.btn{background:#0891b2!important;color:#06202a!important}}"
        "@media (max-width:480px){.wrap{padding:16px!important}}</style></head>"
        '<body style="margin:0;background:#f3f4f6;color:#111827;font-family:Arial,Helvetica,sans-serif;'
        'font-size:16px;line-height:1.5">'
        f'<span style="display:none;max-height:0;overflow:hidden;opacity:0">{e(preheader)}</span>'
        '<div class="wrap" style="max-width:600px;margin:0 auto;background:#ffffff;padding:32px">'
        f'<h1 style="font-size:22px;margin:0 0 20px 0">{e(template.title)}</h1>'
        f'<p style="margin:0 0 16px 0">{e(greeting)}</p>'
        f"{paragraphs}{cta}"
        '<p style="margin:24px 0 0 0">Thank you,<br>The Zoiko iD Team<br>'
        "Zoiko iD is a Zoiko Tech Inc. platform.</p>"
        '<hr style="border:0;border-top:1px solid #d1d5db;margin:24px 0">'
        f'<p style="margin:0;font-size:13px;color:#4b5563">{e(footer)}</p>'
        "</div></body></html>"
    )
