"""The email estate against a real database (Communication Estate §03, §05).

Event-level idempotency, suppression after a hard bounce, marketing consent and one-click unsubscribe, flood
protection, bounded retry, contacts held encrypted in the vault, event dispatch end to end through the consumer
handler, and the HTTP surface. Mail goes to a recording sender: no network.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.main import create_app
from zid_platform.modules.communication.application import contacts, dispatch, service
from zid_platform.modules.communication.application.config import EmailConfig
from zid_platform.modules.communication.application.contacts import ContactError, Recipient
from zid_platform.modules.communication.application.handler import email_handler
from zid_platform.modules.communication.domain import catalogue as cat
from zid_platform.modules.communication.domain.policy import Limits
from zid_platform.modules.communication.domain.render import LinkPolicy, Money
from zid_platform.modules.communication.infrastructure import store
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue
from zid_platform.modules.communication.infrastructure.smtp import SendResult
from zid_platform.settings import Settings

CATALOGUE = load_catalogue()
ROUTES = {f: f"/r/{f}" for t in CATALOGUE for f in t.url_fields}
NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def make_cfg(**kw: Any) -> EmailConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "enabled_templates": None,
        "links": LinkPolicy("https://id.example.com", ROUTES),
        "support_url": "https://id.example.com/help",
        "sender_display_name": "Zoiko iD",
        "limits": Limits(per_template_per_hour=20, per_recipient_per_hour=50),
        "retry_delays_s": (0.0, 0.0, 0.0),
        "legal_entity": "Zoiko Tech Inc.",
        "mailing_address": "1 Example Street",
        "unsubscribe_base": "https://id.example.com",
    }
    return EmailConfig(**{**base, **kw})


class FakeSender:
    """Records every send; `script` is the list of answers to give first, then SENT."""

    def __init__(self, script: list[SendResult] | None = None) -> None:
        self.sent: list[tuple[str, Any, str]] = []
        self.script = list(script or [])

    async def send(self, *, to: str, rendered: Any, message_id: str) -> SendResult:
        self.sent.append((to, rendered, message_id))
        return (
            self.script.pop(0)
            if self.script
            else SendResult("SENT", None, f"fake-{len(self.sent)}")
        )


def merge_for(template_id: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for f in CATALOGUE.get(template_id).fields:
        k = cat.field_kind(f)
        if k is cat.FieldKind.DATETIME:
            out[f] = NOW
        elif k is cat.FieldKind.MONEY:
            out[f] = Money(Decimal(2053), "USD")
        elif k is cat.FieldKind.URL_ROUTE:
            out[f] = None
        elif k is cat.FieldKind.URL_TOKEN:
            out[f] = "t" * 24
        elif k is cat.FieldKind.REFERENCE:
            out[f] = "REF-1"
        else:
            out[f] = f"Sample {f}"
    return out


async def recipient(address: str) -> Recipient:
    return Recipient(address, await contacts.address_hash(address))


async def send(
    rt_url: str,
    cfg: EmailConfig,
    sender: Any,
    tenant: str,
    template: str,
    event: str,
    who: Recipient,
    **kw: Any,
):  # type: ignore[no-untyped-def]
    async with engine(rt_url) as eng:
        return await service.send_email(
            eng,
            cfg,
            sender,
            cell_id="uk-1",
            template_id=template,
            event_id=event,
            event_type="test.event",
            tenant_id=tenant,
            recipient=who,
            merge=kw.pop("merge", merge_for(template)),
            **kw,
        )


async def rows(rt_url: str, tenant: str) -> list[dict[str, Any]]:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        res = await c.execute(
            text(
                "SELECT template_id, status, reason, attempts, event_id FROM platform.email_message ORDER BY created_at"
            )
        )
        return [dict(r) for r in res.mappings()]


# ------------------------------------------------------------------------------------------- sending


def test_one_email_per_event_hard_bounces_suppress_and_a_flood_is_throttled(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        cfg = make_cfg(limits=Limits(per_template_per_hour=2, per_recipient_per_hour=100))
        sender = FakeSender()
        ann = await recipient(f"ann-{uid()[:6]}@example.com")

        first = await send(rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-1", ann)
        assert first.outcome == "SENT" and len(sender.sent) == 1
        again = await send(
            rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-1", ann
        )  # the event is redelivered
        assert again.outcome == "DUPLICATE" and again.reason == "SENT" and len(sender.sent) == 1
        assert (
            await send(rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-2", ann)
        ).outcome == "SENT"
        flooded = await send(rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-3", ann)
        assert (
            flooded.outcome == "THROTTLED"
            and flooded.reason == "TEMPLATE_LIMIT"
            and len(sender.sent) == 2
        )
        # a different template to the same person is not throttled by that template's limit
        assert (
            await send(rt_url, cfg, sender, tenant, "ZID-EM-131", "evt-4", ann)
        ).outcome == "SENT"

        # a permanent refusal of an address is a hard bounce: recorded, suppressed, and never mailed again
        bad = await recipient(f"gone-{uid()[:6]}@example.com")
        bouncer = FakeSender([SendResult("RECIPIENT_REJECTED", "SMTP_550")])
        r = await send(rt_url, cfg, bouncer, tenant, "ZID-EM-130", "evt-5", bad)
        assert r.outcome == "FAILED" and r.reason == "SMTP_550"
        quiet = FakeSender()
        r2 = await send(rt_url, cfg, quiet, tenant, "ZID-EM-131", "evt-6", bad)
        assert (r2.outcome, r2.reason) == ("SUPPRESSED", "BOUNCE_HARD") and not quiet.sent
        # ... in every tenant, because the bounce belongs to the address
        r3 = await send(rt_url, cfg, quiet, uid(), "ZID-EM-130", "evt-7", bad)
        assert r3.reason == "BOUNCE_HARD" and not quiet.sent

        log = {
            (x["event_id"], x["template_id"]): (x["status"], x["reason"])
            for x in await rows(rt_url, tenant)
        }
        assert log[("evt-1", "ZID-EM-130")] == ("SENT", None)
        assert log[("evt-3", "ZID-EM-130")] == ("THROTTLED", "TEMPLATE_LIMIT")
        assert log[("evt-5", "ZID-EM-130")] == ("FAILED", "SMTP_550")
        assert log[("evt-6", "ZID-EM-131")] == ("SUPPRESSED", "BOUNCE_HARD")

    asyncio.run(run())


def test_the_log_never_holds_an_address_a_subject_a_body_or_a_token(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        who = await recipient(f"secret-{uid()[:6]}@example.com")
        await send(rt_url, make_cfg(), FakeSender(), tenant, "ZID-EM-001", "evt-1", who)
        async with engine(owner_url) as eng, eng.begin() as c:
            cols = {
                r[0]
                for r in await c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = 'email_message'"
                    )
                )
            }
            dump = (
                (await c.execute(text("SELECT row_to_json(m)::text FROM platform.email_message m")))
                .scalars()
                .all()
            )
        assert not cols & {
            "address",
            "recipient",
            "email",
            "subject",
            "body",
            "html",
            "text",
            "token",
            "merge",
        }
        blob = " ".join(dump)
        assert who.address not in blob and "t" * 24 not in blob and "Verify your email" not in blob

    asyncio.run(run())


def test_temporary_failures_retry_inside_the_attempt_and_a_failed_row_is_retried_by_the_next_delivery(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        cfg = make_cfg()
        who = await recipient(f"retry-{uid()[:6]}@example.com")
        temp = SendResult("TEMPORARY", "SMTP_450")
        slow = FakeSender([temp, temp, SendResult("SENT", None, "ok")])  # recovers on the third try
        r = await send(rt_url, cfg, slow, tenant, "ZID-EM-130", "evt-a", who)
        assert r.outcome == "SENT" and len(slow.sent) == 3
        assert (await rows(rt_url, tenant))[0]["attempts"] == 3

        down = FakeSender([temp, temp, temp])  # the provider stays down for the whole attempt
        r = await send(rt_url, cfg, down, tenant, "ZID-EM-131", "evt-b", who)
        assert r.outcome == "FAILED" and r.reason == "SMTP_450" and len(down.sent) == 3
        # the broker redelivers the event: the FAILED row is retried, once, and the person gets one email
        up = FakeSender()
        r = await send(rt_url, cfg, up, tenant, "ZID-EM-131", "evt-b", who)
        assert r.outcome == "SENT" and len(up.sent) == 1
        assert (
            await send(rt_url, cfg, up, tenant, "ZID-EM-131", "evt-b", who)
        ).outcome == "DUPLICATE"
        final = next(x for x in await rows(rt_url, tenant) if x["event_id"] == "evt-b")
        assert final["status"] == "SENT" and final["attempts"] == 4

    asyncio.run(run())


def test_a_pending_row_is_left_alone_until_its_lease_runs_out(rt_url: str, owner_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        cfg = make_cfg()
        who = await recipient(f"lease-{uid()[:6]}@example.com")
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(tenant)) as c,
        ):  # a worker reserved it and is still sending
            prepared = await service.prepare(
                c,
                cfg,
                template_id="ZID-EM-130",
                event_id="evt-l",
                event_type="t",
                tenant_id=tenant,
                recipient=who,
                merge=merge_for("ZID-EM-130"),
            )
            assert isinstance(prepared, service.Prepared)
        sender = FakeSender()
        r = await send(rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-l", who)
        assert (r.outcome, r.reason) == ("DUPLICATE", "PENDING") and not sender.sent
        async with engine(owner_url) as eng, eng.begin() as c:  # the worker died ten minutes ago
            await c.execute(
                text("UPDATE platform.email_message SET updated_at = now() - interval '10 minutes'")
            )
        assert (
            await send(rt_url, cfg, sender, tenant, "ZID-EM-130", "evt-l", who)
        ).outcome == "SENT"

    asyncio.run(run())


def test_a_disabled_template_a_bad_merge_and_a_missing_route_send_nothing(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        who = await recipient(f"nope-{uid()[:6]}@example.com")
        sender = FakeSender()
        off = make_cfg(enabled_templates=frozenset({"ZID-EM-001"}))
        r = await send(rt_url, off, sender, tenant, "ZID-EM-130", "e1", who)
        assert (r.outcome, r.reason) == ("DISABLED", "TEMPLATE_NOT_ENABLED")
        assert (
            await send(rt_url, make_cfg(enabled=False), sender, tenant, "ZID-EM-130", "e2", who)
        ).outcome == "DISABLED"
        no_route = make_cfg(links=LinkPolicy("https://id.example.com", {}))
        assert (
            await send(rt_url, no_route, sender, tenant, "ZID-EM-130", "e3", who)
        ).outcome == "DISABLED"
        bad = merge_for("ZID-EM-130")
        bad.pop("organization_name")
        r = await send(rt_url, make_cfg(), sender, tenant, "ZID-EM-130", "e4", who, merge=bad)
        assert (r.outcome, r.reason) == ("RENDER_FAILED", "MERGE_MISSING") and not sender.sent
        assert [x["status"] for x in await rows(rt_url, tenant)] == [
            "RENDER_FAILED"
        ]  # disabled ones leave no row

    asyncio.run(run())


def test_marketing_needs_consent_and_one_click_unsubscribe_never_stops_security_mail(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        cfg = make_cfg(
            links=LinkPolicy(
                "https://id.example.com", {**ROUTES, "trusted_preferences_url": "/preferences"}
            )
        )
        sender = FakeSender()
        who = await recipient(f"fan-{uid()[:6]}@example.com")
        r = await send(rt_url, cfg, sender, tenant, "ZID-EM-142", "m1", who)
        assert (r.outcome, r.reason) == ("SUPPRESSED", "NO_CONSENT") and not sender.sent
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.record_consent(
                c, who.address_hash, "product_updates", "GRANTED", "you asked for updates"
            )
        r = await send(rt_url, cfg, sender, tenant, "ZID-EM-142", "m2", who)
        assert r.outcome == "SENT"
        link = sender.sent[0][1].unsubscribe_url
        q = parse_qs(urlparse(link).query)
        assert link.startswith(
            "https://id.example.com/email/unsubscribe?"
        ) and service.verify_unsubscribe(q["r"][0], q["l"][0], q["s"][0])
        assert not service.verify_unsubscribe(
            q["r"][0], q["l"][0], q["s"][0][:-2] + "xx"
        )  # cannot be forged
        assert not service.verify_unsubscribe(
            "someone-else-" + q["r"][0], q["l"][0], q["s"][0]
        )  # nor re-aimed

        async with engine(rt_url) as eng, eng.begin() as c:  # the recipient clicks it
            await store.add_suppression(
                c,
                who.address_hash,
                store.Suppression.UNSUBSCRIBED,
                source="link",
                list_id="product_updates",
            )
            await store.record_consent(
                c, who.address_hash, "product_updates", "WITHDRAWN", "unsubscribed"
            )
        r = await send(rt_url, cfg, sender, tenant, "ZID-EM-142", "m3", who)
        assert (r.outcome, r.reason) == ("SUPPRESSED", "UNSUBSCRIBED")
        # but a security notice, and the confirmation of the unsubscribe itself, still arrive
        assert (await send(rt_url, cfg, sender, tenant, "ZID-EM-024", "s1", who)).outcome == "SENT"
        assert (await send(rt_url, cfg, sender, tenant, "ZID-EM-145", "s2", who)).outcome == "SENT"

    asyncio.run(run())


# ------------------------------------------------------------------------------------------- contacts


def test_contacts_are_encrypted_in_the_vault_validated_and_tenant_scoped(
    rt_url: str, owner_url: str
) -> None:
    tenant, other, person = uid(), uid(), uid()
    address = f"Bill.{uid()[:6]}@Example.com"

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                one = await contacts.add_role_contact(c, tenant, "BILLING", address, actor=person)
                assert (
                    await contacts.add_role_contact(
                        c, tenant, "BILLING", address.upper(), actor=person
                    )
                    == one
                )  # idempotent
                got = await contacts.resolve_role(c, tenant, "BILLING")
                assert [r.address for r in got] == [address.lower()]
                assert repr(got[0]).startswith("Recipient(hash=") and address.lower() not in repr(
                    got[0]
                )
                masked = await contacts.list_masked(c, tenant)
                assert masked == [
                    {"contact_id": one, "purpose": "BILLING", "address": "b***@example.com"}
                ]
                await contacts.set_principal_contact(
                    c, tenant, person, "me@example.com", actor=person
                )
                await contacts.set_principal_contact(
                    c, tenant, person, "me2@example.com", actor=person
                )  # replaces
                assert [r.address for r in await contacts.resolve_principal(c, tenant, person)] == [
                    "me2@example.com"
                ]
                with pytest.raises(ContactError):
                    await contacts.add_role_contact(
                        c, tenant, "PIRATE", "a@example.com", actor=None
                    )
            async with tenant_transaction(eng, ctx(other)) as c:  # another tenant sees nothing
                assert await contacts.resolve_role(c, other, "BILLING") == []
                assert await contacts.resolve_principal(c, other, person) == []
        async with engine(owner_url) as eng, eng.begin() as c:
            blobs = (
                (
                    await c.execute(
                        text(
                            "SELECT ciphertext FROM evidence.evidence_object WHERE object_type = 'communication_contact'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            rowdump = " ".join(
                (await c.execute(text("SELECT row_to_json(x)::text FROM platform.email_contact x")))
                .scalars()
                .all()
            )
        assert blobs and all(
            b"example.com" not in bytes(b) for b in blobs
        )  # ciphertext: the address is not readable
        assert (
            "example.com" not in rowdump
        )  # the contact row holds a vault reference and a hash only

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-at",
        "a@b",
        "a@b.com\r\nBcc: x@evil.com",
        "a@b.com, c@d.com",
        "<a@b.com>",
        "Ann <a@b.com>",
        "a b@c.com",
        "a@@b.com",
        "x" * 300 + "@b.com",
    ],
)
def test_an_address_that_could_smuggle_a_header_or_a_second_recipient_is_refused(bad: str) -> None:
    with pytest.raises(ContactError):
        contacts.normalize_address(bad)
    assert contacts.normalize_address("  Ann@Example.COM ") == "ann@example.com"


# ------------------------------------------------------------------------------------------ dispatch


def test_platform_events_send_the_right_templates_to_the_right_contacts_once(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    sender = FakeSender()

    def event(name: str, data: dict[str, Any], eid: str | None = None) -> CloudEvent:
        return CloudEvent(
            id=eid or uid(),
            type=f"com.zoiko.id.{name}.v1",
            source="test",
            subject="s",
            time=NOW,
            tenantid=o.tenant,
            cellid="uk-1",
            aggregatetype="x",
            aggregateid=uid(),
            aggregateversion=1,
            data=data,
            correlationid=uid(),
        )

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                await contacts.set_principal_contact(
                    c, o.tenant, o.owner, "owner@example.com", actor=o.owner
                )
                await contacts.add_role_contact(
                    c, o.tenant, "BILLING", "billing@example.com", actor=o.owner
                )
            handle = email_handler(eng, "uk-1", make_cfg(), sender)

            await handle(
                event(
                    "authenticator.registered",
                    {"principal_id": o.owner, "authenticator_type": "WEBAUTHN"},
                )
            )
            await handle(
                event(
                    "commercial.entitlement.changed",
                    {"plan": "BUSINESS", "previous_plan": None, "effective_from": NOW.isoformat()},
                )
            )
            await handle(
                event(
                    "commercial.entitlement.changed",
                    {
                        "plan": "SCALE",
                        "previous_plan": "BUSINESS",
                        "effective_from": NOW.isoformat(),
                    },
                )
            )
            await handle(
                event(
                    "commercial.budget.threshold_crossed",
                    {"key": "BUDGET:MAH", "month": "2026-10", "threshold": 80},
                )
            )
            await handle(
                event("agent.suspended", {"agent_principal_id": uid(), "reason_code": "X"})
            )  # OWNER role: no contact yet
            await handle(event("evidence.export.created", {}))  # not a wired event: nothing

        got = [(to, r.template_id) for to, r, _ in sender.sent]
        assert got == [
            ("owner@example.com", "ZID-EM-023"),
            ("billing@example.com", "ZID-EM-127"),
            ("billing@example.com", "ZID-EM-128"),
            ("billing@example.com", "ZID-EM-129"),
        ]
        by = {r.template_id: r for _, r, _ in sender.sent}
        assert "passkey sign-in method was added" in by["ZID-EM-023"].text
        assert (
            "Acme" in by["ZID-EM-127"].text
        )  # the organization's name, read from the tenant, not the event
        assert "Your Zoiko iD subscription for Acme was activated" in by["ZID-EM-127"].text
        assert "Scale" in by["ZID-EM-128"].text and "80%" in by["ZID-EM-129"].text
        assert (
            "monthly active people" not in by["ZID-EM-129"].text
        )  # BUDGET:MAH names the meter, not the bill
        assert "active people" in by["ZID-EM-129"].text

    asyncio.run(run())


def test_the_consumer_handler_is_idempotent_and_silent_when_email_is_off(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    sender = FakeSender()
    eid = uid()
    ev = CloudEvent(
        id=eid,
        type="com.zoiko.id.authenticator.revoked.v1",
        source="t",
        subject="s",
        time=NOW,
        tenantid=o.tenant,
        cellid="uk-1",
        aggregatetype="authenticator",
        aggregateid=uid(),
        aggregateversion=1,
        data={"principal_id": o.owner, "authenticator_type": "TOTP", "status": "REVOKED"},
        correlationid=uid(),
    )

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                await contacts.set_principal_contact(
                    c, o.tenant, o.owner, "owner@example.com", actor=o.owner
                )
            await email_handler(eng, "uk-1", make_cfg(enabled=False), sender)(ev)
            assert not sender.sent
            handle = email_handler(eng, "uk-1", make_cfg(), sender)
            await handle(ev)
            await handle(ev)  # the broker delivered it twice
            await handle(ev)
        assert [r.template_id for _, r, _ in sender.sent] == ["ZID-EM-024"]
        assert "authenticator app sign-in method was removed" in sender.sent[0][1].text

    asyncio.run(run())


def test_payment_and_invoice_events_read_the_invoice_not_the_event(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = onboard(rt_url)
    sender = FakeSender()

    async def invoice(c: Any, invoice_id: object) -> dict[str, Any]:
        return {
            "number": "ZE-00000007",
            "gross": "2053.00",
            "currency": "USD",
            "due_at": "2026-11-15T00:00:00+00:00",
        }

    monkeypatch.setattr(dispatch, "_invoice", invoice)

    def event(name: str, data: dict[str, Any]) -> CloudEvent:
        return CloudEvent(
            uid(),
            f"com.zoiko.id.{name}.v1",
            "t",
            "s",
            NOW,
            o.tenant,
            "uk-1",
            "x",
            uid(),
            1,
            data,
            uid(),
        )

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                await contacts.add_role_contact(
                    c, o.tenant, "BILLING", "billing@example.com", actor=o.owner
                )
            handle = email_handler(eng, "uk-1", make_cfg(), sender)
            await handle(
                event(
                    "commercial.invoice.issued",
                    {"invoice_id": uid(), "number": "ZE-00000007", "period": "2026-10"},
                )
            )
            await handle(
                event(
                    "commercial.payment.recorded",
                    {
                        "invoice_id": uid(),
                        "status": "CAPTURED",
                        "amount": "2053.00",
                        "currency": "USD",
                    },
                )
            )
            await handle(
                event(
                    "commercial.payment.recorded",
                    {
                        "invoice_id": uid(),
                        "status": "FAILED",
                        "amount": "2053.00",
                        "currency": "USD",
                    },
                )
            )
        sent = {r.template_id: r for _, r, _ in sender.sent}
        assert sorted(sent) == ["ZID-EM-130", "ZID-EM-131", "ZID-EM-132"]
        assert (
            "Total: USD 2,053.00" in sent["ZID-EM-130"].text
            and "Payment due: Nov 15, 2026" in sent["ZID-EM-130"].text
        )
        assert "payment of USD 2,053.00 for Zoiko iD invoice ZE-00000007" in sent["ZID-EM-131"].text
        assert "We will never request full payment card details by" in sent["ZID-EM-132"].text

    asyncio.run(run())


def test_coverage_says_which_templates_an_emitted_event_can_trigger() -> None:
    rows_ = dispatch.coverage()
    assert len(rows_) == 160
    wired = sorted(str(r["template"]) for r in rows_ if r["wired"])
    assert wired == [
        "ZID-EM-013",
        "ZID-EM-015",
        "ZID-EM-016",
        "ZID-EM-023",
        "ZID-EM-024",
        "ZID-EM-026",
        "ZID-EM-029",
        "ZID-EM-030",
        "ZID-EM-036",
        "ZID-EM-039",
        "ZID-EM-074",
        "ZID-EM-077",
        "ZID-EM-078",
        "ZID-EM-082",
        "ZID-EM-115",
        "ZID-EM-116",
        "ZID-EM-127",
        "ZID-EM-128",
        "ZID-EM-129",
        "ZID-EM-130",
        "ZID-EM-131",
        "ZID-EM-132",
        "ZID-EM-154",
    ]
    assert dispatch.event_name("com.zoiko.id.mandate.revoked.v1") == "mandate.revoked"
    assert make_cfg().allows(CATALOGUE.get("ZID-EM-130"))
    assert not make_cfg(links=LinkPolicy("https://id.example.com", {})).allows(
        CATALOGUE.get("ZID-EM-130")
    )


# ----------------------------------------------------------------------------------------------- HTTP


def test_contacts_and_unsubscribe_over_http(rt_url: str, owner_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=["communication.read", "communication.contact.manage"],
        extra_resources=["principal:*"],
    )
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    adm = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        r = http.put(
            "/v1/tenant/contacts",
            json={"purpose": "BILLING", "address": "Pay.Me@Example.com"},
            headers=adm,
        )
        assert r.status_code == 201, r.text
        assert r.json()["address"] == "p***@example.com" and "pay.me" not in r.text.lower()
        again = http.put(
            "/v1/tenant/contacts",
            json={"purpose": "BILLING", "address": "pay.me@example.com"},
            headers=adm,
        )
        assert again.json()["contact_id"] == r.json()["contact_id"]
        listed = http.get("/v1/tenant/contacts", headers=adm).json()["items"]
        assert [(i["purpose"], i["address"]) for i in listed] == [("BILLING", "p***@example.com")]
        assert (
            "pay.me" not in http.get("/v1/tenant/contacts", headers=adm).text.lower()
        )  # never returned in full
        for bad in ("nope", "a@b.com\r\nBcc: x@y.com", "a@b.com,c@d.com"):
            assert (
                http.put(
                    "/v1/tenant/contacts", json={"purpose": "BILLING", "address": bad}, headers=adm
                ).status_code
                == 422
            )
        assert (
            http.put(
                "/v1/tenant/contacts", json={"purpose": "ROOT", "address": "a@b.com"}, headers=adm
            ).status_code
            == 422
        )
        assert (
            http.put(
                "/v1/tenant/contacts",
                json={"purpose": "BILLING", "address": "a@b.com", "x": 1},
                headers=adm,
            ).status_code
            == 422
        )
        assert http.get("/v1/tenant/contacts").status_code == 401  # no credentials, no contacts
        assert (
            http.delete(f"/v1/tenant/contacts/{r.json()['contact_id']}", headers=adm).status_code
            == 204
        )
        assert (
            http.delete(f"/v1/tenant/contacts/{r.json()['contact_id']}", headers=adm).status_code
            == 404
        )
        assert http.get("/v1/tenant/contacts", headers=adm).json()["items"] == []
        mine = http.put("/v1/account/contact", json={"address": "Me@Example.com"}, headers=adm)
        assert mine.status_code == 201 and mine.json()["address"] == "m***@example.com"

        # the public one-click unsubscribe (RFC 8058)
        h = asyncio.run(contacts.address_hash("fan@example.com"))
        url = service.unsubscribe_url("", h, "product_updates")
        assert (
            http.get(url).status_code == 200 and "<form" in http.get(url).text
        )  # a GET only shows a page
        assert asyncio.run(_suppressed(rt_url, h)) == set()
        done = http.post(
            url,
            content="List-Unsubscribe=One-Click",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert done.status_code == 200 and done.json() == {"status": "UNSUBSCRIBED"}
        assert asyncio.run(_suppressed(rt_url, h)) == {"UNSUBSCRIBED"}
        forged = url[:-3] + "AAA"
        assert http.post(forged).status_code == 400 and http.get(forged).status_code == 400
        other = service.unsubscribe_url("", "ph1.1.someone-else-entirely", "product_updates")
        assert (
            http.post(other.replace(other.split("&s=")[1], url.split("&s=")[1])).status_code == 400
        )


async def _suppressed(rt_url: str, h: str) -> set[str]:
    async with engine(rt_url) as eng, eng.begin() as c:
        return {str(x.value) for x in await store.suppressions(c, h, "product_updates")}


def test_account_recovery_proofing_membership_evidence_and_agent_events_send_their_templates(
    rt_url: str, owner_url: str
) -> None:
    """Events the platform emits for recovery, identity proofing, member removal, evidence, agent and workload
    registration send the matching copybook email, with only the values the copy needs."""
    o = onboard(rt_url)
    sender = FakeSender()

    def event(name: str, data: dict[str, Any]) -> CloudEvent:
        return CloudEvent(
            id=uid(), type=f"com.zoiko.id.{name}.v1", source="test", subject="s", time=NOW, tenantid=o.tenant,
            cellid="uk-1", aggregatetype="x", aggregateid=uid(), aggregateversion=1, data=data, correlationid=uid(),
        )  # fmt: skip

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                await contacts.set_principal_contact(
                    c, o.tenant, o.owner, "owner@example.com", actor=o.owner
                )
                await contacts.add_role_contact(
                    c, o.tenant, "SECURITY", "sec@example.com", actor=o.owner
                )
                await contacts.add_role_contact(
                    c, o.tenant, "OWNER", "own@example.com", actor=o.owner
                )
            handle = email_handler(eng, "uk-1", make_cfg(), sender)
            who = {"principal_id": o.owner}
            for name, data in (
                ("recovery.started", {**who, "recovery_id": "rec-1"}),
                ("recovery.completed", {**who, "recovery_id": "rec-1"}),
                ("recovery.denied", {**who, "recovery_id": "rec-1"}),
                ("verification.started", {"subject_principal_id": o.owner, "session_id": "ses-1"}),
                ("verification.completed", {"subject_principal_id": o.owner, "session_id": "ses-1", "assertions": ["a1"]}),
                ("verification.completed", {"subject_principal_id": o.owner, "session_id": "ses-2", "assertions": []}),
                ("tenant.member.changed", {**who, "role_status": "ACTIVE"}),  # not a removal: nothing
                ("tenant.member.changed", {**who, "role_status": "REVOKED"}),
                ("tenant.organization_verified", {}),
                ("evidence.export.created", {"package_id": "pkg-9"}),
                ("evidence.integrity.failed", {"partition_id": "part-3"}),
                ("agent.registered", {"agent_principal_id": uid()}),
                ("machine.workload.registered", {"logical_principal_id": uid()}),
                ("recovery.started", {"recovery_id": "no-principal"}),  # no recipient: nothing
            ):  # fmt: skip
                await handle(event(name, data))

        got = [(to, r.template_id) for to, r, _ in sender.sent]
        assert got == [
            ("owner@example.com", "ZID-EM-013"),
            ("owner@example.com", "ZID-EM-015"),
            ("owner@example.com", "ZID-EM-016"),
            ("owner@example.com", "ZID-EM-026"),
            ("owner@example.com", "ZID-EM-029"),
            ("owner@example.com", "ZID-EM-030"),
            ("owner@example.com", "ZID-EM-154"),
            ("own@example.com", "ZID-EM-036"),
            ("sec@example.com", "ZID-EM-115"),
            ("sec@example.com", "ZID-EM-116"),
            ("own@example.com", "ZID-EM-078"),
            ("own@example.com", "ZID-EM-077"),
        ]
        by = {r.template_id: r for _, r, _ in sender.sent}
        assert "rec-1" in by["ZID-EM-016"].text and "pkg-9" in by["ZID-EM-115"].text
        assert "part-3" in by["ZID-EM-116"].text and "Acme" in by["ZID-EM-036"].text

    asyncio.run(run())
