"""Verification workflows over HTTP against PostgreSQL with row-level security (DTV-30, CR-099): drafts, the safety analysis at approval, maker-checker,
the frozen version, and a run that only moves when the event it awaits arrives. Synthetic people and definitions only."""

from __future__ import annotations

import asyncio
import copy
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_v2_facade_pg import AUD, ISS, uid
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
from zid_platform.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_workflow import GOOD, REGULATED

W = "/v2/workflows"
R = "/v2/workflow-runs"
ALL = [
    "verification.workflow.read",
    "verification.workflow.manage",
    "verification.workflow.approve",
    "verification.workflow.run",
]
RES = ["workflow:*", "workflow-run:*"]


@contextmanager
def world(rt_url: str, line: object | None = None) -> Iterator[tuple[TestClient, dict, str, dict]]:
    tenant = uid()
    people = {n: uid() for n in ("author", "approver", "runner", "reviewer", "subject", "reader")}
    ring = KeyRing(LocalSigner("issuer-wf"))
    grants = {
        "author": (["verification.workflow.read", "verification.workflow.manage", "verification.workflow.approve"], RES),  # may even try to approve their own
        "approver": (["verification.workflow.read", "verification.workflow.approve"], RES),
        "runner": (["verification.workflow.read", "verification.workflow.run"], RES),
        "reviewer": (["verification.workflow.read", "verification.workflow.run"], RES),
        "subject": (["verification.workflow.run"], RES),  # the applicant: holds the run permission, and still cannot review themselves
        "reader": (["verification.workflow.read"], RES),
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
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )
    if line is not None:
        app.state.product_lines = line
    with TestClient(app, base_url=ISS) as http:
        yield http, heads, tenant, people


def doc(**over):
    d = copy.deepcopy(GOOD)
    d.update(over)
    return d


def test_a_draft_is_saved_analyzed_approved_by_a_different_person_and_then_frozen(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, h, tenant, p):
        assert http.post(W, json={"definition": doc()}).status_code == 401

        # the editor's Check button stores nothing
        checked = http.post(f"{W}:check", headers=h["author"], json={"definition": doc()}).json()
        assert checked["valid"] and checked["publishable"] and checked["problems"] == []
        broken = http.post(
            f"{W}:check",
            headers=h["author"],
            json={"definition": {**doc(), "steps": {"identity": {"type": "SCRIPT"}}}},
        ).json()
        assert broken["valid"] is False and broken["parse_error"] == "STEP_INVALID"
        assert http.get(W, headers=h["reader"]).json()["items"] == []

        made = http.post(W, headers=h["author"], json={"definition": doc()})
        assert made.status_code == 201, made.text
        v = made.json()
        assert v["status"] == "DRAFT" and v["problems"] == [] and v["author"] == p["author"]
        url = f"{W}/onboarding/versions/1.0.0"

        # a draft that does not parse is refused whole
        assert (
            http.post(
                W, headers=h["author"], json={"definition": {**doc(), "id": "Bad Id"}}
            ).status_code
            == 409
        )

        # nobody approves their own version
        own = http.post(f"{url}:approve", headers=h["author"])
        assert own.status_code == 403 and own.json()["code"] == "approver_is_author"
        assert (
            http.post(f"{url}:approve", headers=h["reader"]).status_code == 403
        )  # no approve permission

        done = http.post(f"{url}:approve", headers=h["approver"]).json()
        assert done["status"] == "APPROVED" and done["approved_by"] == p["approver"]
        assert (
            http.post(f"{url}:approve", headers=h["approver"]).status_code == 409
        )  # no longer a draft

        # the same id and version cannot be saved again, and the database repeats the rules
        assert http.post(W, headers=h["author"], json={"definition": doc()}).status_code in (
            409,
            500,
        )

        async def tamper(sql: str) -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(text(sql))

        for sql in (
            "UPDATE verify.workflow_version SET definition = '{}'::jsonb",
            "UPDATE verify.workflow_version SET status = 'DRAFT'",
            "DELETE FROM verify.workflow_version",
        ):
            try:
                asyncio.run(tamper(sql))
                raise AssertionError(f"allowed: {sql}")
            except DBAPIError as e:
                assert (
                    "never changes" in str(e)
                    or "never deleted" in str(e)
                    or "draft" in str(e)
                    or "violat" in str(e).lower()
                    or "permission denied" in str(e)  # the runtime role has no DELETE right at all
                )


