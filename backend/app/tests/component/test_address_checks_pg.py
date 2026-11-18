"""WF-05 address and proof of address over HTTP on PostgreSQL (A05-A07, A10; CR-115).

An address is set and formatted (never "verified"); a document type the market does not accept is refused; an accepted one is assessed and waits for
a person; the maker cannot check their own review, another reviewer can; the case evidence then shows the document review and the association as two
separate assertions. The tables refuse a checker who is the maker and an edit of a review step.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
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
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.infrastructure import check_method_config
from zid_platform.settings import Settings

LOOKUP, DOC = cm.CheckMethod.ADDRESS_LOOKUP, cm.CheckMethod.ADDRESS_DOCUMENT
HOME = {"line1": "12 Station Road", "city": "Leeds", "postal_code": "LS1 4AB", "country": "GB"}


def test_an_address_document_is_reviewed_by_one_person_and_checked_by_another(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = uuid.uuid4().hex[:6]
    adapters = {
        LOOKUP: cm.Adapter(LOOKUP, "zid-address-format", v, cm.Route.OPEN),
        DOC: cm.Adapter(DOC, "zid-address-review", v, cm.Route.MANUAL),
    }
    approved = {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }
    monkeypatch.setattr(check_method_config, "adapters", lambda: adapters)
    monkeypatch.setattr(check_method_config, "retention", lambda: approved)
    tenant, admin = _tenant(rt_url)
    other = str(uuid.uuid4())

    async def second_reviewer() -> None:
        from zid_platform.modules.principal.domain.principal import (
            Principal,
            PrincipalStatus,
            PrincipalType,
        )
        from zid_platform.modules.principal.infrastructure.principal_store import create_principal

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(other, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(second_reviewer())
    for m in (LOOKUP, DOC):
        scope = bm.Scope(m, adapters[m].engine_id, v, "GB", "ONBOARDING", tenant)

        async def live(c: Any, scope: bm.Scope = scope, m: cm.CheckMethod = m) -> bm.Record:
            return await _to_production(c, scope, f"{m.value.lower().replace('_', '-')}-{v}")

        asyncio.run(_owner(owner_url, live))

    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"), keyring=ring
    )  # fmt: skip
    app.state.privacy_overlays = {"GB": _signed_overlay("GB")}

    def bearer(subject: str) -> dict[str, str]:
        t = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=subject, tenant_id=tenant, client_id="onboarding-svc", now=datetime.now(UTC),
            scope=frozenset({"verification:execute", "verification:read", "verification:review"}),
        )  # fmt: skip
        return {"Authorization": f"Bearer {t}"}

    a, b = bearer(admin), bearer(other)
    with TestClient(app, base_url=ISS) as http:
        cid = http.post(
            "/v1/verification-cases", headers={**a, "Idempotency-Key": f"a-{v}"},
            json={"subject_ref": "cust-9", "purpose": "ONBOARDING", "market": "GB", "methods": ["ADDRESS_LOOKUP", "ADDRESS_DOCUMENT"]},
        ).json()["case_id"]  # fmt: skip
        base = f"/v1/verification-cases/{cid}"
        set_ = http.put(f"{base}/address", headers=a, json=HOME)
        assert set_.status_code == 200, set_.text
        assert set_.json()["format_outcome"] == "PASS" and "not proof" in set_.json()["note"]

        doc = {
            "upload_ref": f"upl_{v}0001", "doc_type": "UTILITY_BILL", "issuer": "Northern Power", "issued_on": str(date.today() - timedelta(days=10)),
            "name_on_document": "Dana Lee", "address_on_document": HOME, "subject_name": "Dana Lee",
        }  # fmt: skip
        assert (
            http.post(
                f"{base}/address-documents", headers=a, json={**doc, "doc_type": "MOBILE_BILL"}
            ).status_code
            == 422
        )
        added = http.post(f"{base}/address-documents", headers=a, json=doc)
        assert added.status_code == 201, added.text
        assert (
            added.json()["assessment"] == "SUPPORTS" and added.json()["status"] == "AWAITING_REVIEW"
        )
        did = added.json()["document_id"]
        assert (
            "Dana" not in http.get(f"{base}/address-documents", headers=a).text
        )  # the compared name is never stored or shown here

        reviewed = http.post(
            f"{base}/address-documents/{did}:review", headers=a, json={"finding": "CONFIRMED"}
        )
        assert reviewed.status_code == 200 and reviewed.json()["status"] == "AWAITING_CHECKER"
        assert (
            http.post(
                f"{base}/address-documents/{did}:check", headers=a, json={"agrees": True}
            ).status_code
            == 403
        )
        checked = http.post(
            f"{base}/address-documents/{did}:check", headers=b, json={"agrees": True}
        )
        assert checked.status_code == 200 and checked.json()["recorded"] == {
            "ADDRESS_ASSOCIATION_CONFIRMED": "PASS"
        }

        ev = {
            x["type"]: x["outcome"]
            for x in http.get(f"{base}/evidence", headers=a).json()["assertions"]
        }
        assert ev == {
            "ADDRESS_FORMAT_VALID": "PASS",
            "ADDRESS_DOCUMENT_REVIEWED": "PASS",
            "ADDRESS_ASSOCIATION_CONFIRMED": "PASS",
        }

    async def edit() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await c.execute(
                text(
                    "UPDATE verify.check_address_review SET finding = 'NOT_SUPPORTED' WHERE document_id = :d"
                ),
                {"d": uuid.UUID(did)},
            )

    with pytest.raises(DBAPIError):
        asyncio.run(edit())
