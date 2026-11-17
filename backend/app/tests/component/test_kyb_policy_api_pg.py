"""Company-verification market policy packs over HTTP, operator plane (WF02 K09; CR-109), against PostgreSQL with real operator sessions.

Proposing, each sign-off role and publishing are separate operator capabilities. The author signs and publishes nothing, a person holds one signing role, publishing is refused with every missing control
named, a rollback adds an interval, and every step is a protected audit record."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.modules.organization.domain import company_documents as cd
from zid_platform.modules.organization.domain import market_policy as mp
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
BASE = "/v1/kyb/market-policies"
ROLES = ("compliance", "legal", "privacy", "security")
FORM = f"L{uuid.uuid4().hex[:6].upper()}"  # a legal form no other test uses, so the market key is this test's own
GOOD = mp.MarketPolicy(
    jurisdiction="GB",
    region=None,
    legal_form=FORM,
    purpose="ORG_ONBOARDING",
    risk_tier="LOW",
    version="v1",
    sources=(mp.Source("companies_house", 1, "LB-1", "kms://ch", True, "terms-2026-01"),),
    documents=(cd.DocumentRule("REGISTRY_EXTRACT", cd.Claim.CURRENT_STATUS, timedelta(days=90)),),
    ubo=mp.UboPolicy(Decimal(25), True, "compliance-gb"),
    reviewers_for_document_first=2,
    recheck_days={"K1": 180, "K2": 365},
    retention=mp.Retention({c: 365 for c in mp.RECORD_CLASSES}, "LB-RET-1", True, "job-1"),
)
V2 = replace(GOOD, version="v2", ubo=mp.UboPolicy(Decimal(10), True, "compliance-gb"))


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
            operator_tenant_id=o.tenant, operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )  # fmt: skip


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = ("author", "publisher", "stranger", *(f"app_{r}" for r in ROLES))
    who = {n: uid() for n in names} | {"approver_1": uid(), "approver_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["approver_1"]] = {OperatorRole.APPROVER}
    roles[who["approver_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    from zid_platform.modules.operator_access.domain.model import CAPABILITIES

    two = CAPABILITIES[capability].two_person
    approvers = (who["approver_1"], who["approver_2"]) if two else (who["approver_1"],)
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _audit(owner_url: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, target_ref FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND (target_ref LIKE 'sha256:%') ORDER BY created_at, audit_id"
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


def test_a_policy_pack_is_proposed_signed_published_and_rolled_back_by_operators(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    key = "|".join((GOOD.jurisdiction, "-", FORM, GOOD.purpose, GOOD.risk_tier))
    h1, h2 = mp.policy_hash(GOOD), mp.policy_hash(V2)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        # nobody without a token, nobody without a grant
        assert api.get(BASE).status_code == 401
        r = call("POST", BASE, "author", json={"pack": mp.policy_to_dict(GOOD)})
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        assert call("GET", BASE, "stranger").status_code == 403

        _grant(api, o, who, "author", "kyb.policy.author")
        _grant(api, o, who, "publisher", "kyb.policy.publish")
        for role in ROLES:
            _grant(api, o, who, f"app_{role}", f"kyb.policy.approve_{role}")

        # propose: the digest is the identity; an unreadable pack is refused; the same pack again is the same version
        r = call("POST", BASE, "author", json={"pack": mp.policy_to_dict(GOOD)})
        assert r.status_code == 201, r.text
        assert (
            r.json()["policy_hash"] == h1
            and r.json()["complete"] is True
            and r.json()["problems"] == []
        )
        assert (
            call("POST", BASE, "author", json={"pack": mp.policy_to_dict(GOOD)}).json()[
                "policy_hash"
            ]
            == h1
        )
        bad = call("POST", BASE, "author", json={"pack": {"jurisdiction": "GB"}})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "POLICY_UNREADABLE"

        # publishing with no sign-off names every missing control, and the author cannot sign or publish at all
        r = call("POST", f"{BASE}/{h1}:publish", "publisher")
        assert r.status_code == 409 and r.json()["error"]["code"] == "POLICY_NOT_PUBLISHABLE"
        assert all(f"SIGN_OFF_{x.upper()}_MISSING" in r.text for x in ROLES)
        assert (
            call("POST", f"{BASE}/{h1}:approve", "author", json={"role": "LEGAL"}).status_code
            == 403
        )
        assert call("POST", f"{BASE}/{h1}:publish", "author").status_code == 403
        d = call("GET", f"{BASE}/{h1}", "author").json()
        assert (
            d["publishable"] is False
            and "SIGN_OFF_LEGAL_MISSING" in d["problems"]
            and d["in_force"] is False
        )

        # a person signs only in the role they hold, three of four is not enough, then four is
        assert (
            call("POST", f"{BASE}/{h1}:approve", "app_legal", json={"role": "PRIVACY"}).status_code
            == 403
        )
        for role in ("compliance", "legal", "privacy"):
            r = call("POST", f"{BASE}/{h1}:approve", f"app_{role}", json={"role": role.upper()})
            assert r.status_code == 200 and r.json()["approved"] is True, r.text
        r = call("POST", f"{BASE}/{h1}:publish", "publisher")
        assert (
            r.status_code == 409
            and "SIGN_OFF_SECURITY_MISSING" in r.text
            and "SIGN_OFF_LEGAL_MISSING" not in r.text
        )
        assert (
            call(
                "POST", f"{BASE}/{h1}:approve", "app_security", json={"role": "SECURITY"}
            ).status_code
            == 200
        )
        r = call("POST", f"{BASE}/{h1}:publish", "publisher")
        assert r.status_code == 200 and r.json()["in_force"] is True, r.text
        assert (
            call("POST", f"{BASE}/{h1}:publish", "publisher").json()["error"]["code"]
            == "POLICY_ALREADY_ACTIVE"
        )

        # a replacement with a changed ownership threshold goes the same way; the first interval is ended, not rewritten
        assert call("POST", BASE, "author", json={"pack": mp.policy_to_dict(V2)}).status_code == 201
        for role in ROLES:
            assert (
                call(
                    "POST", f"{BASE}/{h2}:approve", f"app_{role}", json={"role": role.upper()}
                ).status_code
                == 200
            )
        assert call("POST", f"{BASE}/{h2}:publish", "publisher").status_code == 200
        listed = {i["policy_hash"]: i for i in call("GET", BASE, "publisher").json()["items"]}
        assert (
            listed[h2]["active"] is True
            and listed[h1]["active"] is False
            and listed[h2]["approved_roles"] == sorted(x.upper() for x in ROLES)
        )

        # roll back: a reason is required, a version that was never active cannot be rolled back to, and the rollback is a new interval
        body = {
            "market_key": key,
            "to_hash": h1,
            "reason": "The lower ownership threshold was not agreed with the market.",
        }
        assert (
            call("POST", f"{BASE}:rollback", "publisher", json={**body, "reason": "no"}).status_code
            == 422
        )
        assert (
            call(
                "POST",
                f"{BASE}:rollback",
                "publisher",
                json={**body, "to_hash": "sha256:" + "0" * 64},
            ).status_code
            == 409
        )
        assert call("POST", f"{BASE}:rollback", "publisher", json=body).status_code == 200
        listed = {i["policy_hash"]: i for i in call("GET", BASE, "publisher").json()["items"]}
        assert listed[h1]["active"] is True and listed[h2]["active"] is False

        # unknown versions are 404, malformed digests never reach the service
        assert call("GET", f"{BASE}/sha256:{'f' * 64}", "publisher").status_code == 404
        assert call("GET", f"{BASE}/not-a-digest", "publisher").status_code == 422

    events = [(a["event_type"], a["outcome"]) for a in _audit(owner_url)]
    for event in (
        "kyb.policy.proposed",
        "kyb.policy.approved",
        "kyb.policy.published",
        "kyb.policy.rolled_back",
    ):
        assert (event, "SUCCESS") in events, event
    assert any(e == "kyb.policy.refused" for e, _ in events)  # a refusal is its own record
    text_blob = json.dumps(events)
    assert (
        "ubo" not in text_blob and "threshold" not in text_blob
    )  # the audit carries ids, codes and digests, never the pack
