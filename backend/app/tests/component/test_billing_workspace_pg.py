"""The customer billing workspace and the eligibility gate (ZID-UX-COM-008 B01, B04; ZID-UX-COM-009 O01; CR-054).

A free account is shown as free with one honest next action; the billing profile is replaced only with the version it was based on, keeps every version, and
never edits the legal payer; an Administrator with no billing grant cannot change it; eligibility names each failed gate and never activates anything.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_commitment_pg import book, signed_order, uid
from test_role_matrix_pg import _seed_role
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import billing_workspace as workspace
from zid_platform.modules.commercial.application import eligibility_service as eligibility
from zid_platform.settings import Settings

ADDRESS = {"line1": "1 High Street", "city": "London", "country": "GB"}
CONTACTS = {"invoice_email": "ap@acme.example"}


def test_a_free_account_the_profile_and_the_payer_over_http(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app) as api:
        own, adm = o.bearer(o.owner), o.bearer(o.second)
        r = api.get("/v1/billing/overview", headers=own)
        assert r.status_code == 200, r.text
        free = r.json()
        assert free["state"] == "FREE" and free["message"].startswith(
            "You are using a free account"
        )
        assert (
            free["plan"]["contract_ref"] is None and free["recent_activity"] == []
        )  # nothing invented
        assert free["next_action"]["kind"] in ("VIEW_PLANS", "NONE")
        assert (
            api.get("/v1/billing/overview", headers=adm).status_code == 403
        )  # an Administrator with no billing grant

        path = "/v1/billing/profile"
        empty = api.get(path, headers=own).json()
        assert (
            empty["version"] == 0
            and empty["address"] is None
            and empty["contact_verification"] == "UNVERIFIED"
        )
        body = {"expected_version": 0, "address": ADDRESS, "tax_ids": [], "contacts": CONTACTS}
        r = api.put(path, json=body, headers=own)
        assert (
            r.status_code == 409 and r.json()["error"]["code"] == "ACCOUNT_REQUIRED"
        )  # no commercial account yet

        async def link() -> None:
            from zid_platform.modules.commercial.application import orders_service as orders

            async with engine(rt_url) as eng, eng.begin() as c:
                account = await orders.create_account(
                    c,
                    legal_name="Acme Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=uid(),
                )
                await orders.link_tenant(c, account, o.tenant)

        asyncio.run(link())
        assert (
            api.put(path, json=body, headers=adm).status_code == 403
        )  # only billing.profile.manage
        r = api.put(path, json={**body, "address": {**ADDRESS, "country": "US"}}, headers=own)
        assert r.status_code == 409 and r.json()["error"]["code"] == "PAYER_CHANGE_REQUIRES_REVIEW"
        assert (
            api.put(
                path, json={**body, "contacts": {"invoice_email": "not-an-email"}}, headers=own
            ).status_code
            == 422
        )
        assert (
            api.put(
                path,
                json={**body, "tax_ids": [{"type": "LOTTERY", "value": "ABCD1234"}]},
                headers=own,
            ).status_code
            == 422
        )
        assert (
            api.put(path, json={**body, "expected_version": 3}, headers=own).json()["error"]["code"]
            == "VERSION_CONFLICT"
        )

        r = api.put(
            path, json={**body, "tax_ids": [{"type": "VAT", "value": "GB123456789"}]}, headers=own
        )
        assert r.status_code == 200, r.text
        saved = r.json()
        assert (
            saved["version"] == 1
            and saved["payer"]["legal_name"] == "Acme Ltd"
            and saved["tax_ids"][0]["verification"] == "UNVERIFIED"
        )
        assert (
            api.put(
                path,
                json={
                    **body,
                    "expected_version": 1,
                    "tax_ids": [{"type": "VAT", "value": "GB123456789"}],
                },
                headers=own,
            ).json()["error"]["code"]
            == "NO_CHANGE"
        )
        r = api.put(
            path,
            json={
                **body,
                "expected_version": 1,
                "contacts": {**CONTACTS, "notices_email": "billing@acme.example"},
                "tax_ids": saved["tax_ids"],
            },
            headers=own,
        )
        assert r.json()["version"] == 2
        hist = api.get(f"{path}/history", headers=own).json()["items"]
        assert [h["version"] for h in hist] == [2, 1] and hist[0]["changed_fields"] == ["contacts"]
        assert "GB123456789" not in str(hist)  # the history names fields, never values
        again = api.get("/v1/billing/overview", headers=own).json()
        assert again["payer"]["legal_name"] == "Acme Ltd" and again["payer"]["country"] == "GB"


def test_eligibility_names_each_failed_gate_and_never_activates(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uid()

    async def run() -> None:
        b = await book(rt_url, owner_url)
        order = await signed_order(rt_url, b, tenant)
        seen: dict[str, str] = {"state": "DRAFT"}

        async def org_state(_c: Any, _t: str) -> str:
            return seen["state"]

        monkeypatch.setattr(eligibility, "_org_state", org_state)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            out = await eligibility.check(
                c, tenant, issuer="ZID", tax_configured=False, payments_configured=False
            )
            gates = {g["key"]: g for g in out["gates"]}
            assert out["state"] == "NOT_ELIGIBLE" and out["classification"] == "FREE"
            assert not gates["IDENTITY"]["passed"] and gates["IDENTITY"]["remedy"]
            assert (
                gates["LEGAL_PAYER"]["passed"]
                and gates["CONTRACT"]["passed"]
                and gates["REGION"]["passed"]
                and gates["ISSUER"]["passed"]
            )
            assert not gates["TAX"]["passed"] and not gates["MERCHANT"]["passed"]
            assert out["schedule"]["order_id"] == order and out["schedule"]["quote_hash"]
            assert "not a charge" in out["note"]
            seen["state"] = "ACTIVE"
            ready = await eligibility.check(
                c, tenant, issuer="ZID", tax_configured=True, payments_configured=True
            )
            assert "IDENTITY" not in ready["blockers"] and "TAX" not in ready["blockers"]
            assert ready["state"] in (
                "ELIGIBLE",
                "AWAITING_APPROVAL",
                "NOT_ELIGIBLE",
            )  # the signed policy gate may still block
            assert ready["state"] != "ACTIVE"  # reading eligibility activates nothing

    asyncio.run(run())


def test_the_profile_only_accepts_what_it_names() -> None:
    addr, taxes, contacts = workspace.clean(
        ADDRESS, [{"type": "VAT", "value": "GB123456789"}], {"invoice_email": " AP@Acme.Example "}
    )
    assert (
        addr["country"] == "GB"
        and taxes[0]["verification"] == "UNVERIFIED"
        and contacts["invoice_email"] == "ap@acme.example"
    )
    for args, code in (
        (({**ADDRESS, "extra": "x"}, [], CONTACTS), "ADDRESS_INVALID"),
        (({**ADDRESS, "country": "gb"}, [], CONTACTS), "ADDRESS_INVALID"),
        ((ADDRESS, [{"type": "VAT", "value": "x"}], CONTACTS), "TAX_ID_INVALID"),
        ((ADDRESS, [{"type": "VAT", "value": "GB123456789"}] * 2, CONTACTS), "TAX_ID_INVALID"),
        ((ADDRESS, [], {"notices_email": "a@b.example"}), "CONTACT_INVALID"),
        ((ADDRESS, [], {**CONTACTS, "phone": "1"}), "CONTACT_INVALID"),
    ):
        with pytest.raises(workspace.ProfileError) as e:
            workspace.clean(*args)
        assert e.value.code == code
