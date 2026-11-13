"""`property.authority_changed` and `provider.entitlement_revoked` (ZID-INT-002 section 13), through the real app and Postgres.

Only a CHANGE is announced: a first check, a repeated one and a check of another property say nothing. Neither event carries a document, a
reference to evidence or any text from one.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_v2_authority_pg import upload
from test_v2_facade_pg import world
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.trust import TrustConfig
from zid_platform.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from pdf_signing import pki, sign_pdf


def events(rt_url: str, tenant: str, kind: str) -> list[dict]:
    async def go() -> list[dict]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                (
                    await c.execute(
                        text(
                            "SELECT payload::text FROM events.outbox_event WHERE event_type LIKE :k ORDER BY created_at"
                        ),
                        {"k": f"%{kind}%"},
                    )
                )
                .scalars()
                .all()
            )
        return [json.loads(r) for r in rows]

    return asyncio.run(go())


def test_a_changed_property_answer_is_announced_and_a_first_or_repeated_one_is_not(
    rt_url: str,
) -> None:
    p = pki()
    base = {
        "property_ref": "prop_evt",
        "host_ref": "sub_host",
        "jurisdiction": {"country": "GB", "subdivision": "GB-ENG"},
        "relationship": "authorized_agent",
        "requested_dimensions": ["ownership", "mandate"],
    }
    with world(rt_url) as (http, mine, tenant, _admin):
        http.app.state.trust_config = TrustConfig(anchors=(p.root.cert,))
        title = upload(http, mine, sign_pdf(p))
        mandate = upload(http, mine, sign_pdf(p))

        def check(**extra) -> dict:
            r = http.post("/v2/rooms/property-checks", headers=mine, json={**base, **extra})
            assert r.status_code == 200, r.text
            return r.json()

        first = check(title_evidence_refs=[title])
        import yaml

        rooms = yaml.safe_load(
            (
                Path(__file__).resolve().parents[4] / "contracts/consumers/zoiko-rooms.yaml"
            ).read_text(encoding="utf-8")
        )["requests"][0]
        assert set(rooms["response_keys"]) <= set(first), "Rooms relies on these keys"
        assert first["listing_authority"] in rooms["listing_authority_values"]
        assert first["listing_authority"] not in rooms["never_returned"]
        assert first["authority_changed_event_emitted"] is False, "the first check is not a change"
        again = check(title_evidence_refs=[title])
        assert again["authority_changed_event_emitted"] is False, "an identical answer is not one"
        other = check(title_evidence_refs=[title], property_ref="prop_other")
        assert other["authority_changed_event_emitted"] is False
        assert events(rt_url, tenant, "property.authority_changed") == []

        moved = check(title_evidence_refs=[title], mandate_evidence_refs=[mandate])
        assert moved["authority_changed_event_emitted"] is True
        sent = events(rt_url, tenant, "property.authority_changed")
        assert len(sent) == 1
        data = sent[0].get("data", sent[0])
        assert data["property_ref"] == "prop_evt" and data["host_ref"] == "sub_host"
        assert data["previous_listing_authority"] == "UNKNOWN"
        assert data["current_listing_authority"] == "REVIEW_REQUIRED"
        assert data["dimensions_changed"] == [
            {"dimension": "mandate", "from": "INDETERMINATE", "to": "VERIFIED_LIMITED"}
        ]
        assert title not in json.dumps(sent) and mandate not in json.dumps(sent)

        # the mandate is withdrawn: that is a change back, and is announced again
        back = check(title_evidence_refs=[title])
        assert back["authority_changed_event_emitted"] is True
        assert len(events(rt_url, tenant, "property.authority_changed")) == 2


def entitlement(**over) -> dict:
    today = date.today()
    return {
        "service_code": "id.document",
        "countries": ["GB", "IE"],
        "purposes": ["onboarding"],
        "processing_regions": ["uk"],
        "valid_from": (today - timedelta(days=1)).isoformat(),
        "valid_until": (today + timedelta(days=300)).isoformat(),
        "assurance": 4,
        **over,
    }


def test_a_revoked_provider_entitlement_closes_the_path_is_announced_once_and_is_never_reopened(
    rt_url: str,
) -> None:
    o = onboard(
        rt_url,
        extra_actions=("credential.claim.verify",),
        extra_resources=("provider-entitlement:*",),
    )
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    h = o.bearer(o.owner)
    ask = {
        "country": "GB",
        "service_code": "id.document",
        "purpose": "onboarding",
        "processing_region": "uk",
        "required_assurance": 3,
    }
    with TestClient(app, base_url=ISS) as api:
        assert api.post("/v2/providers/acme/entitlements", json=entitlement()).status_code == 401
        made = api.post("/v2/providers/acme/entitlements", headers=h, json=entitlement())
        assert made.status_code == 201, made.text
        assert made.json()["status"] == "ACTIVE"
        dup = api.post("/v2/providers/acme/entitlements", headers=h, json=entitlement())
        assert dup.status_code == 409, "one live record per provider and service"
        api.post(
            "/v2/providers/zeta/entitlements", headers=h, json=entitlement(assurance=2)
        )  # lawful but weaker

        routed = api.post("/v2/providers/route", headers=h, json=ask).json()
        assert routed["provider_id"] == "acme" and routed["refusals"] == []
        wrong_place = api.post("/v2/providers/route", headers=h, json={**ask, "country": "FR"})
        assert wrong_place.json()["provider_id"] is None
        assert "COUNTRY_NOT_COVERED" in wrong_place.json()["refusals"]

        # a suspension disables the path but is not announced as a revocation
        s = api.post(
            "/v2/providers/acme/entitlements/id.document/suspend",
            headers=h,
            json={"reason": "contract review"},
        )
        assert s.status_code == 200 and s.json()["status"] == "SUSPENDED"
        assert api.post("/v2/providers/route", headers=h, json=ask).json()["provider_id"] is None
        assert events(rt_url, o.tenant, "provider.entitlement_revoked") == []

        r = api.post(
            "/v2/providers/acme/entitlements/id.document/revoke",
            headers=h,
            json={"reason": "contract ended"},
        )
        assert r.status_code == 200 and r.json()["status"] == "REVOKED"
        sent = events(rt_url, o.tenant, "provider.entitlement_revoked")
        assert len(sent) == 1
        data = sent[0].get("data", sent[0])
        assert data["provider_id"] == "acme" and data["service_code"] == "id.document"
        assert data["previous_status"] == "SUSPENDED"
        assert "contract ended" not in json.dumps(sent), "free text is not put in the event"
        again = api.post(
            "/v2/providers/acme/entitlements/id.document/revoke",
            headers=h,
            json={"reason": "contract ended"},
        )
        assert again.status_code == 404, "a revoked entitlement has nothing left to revoke"
        assert len(events(rt_url, o.tenant, "provider.entitlement_revoked")) == 1

        listed = api.get("/v2/providers/entitlements?provider_id=acme", headers=h).json()
        assert [e["status"] for e in listed["entitlements"]] == ["REVOKED"]
        fresh = api.post("/v2/providers/acme/entitlements", headers=h, json=entitlement())
        assert fresh.status_code == 201, "a new period is a new record"

    async def reopen() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await c.execute(
                text(
                    "UPDATE verify.provider_entitlement SET status = 'ACTIVE' WHERE status = 'REVOKED'"
                )
            )

    with pytest.raises(DBAPIError):
        asyncio.run(reopen())


def test_t34_another_tenants_administrator_cannot_read_export_or_remove_raw_evidence(
    rt_url: str,
) -> None:
    """ZID-INT-002 T34: cross-tenant raw evidence export and broad admin override are denied.

    Tenant B's administrator holds every evidence action in B, and that is still nothing in A: A's upload is invisible to B's list, cannot be
    deleted by B, cannot back a document or mandate check made by B, and no route returns the bytes at all."""
    from test_v2_uploads_pg import announce, put

    pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    with (
        world(rt_url) as (a_http, a_mine, a_tenant, _a),
        world(rt_url) as (b_http, b_mine, b_tenant, _b),
    ):
        assert a_tenant != b_tenant
        a = announce(a_http, a_mine, pdf, "application/pdf").json()
        assert put(a_http, a_mine, a, pdf).status_code == 201
        ref = a["evidence_ref"]

        assert b_http.get("/v2/evidence/uploads", headers=b_mine).json()["items"] == []
        gone = b_http.delete(f"/v2/evidence/{ref}", headers=b_mine)
        assert gone.status_code == 404, "B cannot even tell that the reference exists"
        got = b_http.get(f"/v2/evidence/{ref}", headers=b_mine)
        assert got.status_code in (404, 405)
        for path in (f"/v2/evidence/{ref}/content", f"/v2/evidence/{ref}/export"):
            assert b_http.get(path, headers=a_mine).status_code in (404, 405), (
                "no route returns the raw bytes"
            )

        used = b_http.post(
            "/v2/mandates/verify",
            headers=b_mine,
            json={
                "organization_ref": "org_b",
                "subject_ref": "sub_b",
                "authority_claim": "sign",
                "mandate_evidence_refs": [ref],
            },
        )
        assert used.status_code == 200, used.text
        item = used.json()["evidence"][0]
        assert item["usable"] is False
        assert used.json()["authority"]["outcome"] != "VERIFIED_LIMITED", (
            "another tenant's evidence cannot back B's check"
        )

        # A is untouched by all of it
        listed = a_http.get("/v2/evidence/uploads", headers=a_mine).json()["items"]
        assert [u["evidence_ref"] for u in listed] == [ref]