def test_an_unsafe_workflow_cannot_be_approved_and_the_reason_is_named(rt_url: str) -> None:
    with world(rt_url) as (http, h, _t, _p):
        unsafe = doc(version="2.0.0")
        unsafe["steps"]["liveness"]["next"] = "approved"  # approves with no guard at all
        v = http.post(W, headers=h["author"], json={"definition": unsafe}).json()
        assert "APPROVAL_NOT_GUARDED" in {
            x["code"] for x in v["problems"]
        }  # a draft may have problems: that is what a draft is for
        refused = http.post(f"{W}/onboarding/versions/2.0.0:approve", headers=h["approver"])
        assert (
            refused.status_code == 409
            and refused.json()["code"] == "workflow_not_safe"
            and "APPROVAL_NOT_GUARDED" in refused.text
        )


def test_a_regulated_check_cannot_be_approved_while_its_product_line_is_off_and_can_once_it_is_on(
    rt_url: str,
) -> None:
    sof = copy.deepcopy(REGULATED)
    sof["id"], sof["required_checks"] = "sof_flow", ["SOURCE_OF_FUNDS"]
    sof["steps"]["screen"]["check"] = "SOURCE_OF_FUNDS"
    sof["steps"]["decide"]["branches"][0].update(
        when={"check": "SOURCE_OF_FUNDS", "outcome_in": ["VERIFIED"]}, next="look"
    )
    sof["steps"]["look"] = {"type": "REVIEW", "role": "compliance", "next": "approved"}
    on = parse_lines(
        {
            "version": "t",
            "lines": {
                "source_of_funds": {
                    "dtv": "DTV-28",
                    "state": "STAGING",
                    "countries": ["GB"],
                    "legal_basis_ref": "L",
                }
            },
        }
    )
    with world(rt_url) as (http, h, _t, _p):
        assert http.post(W, headers=h["author"], json={"definition": sof}).status_code == 201
        off = http.post(f"{W}/sof_flow/versions/1:approve", headers=h["approver"])
        assert (
            off.status_code == 409 and "PRODUCT_LINE_NOT_AVAILABLE" in off.text
        )  # the shipped line is DESIGN
        http.app.state.product_lines = on
        assert (
            http.post(f"{W}/sof_flow/versions/1:approve", headers=h["approver"]).status_code == 200
        )


def test_a_run_follows_only_the_event_it_awaits_and_the_applicant_cannot_review_themselves(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, h, tenant, p):
        http.post(W, headers=h["author"], json={"definition": doc()})
        url = f"{W}/onboarding/versions/1.0.0"
        assert (
            http.post(
                f"{url}/runs", headers=h["runner"], json={"subject_principal_id": p["subject"]}
            ).status_code
            == 409
        )  # a draft cannot run
        http.post(f"{url}:approve", headers=h["approver"])

        started = http.post(
            f"{url}/runs", headers=h["runner"], json={"subject_principal_id": p["subject"]}
        )
        assert started.status_code == 201, started.text
        run = started.json()
        rid = run["run_id"]
        assert (
            run["status"] == "WAITING_FOR_CHECK"
            and run["awaiting_check"] == "IDENTITY_DOCUMENT"
            and run["content_hash"]
        )

        # the wrong event is refused; the right one moves it
        wrong = http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "LIVENESS", "outcome": "VERIFIED"},
        )
        assert wrong.status_code == 409 and wrong.json()["code"] == "check_not_awaited"
        assert (
            http.post(
                f"{R}/{rid}:report-check",
                headers=h["runner"],
                json={"check": "IDENTITY_DOCUMENT", "outcome": "PASS"},
            ).status_code
            == 422
        )
        step = http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "IDENTITY_DOCUMENT", "outcome": "VERIFIED"},
        ).json()
        assert step["awaiting_check"] == "LIVENESS" and step["outcome"] is None

        # an adverse result goes to a person: a review, never a decline
        step = http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "LIVENESS", "outcome": "NOT_VERIFIED"},
        ).json()
        assert (
            step["status"] == "WAITING_FOR_REVIEW"
            and step["awaiting_role"] == "verification_reviewer"
            and step["outcome"] is None
        )

        # the applicant holds the run permission and still cannot review their own run; a different person can
        own = http.post(f"{R}/{rid}:complete-review", headers=h["subject"])
        assert own.status_code == 403 and own.json()["code"] == "reviewer_not_independent"
        ended = http.post(f"{R}/{rid}:complete-review", headers=h["reviewer"]).json()
        assert ended["status"] == "ENDED" and ended["outcome"] == "REFER_TO_PERSON"
        assert (
            http.post(f"{R}/{rid}:complete-review", headers=h["reviewer"]).status_code == 409
        )  # an ended run accepts nothing

        listed = http.get(R, headers=h["reader"], params={"status": "ENDED"}).json()
        assert [i["run_id"] for i in listed["items"]] == [rid] and "results" not in listed["items"][
            0
        ]

        # the trace holds words, never a person
        trace = http.get(f"{R}/{rid}", headers=h["reader"]).json()
        assert p["reviewer"] not in str(trace) and p["subject"] not in str(trace["events"])

        async def audit() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT body::text FROM evidence.audit_record WHERE event_type LIKE 'verification.workflow.%'"
                        )
                    )
                ).all()
                return " ".join(r[0] for r in rows)

        trail = asyncio.run(audit())
        assert (
            "verification.workflow.approved" in trail
            and "verification.workflow.review_completed" in trail
        )
        assert (
            "NOT_VERIFIED" not in trail
        )  # outcomes are not in the audit trail, only ids and states


