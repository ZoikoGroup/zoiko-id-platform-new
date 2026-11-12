"""Intake and delivery (INT-010; ADR-09, INV-05): consent, minimal fields, DLP, idempotency, outbox retry, truthful receipts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from assistant_helpers import make_settings
from zid_assistant.application.intake import IntakeCrypto, IntakeService
from zid_assistant.application.retention import erase_intake, purge_expired
from zid_assistant.domain import intake as dom
from zid_assistant.infrastructure.delivery import (
    LogOnlyAdapter,
    OutboxWorker,
    PermanentRejection,
    TemporaryFailure,
    WebhookAdapter,
    check_receiver_url,
)
from zid_assistant.infrastructure.flags import Flags
from zid_assistant.infrastructure.store import MemoryStore

ON = Flags(True, False, True, True, True, False)
OFF = Flags(True, False, False, False, False, False)
KEY = "11111111-aaaa-bbbb-cccc-000000000001"


def service(
    store: MemoryStore | None = None, **kw: object
) -> tuple[IntakeService, MemoryStore, IntakeCrypto]:
    st = store or MemoryStore()
    s = make_settings(
        intake_countries=["US", "GB"],
        intake_notice_sales_id="notice-sales-2026-11",
        intake_notice_support_id="notice-support-2026-11",
        **kw,
    )
    crypto = IntakeCrypto(s)
    notices = {"SALES": ("notice-sales-2026-11", "n"), "SUPPORT": ("notice-support-2026-11", "n")}
    return IntakeService(st, s, crypto, notices), st, crypto


def sales(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "form_revision": dom.SALES_FORM_REV,
        "purpose": "sales_inquiry",
        "country_code": "US",
        "locale": "en-US",
        "notice_accepted": True,
        "fields": {
            "email": "Jane@Example.COM",
            "inquiry_type": "DEMO",
            "message": "We would like a demo next quarter.",
        },
    }
    body.update(over)
    return body


def support(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "form_revision": dom.SUPPORT_FORM_REV,
        "purpose": "support_request",
        "country_code": "GB",
        "locale": "en-GB",
        "notice_accepted": True,
        "fields": {
            "email": "dev@example.com",
            "topic": "API_GUIDANCE",
            "summary": "Where can I find the public API reference?",
        },
    }
    body.update(over)
    return body


@pytest.mark.anyio
async def test_a_valid_sales_request_is_stored_encrypted_with_an_outbox_event() -> None:
    svc, st, crypto = service()
    acc = await svc.submit("SALES", sales(), KEY, ON)
    assert acc.reference.startswith("ZR-") and len(acc.reference) == 11
    row = next(iter(st.intakes.values()))
    assert (
        row.status == "ACCEPTED"
        and row.encrypted_fields
        and b"jane@example.com" not in row.encrypted_fields
    )
    assert crypto.open(row.encrypted_fields)["email"] == "jane@example.com"  # normalized
    assert len(st.outbox) == 1 and not any("example.com" in str(o) for o in st.outbox.values()), (
        "the outbox event carries no personal data"
    )
    assert st.audits[-1]["event_type"] == "INTAKE_ACCEPTED" and "example.com" not in str(st.audits)


@pytest.mark.anyio
async def test_forms_are_unavailable_unless_flag_country_and_notice_are_all_present() -> None:
    svc, _, _ = service()
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit("SALES", sales(), KEY, OFF)
    assert e.value.code == "REGION_OR_POLICY_BLOCK"
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit("SALES", sales(country_code="FR"), KEY, ON)  # a market with no live form
    assert e.value.code == "REGION_OR_POLICY_BLOCK"
    s = make_settings()  # no countries, no notices: everything off (OD-013)
    bare = IntakeService(MemoryStore(), s, IntakeCrypto(s), {})
    assert not bare.available("SALES", ON) and bare.config(ON)["sales"] == {
        "available": False,
        "form_revision": None,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "over", [{"notice_accepted": False}, {"notice_accepted": "yes"}, {"form_revision": "stale-rev"}]
)
async def test_consent_must_be_explicit_and_bound_to_the_live_form_revision(
    over: dict[str, object],
) -> None:
    svc, st, _ = service()
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit("SALES", sales(**over), KEY, ON)
    assert e.value.code == "INVALID_REQUEST" and not st.intakes, "no consent means no record"


@pytest.mark.anyio
async def test_unknown_top_level_and_field_keys_are_rejected() -> None:
    svc, st, _ = service()
    for body in (
        sales(transcript="hello"),
        sales(tenant_id="x"),
        sales(fields={"email": "a@b.co", "inquiry_type": "DEMO", "account_id": "1"}),
    ):
        with pytest.raises(dom.IntakeError):
            await svc.submit("SALES", body, KEY, ON)
    assert not st.intakes


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"email": "not-an-email", "inquiry_type": "DEMO"}, "email"),
        ({"email": "a@b.co", "inquiry_type": "HACK"}, "inquiry_type"),
        ({"inquiry_type": "DEMO"}, "email"),
        ({"email": "a@b.co", "inquiry_type": "DEMO", "message": 5}, "message"),
        ({"email": "a@b.co", "inquiry_type": "DEMO", "message": "x" * 501}, "message"),
    ],
)
async def test_field_validation(fields: dict[str, object], field: str) -> None:
    svc, st, _ = service()
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit("SALES", sales(fields=fields), KEY, ON)
    assert e.value.field == field and not st.intakes


@pytest.mark.anyio
@pytest.mark.parametrize(
    "secret",
    [
        "my password is hunter2xyz",
        "4111 1111 1111 1111",
        "my OTP is 482913",
        "Authorization: Bearer abcdefghijklmnopqrstuvwx",
    ],
)
async def test_secrets_in_free_text_are_blocked_before_storage(secret: str) -> None:
    svc, st, _ = service()
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit(
            "SALES",
            sales(fields={"email": "a@b.co", "inquiry_type": "DEMO", "message": secret}),
            KEY,
            ON,
        )
    assert e.value.code == "SENSITIVE_CONTENT_BLOCKED" and e.value.field == "message"
    assert not st.intakes and secret not in str(st.audits)


@pytest.mark.anyio
async def test_markup_is_stripped_from_free_text() -> None:
    svc, st, crypto = service()
    await svc.submit(
        "SALES",
        sales(
            fields={
                "email": "a@b.co",
                "inquiry_type": "DEMO",
                "message": "Hello <b>team</b><script>x()</script> please call",
            }
        ),
        KEY,
        ON,
    )
    msg = crypto.open(next(iter(st.intakes.values())).encrypted_fields or b"")["message"]
    assert "<" not in msg and ">" not in msg


@pytest.mark.anyio
async def test_idempotency_same_key_same_reference_different_payload_conflicts() -> None:
    svc, st, _ = service()
    a = await svc.submit("SALES", sales(), KEY, ON)
    b = await svc.submit("SALES", sales(), KEY, ON)
    assert a.reference == b.reference and len(st.intakes) == 1 and len(st.outbox) == 1, (
        "nothing is duplicated"
    )
    with pytest.raises(dom.IntakeError) as e:
        await svc.submit(
            "SALES", sales(fields={"email": "other@b.co", "inquiry_type": "DEMO"}), KEY, ON
        )
    assert e.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(dom.IntakeError):
        await svc.submit(
            "SUPPORT", support(), KEY, ON
        )  # the same key cannot be reused for another purpose


@pytest.mark.anyio
async def test_support_form_has_no_marketing_and_rejects_short_summaries() -> None:
    svc, st, _ = service()
    await svc.submit("SUPPORT", support(), "22222222-aaaa-bbbb-cccc-000000000002", ON)
    assert not st.marketing
    with pytest.raises(dom.IntakeError):
        await svc.submit(
            "SUPPORT",
            support(fields={"email": "a@b.co", "topic": "OTHER", "summary": "short"}),
            "33333333-aaaa-bbbb-cccc-000000000003",
            ON,
        )


@pytest.mark.anyio
async def test_marketing_is_a_separate_independent_record() -> None:
    svc, st, _ = service()
    await svc.submit("SALES", sales(), KEY, ON)
    assert not st.marketing, "submitting a form never enrolls anyone"
    await svc.record_marketing(
        {
            "channel": "EMAIL",
            "election": True,
            "country_code": "US",
            "notice_id": "notice-sales-2026-11",
        },
        ON,
    )
    assert len(st.marketing) == 1 and st.marketing[0]["choice"] is True
    for bad in (
        {
            "channel": "SMS",
            "election": True,
            "country_code": "US",
            "notice_id": "notice-sales-2026-11",
        },
        {
            "channel": "EMAIL",
            "election": "yes",
            "country_code": "US",
            "notice_id": "notice-sales-2026-11",
        },
        {
            "channel": "EMAIL",
            "election": True,
            "country_code": "FR",
            "notice_id": "notice-sales-2026-11",
        },
    ):
        with pytest.raises(dom.IntakeError):
            await svc.record_marketing(bad, ON)
    with pytest.raises(dom.IntakeError):
        await svc.record_marketing(
            {"channel": "EMAIL", "election": True, "country_code": "US", "notice_id": "x"}, ON
        )
    with pytest.raises(dom.IntakeError):
        await svc.record_marketing(
            {
                "channel": "EMAIL",
                "election": True,
                "country_code": "US",
                "notice_id": "notice-sales-2026-11",
            },
            Flags(True, False, True, True, False, False),
        )


def test_production_requires_an_encryption_key_and_local_does_not() -> None:
    IntakeCrypto(make_settings())  # local/test: an ephemeral key
    with pytest.raises(RuntimeError, match="encryption key"):
        IntakeCrypto(make_settings(environment="production"))


# ---------------------------------------------------------------------------------------------- delivery
async def _queued(n: int = 1) -> tuple[MemoryStore, IntakeService, IntakeCrypto]:
    svc, st, crypto = service()
    for i in range(n):
        await svc.submit(
            "SALES",
            sales(fields={"email": f"p{i}@b.co", "inquiry_type": "DEMO"}),
            f"{i:08d}-aaaa-bbbb-cccc-000000000000",
            ON,
        )
    return st, svc, crypto


@pytest.mark.anyio
async def test_the_log_only_adapter_marks_delivered_and_keeps_the_receipt_private() -> None:
    st, _, crypto = await _queued()
    worker = OutboxWorker(st, crypto, {"sales-receiver": LogOnlyAdapter()}, 5)
    assert await worker.run_once() == 1
    row = next(iter(st.intakes.values()))
    assert (
        row.status == "DELIVERED"
        and next(iter(st.receipts.values()))["recipient_ref"] == "log-only"
    )
    assert await worker.run_once() == 0, "a delivered row is never sent again"


def webhook(handler: httpx.MockTransport | None = None, status: int = 200) -> WebhookAdapter:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"case_ref": "CASE-42"})

    adapter = WebhookAdapter(
        "https://crm.example.test/hook",
        b"s3cret",
        allow_local_http=False,
        transport=handler or httpx.MockTransport(respond),
    )
    adapter.seen = seen  # type: ignore[attr-defined]
    return adapter


@pytest.mark.anyio
async def test_webhook_payload_is_minimal_signed_and_idempotent() -> None:
    import hashlib
    import hmac
    import json

    ad = webhook()
    r = await ad.deliver("req-1", "SALES", "US", {"email": "a@b.co"})
    assert r.recipient_ref == "CASE-42"
    req = ad.seen[0]  # type: ignore[attr-defined]
    body = json.loads(req.content)
    assert set(body) == {"request_id", "purpose", "country_code", "fields", "adapter_rev"}, (
        "no transcript, no session, no extras"
    )
    assert req.headers["idempotency-key"] == "req-1"
    assert (
        req.headers["x-zid-signature"]
        == "sha256=" + hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (500, TemporaryFailure),
        (503, TemporaryFailure),
        (429, TemporaryFailure),
        (408, TemporaryFailure),
        (400, PermanentRejection),
        (404, PermanentRejection),
    ],
)
async def test_webhook_status_classification(status: int, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        await webhook(status=status).deliver("r", "SALES", "US", {})


@pytest.mark.anyio
async def test_a_transport_error_is_temporary() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(TemporaryFailure):
        await webhook(httpx.MockTransport(boom)).deliver("r", "SALES", "US", {})


@pytest.mark.anyio
async def test_retry_backoff_then_dead_letter_and_no_fake_success() -> None:
    st, _, crypto = await _queued()
    worker = OutboxWorker(st, crypto, {"sales-receiver": webhook(status=503)}, max_attempts=3)
    now = datetime.now(UTC) + timedelta(seconds=1)
    await worker.run_once(now)
    o = next(iter(st.outbox.values()))
    assert o.state == "RETRYING" and o.attempts == 1 and o.next_attempt_at > now
    assert await worker.run_once(now) == 0, "not due yet"
    await worker.run_once(now + timedelta(hours=1))
    await worker.run_once(now + timedelta(hours=2))
    assert o.state == "DEAD_LETTER" and o.attempts == 3
    assert next(iter(st.intakes.values())).status == "DEAD_LETTER" and not st.receipts, (
        "an undelivered request never shows as delivered"
    )


@pytest.mark.anyio
async def test_a_permanent_rejection_is_not_retried() -> None:
    st, _, crypto = await _queued()
    await OutboxWorker(st, crypto, {"sales-receiver": webhook(status=400)}, 5).run_once()
    assert (
        next(iter(st.outbox.values())).state == "REJECTED"
        and next(iter(st.intakes.values())).status == "REJECTED"
    )


@pytest.mark.anyio
async def test_a_missing_adapter_dead_letters_and_an_erased_request_is_not_sent() -> None:
    st, _, crypto = await _queued(2)
    ids = list(st.intakes.values())
    assert await erase_intake(st, ids[1].reference)
    await OutboxWorker(st, crypto, {}, 5).run_once()
    states = sorted(o.state for o in st.outbox.values())
    assert states == ["DEAD_LETTER", "REJECTED"]
    assert ids[1].encrypted_fields is None and ids[1].status == "ERASED"


@pytest.mark.parametrize(
    "url",
    [
        "http://crm.example.test/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://user:pw@crm.example.test/x",
        "https://crm.example.test:9999/x",
        "ftp://crm.example.test",
        "https:///nohost",
    ],
)
def test_receiver_urls_must_be_https_dns_hosts_without_credentials(url: str) -> None:
    with pytest.raises(ValueError):
        check_receiver_url(url, allow_local_http=False)


def test_local_http_receiver_is_allowed_only_in_local() -> None:
    check_receiver_url("http://localhost:9000/hook", allow_local_http=True)
    with pytest.raises(ValueError):
        check_receiver_url("http://localhost:9000/hook", allow_local_http=False)
    with pytest.raises(ValueError):
        check_receiver_url("http://evil.example/hook", allow_local_http=True)
    check_receiver_url("https://crm.example.test/hook", allow_local_http=False)


@pytest.mark.anyio
async def test_retention_purges_old_metadata_but_never_touches_intake() -> None:
    st = MemoryStore()
    await st.record_feedback("rsp_" + "a" * 32, "USEFUL", None, "s")
    await st.record_safety("PASSWORD", "messages")
    st.feedback[0]["created_at"] = datetime.now(UTC) - timedelta(days=200)
    st.safety[0]["created_at"] = datetime.now(UTC) - timedelta(days=200)
    st2, _svc, _ = await _queued()
    out = await purge_expired(st, make_settings())
    assert out["feedback"] == 1 and out["safety"] == 1 and not st.feedback and not st.safety
    assert len(st2.intakes) == 1 and (await purge_expired(st2, make_settings()))["feedback"] == 0
