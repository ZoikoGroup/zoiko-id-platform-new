"""Official sanctions source releases over HTTP, operator plane (WF06 S10; CR-113), against PostgreSQL with real operator sessions.

Staging, approving and publishing are separate capabilities: the stager approves nothing, two other people approve, a corpus enters only by being fetched from the recorded address, the corpus in
use never changes until two have approved, a rollback needs two approvals of its own, and every step (and every refusal) is a protected audit record."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.modules.screening.infrastructure.config import parse
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
CFG = parse(
    {
        "version": "t",
        "policy": {"required_lists": ["UN_SC"], "list_max_age_hours": 48},
        "sources": {"UN_SC": {"url": UN_URL, "format": "xml", "redirect_hosts": []}},
    }
)


def uid() -> str:
    return str(uuid.uuid4())


def un_xml(count: int, tag: str = "A") -> bytes:
    people = "".join(
        f"<INDIVIDUAL><DATAID>{1000 + i}</DATAID><FIRST_NAME>{tag}{i}</FIRST_NAME><SECOND_NAME>Person</SECOND_NAME>"
        f"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL>"
        for i in range(count)
    )
    return f"<CONSOLIDATED_LIST><INDIVIDUALS>{people}</INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>".encode()


def _app(rt_url: str, o: Operators, served: dict[str, bytes]) -> Any:
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
            operator_tenant_id=o.tenant, operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.screening_config = CFG
    app.state.screening_fetch_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=served["body"])
    )
    return app


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = ("stager", "approver_a", "approver_b", "publisher", "stranger")
    who = {n: uid() for n in names} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    two = CAPABILITIES[capability].two_person
    approvers = (who["grant_1"], who["grant_2"]) if two else (who["grant_1"],)
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _audit(owner_url: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND event_type LIKE 'screening.source.%' ORDER BY created_at, audit_id"
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


def test_a_source_is_fetched_approved_by_two_others_published_and_rolled_back(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    served = {"body": un_xml(10, "A")}
    with TestClient(_app(rt_url, o, served), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        assert api.get("/v1/screening/sources").status_code == 401
        denied = call("POST", "/v1/screening/sources/UN_SC/releases:fetch", "stager")
        assert (
            denied.status_code == 403
            and denied.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        )
        assert call("GET", "/v1/screening/sources", "stranger").status_code == 403

        _grant(api, o, who, "stager", "screening.source.stage")
        _grant(api, o, who, "approver_a", "screening.source.approve")
        _grant(api, o, who, "approver_b", "screening.source.approve")
        _grant(api, o, who, "publisher", "screening.source.publish")

        listed = {
            i["source_id"]: i
            for i in call("GET", "/v1/screening/sources", "stager").json()["items"]
        }
        assert (
            listed["UN_SC"]["address_on_record"] is True
            and listed["UN_SC"]["freshness"] == "NEVER_RELEASED"
        )
        assert (
            listed["OFAC_SDN"]["address_on_record"] is False
        )  # no verified address, so nothing can be fetched

        # a source with no recorded address cannot be fetched, and the refusal is audited
        none = call("POST", "/v1/screening/sources/OFAC_SDN/releases:fetch", "stager")
        assert none.status_code == 409 and none.json()["error"]["code"] == "NO_ADDRESS"

        # fetch and stage: the release is inactive, and the stager can neither approve nor publish
        r = call("POST", "/v1/screening/sources/UN_SC/releases:fetch", "stager")
        assert r.status_code == 201, r.text
        one = r.json()
        assert (
            one["state"] == "VALIDATED"
            and one["blocked"] == []
            and one["receipt"]["item_count"] == 10
        )
        rid = one["release_id"]
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve", "stager", json={}).status_code
            == 403
        )
        assert call("POST", f"/v1/screening/releases/{rid}:publish", "stager").status_code == 403

        # publishing with no approvals, then one, is refused with the reason; two others make it publishable
        r = call("POST", f"/v1/screening/releases/{rid}:publish", "publisher")
        assert r.status_code == 409 and "TWO_APPROVERS_REQUIRED" in r.text
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve", "approver_a", json={}).status_code
            == 200
        )
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve", "approver_a", json={}).status_code
            == 409
        )  # not twice
        assert call("POST", f"/v1/screening/releases/{rid}:publish", "publisher").status_code == 409
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve", "approver_b", json={}).status_code
            == 200
        )
        r = call("POST", f"/v1/screening/releases/{rid}:publish", "publisher")
        assert r.status_code == 200 and r.json()["state"] == "ACTIVE", r.text
        listed = {
            i["source_id"]: i
            for i in call("GET", "/v1/screening/sources", "publisher").json()["items"]
        }
        assert (
            listed["UN_SC"]["freshness"] == "FRESH"
            and listed["UN_SC"]["snapshot"] == one["receipt"]["sha256"]
        )

        # a second corpus with a large drift needs each anomaly acknowledged by name
        served["body"] = un_xml(40, "B")
        two = call("POST", "/v1/screening/sources/UN_SC/releases:fetch", "stager").json()
        assert two["flags"] == ["COUNT_DRIFT_ABOVE_LIMIT"]
        bare = call(
            "POST", f"/v1/screening/releases/{two['release_id']}:approve", "approver_a", json={}
        )
        assert bare.status_code == 409 and bare.json()["error"]["code"] == "FLAGS_NOT_ACKNOWLEDGED"
        diff = call(
            "GET", f"/v1/screening/releases/{two['release_id']}/comparison", "stager"
        ).json()
        assert diff["added"] == 30 and diff["changed"] == 10 and diff["removed"] == 0
        for who_ in ("approver_a", "approver_b"):
            ok = call(
                "POST",
                f"/v1/screening/releases/{two['release_id']}:approve",
                who_,
                json={"acknowledged": ["COUNT_DRIFT_ABOVE_LIMIT"]},
            )
            assert ok.status_code == 200, ok.text
        assert (
            call(
                "POST", f"/v1/screening/releases/{two['release_id']}:publish", "publisher"
            ).status_code
            == 200
        )
        history = call("GET", "/v1/screening/sources/UN_SC/releases", "stager").json()["items"]
        assert {h["release_id"]: h["state"] for h in history} == {
            rid: "SUPERSEDED",
            two["release_id"]: "ACTIVE",
        }

        # a rollback needs two approvals of its own, then the earlier corpus is back and the later one is superseded, not deleted
        assert (
            call("POST", f"/v1/screening/releases/{rid}:rollback", "publisher").status_code == 409
        )
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve-rollback", "stager").status_code
            == 403
        )
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve-rollback", "approver_a").status_code
            == 200
        )
        assert (
            call("POST", f"/v1/screening/releases/{rid}:rollback", "publisher").status_code == 409
        )
        assert (
            call("POST", f"/v1/screening/releases/{rid}:approve-rollback", "approver_b").status_code
            == 200
        )
        back = call("POST", f"/v1/screening/releases/{rid}:rollback", "publisher")
        assert back.status_code == 200 and back.json()["state"] == "ACTIVE", back.text
        history = call("GET", "/v1/screening/sources/UN_SC/releases", "stager").json()["items"]
        assert {h["release_id"]: h["state"] for h in history} == {
            rid: "ACTIVE",
            two["release_id"]: "SUPERSEDED",
        }
        assert call("GET", f"/v1/screening/releases/{uuid.uuid4()}", "stager").status_code == 404

    events = _audit(owner_url)
    kinds = [(e["event_type"], e["outcome"]) for e in events]
    for expected in (
        ("screening.source.staged", "SUCCESS"),
        ("screening.source.approved", "SUCCESS"),
        ("screening.source.published", "SUCCESS"),
        ("screening.source.rollback_approved", "SUCCESS"),
        ("screening.source.rolled_back", "SUCCESS"),
        ("screening.source.refused", "DENIED"),
    ):
        assert expected in kinds, expected
    assert any(e["reason_code"] == "NO_ADDRESS" for e in events)
    assert all(
        "Person" not in str(e) for e in events
    )  # no name from a list is ever in an audit record
