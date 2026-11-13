"""Issuer and accreditation history in PostgreSQL and through the operator API (ZID-RCG-003 RCG-G04; CR-075): an append-only ledger that answers for
any past moment, the rules the table keeps when the domain is skipped, and issuer at award time end to end with real operator sessions."""

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
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.issuer_history import (
    IssuerHistoryError,
    Kind,
    Statement,
)
from zid_platform.modules.credential.infrastructure import issuer_history_store as store

NOW = datetime.now(UTC)


def _rid() -> str:
    return f"uni-{uuid.uuid4().hex[:10]}"


def _record(issuer_id: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "issuer_id": issuer_id,
        "kind": "UNIVERSITY",
        "authority": "OFFICIAL",
        "countries": ["US"],
        "claim_types": ["ACADEMIC_AWARD"],
        "names": [
            {"name": "Old College", "until": "2015-06-30"},
            {"name": "New University", "from": "2015-07-01"},
        ],
        "accreditations": [
            {
                "body": "acc-1",
                "program": "BSc Computing",
                "from": "2000-01-01",
                "until": "2012-12-31",
            }
        ],
    }
    base.update(over)
    return base


def _stmt(
    issuer_id: str,
    seq: int,
    *,
    ago: timedelta,
    rec: dict[str, Any] | None = None,
    kind: Kind = Kind.UPDATE,
    reason: str | None = None,
) -> Statement:
    return Statement(
        statement_id=f"s-{issuer_id}-{seq}",
        issuer_id=issuer_id,
        seq=seq,
        kind=kind,
        reason=reason,
        record=rec or _record(issuer_id),
        source_url="https://accreditor.example.test/entry/1",
        source_hash="b" * 64,
        evidence_locator="vault:issuers/x",
        recorded_by="reg-ops",
        recorded_at=NOW - ago,
    )


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, work: Any, *fragments: str) -> None:
    with pytest.raises(DBAPIError) as e:
        _run(url, work)
    assert any(f in str(e.value) for f in fragments), str(e.value)


def test_statements_build_a_ledger_that_answers_for_any_moment(rt_url: str) -> None:
    iid = _rid()
    # a later accreditation is a forward fact: it adds to what was said, it does not change it
    continuing = _record(
        iid,
        accreditations=_record(iid)["accreditations"] + [{"body": "acc-2", "from": "2019-01-01"}],
    )

    async def work(c: Any) -> tuple[Any, ...]:
        await store.record(c, _stmt(iid, 1, ago=timedelta(days=60)), now=NOW)
        await store.record(c, _stmt(iid, 2, ago=timedelta(days=5), rec=continuing), now=NOW)
        base = tg.parse({"issuers": []})
        return (
            await store.history(c, iid),
            await store.registry_at(c, NOW - timedelta(days=30), base),
            await store.registry_at(c, NOW, base),
            await store.registry_at(c, NOW - timedelta(days=90), base),
        )

    hist, then, now_, before = _run(rt_url, work)
    assert [s.seq for s in hist] == [1, 2]
    assert (
        tg.accreditation_at_award(then.issuers[iid], "BSc Computing", datetime(2020, 1, 1).date())[
            0
        ]
        is tg.Accredited.NOT_ACCREDITED
    )
    assert (
        tg.accreditation_at_award(now_.issuers[iid], "BSc Computing", datetime(2020, 1, 1).date())[
            0
        ]
        is tg.Accredited.ACCREDITED
    )
    assert iid not in before.issuers


