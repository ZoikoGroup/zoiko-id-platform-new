"""Webhook rules (ZID-EVENTS-001 §15-19; FR-API-007): SSRF, RFC 9421 signing, classification, retries."""

import base64
import random
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from zid_platform.modules.webhook.domain.webhooks import (
    RETRY_LADDER,
    DeliveryStatus,
    Outcome,
    WebhookError,
    address_allowed,
    check_resolution,
    classify,
    content_digest,
    next_step,
    sign_request,
    validate_url,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",
        "https://user:pw@hooks.example.com/x",
        "https://hooks.example.com/x#frag",
        "https://hooks.example.com:22/x",
        "https://10.0.0.5/x",
        "https://[::1]/x",
        "ftp://hooks.example.com",
    ],
)
def test_bad_urls_are_rejected(url: str) -> None:
    with pytest.raises(WebhookError):
        validate_url(url)


def test_good_url_is_normalized() -> None:
    assert (
        validate_url(" https://Hooks.Example.com/zoiko?x=1 ")
        == "https://hooks.example.com/zoiko?x=1"
    )


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("192.168.1.1", False),
        ("172.16.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:10.0.0.1", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),
    ],
)
def test_address_policy(address: str, allowed: bool) -> None:
    assert address_allowed(address) is allowed


def test_one_private_record_blocks_the_whole_name() -> None:
    """DNS rebinding: a public and a private answer together are refused."""
    with pytest.raises(WebhookError):
        check_resolution(["93.184.216.34", "10.0.0.1"])
    with pytest.raises(WebhookError):
        check_resolution([])


def test_rfc9421_signature_verifies_with_the_public_key() -> None:
    key = Ed25519PrivateKey.generate()
    body = b'{"id":"019f","type":"com.zoiko.id.mandate.revoked.v1"}'
    signed = sign_request(
        url="https://hooks.example.com/zoiko",
        body=body,
        event_id="019f",
        delivery_id="d1",
        key_id="zid-whk-2026-10",
        created=1760000000,
        sign=key.sign,
    )
    h = signed.headers
    assert h["Content-Digest"] == content_digest(body)
    assert h["Signature-Input"].startswith(
        'zid=("@method" "@authority" "@target-uri" "content-type" '
        '"content-digest" "x-zoiko-event-id");created=1760000000;keyid='
    )
    signature = base64.b64decode(h["Signature"].removeprefix("zid=:").removesuffix(":"))
    key.public_key().verify(signature, signed.signature_base.encode())  # raises if invalid
    assert (
        '"x-zoiko-event-id": 019f' in signed.signature_base
        and '"@method": POST' in signed.signature_base
    )


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (200, Outcome.SUCCESS),
        (204, Outcome.SUCCESS),
        (301, Outcome.REDIRECT),
        (400, Outcome.CONTRACT),
        (401, Outcome.RECEIVER_AUTH),
        (404, Outcome.CONTRACT),
        (408, Outcome.TRANSIENT),
        (410, Outcome.GONE),
        (429, Outcome.TRANSIENT),
        (503, Outcome.TRANSIENT),
        (None, Outcome.TRANSIENT),
    ],
)
def test_response_classification(code: int | None, outcome: Outcome) -> None:
    assert classify(code) is outcome


def test_retry_schedule_and_limits() -> None:
    rng = random.Random(1)
    step = next_step(Outcome.TRANSIENT, attempt=3, now=NOW, rng=rng)
    assert step.status is DeliveryStatus.RETRY_SCHEDULED
    assert NOW <= step.next_attempt_at <= NOW + RETRY_LADDER[3]  # type: ignore[operator]
    assert (
        next_step(Outcome.TRANSIENT, attempt=len(RETRY_LADDER), now=NOW).status
        is DeliveryStatus.EXHAUSTED
    )
    assert (
        next_step(Outcome.CONTRACT, attempt=3, now=NOW).status is DeliveryStatus.FAILED
    )  # bounded
    gone = next_step(Outcome.GONE, attempt=1, now=NOW)
    assert gone.status is DeliveryStatus.FAILED and gone.disable_endpoint
    assert (
        next_step(Outcome.REDIRECT, attempt=1, now=NOW).status is DeliveryStatus.FAILED
    )  # never followed
    honoured = next_step(
        Outcome.TRANSIENT,
        attempt=1,
        now=NOW,
        retry_after=timedelta(minutes=5),
        rng=random.Random(0),
    )
    assert honoured.next_attempt_at >= NOW + timedelta(minutes=5)  # type: ignore[operator]
    capped = next_step(Outcome.TRANSIENT, attempt=1, now=NOW, retry_after=timedelta(days=9))
    assert capped.next_attempt_at <= NOW + timedelta(hours=1, seconds=30)  # type: ignore[operator]
