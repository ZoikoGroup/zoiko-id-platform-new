"""Evidence packages over HTTP, operator plane (WF06 S13; CR-113), against PostgreSQL with real operator sessions.

A package states its purpose and is built from the case's own records; sensitive material needs a second person who is not the requester and still leaves only as hashes; the package is checked as it leaves
(an edit is found, a personal field is found); it expires and is then cleared; and every step is a protected audit record that holds no name."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from operator_fixture import ISS, Operators, operators
from sqlalchemy import text
from test_screening_cases_api_pg import _app, _grant, _seed
from zid_platform.modules.operator_access.domain.model import OperatorRole

Q = "/v1/screening/review"
PURPOSE = "Annual audit of screening decisions for the 2026 review"


def uid() -> str:
    return str(uuid.uuid4())


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = ("requester", "approver", "stranger")
    who = {n: uid() for n in names} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _owner_sql(owner_url: str, sql: str, **params: Any) -> None:
    async def run() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text(sql), params)

    asyncio.run(run())


def _audit(owner_url: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, body::text AS body FROM evidence.audit_record WHERE tenant_id IS NULL AND event_type LIKE 'screening.export.%' ORDER BY created_at, audit_id"
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


def test_a_package_is_requested_approved_fetched_checked_and_expires(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    case = _seed(rt_url, uid())["hit"]
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        assert (
            call(
                "POST", f"{Q}/cases/{case}/exports", "stranger", json={"purpose": PURPOSE}
            ).status_code
            == 403
        )
        _grant(api, o, who, "requester", "screening.evidence.request")
        _grant(api, o, who, "approver", "screening.evidence.approve")

        assert (
            call(
                "POST", f"{Q}/cases/{case}/exports", "requester", json={"purpose": "audit"}
            ).status_code
            == 422
        )  # a purpose is required
        assert (
            call(
                "POST", f"{Q}/cases/{uuid.uuid4()}/exports", "requester", json={"purpose": PURPOSE}
            ).status_code
            == 404
        )

        # without sensitive material the package is built now, holds the open kinds only, and verifies
        r = call("POST", f"{Q}/cases/{case}/exports", "requester", json={"purpose": PURPOSE})
        assert r.status_code == 201 and r.json()["state"] == "READY", r.text
        open_id = r.json()["export_id"]
        got = call("GET", f"{Q}/exports/{open_id}", "requester").json()
        kinds = {i["kind"] for i in got["bundle"]["included"]}
        assert got["verified"] is True and got["problems"] == []
        assert kinds == {
            "REQUEST_AUTHORISATION",
            "POLICY",
            "COVERAGE",
            "DISPOSITION",
            "SOURCE_SNAPSHOT",
        }
        assert {x["kind"] for x in got["bundle"]["excluded"]} == {"RAW_INPUT", "MATCH_CANDIDATE"}
        assert (
            "Badege" not in str(got)
            and "auditor" not in got["bundle"]["watermark"]
            and "Annual audit" in got["bundle"]["watermark"]
        )
        assert call("GET", f"{Q}/exports/{uuid.uuid4()}", "requester").status_code == 404

        # with sensitive material it waits; the requester cannot approve it; a second person can, and it still leaves only hashes
        s = call(
            "POST",
            f"{Q}/cases/{case}/exports",
            "requester",
            json={"purpose": PURPOSE, "include_sensitive": True},
        )
        assert s.status_code == 201 and s.json()["state"] == "PENDING"
        sid = s.json()["export_id"]
        assert call("GET", f"{Q}/exports/{sid}", "requester").status_code == 409  # not ready
        assert (
            call("POST", f"{Q}/exports/{sid}:approve", "requester").status_code == 403
        )  # no such capability
        ok = call("POST", f"{Q}/exports/{sid}:approve", "approver")
        assert ok.status_code == 200 and ok.json()["state"] == "READY", ok.text
        assert (
            call("POST", f"{Q}/exports/{sid}:approve", "approver").status_code == 409
        )  # already approved
        sens = call("GET", f"{Q}/exports/{sid}", "approver").json()
        by_kind = {i["kind"]: i for i in sens["bundle"]["included"]}
        assert {"RAW_INPUT", "MATCH_CANDIDATE"} <= set(by_kind) and sens["verified"] is True
        assert by_kind["RAW_INPUT"]["data"]["name"].startswith("redacted:") and by_kind[
            "MATCH_CANDIDATE"
        ]["data"]["name"].startswith("redacted:")
        assert "Badege" not in str(sens) and sens["bundle"]["approver"] == who["approver"]

        # an edit after the package was made is found as it leaves
        _owner_sql(
            owner_url,
            "UPDATE verify.screening_evidence_export SET bundle = jsonb_set(bundle, '{included,0,data,state}', '\"EDITED\"') WHERE export_id = CAST(:e AS uuid)",
            e=open_id,
        )
        tampered = call("GET", f"{Q}/exports/{open_id}", "requester").json()
        assert tampered["verified"] is False and any(
            p.startswith("ITEM_ALTERED") for p in tampered["problems"]
        )

        # a package expires, and is then cleared
        _owner_sql(
            owner_url,
            "UPDATE verify.screening_evidence_export SET expires_at = now() - interval '1 minute' WHERE export_id = CAST(:e AS uuid)",
            e=sid,
        )
        gone = call("GET", f"{Q}/exports/{sid}", "approver")
        assert gone.status_code == 410 and gone.json()["error"]["code"] == "EXPORT_EXPIRED"
        assert call("GET", f"{Q}/exports/{sid}", "approver").status_code == 410

    events = _audit(owner_url)
    kinds = {(e["event_type"], e["outcome"]) for e in events}
    for expected in (
        ("screening.export.requested", "SUCCESS"),
        ("screening.export.approved", "SUCCESS"),
        ("screening.export.fetched", "SUCCESS"),
        ("screening.export.refused", "DENIED"),
    ):
        assert expected in kinds, expected
    assert all("Badege" not in e["body"] for e in events)
