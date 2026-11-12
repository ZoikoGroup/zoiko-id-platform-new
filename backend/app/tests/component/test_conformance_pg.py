"""Independent conformance runs on PostgreSQL and through the operator plane (ZID-RCG-003 section 12 and 12.1; CR-083)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from operator_fixture import ISS
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_regulatory_releases_api_pg import _app, _audit, _grant, _people
from zid_platform.modules.verification.domain import conformance as cf


def _body(run_id: str, *, failing: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "run_id": run_id,
        "commit": uuid.uuid4().hex + uuid.uuid4().hex[:8],
        "environment": "ci",
        "matrix_hash": "d" * 64,
        "started_at": (now - timedelta(minutes=9)).isoformat(),
        "finished_at": now.isoformat(),
        "results": [
            {
                "case": c,
                "outcome": "FAIL" if c == failing else "PASS",
                "passed": [f"app/tests/x.py::test_{c.lower()}"],
                "failed": [],
                "not_run": [],
            }
            for c in cf.CASE_IDS
        ],
    }


def test_a_run_is_recorded_reviewed_by_someone_else_and_only_then_meets_the_p0_exit(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert (
            call(
                "POST", "/v1/regulatory/conformance-runs", "author", json=_body(run_id)
            ).status_code
            == 403
        )
        _grant(api, o, who, "author", "regulatory.conformance.record")
        _grant(api, o, who, "recorder", "regulatory.conformance.review")

        r = call("POST", "/v1/regulatory/conformance-runs", "author", json=_body(run_id))
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["p0_exit"]["met"] is False and run["p0_exit"]["reasons"] == [
            "NOT_INDEPENDENTLY_ACCEPTED"
        ]
        assert run["p0_exit"]["counts"]["PASS"] == 48
        digest = run["run_hash"]
        assert (
            call("POST", "/v1/regulatory/conformance-runs", "author", json=_body(run_id)).json()[
                "error"
            ]["code"]
            == "CONFORMANCE_RUN_EXISTS"
        )

        review = {"decision": "ACCEPTED", "signed_hash": digest, "note_ref": "QA-REVIEW-1"}
        assert (
            call(
                "POST", f"/v1/regulatory/conformance-runs/{run_id}:review", "author", json=review
            ).status_code
            == 403
        )  # the recorder holds no review capability
        r = call(
            "POST",
            f"/v1/regulatory/conformance-runs/{run_id}:review",
            "recorder",
            json=review | {"signed_hash": "0" * 64},
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "CONFORMANCE_HASH_MISMATCH"
        r = call(
            "POST", f"/v1/regulatory/conformance-runs/{run_id}:review", "recorder", json=review
        )
        assert r.status_code == 200 and r.json()["p0_exit"]["met"] is True, r.text
        assert (
            call(
                "POST", f"/v1/regulatory/conformance-runs/{run_id}:review", "recorder", json=review
            ).json()["error"]["code"]
            == "CONFORMANCE_ALREADY_REVIEWED"
        )

        failing = f"run-{uuid.uuid4().hex[:10]}"
        r = call(
            "POST", "/v1/regulatory/conformance-runs", "author", json=_body(failing, failing="T45")
        )
        assert r.json()["p0_exit"]["reasons"] == [
            "CASES_NOT_PASSING:1",
            "NOT_INDEPENDENTLY_ACCEPTED",
        ]

        assert run_id in [
            x["run_id"]
            for x in call("GET", "/v1/regulatory/conformance-runs", "recorder").json()["items"]
        ]
        matrix = call("GET", "/v1/regulatory/conformance-matrix", "recorder").json()
        assert len(matrix["cases"]) == 48 and sum(matrix["counts"].values()) == 48

    events = [r["event_type"] for r in _audit(owner_url, run_id)]
    assert {
        "regulatory.conformance.recorded",
        "regulatory.conformance.reviewed",
        "regulatory.conformance.refused",
    } <= set(events)


def test_the_tables_keep_the_rules_when_the_application_is_skipped(
    rt_url: str, owner_url: str
) -> None:
    run_id = f"run-{uuid.uuid4().hex[:10]}"

    async def seed(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO verify.conformance_run (run_id, commit_sha, environment, matrix_hash, started_at, finished_at, recorded_by, results, run_hash) "
                "VALUES (:i, :c, 'ci', :m, now(), now(), 'runner', CAST(:r AS jsonb), :h)"
            ),
            {
                "i": run_id,
                "c": "a" * 40,
                "m": "b" * 64,
                "r": "[" + ",".join(["{}"] * 48) + "]",
                "h": "e" * 64,
            },
        )

    def run(url: str, work: Any) -> None:
        async def go() -> None:
            async with engine(url) as eng, eng.begin() as c:
                await work(c)

        asyncio.run(go())

    run(rt_url, seed)

    def refused(url: str, statement: str, fragment: str) -> None:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"i": run_id})

        with pytest.raises(DBAPIError) as e:
            run(url, work)
        assert fragment in str(e.value), str(e.value)

    refused(
        rt_url,
        "INSERT INTO verify.conformance_review VALUES (:i, 'runner', repeat('e', 64), 'ACCEPTED', 'QA', now())",
        "cannot review it",
    )
    refused(
        rt_url,
        "INSERT INTO verify.conformance_review VALUES (:i, 'qa', repeat('f', 64), 'ACCEPTED', 'QA', now())",
        "stored hash",
    )
    refused(
        rt_url,
        "UPDATE verify.conformance_run SET environment = 'prod' WHERE run_id = :i",
        "append-only",
    )
    refused(owner_url, "DELETE FROM verify.conformance_run WHERE run_id = :i", "append-only")
