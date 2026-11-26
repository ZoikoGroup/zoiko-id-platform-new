"""security.txt (RFC 9116) for the Trust Center (ZID-CP-WF12 v2.0, WF-12.07). Pure domain, no I/O.

A file that has expired is worse than none, so a render with an `Expires` in the past is refused, and a checker reads back what is served and says what is wrong with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_VALIDITY = timedelta(days=366)  # RFC 9116 recommends under a year


@dataclass(frozen=True)
class SecurityTxt:
    contacts: tuple[str, ...]  # mailto:, https: or tel: URIs
    expires: datetime
    canonical: tuple[str, ...]
    policy: str | None = None
    acknowledgments: str | None = None
    preferred_languages: tuple[str, ...] = ("en",)
    encryption: str | None = None


def problems(t: SecurityTxt, *, now: datetime) -> list[str]:
    out: list[str] = []
    if not t.contacts:
        out.append("Contact is required.")
    for c in t.contacts:
        if not c.startswith(("mailto:", "https://", "tel:")):
            out.append(f"Contact must be a mailto:, https: or tel: URI: {c}")
    if t.expires.tzinfo is None:
        out.append("Expires must carry a time zone.")
    elif t.expires <= now:
        out.append("Expires is in the past.")
    elif t.expires - now > MAX_VALIDITY:
        out.append("Expires is more than a year away; RFC 9116 asks for less.")
    for u in (*t.canonical, *(x for x in (t.policy, t.acknowledgments, t.encryption) if x)):
        if not u.startswith("https://"):
            out.append(f"Every URI must be https: {u}")
    return out


def render(t: SecurityTxt, *, now: datetime | None = None) -> str:
    """The file body. Raises on anything RFC 9116 forbids, so a bad file is never served."""
    bad = problems(t, now=now or datetime.now(UTC))
    if bad:
        raise ValueError("; ".join(bad))
    lines = [f"Contact: {c}" for c in t.contacts]
    lines.append("Expires: " + t.expires.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    if t.encryption:
        lines.append(f"Encryption: {t.encryption}")
    if t.acknowledgments:
        lines.append(f"Acknowledgments: {t.acknowledgments}")
    lines.append("Preferred-Languages: " + ", ".join(t.preferred_languages))
    lines += [f"Canonical: {c}" for c in t.canonical]
    if t.policy:
        lines.append(f"Policy: {t.policy}")
    return "\n".join(lines) + "\n"


def check(served: str, *, content_type: str, served_url: str, now: datetime) -> list[str]:
    """Read back what a live URL returned. Fields, header and canonical must be right."""
    out: list[str] = []
    if not content_type.lower().startswith("text/plain") or "utf-8" not in content_type.lower():
        out.append("Content-Type must be text/plain; charset=utf-8.")
    fields: dict[str, list[str]] = {}
    for raw in served.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition(":")
        fields.setdefault(k.strip().lower(), []).append(v.strip())
    if "contact" not in fields:
        out.append("Contact is missing.")
    exp = fields.get("expires", [])
    if len(exp) != 1:
        out.append("Expires must appear exactly once.")
    else:
        try:
            when = datetime.fromisoformat(exp[0])
            if when <= now:
                out.append("Expires is in the past.")
        except ValueError:
            out.append("Expires is not an ISO 8601 date-time.")
    canon = fields.get("canonical", [])
    if canon and served_url not in canon:
        out.append("None of the Canonical URIs is the URL this was served from.")
    return out


def dead_links(
    links: Sequence[tuple[str, int]],
    *,
    placeholders: Sequence[str] = ("#", "javascript:", "about:blank"),
) -> list[str]:
    """Pass (href, status) pairs from a crawl. A placeholder anchor or a non-2xx/3xx target is a defect in a trust page."""
    out: list[str] = []
    for href, status in links:
        if href.strip() in ("", *placeholders) or href.startswith(
            tuple(p for p in placeholders if p != "#")
        ):
            out.append(f"Placeholder link: {href!r}")
        elif status >= 400 or status == 0:
            out.append(f"Unreachable ({status}): {href}")
    return out
