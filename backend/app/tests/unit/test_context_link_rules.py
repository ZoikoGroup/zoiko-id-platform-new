"""The signed market link (ZID-UX-PRICE-004 WF-P01): only the market, signed, expiring, and never trusted before the signature checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from zid_platform.modules.commercial.domain import context_link as cl

KEY = b"unit-test-key"
OTHER = b"another-key"


def mac(key: bytes = KEY):
    return lambda m: hmac.new(key, m, hashlib.sha256).digest()


CTX = cl.Context("DE", "FR", "SE", "eu-central")


def test_a_link_round_trips_and_carries_only_the_market() -> None:
    token, exp = cl.make(CTX, mac(), now=1_000)
    assert exp == 1_000 + cl.DEFAULT_TTL_S
    got, got_exp = cl.read(token, mac(), now=1_001)
    assert got == CTX and got_exp == exp
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert set(payload) == {"v", "s", "p", "i", "r", "iat", "exp"}, (
        "no amount, plan, price or identity is in a link"
    )


def test_the_same_choice_at_the_same_second_is_the_same_link_and_a_different_choice_is_not() -> (
    None
):
    assert cl.make(CTX, mac(), now=5)[0] == cl.make(CTX, mac(), now=5)[0]
    assert (
        cl.make(CTX, mac(), now=5)[0]
        != cl.make(cl.Context("DE", "FR", "SE", "eu-west"), mac(), now=5)[0]
    )


def test_a_forged_altered_or_cross_key_link_fails() -> None:
    token, _ = cl.make(CTX, mac(), now=10)
    body, sig = token.split(".")
    with pytest.raises(cl.LinkError) as wrong_key:
        cl.read(token, mac(OTHER), now=11)
    assert wrong_key.value.code == "LINK_INVALID"
    altered = json.loads(base64.urlsafe_b64decode(body + "=="))
    altered["p"] = "US"
    fake_body = (
        base64.urlsafe_b64encode(
            json.dumps(altered, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    for bad in (
        f"{fake_body}.{sig}",
        f"{body}.AAAA",
        token + "x",
        "no-dot",
        "",
        "a.b.c",
        "x" * 2000,
    ):
        with pytest.raises(cl.LinkError) as e:
            cl.read(bad, mac(), now=11)
        assert e.value.code == "LINK_INVALID", bad


def test_an_expired_link_is_refused_at_the_second_it_ends() -> None:
    token, exp = cl.make(CTX, mac(), now=100, ttl_s=3600)
    cl.read(token, mac(), now=exp - 1)
    with pytest.raises(cl.LinkError) as e:
        cl.read(token, mac(), now=exp)
    assert e.value.code == "LINK_EXPIRED"


@pytest.mark.parametrize(
    ("ctx", "ttl", "code"),
    [
        (cl.Context(), cl.DEFAULT_TTL_S, "CONTEXT_EMPTY"),
        (cl.Context(subject_country="Germany"), cl.DEFAULT_TTL_S, "CONTEXT_INVALID"),
        (cl.Context(operating_region="a b"), cl.DEFAULT_TTL_S, "CONTEXT_INVALID"),
        (cl.Context(subject_country="DE"), 10, "TTL_INVALID"),
        (cl.Context(subject_country="DE"), cl.MAX_TTL_S + 1, "TTL_INVALID"),
    ],
)
def test_a_link_is_refused_for_the_named_reason(ctx: cl.Context, ttl: int, code: str) -> None:
    with pytest.raises(cl.LinkError) as e:
        cl.make(ctx, mac(), ttl_s=ttl)
    assert e.value.code == code


def test_a_signed_payload_with_an_extra_field_or_a_bad_value_is_still_refused() -> None:
    """Even a payload the platform signed itself is not trusted if its shape is not exactly what a link carries."""
    for payload in (
        {"v": 1, "s": "DE", "p": None, "i": None, "r": None, "iat": 1, "exp": 10**12, "price": "1"},
        {"v": 2, "s": "DE", "p": None, "i": None, "r": None, "iat": 1, "exp": 10**12},
        {"v": 1, "s": "Germany", "p": None, "i": None, "r": None, "iat": 1, "exp": 10**12},
    ):
        body = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        sig = base64.urlsafe_b64encode(mac()(cl.DOMAIN + body.encode())).rstrip(b"=").decode()
        with pytest.raises(cl.LinkError):
            cl.read(f"{body}.{sig}", mac(), now=5)
