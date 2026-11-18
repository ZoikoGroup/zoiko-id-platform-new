"""WF-05 contact challenges and the confirmed change of a security email on PostgreSQL (ZID-DTV-WF-05 A02-A04, §22; QA-003, QA-004, QA-014;
CR-114).

1. Over HTTP: a case's email is added (masked in every answer), challenged with an Idempotency-Key (a replay sends nothing again), the code reaches
   only the sender, a wrong code counts, the right code records CONTACT_CONTROL_CONFIRMED and nothing else, and a used code fails. Changing the
   contact revokes the open challenge. SMS goes only to an allowed calling code. The tables refuse an edited final challenge.
2. A security email change: nothing changes until the link is used; a wrong token is refused; the right one applies once.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_check_cases_pg import _owner, _tenant
from test_check_methods_pg import _signed_overlay, _to_production
from test_policy_gate_http_pg import AUD, ISS
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.communication.application import contact_change, contacts
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.application import contact_challenges as svc
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import contact_challenges as ch
from zid_platform.modules.verification.infrastructure import check_method_config
from zid_platform.settings import Settings

EMAIL, SMS = cm.CheckMethod.EMAIL_CHALLENGE, cm.CheckMethod.PHONE_SMS_CHALLENGE


class Capture:
    def __init__(self) -> None:
        self.sent: list[svc.Delivery] = []

    async def send(self, delivery: svc.Delivery) -> None:
        self.sent.append(delivery)


def test_a_case_contact_is_challenged_once_and_only_contact_control_is_recorded(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = uuid.uuid4().hex[:6]
    adapters = {
        m: cm.Adapter(m, f"zid-{m.value.lower().replace('_', '-')}", v, cm.Route.OPEN)
        for m in (EMAIL, SMS)
    }
    approved = {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }
    monkeypatch.setattr(check_method_config, "adapters", lambda: adapters)
    monkeypatch.setattr(check_method_config, "retention", lambda: approved)
    monkeypatch.setattr(
        svc, "limits",
        lambda: ch.Limits(3, 10, 50, {ch.Channel.EMAIL: 100, ch.Channel.SMS: 100, ch.Channel.VOICE: 0}, frozenset({"44"}), "SAMPLE"),
    )  # fmt: skip
    tenant, admin = _tenant(rt_url)
    for m in (EMAIL, SMS):
        scope = bm.Scope(m, adapters[m].engine_id, v, "GB", "ONBOARDING", tenant)

        async def live(c: Any, scope: bm.Scope = scope, m: cm.CheckMethod = m) -> bm.Record:
            return await _to_production(c, scope, f"{m.value.lower().replace('_', '-')}-{v}")

        asyncio.run(_owner(owner_url, live))

    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"), keyring=ring
    )  # fmt: skip
    app.state.privacy_overlays = {"GB": _signed_overlay("GB")}
    sender = Capture()
    app.state.contact_challenge_sender = sender
    token = issue_access_token(
        ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="onboarding-svc", now=datetime.now(UTC),
        scope=frozenset({"verification:execute", "verification:read"}),
    )  # fmt: skip
    h = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url=ISS) as http:
        case = http.post(
            "/v1/verification-cases", headers={**h, "Idempotency-Key": f"c-{v}"},
            json={"subject_ref": "cust-1", "purpose": "ONBOARDING", "market": "GB", "methods": ["EMAIL_CHALLENGE", "PHONE_SMS_CHALLENGE"]},
        ).json()  # fmt: skip
        cid = case["case_id"]
        contact = http.post(
            f"/v1/verification-cases/{cid}/contacts",
            headers=h,
            json={"type": "EMAIL", "value": "Dana.Lee@Example.com"},
        )
        assert contact.status_code == 201, contact.text
        assert contact.json()["masked"] == "D***@example.com" and "Dana" not in contact.text
        kid = contact.json()["contact_id"]

        key = {**h, "Idempotency-Key": f"ch-{v}"}
        body = {"channel": "EMAIL", "session_ref": "sess-1"}
        started = http.post(f"/v1/contacts/{kid}/challenges", headers=key, json=body)
        assert started.status_code == 202, started.text
        assert (
            http.post(f"/v1/contacts/{kid}/challenges", headers=key, json=body).json()
            == started.json()
        )
        assert len(sender.sent) == 1 and sender.sent[0].destination == "Dana.Lee@example.com"
        secret, chid = sender.sent[0].code, started.json()["challenge_id"]
        assert secret not in started.text

        confirm = {"challenge_id": chid, "session_ref": "sess-1"}
        assert (
            http.post(
                f"/v1/contacts/{kid}/confirm", headers=h, json={**confirm, "code": "A" * 43}
            ).status_code
            == 422
        )
        ok = http.post(f"/v1/contacts/{kid}/confirm", headers=h, json={**confirm, "code": secret})
        assert ok.status_code == 200 and ok.json()["assertion"] == "CONTACT_CONTROL_CONFIRMED"
        assert (
            http.post(
                f"/v1/contacts/{kid}/confirm", headers=h, json={**confirm, "code": secret}
            ).status_code
            == 422
        )
        ev = http.get(f"/v1/verification-cases/{cid}/evidence", headers=h).json()
        assert [a["type"] for a in ev["assertions"]] == ["CONTACT_CONTROL_CONFIRMED"]
        assert ev["assertions"][0]["source"] == f"zid-email-challenge@{v}"

        # a phone: SMS only to an allowed calling code; a changed number revokes the open challenge
        us = http.post(
            f"/v1/verification-cases/{cid}/contacts",
            headers=h,
            json={"type": "PHONE", "value": "+1 415 555 2671"},
        ).json()
        sms = {"channel": "SMS", "session_ref": "sess-1"}
        refused = http.post(
            f"/v1/contacts/{us['contact_id']}/challenges",
            headers={**h, "Idempotency-Key": f"us-{v}"},
            json=sms,
        )
        assert refused.status_code == 409
        gb = http.post(
            f"/v1/verification-cases/{cid}/contacts",
            headers=h,
            json={"type": "PHONE", "value": "07700 900123"},
        ).json()
        assert gb["revision"] == 2
        sent = http.post(
            f"/v1/contacts/{gb['contact_id']}/challenges",
            headers={**h, "Idempotency-Key": f"gb-{v}"},
            json=sms,
        ).json()
        code = sender.sent[-1].code
        http.post(
            f"/v1/verification-cases/{cid}/contacts",
            headers=h,
            json={"type": "PHONE", "value": "07700 900456"},
        )
        late = http.post(
            f"/v1/contacts/{gb['contact_id']}/confirm", headers=h,
            json={"challenge_id": sent["challenge_id"], "session_ref": "sess-1", "code": code},
        )  # fmt: skip
        assert late.status_code == 422  # the number changed: the code is revoked

    async def edit(c: Any) -> None:
        await c.execute(
            text("UPDATE verify.check_challenge SET attempt_count = 0 WHERE challenge_id = :c"),
            {"c": uuid.UUID(chid)},
        )

    async def in_tenant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await edit(c)

    with pytest.raises(DBAPIError):
        asyncio.run(in_tenant())


def test_a_security_email_changes_only_when_the_new_address_confirms_it(rt_url: str) -> None:
    tenant, principal = _tenant(rt_url)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await contacts.set_principal_contact(
                c, tenant, principal, "old@example.com", actor=principal
            )
            req = await contact_change.request(
                c, tenant, principal, "New@Example.com", now=datetime.now(UTC)
            )
            assert (await contacts.resolve_principal(c, tenant, principal))[
                0
            ].address == "old@example.com"
            assert req.old_address == "old@example.com" and len(req.token) == 75
            with pytest.raises(contact_change.ChangeError):
                await contact_change.confirm(
                    c, tenant, principal, req.token[:32] + "B" * 43, now=datetime.now(UTC)
                )
            new, old = await contact_change.confirm(
                c, tenant, principal, req.token, now=datetime.now(UTC)
            )
            assert (new, old) == ("new@example.com", "old@example.com")
            assert (await contacts.resolve_principal(c, tenant, principal))[
                0
            ].address == "new@example.com"
            with pytest.raises(contact_change.ChangeError):
                await contact_change.confirm(c, tenant, principal, req.token, now=datetime.now(UTC))

    asyncio.run(go())