def test_the_store_refuses_what_changes_the_past_without_a_correction(rt_url: str) -> None:
    iid = _rid()
    shortened = _record(
        iid,
        accreditations=[
            {
                "body": "acc-1",
                "program": "BSc Computing",
                "from": "2000-01-01",
                "until": "2009-12-31",
            }
        ],
    )
    _run(rt_url, lambda c: store.record(c, _stmt(iid, 1, ago=timedelta(days=30)), now=NOW))
    with pytest.raises(IssuerHistoryError) as e:
        _run(
            rt_url,
            lambda c: store.record(c, _stmt(iid, 2, ago=timedelta(days=1), rec=shortened), now=NOW),
        )
    assert e.value.code == "ISSUER_STATEMENT_RETROACTIVE"
    fixed = _stmt(
        iid,
        2,
        ago=timedelta(days=1),
        rec=shortened,
        kind=Kind.CORRECTION,
        reason="the accreditor re-dated the lapse",
    )
    _, changes = _run(rt_url, lambda c: store.record(c, fixed, now=NOW))
    assert changes == ["accreditation_end_changed:acc-1/BSc Computing"]
    assert len(_run(rt_url, lambda c: store.history(c, iid))) == 2  # both statements stay visible


def _raw_insert(
    iid: str,
    seq: int,
    *,
    at: datetime,
    source_url: str = "https://x.example.test/a",
    record_id: str | None = None,
) -> Any:
    async def work(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO cred.issuer_statement (issuer_id, seq, statement_id, kind, reason, record, source_url, source_hash, evidence_locator, "
                "recorded_by, recorded_at) VALUES (:i, :q, :sid, 'UPDATE', NULL, CAST(:rec AS jsonb), :su, :sh, 'vault:x/y', 'ops', :at)"
            ),
            {
                "i": iid,
                "q": seq,
                "sid": f"raw-{uuid.uuid4().hex[:10]}",
                "rec": '{"issuer_id": "%s"}' % (record_id or iid),
                "su": source_url,
                "sh": "c" * 64,
                "at": at,
            },
        )

    return work


def test_the_table_refuses_what_the_domain_would_refuse_even_when_the_domain_is_skipped(
    rt_url: str, owner_url: str
) -> None:
    iid = _rid()
    _run(rt_url, lambda c: store.record(c, _stmt(iid, 1, ago=timedelta(days=2)), now=NOW))
    _refused(rt_url, _raw_insert(iid, 3, at=NOW - timedelta(days=1)), "rise by one")
    _refused(rt_url, _raw_insert(iid, 2, at=NOW - timedelta(days=5)), "newer than the one before")
    _refused(rt_url, _raw_insert(iid, 2, at=NOW + timedelta(hours=2)), "from the future")
    _refused(
        rt_url,
        _raw_insert(iid, 2, at=NOW - timedelta(hours=1), source_url="http://x.example.test/a"),
        "source_url",
        "violates",
    )
    _refused(
        rt_url,
        _raw_insert(iid, 2, at=NOW - timedelta(hours=1), record_id="someone-else"),
        "issuer_statement",
        "violates",
    )

    def sql(statement: str) -> Any:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"i": iid})

        return work

    _refused(
        rt_url,
        sql("UPDATE cred.issuer_statement SET recorded_by = 'x' WHERE issuer_id = :i"),
        "append-only",
    )
    _refused(
        owner_url, sql("DELETE FROM cred.issuer_statement WHERE issuer_id = :i"), "append-only"
    )