def test_a_verified_pair_of_checks_ends_in_an_approval_and_a_simulation_stores_nothing(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, h, _t, p):
        http.post(W, headers=h["author"], json={"definition": doc()})
        url = f"{W}/onboarding/versions/1.0.0"
        sim = http.post(
            f"{url}:simulate",
            headers=h["author"],
            json={"results": {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED"}},
        ).json()
        assert sim["outcome"] == "APPROVED" and "stores and sends nothing" in sim["side_effects"]
        none = http.post(f"{url}:simulate", headers=h["author"], json={"results": {}}).json()
        assert none["outcome"] == "REFER_TO_PERSON"  # no result means unavailable, never fine
        assert http.get(R, headers=h["reader"]).json()["items"] == []  # a simulation made no run

        http.post(f"{url}:approve", headers=h["approver"])
        rid = http.post(
            f"{url}/runs", headers=h["runner"], json={"subject_principal_id": p["subject"]}
        ).json()["run_id"]
        http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "IDENTITY_DOCUMENT", "outcome": "VERIFIED"},
        )
        done = http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "LIVENESS", "outcome": "VERIFIED"},
        ).json()
        assert done["status"] == "ENDED" and done["outcome"] == "APPROVED"


def test_a_retired_version_starts_no_new_run_and_governed_erasure_empties_a_runs_results(
    rt_url: str,
) -> None:
    from zid_platform.modules.verification.infrastructure import workflow_store as store

    with world(rt_url) as (http, h, tenant, p):
        http.post(W, headers=h["author"], json={"definition": doc()})
        url = f"{W}/onboarding/versions/1.0.0"
        http.post(f"{url}:approve", headers=h["approver"])
        rid = http.post(
            f"{url}/runs", headers=h["runner"], json={"subject_principal_id": p["subject"]}
        ).json()["run_id"]
        http.post(
            f"{R}/{rid}:report-check",
            headers=h["runner"],
            json={"check": "IDENTITY_DOCUMENT", "outcome": "VERIFIED"},
        )

        retired = http.post(f"{url}:retire", headers=h["approver"]).json()
        assert retired["status"] == "RETIRED"
        assert (
            http.post(
                f"{url}/runs", headers=h["runner"], json={"subject_principal_id": p["subject"]}
            ).status_code
            == 409
        )

        async def other_tenant() -> int:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
                return int(
                    (await c.execute(text("SELECT count(*) FROM verify.workflow_run"))).scalar_one()
                )

        assert asyncio.run(other_tenant()) == 0  # another company sees nothing

        async def erase() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await store.erase_run(c, rid, datetime.now(UTC))

        asyncio.run(erase())
        after = http.get(f"{R}/{rid}", headers=h["reader"]).json()
        assert after["erased"] is True and after["results"] == {} and after["events"] == []
        assert (
            http.post(
                f"{R}/{rid}:report-check",
                headers=h["runner"],
                json={"check": "LIVENESS", "outcome": "VERIFIED"},
            ).status_code
            == 409
        )
        assert rid not in [i["run_id"] for i in http.get(R, headers=h["reader"]).json()["items"]]
