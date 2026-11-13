"""A signed, shareable market context for the public pricing page (ZID-UX-PRICE-004 WF-P01: "market choice preserves context in a signed shareable link, never in an untrusted client amount"). Pure.

The link carries only the market a buyer chose: the subject country, the payer country, the document issuer's country and the data region, with when it was made and when it stops working. It carries
no amount, no price, no plan and no identity. It is signed with the platform's internal MAC key, so a changed field or a forged link fails verification; it expires; and following it only pre-fills the
market choice, which the server then judges afresh, so an old link can never show an offer that is no longer there.
"""

from __future__ import annotations

import base64
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

DOMAIN = b"pricing-context-v1|"
DEFAULT_TTL_S = 14 * 86400
MAX_TTL_S = 30 * 86400
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_REGION = re.compile(r"^[A-Za-z0-9._-]{2,40}$")


class LinkError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Context:
    subject_country: str | None = None
    payer_country: str | None = None
    issuer_country: str | None = None
    operating_region: str | None = None

    def named(self) -> bool:
        return any(
            (self.subject_country, self.payer_country, self.issuer_country, self.operating_region)
        )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def check_context(ctx: Context) -> None:
    for label, value in (
        ("subject_country", ctx.subject_country),
        ("payer_country", ctx.payer_country),
        ("issuer_country", ctx.issuer_country),
    ):
        if value is not None and not _COUNTRY.match(value):
            raise LinkError("CONTEXT_INVALID", label)
    if ctx.operating_region is not None and not _REGION.match(ctx.operating_region):
        raise LinkError("CONTEXT_INVALID", "operating_region")
    if not ctx.named():
        raise LinkError("CONTEXT_EMPTY", "a link needs at least one market field")


def make(
    ctx: Context,
    mac: Callable[[bytes], bytes],
    *,
    now: float | None = None,
    ttl_s: int = DEFAULT_TTL_S,
) -> tuple[str, int]:
    """The token and the second it expires. The payload is canonical JSON with sorted keys, so the same choice at the same second gives the same token."""
    check_context(ctx)
    if not 60 <= ttl_s <= MAX_TTL_S:
        raise LinkError("TTL_INVALID", f"60 to {MAX_TTL_S} seconds")
    issued = int(now if now is not None else time.time())
    payload = {
        "v": 1,
        "s": ctx.subject_country,
        "p": ctx.payer_country,
        "i": ctx.issuer_country,
        "r": ctx.operating_region,
        "iat": issued,
        "exp": issued + ttl_s,
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_b64(mac(DOMAIN + body.encode()))}", issued + ttl_s


def read(
    token: str, mac: Callable[[bytes], bytes], *, now: float | None = None
) -> tuple[Context, int]:
    """The market a valid, unexpired link carries and when it expires. A forged, altered, malformed or expired link raises; nothing in it is trusted before the signature checks."""
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 1024:
        raise LinkError("LINK_INVALID")
    body, sig = token.split(".")
    try:
        given = _unb64(sig)
    except Exception as e:
        raise LinkError("LINK_INVALID") from e
    if not hmac.compare_digest(given, mac(DOMAIN + body.encode())):
        raise LinkError("LINK_INVALID")
    try:
        payload = json.loads(_unb64(body))
    except Exception as e:
        raise LinkError("LINK_INVALID") from e
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or set(payload) != {"v", "s", "p", "i", "r", "iat", "exp"}
    ):
        raise LinkError("LINK_INVALID")
    exp = payload["exp"]
    if not isinstance(exp, int) or (now if now is not None else time.time()) >= exp:
        raise LinkError("LINK_EXPIRED")
    ctx = Context(payload["s"], payload["p"], payload["i"], payload["r"])
    check_context(ctx)
    return ctx, exp
