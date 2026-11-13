"""ZID-UX-PRICE-004 acceptance row P47: no live card number (PAN) or security code (CVC) enters an application log or a span attribute.

The card is entered on the payment processor's hosted page, so the application never holds one; this is the second line, that if one ever reached a log line in any common shape it is
replaced before it is written. The numbers below are public processor test numbers, not real cards.
"""

from __future__ import annotations

import io
import logging

import pytest
from zid_telemetry import JsonFormatter
from zid_telemetry.redaction import REDACTED, redact, scrub

TEST_PANS = [
    "4111111111111111",  # Visa test number, contiguous
    "4111 1111 1111 1111",
    "4111-1111-1111-1111",
    "5555555555554444",  # Mastercard test number
    "378282246310005",  # American Express test number, contiguous
    "3782 822463 10005",  # American Express 4-6-5
    "4000056655665556",
    "6011111111111117",
    "4222222222222",  # 13-digit Visa test number
    "6200000000000005",
]


@pytest.mark.parametrize("pan", TEST_PANS)
@pytest.mark.parametrize(
    "shape", ["{p}", "card {p} declined", "number={p}", "payment failed for {p}.", '{{"x": "{p}"}}']
)
def test_a_card_number_is_redacted_in_every_shape(pan: str, shape: str) -> None:
    out = scrub(shape.format(p=pan))
    assert REDACTED in out
    digits = "".join(c for c in pan if c.isdigit())
    assert digits not in "".join(c for c in out if c.isdigit()), out


@pytest.mark.parametrize(
    "text",
    [
        "cvc=123",
        "CVC: 4567",
        "cvv 123",
        '"cvc": "123"',
        "cvv2=987",
        "security code: 123",
        "security_code=1234",
        "card verification value = 321",
        "csc:123",
        "cid=1234",
    ],
)
def test_a_security_code_is_redacted(text: str) -> None:
    out = scrub(text)
    assert REDACTED in out
    assert not any(ch.isdigit() for ch in out), out


@pytest.mark.parametrize(
    "text",
    [
        'card_number="4242 4242 4242 4242"',
        "pan: 4242424242424242",
        "Card Number = 4242-4242-4242-4242",
        '"card_number": "4242424242424242", "cvc": "123"',
    ],
)
def test_a_labelled_card_field_is_redacted_whole(text: str) -> None:
    out = scrub(text)
    assert REDACTED in out and not any(ch.isdigit() for ch in out), out


@pytest.mark.parametrize(
    "text",
    [
        "tenant 01a1d4d0-1677-7d58-b1f7-981a584e449 started",
        "duration_ms=1234 status=200",
        "amount=12345 currency=EUR",
        "request req_01a1d4d0167a77d58b1f7981a584e449 handled",
        "version 4 of the rate card",
        "invoice 2026-11 total 100.00",
        "order 123456789 accepted",  # nine digits: not a card length
    ],
)
def test_ordinary_numbers_and_identifiers_are_left_alone(text: str) -> None:
    assert scrub(text) == text


def test_a_log_line_and_a_span_attribute_never_carry_a_card() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("svc", "uk-1"))
    log = logging.getLogger("p47")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("payment declined for 4111 1111 1111 1111 cvc=123")
    line = stream.getvalue()
    assert REDACTED in line and "4111" not in line and "cvc=123" not in line
    attrs = redact({"zoiko.reason_code": "card 4242424242424242 cvv 123"})
    assert "4242" not in str(attrs) and "123" not in str(attrs)
