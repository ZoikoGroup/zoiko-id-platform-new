"""Disputes, appeals and erasure requests for a Verify API partner (CR-159), against the real database and the real HTTP endpoints.

The company raises a case about one session or one customer reference through its signed-in administrator; an operator takes it into review and a DIFFERENT operator resolves or rejects it. An erasure removes the
customer reference and metadata from the sessions and leaves the decisions. One company never sees or changes another's cases.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from test_verify_api_go_live_pg import Company, code, operator_tx, uid
from zid_platform.modules.verify_api.application import cases_service as svc
from zid_platform.modules.verify_api.domain import model as m


def make_session(co: Company, server: dict[str, str], outcome: str, reference: str) -> str:
    r = co.http.post(
        f"/v1/verify-api/sandbox/fixtures/{outcome}/sessions",
        headers={**server, "Idempotency-Key": uid()},
        json={"customer_reference": reference},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["session"]["session_id"])


def raise_case(co: Company, **body: Any) -> Any:
    return co.http.post("/v1/verify-api/cases", headers=co.h, json=body)


def test_a_company_disputes_and_appeals_a_decision_and_the_decision_itself_never_changes(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        server = co.sandbox_server()
        passed = make_session(co, server, "approved", "cust-pass")
        failed = make_session(co, server, "declined", "cust-fail")
        pending = co.http.post(
            "/v1/verify-api/verification-sessions",
            headers={**server, "Idempotency-Key": uid()},
            json={
                "customer_reference": "cust-wait",
                "country": "GB",
                "purpose_code": "customer_onboarding",
            },
        ).json()["session_id"]

        # an appeal is for a result that was not a pass; a session with no result cannot be disputed at all
        no = raise_case(
            co, kind="APPEAL", session_id=passed, reason="We believe this should be reviewed again."
        )
        assert no.status_code == 409 and code(no) == "CASE_NOT_ALLOWED"
        none_yet = raise_case(
            co, kind="DISPUTE", session_id=pending, reason="There is no result so far at all."
        )
        assert none_yet.status_code == 409 and code(none_yet) == "CASE_NOT_ALLOWED"
        # the shape is checked: a session for a dispute, never a reference; a real session of this company
        assert (
            code(
                raise_case(
                    co,
                    kind="DISPUTE",
                    customer_reference="cust-pass",
                    reason="Wrong target given here.",
                )
            )
            == "VALIDATION_FAILED"
        )
        assert (
            code(
                raise_case(
                    co,
                    kind="DISPUTE",
                    session_id="vfs_" + "0" * 32,
                    reason="No such session at all.",
                )
            )
            == "NOT_FOUND"
        )
        assert (
            code(raise_case(co, kind="DISPUTE", session_id=failed, reason="short"))
            == "VALIDATION_FAILED"
        )

        made = raise_case(
            co,
            kind="APPEAL",
            session_id=failed,
            reason="The applicant was verified in person at a branch.",
        )
        assert made.status_code == 201, made.text
        case = made.json()
        assert (
            case["status"] == "OPEN" and case["kind"] == "APPEAL" and case["session_id"] == failed
        )
        # raising it twice while it waits is refused
        again = raise_case(
            co, kind="APPEAL", session_id=failed, reason="The applicant was verified in person."
        )
        assert again.status_code == 409 and code(again) == "CASE_OPEN"
        # a dispute about the same session is a different kind, so it can stand beside it
        assert (
            raise_case(
                co, kind="DISPUTE", session_id=passed, reason="We want the check method confirmed."
            ).status_code
            == 201
        )

        listed = co.http.get("/v1/verify-api/cases", headers=co.h).json()["items"]
        assert {i["case_id"] for i in listed} >= {case["case_id"]}

        # withdrawing is the company's, once; a withdrawn case is closed for good
        withdrawn = co.http.post(f"/v1/verify-api/cases/{case['case_id']}:withdraw", headers=co.h)
        assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "WITHDRAWN"
        assert (
            code(co.http.post(f"/v1/verify-api/cases/{case['case_id']}:withdraw", headers=co.h))
            == "CASE_STATE_INVALID"
        )

        # the decision was never touched by any of this
        r = co.http.get(f"/v1/verify-api/sessions/{failed}", headers=co.h).json()
        assert [d["outcome"] for d in r["decisions"]] == ["not_verified"]
    finally:
        co.close()


def test_an_operator_reviews_a_different_operator_resolves_and_an_erasure_removes_the_reference_but_keeps_the_decisions(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        server = co.sandbox_server()
        s1 = make_session(co, server, "approved", "customer-to-erase")
        s2 = make_session(co, server, "declined", "customer-to-erase")
        keep = make_session(co, server, "approved", "another-customer")

        # a reference nobody used is refused; the real one is accepted and echoed back without the reference
        assert (
            code(
                raise_case(
                    co,
                    kind="ERASURE",
                    customer_reference="nobody",
                    reason="Our customer asked us to erase.",
                )
            )
            == "NOT_FOUND"
        )
        made = raise_case(
            co,
            kind="ERASURE",
            customer_reference="customer-to-erase",
            reason="Our customer asked us to erase their data.",
        )
        assert made.status_code == 201, made.text
        case = made.json()
        assert "customer-to-erase" not in made.text and case["session_id"] is None
        assert (
            code(
                raise_case(
                    co,
                    kind="ERASURE",
                    customer_reference="customer-to-erase",
                    reason="Again, the same request.",
                )
            )
            == "CASE_OPEN"
        )

        # what is stored is a hash, never the reference
        async def stored() -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return (
                    await c.execute(
                        text(
                            "SELECT reference_hash, reason FROM verify.api_case WHERE case_id = :i"
                        ),
                        {"i": uuid.UUID(case["case_id"])},
                    )
                ).one()

        ref_hash, _reason = asyncio.run(stored())
        assert len(bytes(ref_hash)) == 32 and b"customer-to-erase" not in bytes(ref_hash)

        op1, op2 = uid(), uid()

        async def operate() -> None:
            async with operator_tx(rt_url) as c:
                mine = next(
                    i for i in await svc.operator_open(c) if i["case_id"] == case["case_id"]
                )
                assert (
                    mine["tenant_id"] == co.o.tenant
                    and mine["status"] == "OPEN"
                    and "customer-to-erase" not in str(mine)
                )
            async with operator_tx(rt_url) as c:  # nobody resolves what nobody reviewed
                with pytest.raises(m.PartnerError) as e:
                    await svc.resolve(c, case["case_id"], op1, "Erased.")
                assert e.value.code == "CASE_STATE_INVALID"
            async with operator_tx(rt_url) as c:
                assert (await svc.start_review(c, case["case_id"], op1))["status"] == "IN_REVIEW"
            async with operator_tx(rt_url) as c:  # the reviewer cannot also resolve
                with pytest.raises(m.PartnerError) as e:
                    await svc.resolve(c, case["case_id"], op1, "Erased.")
                assert e.value.code == "CASE_SELF_RESOLVE"
            async with operator_tx(rt_url) as c:  # a resolution needs a reason the company can read
                with pytest.raises(m.PartnerError) as e:
                    await svc.resolve(c, case["case_id"], op2, "  ")
                assert e.value.code == "NOTE_REQUIRED"
            async with operator_tx(rt_url) as c:
                done = await svc.resolve(
                    c, case["case_id"], op2, "Erased at the company's request."
                )
                assert done["status"] == "RESOLVED" and done["sessions_erased"] == 2

        asyncio.run(operate())

        # the reference is gone from exactly those sessions; the other customer and every decision are untouched
        async def rows() -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                sessions = (
                    await c.execute(
                        text(
                            "SELECT session_id, customer_reference, metadata, return_origin FROM verify.api_session WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(co.o.tenant)},
                    )
                ).all()
                decisions = (
                    await c.execute(
                        text("SELECT count(*) FROM verify.api_decision WHERE tenant_id = :t"),
                        {"t": uuid.UUID(co.o.tenant)},
                    )
                ).scalar_one()
                return sessions, decisions

        sessions, decisions = asyncio.run(rows())
        by_id = {r[0]: r for r in sessions}
        assert (
            by_id[s1][1].startswith("erased-")
            and by_id[s2][1].startswith("erased-")
            and by_id[s1][1] != by_id[s2][1]
        )
        assert by_id[s1][2] == {} and by_id[s1][3] is None
        assert by_id[keep][1] == "another-customer"
        assert decisions == 3
        assert (
            co.http.get(f"/v1/verify-api/sessions/{s2}", headers=co.h).json()["decisions"][0][
                "outcome"
            ]
            == "not_verified"
        )
        # the company reads the outcome and how many sessions changed
        seen = next(
            i
            for i in co.http.get("/v1/verify-api/cases", headers=co.h).json()["items"]
            if i["case_id"] == case["case_id"]
        )
        assert (
            seen["status"] == "RESOLVED"
            and seen["sessions_erased"] == 2
            and seen["note"] == "Erased at the company's request."
        )
        # a resolved case is closed
        assert (
            code(co.http.post(f"/v1/verify-api/cases/{case['case_id']}:withdraw", headers=co.h))
            == "CASE_STATE_INVALID"
        )
    finally:
        co.close()


def test_a_rejected_case_tells_the_company_why_and_another_company_sees_none_of_it(
    rt_url: str, owner_url: str
) -> None:
    co, other = Company(rt_url), Company(rt_url)
    try:
        server = co.sandbox_server()
        failed = make_session(co, server, "declined", "cust-1")
        case = raise_case(
            co, kind="DISPUTE", session_id=failed, reason="We believe the document was genuine."
        ).json()

        async def operate() -> None:
            async with operator_tx(rt_url) as c:
                with pytest.raises(m.PartnerError) as e:
                    await svc.reject(c, case["case_id"], uid(), "")
                assert e.value.code == "NOTE_REQUIRED"
                out = await svc.reject(
                    c,
                    case["case_id"],
                    uid(),
                    "The decision stands: the document failed an authenticity check.",
                )
                assert out["status"] == "REJECTED"

        asyncio.run(operate())
        seen = co.http.get("/v1/verify-api/cases", headers=co.h).json()["items"][0]
        assert seen["status"] == "REJECTED" and "authenticity" in seen["note"]
        # another company cannot see it, raise one about it, or withdraw it
        assert other.http.get("/v1/verify-api/cases", headers=other.h).json()["items"] == []
        assert (
            code(
                raise_case(
                    other,
                    kind="DISPUTE",
                    session_id=failed,
                    reason="Trying a session that is not ours.",
                )
            )
            == "NOT_FOUND"
        )
        assert (
            code(
                other.http.post(f"/v1/verify-api/cases/{case['case_id']}:withdraw", headers=other.h)
            )
            == "NOT_FOUND"
        )
        # and the erasure of a reference is matched per company: the same reference text at another company matches nothing
        assert (
            code(
                raise_case(
                    other,
                    kind="ERASURE",
                    customer_reference="cust-1",
                    reason="Trying a reference that is not ours.",
                )
            )
            == "NOT_FOUND"
        )
    finally:
        co.close()
        other.close()


def test_the_case_routes_need_a_signed_in_administrator(rt_url: str, owner_url: str) -> None:
    co = Company(rt_url)
    try:
        assert co.http.get("/v1/verify-api/cases").status_code == 401
        assert (
            co.http.post(
                "/v1/verify-api/cases",
                json={"kind": "DISPUTE", "reason": "No sign in at all here."},
            ).status_code
            == 401
        )
        assert co.http.get("/v1/verify-api/cases", headers=co.h).status_code == 200
    finally:
        co.close()
