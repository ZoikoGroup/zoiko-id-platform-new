"""Regulatory policy pack API, operator plane (ZID-RCG-003 RCG-G01; CR-067), against PostgreSQL with real operator sessions.

Authoring, each approver role, and publishing are separate operator capabilities, each granted just-in-time with two approvers. The
author signs nothing, each approver holds one role, a pack changes only through a new version, and rolling back is retiring the newer one.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings

SCOPE = "regulatory:*"


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
            operator_tenant_id=o.tenant,
            operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )


def _body(market: dict[str, str], **kw: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "pack_id": f"t-{uuid.uuid4().hex[:12]}",
        "country": "GB",
        "subdivision": "GB-ENG",
        "profession": "nurse",
        **market,
        "sources": [
            {
                "instrument": "Example Act 2024",
                "url": "https://example.test/act",
                "status": "LAW",
                "retrieved_at": (now - timedelta(days=1)).isoformat(),
                "effective_from": (now - timedelta(days=30)).isoformat(),
                "owner": "legal-team",
            }
        ],
        "rules": {"evidence_minimum": ["LICENSE_STANDING"], "lawful_basis": "contract"},
        **kw,
    }


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = ("maker", "legal", "grc", "security", "business_owner", "publisher")
    who = {n: uid() for n in names} | {"approver_1": uid(), "approver_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["approver_1"]] = {OperatorRole.APPROVER}
    roles[who["approver_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    # as many approvers as the capability needs: one for authoring, two for the approver roles and publishing
    approvers = (
        (who["approver_1"],)
        if capability == "regulatory.pack.author"
        else (who["approver_1"], who["approver_2"])
    )
    activate_grant(
        api,
        o,
        who[person],
        approvers,
        capability=capability,
        resource_scope=SCOPE,
    )


def _audit_records(owner_url: str, pack_id: str) -> list[dict[str, Any]]:
    """The platform-partition audit records about one pack (tenant_id NULL), oldest first."""

    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, target_ref, body FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND (target_ref = :p OR target_ref LIKE :v) ORDER BY created_at, audit_id"
                    ),
                    {"p": pack_id, "v": f"{pack_id}:v%"},
                )
            ).all()
        out = [dict(r._mapping) for r in rows]
        for r in out:
            if isinstance(r["body"], str):
                r["body"] = json.loads(r["body"])
        return out

    return asyncio.run(read())


def test_a_pack_goes_from_draft_to_published_and_back_through_the_operator_plane(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    market = {"product": f"P{uuid.uuid4().hex[:10].upper()}", "purpose": "ENGAGEMENT"}
    base = "/v1/regulatory/policy-packs"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        # nobody without a token, and nobody without the grant
        assert api.post(base, json=_body(market)).status_code == 401
        r = call("POST", base, "maker", json=_body(market))
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        assert call("GET", base, "maker").status_code == 403

        _grant(api, o, who, "maker", "regulatory.pack.author")
        for role in ("legal", "grc", "security", "business_owner"):
            _grant(api, o, who, role, f"regulatory.pack.approve_{role}")
        _grant(api, o, who, "publisher", "regulatory.pack.publish")

        r = call("POST", base, "maker", json=(draft := _body(market)))
        assert r.status_code == 201, r.text
        v1 = r.json()
        pid, digest = v1["pack_id"], v1["pack_hash"]
        assert (v1["version"], v1["status"], v1["author"]) == (1, "DRAFT", who["maker"])
        assert v1["missing_approvals"] == ["legal", "grc", "security", "business_owner"]
        assert call("POST", base, "maker", json=draft).json()["error"]["code"] == "PACK_EXISTS"

        one = f"{base}/{pid}/versions/1"
        # the author holds no approver capability, and an approval must sign the hash it was shown
        r = call("POST", f"{one}:approve", "maker", json={"role": "legal", "signed_hash": digest})
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        r = call("POST", f"{one}:approve", "legal", json={"role": "legal", "signed_hash": "0" * 64})
        assert r.status_code == 409 and r.json()["error"]["code"] == "PACK_APPROVAL_HASH_MISMATCH"
        # a person holds one role: legal's capability does not sign as security
        r = call(
            "POST", f"{one}:approve", "legal", json={"role": "security", "signed_hash": digest}
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"

        r = call("POST", f"{one}:approve", "legal", json={"role": "legal", "signed_hash": digest})
        assert r.status_code == 200 and r.json()["missing_approvals"] == [
            "grc",
            "security",
            "business_owner",
        ]
        r = call("POST", f"{one}:publish", "publisher")
        assert r.status_code == 409 and r.json()["error"]["code"] == "PACK_NOT_PUBLISHABLE"
        assert (
            call("POST", f"{one}:publish", "maker").status_code == 403
        )  # the author cannot publish either

        for role in ("grc", "security", "business_owner"):
            r = call("POST", f"{one}:approve", role, json={"role": role, "signed_hash": digest})
            assert r.status_code == 200, r.text
        r = call("POST", f"{one}:publish", "publisher")
        assert (
            r.status_code == 200
            and r.json()["status"] == "APPROVED"
            and r.json()["missing_approvals"] == []
        )
        assert (
            call(
                "POST", f"{one}:approve", "legal", json={"role": "legal", "signed_hash": digest}
            ).json()["error"]["code"]
            == "PACK_NOT_DRAFT"
        )

        def resolve(**q: str) -> dict[str, Any]:
            r = call(
                "GET", "/v1/regulatory/resolution", "maker", params={**market, "country": "GB", **q}
            )
            assert r.status_code == 200, r.text
            body: dict[str, Any] = r.json()
            return body

        assert resolve(subdivision="GB-ENG", profession="nurse")["outcome"] == "APPLICABLE"
        scot = resolve(subdivision="GB-SCT", profession="nurse")
        assert (scot["outcome"], scot["reason"]) == ("REVIEW", "policy_missing")
        fr = call(
            "GET", "/v1/regulatory/resolution", "maker", params={**market, "country": "FR"}
        ).json()
        assert (fr["outcome"], fr["reason"]) == ("UNAVAILABLE", "market_not_approved")

        # a change is a new version; one open draft at a time; the published version is untouched
        r = call(
            "POST",
            f"{base}/{pid}:revise",
            "maker",
            json={"rules": {"evidence_minimum": ["LICENSE_STANDING", "INSURANCE"]}},
        )
        assert r.status_code == 201 and (r.json()["version"], r.json()["status"]) == (2, "DRAFT")
        assert (
            call(
                "POST", f"{base}/{pid}:revise", "maker", json={"rules": {"evidence_minimum": ["X"]}}
            ).json()["error"]["code"]
            == "PACK_DRAFT_OPEN"
        )
        assert call("GET", one, "maker").json()["rules"]["evidence_minimum"] == ["LICENSE_STANDING"]
        two = f"{base}/{pid}/versions/2"
        h2 = r.json()["pack_hash"]
        assert h2 != digest
        for role in ("legal", "grc", "security", "business_owner"):
            assert (
                call(
                    "POST", f"{two}:approve", role, json={"role": role, "signed_hash": h2}
                ).status_code
                == 200
            )
        assert call("POST", f"{two}:publish", "publisher").status_code == 200
        assert resolve(subdivision="GB-ENG", profession="nurse")["pack"]["version"] == 2

        # rolling back is retiring the newer version: the last approved one governs again
        assert call("POST", f"{two}:retire", "publisher").json()["status"] == "RETIRED"
        assert resolve(subdivision="GB-ENG", profession="nurse")["pack"]["version"] == 1
        assert (
            call("POST", f"{two}:retire", "publisher").json()["error"]["code"]
            == "PACK_NOT_APPROVED"
        )

        listed = call("GET", base, "maker", params={"country": "GB", "status": "APPROVED"}).json()[
            "items"
        ]
        assert [(p["pack_id"], p["version"]) for p in listed if p["pack_id"] == pid] == [(pid, 1)]
        assert (
            call("GET", f"{base}/{pid}/versions/9", "maker").json()["error"]["code"]
            == "PACK_NOT_FOUND"
        )

    # ---- the audit trail: one record per change, none for an attempt that rolled back, a refusal record for every refusal
    records = _audit_records(owner_url, pid)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_event.setdefault(r["event_type"], []).append(r)
    assert [(r["target_ref"], r["outcome"]) for r in by_event["regulatory.pack.drafted"]] == [
        (f"{pid}:v1", "SUCCESS")
    ]
    assert (
        len(by_event["regulatory.pack.approved"]) == 8
    )  # four roles on each of two versions; the refused attempts left none
    v1_roles = sorted(
        r["body"]["details"]["role"]
        for r in by_event["regulatory.pack.approved"]
        if r["target_ref"] == f"{pid}:v1"
    )
    assert v1_roles == ["business_owner", "grc", "legal", "security"]
    assert [r["target_ref"] for r in by_event["regulatory.pack.published"]] == [
        f"{pid}:v1",
        f"{pid}:v2",
    ]
    assert [r["target_ref"] for r in by_event["regulatory.pack.revised"]] == [f"{pid}:v2"]
    assert [r["target_ref"] for r in by_event["regulatory.pack.retired"]] == [f"{pid}:v2"]
    published_v1 = by_event["regulatory.pack.published"][0]
    assert published_v1["body"]["details"]["pack_hash"] == digest
    refused = by_event["regulatory.pack.refused"]
    assert {r["reason_code"] for r in refused} >= {
        "OPERATOR_CAPABILITY_REQUIRED",
        "PACK_APPROVAL_HASH_MISMATCH",
        "PACK_NOT_PUBLISHABLE",
        "PACK_NOT_DRAFT",
        "PACK_DRAFT_OPEN",
        "PACK_NOT_APPROVED",
        "PACK_EXISTS",
    }
    assert all(r["outcome"] == "DENIED" for r in refused)
    assert all(
        r["body"]["details"].get("attempted", "").startswith("regulatory.pack.") for r in refused
    )
    # ids, codes and the hash only: neither the rules nor the legal text ever reach the record
    everything = " ".join(str(r["body"]) for r in records)
    assert (
        "LICENSE_STANDING" not in everything
        and "Example Act" not in everything
        and "example.test" not in everything
    )


def test_whoever_authors_a_pack_cannot_also_hold_an_approver_role(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        _grant(api, o, who, "maker", "regulatory.pack.author")
        r = api.post(
            "/operator/access-requests",
            json={
                "capability": "regulatory.pack.approve_legal",
                "resource_scope": SCOPE,
                "case_ref": "INC-OPS-1",
                "reason": "operational change under review",
                "duration_minutes": 60,
            },
            headers=o.bearer(who["maker"]),
        )
        assert r.status_code in (403, 409, 422), r.text
        assert "SOD" in r.text, r.text  # OPERATOR_SOD_CONFLICT: the maker is never also a checker


def test_a_bad_request_is_refused_before_it_reaches_the_pack(rt_url: str) -> None:
    o, who = _people(rt_url)
    market = {"product": f"P{uuid.uuid4().hex[:10].upper()}", "purpose": "ENGAGEMENT"}
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        _grant(api, o, who, "maker", "regulatory.pack.author")

        def post(body: dict[str, Any]) -> int:
            return api.post(
                "/v1/regulatory/policy-packs", json=body, headers=o.bearer(who["maker"])
            ).status_code

        assert post(_body(market, pack_id="Upper_Case")) == 422
        assert post(_body(market, country="gb")) == 422
        assert post(_body(market, subdivision="england")) == 422
        assert post(_body(market, rules={"evidence_minimum": []})) == 422
        assert post(_body(market, unknown_field=1)) == 422
        naive = _body(market)
        naive["sources"][0]["effective_from"] = "2026-01-01T00:00:00"  # no time zone
        assert post(naive) == 422
        insecure = _body(market)
        insecure["sources"][0]["url"] = "http://example.test/act"
        assert post(insecure) == 422
        assert post(_body(market)) == 201  # a good one still works
