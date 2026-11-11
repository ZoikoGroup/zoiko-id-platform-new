"""The /v2 verification case over HTTP (ZID-INT-002 sections 04, 05.1 and 14): separate tasks, never one pass, 409 on a finished case."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from sqlalchemy import text
from test_v2_facade_pg import BODY, uid, world
from zid_db import tenant_transaction

CASE = {
    "purpose": "professional_engagement",
    "jurisdiction": {"country": "US", "subdivision": "US-CA"},
    "workflow": {"profile_id": "professional-standard", "version": "2.0"},
    "requested_checks": ["identity", "license"],
    "consent_ref": "consent_1",
    "client_ref": "ext-42",
}
TASK = {"issuer_claim": BODY["issuer_claim"], "credential_claim": BODY["credential_claim"]}


def count(rt_url: str, tenant: str, table: str) -> int:
    async def go() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (await c.execute(text(f"SELECT count(*) FROM cred.{table}"))).scalar_one()

    return asyncio.run(go())


def key(name: str) -> dict[str, str]:
    return {"Idempotency-Key": name}


def test_a_case_keeps_each_check_separate_and_a_finished_case_is_never_changed(rt_url: str) -> None:
    with world(rt_url) as (http, mine, tenant, admin):
        h = {**mine, **key("case-key-0001")}

        # an unsupported check, or a market with no pack for a requested check, creates nothing at all
        bad = http.post(
            "/v2/verifications",
            headers=h,
            json={**CASE, "requested_checks": ["identity", "wizard"]},
        )
        assert bad.status_code == 422 and bad.json()["code"] == "check_not_supported"
        nowhere = http.post(
            "/v2/verifications",
            headers={**mine, **key("case-key-0002")},
            json={**CASE, "jurisdiction": {"country": "ZZ"}},
        )
        assert (
            nowhere.status_code == 422 and nowhere.json()["code"] == "service_unavailable_in_market"
        )
        assert count(rt_url, tenant, "verification_case") == 0

        # a client cannot name another tenant; the callback must be one of the tenant's own active endpoints
        other = http.post(
            "/v2/verifications",
            headers={**mine, **key("case-key-0003")},
            json={**CASE, "tenant_ref": str(uuid.uuid4())},
        )
        assert other.status_code == 403 and other.json()["code"] == "scope_forbidden"
        cb = http.post(
            "/v2/verifications",
            headers={**mine, **key("case-key-0004")},
            json={**CASE, "callback_ref": f"wh_{uid()}"},
        )
        assert cb.status_code == 422 and cb.json()["code"] == "callback_not_authorized"
        assert count(rt_url, tenant, "verification_case") == 0

        made = http.post("/v2/verifications", headers=h, json=CASE)
        assert made.status_code == 202, made.text
        c = made.json()
        assert c["state"] == "PENDING" and c["case_id"].startswith("case_")
        assert c["client_ref"] == "ext-42"
        assert [t["check"] for t in c["tasks"]] == ["identity", "license"]
        assert {t["state"] for t in c["tasks"]} == {"PENDING_INPUT"}
        assert all(t["outcome"] is None for t in c["tasks"])
        assert [p["check"] for p in c["policy_pack"]] == [
            "license"
        ]  # identity has a country capability, not a pack
        assert {w["check"] for w in c["pending_work"]} == {"identity", "license"}
        for field in ("request_id", "correlation_id", "status_url", "expires_at"):
            assert field in c

        # replay: the same key and body is the same case; a changed body under the key is a conflict
        assert (
            http.post("/v2/verifications", headers=h, json=CASE).json()["case_id"] == c["case_id"]
        )
        assert count(rt_url, tenant, "verification_case") == 1
        clash = http.post("/v2/verifications", headers=h, json={**CASE, "client_ref": "other"})
        assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"

        # the licence task runs the real check: its own outcome and reference, and the case is still PENDING (identity is open)
        base = f"/v2/verifications/{c['case_id']}"
        done = http.post(
            f"{base}/tasks/license", headers={**mine, **key("task-key-0001")}, json=TASK
        )
        assert done.status_code == 200, done.text
        d = done.json()
        lic = next(t for t in d["tasks"] if t["check"] == "license")
        assert lic["state"] == "COMPLETE" and lic["outcome"] == "VERIFIED"
        assert lic["credential_ref"].startswith("cred_")
        assert d["state"] == "PENDING", "one finished check never completes the case"
        assert [w["check"] for w in d["pending_work"]] == ["identity"]
        # the same task cannot be submitted twice; a check outside the case, or an unknown case, is not found
        again = http.post(
            f"{base}/tasks/license", headers={**mine, **key("task-key-0002")}, json=TASK
        )
        assert again.status_code == 409 and again.json()["code"] == "incompatible_state"
        outside = http.post(
            f"{base}/tasks/degree", headers={**mine, **key("task-key-0003")}, json=TASK
        )
        assert outside.status_code == 404
        assert http.get(f"/v2/verifications/case_{uid()}", headers=mine).status_code == 404

        # the identity task needs the person's OWN identity verification: an unknown one is not found
        bad_link = http.post(
            f"{base}/tasks/identity",
            headers={**mine, **key("task-key-0004")},
            json={"verification_id": uid()},
        )
        assert bad_link.status_code == 404

        # a real identity session of this person, in progress, links but does not complete; once final it completes the case
        profile, session = uid(), uid()

        async def seed_session() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as conn:
                now = datetime.now(UTC)
                await conn.execute(
                    text(
                        "INSERT INTO verify.assurance_profile (assurance_profile_id, tenant_id, profile_name, "
                        "profile_version, level, requirements_json, status) "
                        "VALUES (:p, :t, 'p', 1, 'IAL-Z2', '{}'::jsonb, 'ACTIVE')"
                    ),
                    {"p": uuid.UUID(profile), "t": uuid.UUID(tenant)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO verify.verification_session (session_id, tenant_id, assurance_profile_id, "
                        "subject_principal_id, purpose, jurisdiction, status, created_at, updated_at, expires_at) "
                        "VALUES (:s, :t, :p, :sub, 'x', 'US', 'IN_PROGRESS', :n, :n, :e)"
                    ),
                    {
                        "s": uuid.UUID(session),
                        "t": uuid.UUID(tenant),
                        "p": uuid.UUID(profile),
                        "sub": uuid.UUID(admin),
                        "n": now,
                        "e": now + timedelta(days=1),
                    },
                )

        async def finish_session() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as conn:
                await conn.execute(
                    text(
                        "UPDATE verify.verification_session SET status = 'VERIFIED' WHERE session_id = :s"
                    ),
                    {"s": uuid.UUID(session)},
                )

        asyncio.run(seed_session())
        linked = http.post(
            f"{base}/tasks/identity",
            headers={**mine, **key("task-key-0005")},
            json={"verification_id": session},
        )
        assert linked.status_code == 200, linked.text
        ident = next(t for t in linked.json()["tasks"] if t["check"] == "identity")
        assert ident["state"] == "IN_PROGRESS" and ident["outcome"] is None
        assert linked.json()["state"] == "PENDING"

        asyncio.run(finish_session())
        final = http.get(base, headers=mine).json()
        assert final["state"] == "COMPLETE" and final["pending_work"] == []
        outcomes = {t["check"]: t["outcome"] for t in final["tasks"]}
        assert outcomes == {"identity": "VERIFIED", "license": "VERIFIED"}, (
            "separate outcomes, never one pass"
        )
        assert "PASS" not in str(final)

        # a finished case is preserved: cancel is 409 incompatible_state and nothing changes
        cancel = http.post(f"{base}/cancel", headers=mine)
        assert cancel.status_code == 409 and cancel.json()["code"] == "incompatible_state"
        assert http.get(base, headers=mine).json()["state"] == "COMPLETE"


def test_a_pending_case_can_be_canceled_once_and_a_canceled_case_takes_no_more_work(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, tenant, _admin):
        made = http.post(
            "/v2/verifications",
            headers={**mine, **key("case-key-1001")},
            json={**CASE, "requested_checks": ["license"]},
        )
        cid = made.json()["case_id"]
        cancel = http.post(f"/v2/verifications/{cid}/cancel", headers=mine)
        assert cancel.status_code == 200 and cancel.json()["state"] == "CANCELED"
        assert http.post(f"/v2/verifications/{cid}/cancel", headers=mine).status_code == 409
        late = http.post(
            f"/v2/verifications/{cid}/tasks/license",
            headers={**mine, **key("task-key-1001")},
            json=TASK,
        )
        assert late.status_code == 409 and late.json()["code"] == "incompatible_state"
        assert count(rt_url, tenant, "claim_check") == 0, (
            "no source was asked after the case was canceled"
        )


def test_erasing_a_person_removes_their_cases_and_tasks_and_leaves_other_peoples(
    rt_url: str,
) -> None:
    """credential_0010 extends cred.erase_subject: the case tables hold a person's reference, so erasure must reach them."""
    from zid_platform.modules.credential.application import case_service as svc

    tenant, gone, kept = uid(), uid(), uid()
    now = datetime.now(UTC)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for subject in (gone, kept):
                await svc.open_case(
                    c, tenant_id=tenant, subject=subject, purpose="X", country="US", subdivision=None,
                    profile_id="p", profile_version="1", consent_ref=None, callback_endpoint_id=None, client_ref=None,
                    checks=["identity", "license"], policy_packs=[], created_by=subject, created_at=now,
                    expires_at=now + timedelta(days=1),
                )  # fmt: skip
            removed = (
                await c.execute(
                    text("SELECT cred.erase_subject(:t, :s)"),
                    {"t": uuid.UUID(tenant), "s": uuid.UUID(gone)},
                )
            ).scalar_one()
            assert removed == 3  # one case and its two tasks
            left = (
                (await c.execute(text("SELECT subject_principal_id FROM cred.verification_case")))
                .scalars()
                .all()
            )
            assert [str(x) for x in left] == [kept]
            assert (
                await c.execute(text("SELECT count(*) FROM cred.verification_case_task"))
            ).scalar_one() == 2
            # idempotent: a second erasure finds nothing
            assert (
                await c.execute(
                    text("SELECT cred.erase_subject(:t, :s)"),
                    {"t": uuid.UUID(tenant), "s": uuid.UUID(gone)},
                )
            ).scalar_one() == 0

    asyncio.run(go())
