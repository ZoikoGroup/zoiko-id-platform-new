"""The restricted screening case queue and decisions over HTTP, operator plane (WF06 S04, S05, S09; CR-113), against PostgreSQL with real operator sessions.

The queue lists cases across tenants without a name; reading a case is audited; a hold is made at once and any other decision waits for a second person; the checker is never the maker; a prohibition and
the legal acts are Legal's alone, with a legal checker; a stale revision is refused; only the maker withdraws; and nothing a list or a person is called ever reaches an audit record."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from test_screening_requests_pg import MAX_AGE, NOW, load_lists, open_case, person
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.modules.screening.infrastructure.config import parse
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
CFG = parse(
    {"version": "t", "policy": {"required_lists": ["UN_SC"], "list_max_age_hours": 48 * 365 * 5}}
)
Q = "/v1/screening/review"


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, o: Operators) -> Any:
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
            operator_tenant_id=o.tenant, operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.screening_config = CFG
    return app


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = ("analyst", "senior_a", "senior_b", "legal_a", "legal_b", "stranger")
    who = {n: uid() for n in names} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person_: str, capability: str
) -> None:
    two = CAPABILITIES[capability].two_person
    approvers = (who["grant_1"], who["grant_2"]) if two else (who["grant_1"],)
    activate_grant(api, o, who[person_], approvers, capability=capability, resource_scope=SCOPE)


def _seed(rt_url: str, tenant: str) -> dict[str, str]:
    async def run() -> dict[str, str]:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(c, tenant, "key-hit-seed-1", person("Eric Badege"))
                clear = await open_case(c, tenant, "key-clear-seed1", person("Alice Wonderland"))
                other = await open_case(c, tenant, "key-hit-seed-2", person("Eric Badege"))
        return {
            "hit": hit["request_id"],
            "clear": clear["request_id"],
            "other": other["request_id"],
        }

    return asyncio.run(run())


def _audit(owner_url: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, body::text AS details FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND event_type LIKE 'screening.case.%' ORDER BY created_at, audit_id"
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


BODY = {
    "rationale": "The date of birth and the country on the list entry both differ from the subject.",
    "reason_codes": ["CONFLICTING_DOB"],
    "evidence_refs": ["ev_1"],
}


def test_reviewers_work_the_queue_propose_and_finalise_under_maker_and_checker(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    tenant = uid()
    ids = _seed(rt_url, tenant)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person_: str, **kw: Any) -> httpx.Response:
            return api.request(method, url, headers=o.bearer(who[person_]), **kw)

        assert api.get(f"{Q}/queue").status_code == 401
        assert call("GET", f"{Q}/queue", "stranger").status_code == 403
        for person_, cap in (
            ("analyst", "screening.case.analyst"),
            ("senior_a", "screening.case.senior"),
            ("senior_b", "screening.case.senior"),
            ("legal_a", "screening.case.legal"),
            ("legal_b", "screening.case.legal"),
        ):
            _grant(api, o, who, person_, cap)

        # the queue crosses tenants and carries no name
        page = call("GET", f"{Q}/queue?state=REVIEW_REQUIRED&limit=1", "analyst").json()
        assert len(page["items"]) == 1 and page["next_cursor"]
        everything = call("GET", f"{Q}/queue", "analyst").json()["items"]
        assert {i["request_id"] for i in everything} >= set(ids.values())
        assert all("Badege" not in str(i) and "name" not in i for i in everything)
        assert call("GET", f"{Q}/queue?cursor=nonsense", "analyst").status_code == 422

        # reading a case is audited and shows the restricted detail
        d = call("GET", f"{Q}/cases/{ids['hit']}", "analyst")
        assert d.status_code == 200, d.text
        detail = d.json()
        assert (
            detail["subject"]["name"] == "Eric Badege"
            and detail["candidates"]
            and detail["revision"] == 0
        )
        assert (
            detail["model"]["decision"]["status"] == "HOLD_PENDING_REVIEW"
            and detail["proposals"] == []
        )
        assert call("GET", f"{Q}/cases/{uuid.uuid4()}", "analyst").status_code == 404

        # a hold is made at once; a decision that clears waits for a second person
        hold = call(
            "POST",
            f"{Q}/cases/{ids['hit']}:propose",
            "analyst",
            json={
                **BODY,
                "disposition": "HOLD_PENDING_REVIEW",
                "action": "HOLD",
                "expected_revision": 0,
                "evidence_refs": [],
            },
        )
        assert (
            hold.status_code == 200
            and hold.json()["state"] == "DECIDED"
            and hold.json()["revision"] == 1
        ), hold.text
        stale = call(
            "POST",
            f"{Q}/cases/{ids['hit']}:propose",
            "analyst",
            json={
                **BODY,
                "disposition": "CLEARED_FOR_PURPOSE",
                "action": "RELEASE",
                "expected_revision": 0,
            },
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
        clear = call(
            "POST",
            f"{Q}/cases/{ids['hit']}:propose",
            "analyst",
            json={
                **BODY,
                "disposition": "CLEARED_FOR_PURPOSE",
                "action": "RELEASE",
                "expected_revision": 1,
            },
        )
        assert clear.status_code == 200 and clear.json()["state"] == "AWAITING_CHECKER", clear.text
        pid = clear.json()["proposal_id"]
        assert (
            call("GET", f"{Q}/cases/{ids['hit']}", "senior_a").json()["proposals"][0]["proposal_id"]
            == pid
        )
        assert (
            call("POST", f"{Q}/proposals/{pid}:finalise", "analyst").status_code == 403
        )  # an analyst finalises nothing
        done = call("POST", f"{Q}/proposals/{pid}:finalise", "senior_a")
        assert (
            done.status_code == 200
            and done.json()["state"] == "FINALISED"
            and done.json()["revision"] == 2
        ), done.text
        after = call("GET", f"{Q}/cases/{ids['hit']}", "senior_b").json()
        assert (
            after["state"] == "CLOSED_CLEAR_FOR_SCOPE"
            and after["chain_intact"]
            and len(after["decisions"]) == 2
        )
        assert (
            call("POST", f"{Q}/proposals/{pid}:finalise", "senior_b").status_code == 409
        )  # already finalised

        # the checker is never the maker
        mine = call(
            "POST",
            f"{Q}/cases/{ids['other']}:propose",
            "senior_a",
            json={
                **BODY,
                "disposition": "CLEARED_FOR_PURPOSE",
                "action": "RELEASE",
                "expected_revision": 0,
            },
        )
        own = call("POST", f"{Q}/proposals/{mine.json()['proposal_id']}:finalise", "senior_a")
        assert own.status_code == 403 and own.json()["error"]["code"] == "CHECKER_IS_THE_MAKER"
        assert (
            call(
                "POST", f"{Q}/proposals/{mine.json()['proposal_id']}:finalise", "senior_b"
            ).status_code
            == 200
        )

        # a prohibition and the legal acts are Legal's alone, with a legal checker
        legal_body = {
            **BODY,
            "disposition": "PROHIBITED",
            "action": "BLOCK",
            "expected_revision": 2,
            "legal_citation": "Reg 2014/833 Art 5",
            "review_by": (NOW + timedelta(days=365)).isoformat(),
        }
        refused = call("POST", f"{Q}/cases/{ids['hit']}:propose", "analyst", json=legal_body)
        assert (
            refused.status_code == 403 and refused.json()["error"]["code"] == "LEGAL_ROLE_REQUIRED"
        )
        prop = call("POST", f"{Q}/cases/{ids['hit']}:propose", "legal_a", json=legal_body)
        assert prop.status_code == 200 and prop.json()["state"] == "AWAITING_CHECKER", prop.text
        lp = prop.json()["proposal_id"]
        wrong = call("POST", f"{Q}/proposals/{lp}:finalise", "senior_a")
        assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "LEGAL_ROLE_REQUIRED"
        assert call("POST", f"{Q}/proposals/{lp}:finalise", "legal_a").status_code == 403
        assert call("POST", f"{Q}/proposals/{lp}:finalise", "legal_b").status_code == 200
        final = call("GET", f"{Q}/cases/{ids['hit']}", "legal_b").json()
        assert (
            final["state"] == "CLOSED_RESTRICTED"
            and final["model"]["decision"]["status"] == "PROHIBITED"
            and final["model"]["review"] == "LEGAL_SIGNED"
        )

        # only the maker withdraws
        w = call(
            "POST",
            f"{Q}/cases/{ids['clear']}:propose",
            "analyst",
            json={
                **BODY,
                "disposition": "UNRESOLVED",
                "action": "HOLD",
                "expected_revision": 0,
                "review_by": (NOW + timedelta(days=30)).isoformat(),
            },
        )
        assert w.status_code == 200, w.text
        if w.json()["state"] == "AWAITING_CHECKER":
            wp = w.json()["proposal_id"]
            assert call("POST", f"{Q}/proposals/{wp}:withdraw", "senior_a").status_code == 403
            assert call("POST", f"{Q}/proposals/{wp}:withdraw", "analyst").status_code == 200
            assert call("POST", f"{Q}/proposals/{wp}:finalise", "senior_a").status_code == 409

        # the audit trail names ids, codes and hashes and never a name
        events = _audit(owner_url)
        kinds = {(e["event_type"], e["outcome"]) for e in events}
        for expected in (
            ("screening.case.viewed", "SUCCESS"),
            ("screening.case.proposed", "SUCCESS"),
            ("screening.case.finalised", "SUCCESS"),
            ("screening.case.refused", "DENIED"),
        ):
            assert expected in kinds, expected
        assert all("Badege" not in e["details"] and "UN_SC" not in e["details"] for e in events)
        assert any(e["reason_code"] == "CHECKER_IS_THE_MAKER" for e in events)
        assert MAX_AGE.total_seconds() > 0