def test_issuer_at_award_time_through_the_operator_plane_with_audit(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    iid = _rid()
    url = "/v1/regulatory/issuers"

    def body(**over: Any) -> dict[str, Any]:
        b: dict[str, Any] = {
            "issuer_id": iid,
            "issuer_kind": "UNIVERSITY",
            "authority": "OFFICIAL",
            "countries": ["US"],
            "claim_types": ["ACADEMIC_AWARD"],
            "names": [
                {"name": "Old College", "until": "2015-06-30"},
                {"name": "New University", "from": "2015-07-01"},
            ],
            "accreditations": [
                {
                    "body": "acc-1",
                    "program": "BSc Computing",
                    "from": "2000-01-01",
                    "until": "2012-12-31",
                }
            ],
            "source_url": "https://accreditor.example.test/entry/1",
            "source_hash": uuid.uuid4().hex + uuid.uuid4().hex,
            "evidence_locator": "vault:issuers/x",
        }
        b.update(over)
        return b

    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert api.post(url, json=body()).status_code == 401
        r = call("POST", url, "vendor", json=body())
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"

        _grant(api, o, who, "vendor", "regulatory.issuer.record")
        r = call("POST", url, "vendor", json=body())
        assert r.status_code == 201, r.text
        assert (r.json()["seq"], r.json()["statement"], r.json()["retroactive_changes"]) == (
            1,
            "UPDATE",
            [],
        )
        assert call("POST", url, "author", json=body()).status_code == 403

        def award(awarded_on: str, **q: str) -> dict[str, Any]:
            r = call(
                "GET",
                f"{url}/{iid}/award-check",
                "vendor",
                params={"program": "BSc Computing", "awarded_on": awarded_on, **q},
            )
            assert r.status_code == 200, r.text
            out: dict[str, Any] = r.json()
            return out

        assert (award("2010-06-01")["outcome"], award("2010-06-01")["name_at_award"]) == (
            "PASS",
            "Old College",
        )
        late = award("2014-06-01")
        assert (late["outcome"], late["reasons"]) == ("REVIEW", ["NOT_ACCREDITED_AT_AWARD_DATE"])
        before = award("2010-06-01", known_at=(NOW - timedelta(days=1)).isoformat())
        assert before["reasons"] == ["UNKNOWN_HISTORICAL_ISSUER"]  # nothing was recorded yet

        # shortening a past accreditation is refused as an update and accepted only as a correction with a reason
        shorter = body(
            accreditations=[
                {
                    "body": "acc-1",
                    "program": "BSc Computing",
                    "from": "2000-01-01",
                    "until": "2009-12-31",
                }
            ]
        )
        r = call("POST", url, "vendor", json=shorter)
        assert r.status_code == 422 and r.json()["error"]["code"] == "ISSUER_STATEMENT_RETROACTIVE"
        r = call("POST", url, "vendor", json=shorter | {"statement": "CORRECTION"})
        assert (
            r.status_code == 422
            and r.json()["error"]["code"] == "ISSUER_CORRECTION_REASON_REQUIRED"
        )
        r = call(
            "POST",
            url,
            "vendor",
            json=shorter
            | {"statement": "CORRECTION", "reason": "the accreditor re-dated the lapse"},
        )
        assert r.status_code == 201 and r.json()["retroactive_changes"] == [
            "accreditation_end_changed:acc-1/BSc Computing"
        ]
        assert award("2010-06-01")["outcome"] == "REVIEW"  # now known to have lapsed before 2010

        stmts = call("GET", f"{url}/{iid}/statements", "vendor").json()["items"]
        assert [(s["seq"], s["statement"]) for s in stmts] == [(1, "UPDATE"), (2, "CORRECTION")]
        assert (
            call("GET", f"{url}/nobody/statements", "vendor").json()["error"]["code"]
            == "ISSUER_NOT_FOUND"
        )
        assert iid in call("GET", url, "vendor").json()["items"]

        assert (
            call(
                "POST", url, "vendor", json=body(source_url="http://insecure.example.test/x")
            ).status_code
            == 422
        )
        assert call("POST", url, "vendor", json=body(source_hash="nothex")).status_code == 422
        assert call("POST", url, "vendor", json=body(issuer_id="Bad_ID")).status_code == 422

    records = _audit(owner_url, f"{iid}", f"{iid}:s%")
    recorded = [r for r in records if r["event_type"] == "regulatory.issuer.recorded"]
    assert [r["body"]["details"]["statement"] for r in recorded] == ["UPDATE", "CORRECTION"]
    assert recorded[1]["body"]["details"]["retroactive_kinds"] == ["accreditation_end_changed"]
    refused = {r["reason_code"] for r in records if r["event_type"] == "regulatory.issuer.refused"}
    assert refused >= {
        "OPERATOR_CAPABILITY_REQUIRED",
        "ISSUER_STATEMENT_RETROACTIVE",
        "ISSUER_CORRECTION_REASON_REQUIRED",
    }
    assert "BSc Computing" not in " ".join(
        str(r["body"]) for r in records
    )  # ids, codes and the source hash only
