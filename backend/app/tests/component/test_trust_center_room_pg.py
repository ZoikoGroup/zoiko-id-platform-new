"""Privacy disclosures and the restricted evidence room over HTTP (ZID-CP-WF12 WF-12.03 and 12.05; CR-174), against PostgreSQL.

A disclosure is public only once someone other than its author approved it, and a universal notice promise is refused. A restricted document reaches a business only through a request staff approved,
bound to the person who asked: a forwarded token, an expired or revoked grant and a used-up download limit all fail, and every attempt is on record."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
TODAY = date.today()
NAMES = ("privacy_author", "privacy_approver", "evidence_mgr", "evidence_checker", "room_approver", "room_revoker", "customer", "forwardee")


def uid() -> str:
    return str(uuid.uuid4())


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    who = {n: uid() for n in NAMES} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in NAMES}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL, operator_tenant_id=o.tenant, operator_client_ids=(o.client,)),
        keyring=o.ring,
    )  # fmt: skip


def _grant(api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str) -> None:
    approvers = (who["grant_1"], who["grant_2"]) if CAPABILITIES[capability].two_person else (who["grant_1"],)
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _sql(owner_url: str, stmt: str, **params: Any) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            r = await c.execute(text(stmt), params)
            return [dict(x._mapping) for x in r.all()] if r.returns_rows else []

    return asyncio.run(go())


def test_a_disclosure_is_public_only_after_a_second_person_approves_and_never_carries_an_invented_number(rt_url: str) -> None:
    o, who = _people(rt_url)
    sub = {"purpose": "Hosting", "country": "GB", "access_mode": "hosting", "data_categories": ["DC2"], "contract_ref": "DPA-7"}
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        _grant(api, o, who, "privacy_author", "trust.privacy.author")
        _grant(api, o, who, "privacy_approver", "trust.privacy.approve")
        empty = api.get("/public/trust/v1/privacy?market=GB").json()
        assert empty["subprocessor"] == [] and empty["empty"]["subprocessor"] == "Not yet published for this market."

        body = {"kind": "subprocessor", "subject": "CloudCo", "market": "GB", "payload": sub | {"notice_days": 30}, "effective_from": str(TODAY)}
        no_source = call("POST", "/v1/trust-center/privacy/disclosures", "privacy_author", json=body)
        assert no_source.status_code == 422 and "notice_source" in no_source.json()["error"]["message"]
        ok = call("POST", "/v1/trust-center/privacy/disclosures", "privacy_author", json=body | {"payload": sub | {"notice_days": 30, "notice_source": "DPA-7 clause 9"}})
        assert ok.status_code == 201, ok.text
        did = ok.json()["disclosure_id"]
        assert api.get("/public/trust/v1/privacy?market=GB").json()["subprocessor"] == []  # a draft is not public
        assert call("POST", f"/v1/trust-center/privacy/disclosures/{did}:approve", "privacy_author").status_code == 403
        assert call("POST", f"/v1/trust-center/privacy/disclosures/{did}:approve", "privacy_approver").status_code == 200
        shown = api.get("/public/trust/v1/privacy?market=GB").json()
        assert shown["subprocessor"][0]["subject"] == "CloudCo" and shown["subprocessor"][0]["notice_days"] == 30 and "subprocessor" not in shown["empty"] and "retention" in shown["empty"]
        assert api.get("/public/trust/v1/privacy?market=FR").json()["subprocessor"] == []  # another market sees none of it

        # a residency claim is blocked while a subprocessor holding restricted data sits elsewhere
        far = call("POST", "/v1/trust-center/privacy/disclosures", "privacy_author", json={"kind": "subprocessor", "subject": "SupportCo", "market": "GB", "payload": sub | {"country": "IN", "access_mode": "support_remote", "data_categories": ["DC3"], "contract_ref": "DPA-9"}, "effective_from": str(TODAY)})
        call("POST", f"/v1/trust-center/privacy/disclosures/{far.json()['disclosure_id']}:approve", "privacy_approver")
        chk = call("GET", "/v1/trust-center/privacy/region-check?region=GB", "privacy_author").json()
        assert chk["may_claim_data_stays"] is False and "SupportCo" in chk["blockers"][0]


def test_a_restricted_document_reaches_only_the_person_who_asked_and_every_attempt_is_recorded(rt_url: str, owner_url: str) -> None:
    o, who = _people(rt_url)
    pdf = b"%PDF-1.4 restricted assurance report"
    digest = hashlib.sha256(pdf).hexdigest()
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        for person, cap in (("evidence_mgr", "trust.evidence.manage"), ("evidence_checker", "trust.evidence.manage"), ("room_approver", "trust.room.approve"), ("room_revoker", "trust.room.revoke")):
            _grant(api, o, who, person, cap)
        ev = call("POST", "/v1/trust-center/evidence", "evidence_mgr", json={
            "digest": digest, "evidence_type": "report", "scheme": "SOC 2", "issuer": "Auditor LLP", "legal_entity": "entity_demo", "products": ["verify_api"], "regions": ["US"],
            "period_start": str(TODAY - timedelta(days=30)), "period_end": str(TODAY + timedelta(days=300)), "holder": "zoiko", "restricted": True,
        }).json()  # fmt: skip
        eid = ev["evidence_id"]
        assert call("GET", "/trust/v1/room/catalogue", "customer").json()["items"] == []  # no file and not confirmed yet: nothing to ask for
        call("POST", f"/v1/trust-center/evidence/{eid}:confirm", "evidence_checker")

        wrong = call("POST", f"/v1/trust-center/evidence/{eid}/file", "evidence_mgr", json={"content_base64": base64.b64encode(b"another file").decode(), "content_type": "application/pdf"})
        assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "DIGEST_MISMATCH"
        up = call("POST", f"/v1/trust-center/evidence/{eid}/file", "evidence_mgr", json={"content_base64": base64.b64encode(pdf).decode(), "content_type": "application/pdf"})
        assert up.status_code == 201, up.text
        assert call("POST", f"/v1/trust-center/evidence/{eid}/file", "evidence_mgr", json={"content_base64": base64.b64encode(pdf).decode(), "content_type": "application/pdf"}).status_code == 422  # stored once

        assert api.get("/trust/v1/room/catalogue").status_code == 401
        cat = call("GET", "/trust/v1/room/catalogue", "customer").json()
        assert [i["evidence_id"] for i in cat["items"]] == [eid] and "content" not in str(cat)
        req = {"evidence_id": eid, "organization": "Buyer Corp", "purpose": "Vendor security review for procurement", "accept_terms": False}
        assert call("POST", "/trust/v1/evidence-requests", "customer", json=req).status_code == 422  # terms first
        made = call("POST", "/trust/v1/evidence-requests", "customer", json=req | {"accept_terms": True})
        assert made.status_code == 201, made.text
        rid = made.json()["request_id"]
        assert made.json()["status"] == "REQUESTED"
        assert call("GET", "/trust/v1/evidence-requests", "forwardee").json()["items"][0]["request_id"] == rid  # same company sees it; another person still cannot download

        # an unapproved request has no grant, and approval needs the capability and a second grant approver
        assert call("POST", f"/v1/trust-center/room/requests/{rid}:decide", "customer", json={"approve": True}).status_code == 403
        denied = call("POST", f"/v1/trust-center/room/requests/{rid}:decide", "room_approver", json={"approve": False})
        assert denied.status_code == 422 and denied.json()["error"]["code"] == "REASON_REQUIRED"
        ok = call("POST", f"/v1/trust-center/room/requests/{rid}:decide", "room_approver", json={"approve": True, "hours": 24, "max_downloads": 2})
        assert ok.status_code == 200, ok.text
        token = ok.json()["access_token"]
        assert token.startswith("zrg_") and "access_token" not in str(call("GET", "/trust/v1/evidence-requests", "customer").json())  # shown once

        # a forwarded token fails: the second person is not who it was issued to
        fwd = call("POST", "/trust/v1/room/downloads", "forwardee", json={"token": token})
        assert fwd.status_code == 403 and fwd.json()["error"]["code"] == "ACCESS_DENIED"
        assert call("POST", "/trust/v1/room/downloads", "customer", json={"token": "zrg_" + "x" * 40}).status_code == 403  # an unknown token looks the same
        got = call("POST", "/trust/v1/room/downloads", "customer", json={"token": token})
        assert got.status_code == 200 and got.content == pdf and got.headers["x-zoiko-watermark"].startswith("Provided to Buyer Corp") and got.headers["cache-control"] == "no-store"
        assert call("POST", "/trust/v1/room/downloads", "customer", json={"token": token}).status_code == 200
        limit = call("POST", "/trust/v1/room/downloads", "customer", json={"token": token})
        assert limit.status_code == 403  # two downloads allowed, the third is refused

        # revoking cuts access at once and returns the history
        rev = call("POST", f"/v1/trust-center/room/requests/{rid}:revoke", "room_revoker")
        assert rev.status_code == 200 and [h["outcome"] for h in rev.json()["history"]] == ["NOT_THE_REQUESTER", "ALLOWED", "ALLOWED", "DOWNLOAD_LIMIT_REACHED"]

    rows = _sql(owner_url, "SELECT count(*) AS n FROM platform.tc_room_access")
    assert rows[0]["n"] >= 4


def test_an_expired_grant_fails_even_for_the_right_person_with_the_right_token(rt_url: str, owner_url: str) -> None:
    o, who = _people(rt_url)
    txt = b"restricted summary"
    digest = hashlib.sha256(txt).hexdigest()
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        for person, cap in (("evidence_mgr", "trust.evidence.manage"), ("evidence_checker", "trust.evidence.manage"), ("room_approver", "trust.room.approve")):
            _grant(api, o, who, person, cap)
        eid = call("POST", "/v1/trust-center/evidence", "evidence_mgr", json={
            "digest": digest, "evidence_type": "assessment", "scheme": "Penetration test", "issuer": "Tester Ltd", "legal_entity": "entity_demo", "products": ["verify_api"],
            "period_start": str(TODAY - timedelta(days=10)), "period_end": str(TODAY + timedelta(days=100)), "holder": "zoiko", "restricted": True,
        }).json()["evidence_id"]  # fmt: skip
        call("POST", f"/v1/trust-center/evidence/{eid}:confirm", "evidence_checker")
        assert call("POST", f"/v1/trust-center/evidence/{eid}/file", "evidence_mgr", json={"content_base64": base64.b64encode(txt).decode(), "content_type": "text/plain"}).status_code == 201
        rid = call("POST", "/trust/v1/evidence-requests", "customer", json={"evidence_id": eid, "organization": "Buyer Corp", "purpose": "Vendor security review for procurement", "accept_terms": True}).json()["request_id"]
        token = call("POST", f"/v1/trust-center/room/requests/{rid}:decide", "room_approver", json={"approve": True}).json()["access_token"]
        ok = call("POST", "/trust/v1/room/downloads", "customer", json={"token": token})
        assert ok.status_code == 200 and ok.content.startswith(txt) and b"Provided to Buyer Corp" in ok.content  # plain text carries its watermark in the body
        _sql(owner_url, "UPDATE platform.tc_room_grant SET expires_at = now() - interval '1 minute' WHERE request_id = CAST(:r AS uuid)", r=rid)
        late = call("POST", "/trust/v1/room/downloads", "customer", json={"token": token})
        assert late.status_code == 410 and late.json()["error"]["code"] == "ACCESS_DENIED"
        assert _sql(owner_url, "SELECT outcome FROM platform.tc_room_access ORDER BY at DESC LIMIT 1")[0]["outcome"] == "EXPIRED"
