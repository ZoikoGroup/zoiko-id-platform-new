"""P0-01: secrets and identity data are intercepted before anything else touches them (ADR-08, INV-03)."""

from __future__ import annotations

import pytest
from zid_assistant.domain.sensitive import normalize, scan, scan_many

SECRETS = [
    ("-----BEGIN " + "PRIVATE KEY-----\nMIIBVQ", "PRIVATE_KEY"),
    (
        "here is my token eyJ"
        + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
        "JWT",
    ),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwx", "AUTH_HEADER"),
    ("use bearer abcdefghijklmnop1234567890", "BEARER_TOKEN"),
    ("Cookie: session=abc123def456", "COOKIE_HEADER"),
    ("sk-live-abcdefghijklmnop1234", "API_KEY"),
    ("AKI" + "AIOSFODNN7EXAMPLE", "API_KEY"),
    ("gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789", "API_KEY"),
    ("xo" + "xb-1234567890-abcdefghij", "API_KEY"),
    ("my password is hunter2xyz", "PASSWORD"),
    ("passcode: letmein123", "PASSWORD"),
    ("client_secret = s3cr3tvalue99", "SECRET_ASSIGN"),
    ("my OTP is 482913", "OTP"),
    ("verification code: 551203", "OTP"),
    ("482913 is my code", "OTP"),
    ("recovery code: ABCD-EFGH-1234", "RECOVERY_CODE"),
    ("data:image/png;base64,iVBORw0KGgo", "DATA_URL"),
    ("postgres://admin:hunter2@db.example.com/prod", "URL_CREDENTIALS"),
    ("123-45-6789", "SSN"),
    ("my aadhaar is 2345 6789 0123", "AADHAAR"),
    ("PAN ABCDE1234F", "PAN"),
    ("passport number X1234567", "PASSPORT"),
    ("GB82 WEST 1234 5698 7654 32", "IBAN"),
    ("4111 1111 1111 1111", "PAYMENT_CARD"),
    ("A" * 200, "ENCODED_BLOB"),
    ("deadbeef" * 6, "HEX_SECRET"),
]

BENIGN = [
    "What is Zoiko iD?",
    "How does a passkey work compared with a password?",
    "Explain OAuth 2.0 versus OpenID Connect",
    "What is the difference between a one-time password and a passkey?",
    "Does Zoiko iD support SCIM 2.0 provisioning?",
    "Tell me about role-based access control",
    "What does an API key do in general?",
    "Can you explain what a JSON Web Token is?",
    "How many digits are in a 6 digit code in general?",
    "Which countries is registration available in?",
    "I'd like a demo in Q3 2027",
    "Our team has 250 employees across 12 offices",
]


@pytest.mark.parametrize(("text", "category"), SECRETS)
def test_secret_is_blocked_with_category_only(text: str, category: str) -> None:
    f = scan(text)
    assert f.blocked, text
    assert category in f.categories, (text, f.categories)
    # the finding never carries the matched text
    assert all(len(c) < 40 and c.isupper() or "_" in c for c in f.categories)


@pytest.mark.parametrize("text", BENIGN)
def test_benign_question_is_not_blocked(text: str) -> None:
    assert not scan(text).blocked, text


def test_zero_width_and_compat_characters_do_not_evade() -> None:
    assert scan("my pass\u200bword is hunter2xyz").blocked
    assert scan("my ｐａｓｓｗｏｒｄ is hunter2xyz").blocked  # fullwidth letters fold under NFKC
    assert normalize("a\u200bb") == "ab"


def test_scan_many_merges_and_ignores_none() -> None:
    f = scan_many("hello", None, "my password is hunter2xyz", "123-45-6789")
    assert {"PASSWORD", "SSN"} <= f.categories
    assert not scan_many(None, "").blocked


@pytest.mark.parametrize(
    "text",
    [
        "my email is john@example.com",
        "call me on +44 20 7946 0958",
        "my number is 555-123-4567",
        "reach me at (555) 123 4567",
    ],
)
def test_chat_blocks_an_email_or_phone_but_the_plain_scan_does_not(text: str) -> None:
    from zid_assistant.domain.sensitive import scan_chat

    assert (
        scan_chat(text).blocked and not scan(text).blocked
    )  # the handoff form uses `scan` and may carry an email


@pytest.mark.parametrize(
    "text", ["What is ISO 27001?", "version 1.2.3 of the SDK", "Is it 24/7?", "plan for 2026-11-11"]
)
def test_chat_does_not_block_ordinary_questions_with_numbers(text: str) -> None:
    from zid_assistant.domain.sensitive import scan_chat

    assert not scan_chat(text).blocked
