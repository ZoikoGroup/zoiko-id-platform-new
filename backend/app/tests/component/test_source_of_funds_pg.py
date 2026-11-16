"""DTV-28 source of funds and wealth over HTTP, against PostgreSQL with row-level security (CR-096). Synthetic people and documents only.

The shipped configuration lists the line as DESIGN, so every route that starts or changes a case refuses. The flow tests switch it to STAGING for one
app instance. Nobody is cleared by the system: each outcome below is a person's decision, and the rules about who may decide are checked from outside.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_v2_facade_pg import AUD, ISS, uid
from test_v2_uploads_pg import announce, put
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.domain.product_lines import parse as parse_lines
from zid_platform.modules.verification.infrastructure import sof_store
from zid_platform.settings import Settings

BASE = "/v2/source-of-funds"
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
UPLOAD_ACTIONS = ["evidence.upload.manage", "evidence.read"]
UPLOAD_RESOURCES = ["evidence-upload:*", "document:*"]


def lines(state: str, **extra: object) -> object:
    entry = {
        "dtv": "DTV-28",
        "state": state,
        "countries": ["GB"],
        "legal_basis_ref": "TEST-LEGAL-1",
        **extra,
    }
    return parse_lines({"version": "test", "lines": {"source_of_funds": entry}})


@contextmanager
def world(rt_url: str, line: object | None = None) -> Iterator[tuple[TestClient, dict, str, dict]]:
    """One company, four people: the submitter (may also review, to test that nobody decides their own case), two reviewers, the person concerned."""
    tenant = uid()
    people = {n: uid() for n in ("submitter", "reviewer", "reviewer2", "subject", "reader")}
    ring = KeyRing(LocalSigner("issuer-sof"))
    grants = {
        "submitter": (["verification.sof.submit", "verification.sof.read", "verification.sof.review", *UPLOAD_ACTIONS], ["source-of-funds:*", *UPLOAD_RESOURCES]),
        "reviewer": (["verification.sof.review", "verification.sof.read"], ["source-of-funds:*"]),
        "reviewer2": (["verification.sof.review", "verification.sof.read"], ["source-of-funds:*"]),
        "subject": (["verification.sof.submit"], ["source-of-funds:*"]),
        "reader": (["verification.sof.review"], ["source-of-funds:*"]),  # may see the queue, not the answers
    }  # fmt: skip

    async def seed() -> dict[str, str]:
        now = datetime.now(UTC)
        sessions: dict[str, str] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i, (name, pid) in enumerate(people.items()):
                await create_principal(
                    c, Principal(pid, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
                actions, resources = grants[name]
                await make_admin(c, tenant, [pid], actions, resources, release=(i == 0))
                principal, _ = await load_principal(c, pid)
                sid = uid()
                await create_session(
                    c,
                    Session(
                        sid, tenant, pid, now - timedelta(seconds=30), "native:webauthn", 2,
                        principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                    ),
                )  # fmt: skip
                sessions[name] = sid
        return sessions

    sessions = asyncio.run(seed())
    heads = {
        name: {
            "Authorization": "Bearer "
            + issue_access_token(
                ring, issuer=ISS, audience=AUD, subject=people[name], tenant_id=tenant, client_id="admin-console",
                now=datetime.now(UTC), session_id=sessions[name], scope=frozenset(),
            )
        }
        for name in people
    }  # fmt: skip
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", upload_retention_days=30),
        keyring=ring,
    )  # fmt: skip
    if line is not None:
        app.state.product_lines = line
    with TestClient(app, base_url=ISS) as http:
        yield http, heads, tenant, people


def upload_id(http: TestClient, head: dict) -> str:
    a = announce(http, head, PDF, "application/pdf").json()
    assert put(http, head, a, PDF).status_code == 201
    return a["evidence_ref"].removeprefix("ev_")


GOOD = {
    "SOF-01": "EMPLOYMENT",
    "SOF-02": {"value": "40000.00", "currency": "GBP"},
    "SOF-03": "2019-06-01",
    "SOF-04": False,
    "SOF-05": "Salary from my employer over several years.",
}


def test_the_shipped_line_is_design_so_every_route_that_starts_or_changes_a_case_refuses(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, h, _t, p):
        assert http.post(f"{BASE}/cases", json={}).status_code == 401
        out = http.post(
            f"{BASE}/cases",
            headers=h["submitter"],
            json={"subject_principal_id": p["subject"], "country": "GB"},
        )
        assert out.status_code == 409 and out.json()["code"] == "service_unavailable", out.text
        assert "design" in out.text.lower()
        q = http.get(f"{BASE}/questionnaire", headers=h["submitter"], params={"country": "GB"})
        assert q.status_code == 409


def test_a_case_goes_from_incomplete_to_a_person_deciding_and_an_appeal_is_decided_by_a_third_person(
    rt_url: str,
) -> None:
    with world(rt_url, lines("STAGING")) as (http, h, tenant, p):
        sub, rev, rev2, subject = h["submitter"], h["reviewer"], h["reviewer2"], h["subject"]
        q = http.get(f"{BASE}/questionnaire", headers=sub, params={"country": "GB"}).json()
        assert q["status"] == "SAMPLE" and [x["question_id"] for x in q["questions"]][:2] == [
            "SOF-01",
            "SOF-02",
        ]
        assert (
            http.get(f"{BASE}/questionnaire", headers=sub, params={"country": "US"}).status_code
            == 409
        )  # the line lists GB only

        made = http.post(
            f"{BASE}/cases",
            headers=sub,
            json={"subject_principal_id": p["subject"], "country": "GB"},
        )
        assert made.status_code == 201, made.text
        case = made.json()
        cid = case["case_id"]
        assert (
            case["state"] == "DRAFT" and case["questionnaire"]["content_hash"] == q["content_hash"]
        )

        # answers without the evidence the answer asks for: INCOMPLETE, and it says what is missing
        thin = http.post(
            f"{BASE}/cases/{cid}:submit", headers=sub, json={"answers": GOOD, "evidence": []}
        ).json()
        assert thin["state"] == "INCOMPLETE"
        assert (
            "EVIDENCE_MISSING:SOF-01:PAYSLIP" in thin["problems"]
            and "EVIDENCE_MISSING:SOF-01:BANK_STATEMENT" in thin["problems"]
        )

        # an evidence reference to an upload that does not exist does not count
        ghost = [
            {"question_id": "SOF-01", "type": t, "upload_id": str(uuid.uuid4())}
            for t in ("PAYSLIP", "BANK_STATEMENT")
        ]
        bad = http.post(
            f"{BASE}/cases/{cid}:submit", headers=sub, json={"answers": GOOD, "evidence": ghost}
        ).json()
        assert (
            bad["state"] == "INCOMPLETE" and "EVIDENCE_UPLOAD_NOT_USABLE:SOF-01" in bad["problems"]
        )

        refs = [
            {"question_id": "SOF-01", "type": t, "upload_id": upload_id(http, sub)}
            for t in ("PAYSLIP", "BANK_STATEMENT")
        ]
        ready = http.post(
            f"{BASE}/cases/{cid}:submit", headers=sub, json={"answers": GOOD, "evidence": refs}
        ).json()
        assert ready["state"] == "NEEDS_REVIEW" and ready["problems"] == []
        assert "cleared" not in ready["state"].lower() and "never clears anyone" in ready["notice"]

        # the system never moves it further by itself, and a case waiting for review accepts no more answers
        assert (
            http.post(
                f"{BASE}/cases/{cid}:submit", headers=sub, json={"answers": GOOD, "evidence": refs}
            ).status_code
            == 409
        )

        # the queue shows no answers; reading them is its own permission
        queue = http.get(
            f"{BASE}/cases", headers=h["reader"], params={"state": "NEEDS_REVIEW"}
        ).json()
        assert [i["case_id"] for i in queue["items"]] == [cid] and "answers" not in queue["items"][
            0
        ]
        assert http.get(f"{BASE}/cases/{cid}", headers=h["reader"]).status_code == 403
        assert (
            http.get(f"{BASE}/cases/{cid}", headers=rev).json()["answers"]["SOF-01"] == "EMPLOYMENT"
        )

        # the submitter holds the review permission here, and still cannot decide their own case
        own = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=sub,
            json={"decision": "CLEAR", "reason_codes": ["FUNDS_EXPLAINED_AND_EVIDENCED"]},
        )
        assert own.status_code == 403 and own.json()["code"] == "reviewer_not_independent"
        none = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=rev,
            json={"decision": "CLEAR", "reason_codes": ["MADE_UP"]},
        )
        assert none.status_code == 422 and none.json()["code"] == "reason_code_required"

        adverse = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=rev,
            json={"decision": "NOT_CLEAR", "reason_codes": ["EVIDENCE_INSUFFICIENT"]},
        ).json()
        assert (
            adverse["state"] == "NOT_CLEARED_PENDING_APPEAL" and adverse["valid_until"] is None
        )  # never final first time

        # only the person concerned may appeal
        assert http.post(f"{BASE}/cases/{cid}:appeal", headers=rev).status_code == 403
        appealed = http.post(f"{BASE}/cases/{cid}:appeal", headers=subject).json()
        assert appealed["state"] == "APPEALED"

        # the first reviewer cannot decide the appeal; a different one can
        same = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=rev,
            json={"decision": "NOT_CLEAR", "reason_codes": ["APPEAL_NO_NEW_INFORMATION"]},
        )
        assert (
            same.status_code == 403 and same.json()["code"] == "appeal_needs_a_different_reviewer"
        )
        won = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=rev2,
            json={"decision": "CLEAR", "reason_codes": ["APPEAL_NEW_EVIDENCE"]},
        ).json()
        assert won["state"] == "CLEARED" and won["valid_until"]
        assert [d["decision"] for d in won["decisions"]] == ["NOT_CLEAR", "APPEAL_FILED", "CLEAR"]
        assert [d["to_state"] for d in won["decisions"]] == [
            "NOT_CLEARED_PENDING_APPEAL",
            "APPEALED",
            "CLEARED",
        ]
        assert datetime.fromisoformat(won["valid_until"]) > datetime.now(UTC) + timedelta(days=360)

        # a decided case cannot be decided again, and no answer, amount or reference is in the audit trail
        again = http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=rev2,
            json={"decision": "NOT_CLEAR", "reason_codes": ["EVIDENCE_INCONSISTENT"]},
        )
        assert again.status_code == 409 and again.json()["code"] == "case_not_awaiting_decision"

        async def audit() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT body::text FROM evidence.audit_record WHERE event_type LIKE 'verification.sof.%'"
                        )
                    )
                ).all()
                return " ".join(r[0] for r in rows)

        trail = asyncio.run(audit())
        assert (
            "verification.sof.decided" in trail and "verification.sof.appealed" in trail
        )  # the trail exists
        assert "40000" not in trail and "EMPLOYMENT" not in trail and "Salary" not in trail


def test_a_sample_questionnaire_is_refused_when_the_line_is_live(rt_url: str) -> None:
    live = lines("LIVE", approved_by="Compliance Officer")
    with world(rt_url, live) as (http, h, _t, p):
        out = http.post(
            f"{BASE}/cases",
            headers=h["submitter"],
            json={"subject_principal_id": p["subject"], "country": "GB"},
        )
        assert out.status_code == 409 and out.json()["code"] == "questionnaire_not_approved", (
            out.text
        )


def test_a_case_started_on_one_questionnaire_version_is_not_answered_against_another(
    rt_url: str,
) -> None:
    from zid_platform.modules.verification.domain import source_of_funds as sof
    from zid_platform.modules.verification.infrastructure.questionnaire_registry import (
        current_questionnaire,
    )

    with world(rt_url, lines("STAGING")) as (http, h, _t, p):
        cid = http.post(
            f"{BASE}/cases",
            headers=h["submitter"],
            json={"subject_principal_id": p["subject"], "country": "GB"},
        ).json()["case_id"]
        changed = current_questionnaire()
        http.app.state.sof_questionnaire = sof.Questionnaire(
            changed.code,
            "0.2.0-SAMPLE",
            changed.status,
            changed.countries,
            changed.questions,
            None,
            None,
            "f" * 64,
        )
        out = http.post(
            f"{BASE}/cases/{cid}:submit",
            headers=h["submitter"],
            json={"answers": GOOD, "evidence": []},
        )
        assert out.status_code == 409 and out.json()["code"] == "questionnaire_changed"


def test_the_database_keeps_decisions_forever_isolates_companies_and_governed_erasure_empties_the_answers(
    rt_url: str,
) -> None:
    with world(rt_url, lines("STAGING")) as (http, h, tenant, p):
        sub = h["submitter"]
        cid = http.post(
            f"{BASE}/cases",
            headers=sub,
            json={"subject_principal_id": p["subject"], "country": "GB"},
        ).json()["case_id"]
        refs = [
            {"question_id": "SOF-01", "type": t, "upload_id": upload_id(http, sub)}
            for t in ("PAYSLIP", "BANK_STATEMENT")
        ]
        assert (
            http.post(
                f"{BASE}/cases/{cid}:submit", headers=sub, json={"answers": GOOD, "evidence": refs}
            ).json()["state"]
            == "NEEDS_REVIEW"
        )
        http.post(
            f"{BASE}/cases/{cid}:decide",
            headers=h["reviewer"],
            json={"decision": "ASK_FOR_MORE", "reason_codes": ["EVIDENCE_INSUFFICIENT"]},
        )

        async def tamper() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(text("UPDATE verify.sof_decision SET decided_by = decided_by"))

        try:
            asyncio.run(tamper())
            raise AssertionError("a decision was changed")
        except DBAPIError as e:
            assert "append-only" in str(e)

        # another company sees nothing of this case, not even that it exists
        other = uid()

        async def foreign() -> int:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
                return int(
                    (await c.execute(text("SELECT count(*) FROM verify.sof_case"))).scalar_one()
                )

        assert asyncio.run(foreign()) == 0

        # governed erasure: the answers and evidence references go; the state and the decision record stay; it can no longer be decided
        async def erase() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await sof_store.erase_case(c, cid, datetime.now(UTC))

        asyncio.run(erase())
        after = http.get(f"{BASE}/cases/{cid}", headers=h["reviewer"]).json()
        assert (
            after["erased"] is True
            and after["answers"] == {}
            and after["evidence"] == []
            and after["state"] == "CANCELED"
        )
        assert len(after["decisions"]) == 1
        queue = http.get(f"{BASE}/cases", headers=h["reader"]).json()
        assert cid not in [i["case_id"] for i in queue["items"]]
