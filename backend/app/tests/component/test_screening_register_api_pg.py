"""Screening over HTTP with the official register, the data region, appeals, replay and monitoring (ZID-ED-04; EDR04-04 and EDR04-06).

The company is a stand-in register that lists directors and persons with significant control; everything else is real: the routes,
the roles, the residency gate, the database, the evidence, the worker passes."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from sqlalchemy import text
from test_org_claims_api_pg import REVIEW_ACTIONS, REVIEW_RESOURCES, Register, error
from test_org_claims_trust_pg import CLAIM, app_for, key
from test_screening_pg import CFG, load_lists, sign, un_xml
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.application.monitoring import register_watch_pass
from zid_platform.modules.organization.domain.kyb import KybError, RegisterPersonFact, RegisterRead
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.application.passes import rescreen_pass
from zid_platform.modules.tenant.application import residency

OWNER, HIDDEN, LATE = "Olivia Owner", "Peter Hidden", "Laura Late"


class WithPeople(Register):
    """The register with a list of people on it. `down` makes it fail the way an outage does."""

    def __init__(self, *names: str) -> None:
        super().__init__()
        self.down_people = False
        self.names = list(names)

    async def register_people(self, number: str, country: str, now: datetime) -> RegisterRead:
        if self.down_people:
            raise KybError("SOURCE_UNAVAILABLE")
        # the reader normalizes to natural order, as the Companies House adapter does
        people = tuple(
            RegisterPersonFact("PSC", n, False, ("ownership-of-shares-25-to-50-percent",))
            for n in sorted(self.names)
        )
        digest = hashlib.sha256("|".join(sorted(self.names)).encode()).hexdigest()
        return RegisterRead("OK", people, digest, "companies-house")


BODY = {
    "representative": {"ref": "rep", "name": "Alice Smith"},
    "owners": [{"ref": "o1", "name": OWNER}],
    "holdings": [{"owner": "o1", "shares_pct": "60"}],
}


def make(
    rt_url: str, register: Register, **extra: Any
) -> tuple[Any, TestClient, str, dict[str, str]]:
    o = onboard(rt_url, **extra)

    async def place() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await residency.set_residency(
                c, o.tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
                key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=o.owner,
            )  # fmt: skip

    asyncio.run(place())
    api = TestClient(app_for(o, rt_url, register))
    return o, api, o.tenant, o.bearer(o.owner)


def fresh_lists(rt_url: str) -> datetime:
    now = datetime.now(UTC)

    async def go() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, un=un_xml(("Aaa", "Bbb")), at=now)

    asyncio.run(go())
    return now


def scan(owner_url: str, tenant: str) -> str:
    async def go() -> str:
        async with engine(owner_url) as eng, eng.begin() as c:
            events = (
                (
                    await c.execute(
                        text("SELECT payload::text FROM events.outbox_event WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                )
                .scalars()
                .all()
            )
            audits = (
                (
                    await c.execute(
                        text("SELECT body::text FROM evidence.audit_record WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                )
                .scalars()
                .all()
            )
            evidence = (
                (
                    await c.execute(
                        text(
                            "SELECT body::text FROM verify.screening_evidence WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                )
                .scalars()
                .all()
            )
        return " ".join([*events, *audits, *evidence])

    return asyncio.run(go())


def test_a_person_the_register_lists_is_a_task_screened_and_never_named_outside_the_reviewers_view(
    rt_url: str, owner_url: str
) -> None:
    fresh_lists(rt_url)
    register = WithPeople(OWNER, HIDDEN)
    o, api, tenant, owner = make(
        rt_url, register, extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES
    )
    second = o.bearer(o.second)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        r = api.post(f"/v1/org-claims/{claim}/screening", headers=owner, json=BODY)
        assert r.status_code == 201, r.text
        screening = r.json()["screening"]
        assert [t["code"] for t in screening["tasks"]] == ["DECLARE_REGISTER_PSC"] and screening[
            "tasks"
        ][0]["count"] == 1
        for leaked in ("Peter", "Hidden", "HIDDEN"):
            assert leaked not in r.text, leaked

        # nothing the register said or the applicant declared is in an event, an audit record or the signed evidence
        blob = scan(owner_url, tenant)
        assert blob
        for name in ("Peter", "Hidden", "Olivia", "Alice", "HIDDEN"):
            assert name not in blob, name

        # only a reviewer with the protected action sees what the register listed, beside what was declared
        case_id = _case(rt_url, tenant, claim)
        detail = api.get(f"/v1/reviews/screening-cases/{case_id}", headers=second).json()
        assert {p["name"] for p in detail["register"]["people"]} == {OWNER, HIDDEN} and detail[
            "appeals"
        ] == []

        # replay: decide again from what the first decision used; it agrees with the signed evidence
        replayed = api.get(
            f"/v1/reviews/screening-cases/{case_id}/replay", params={"version": 1}, headers=second
        )
        assert (
            replayed.status_code == 200
            and replayed.json()["replayable"]
            and replayed.json()["matches"]
        ), replayed.text

        # an owner added to the claim now (a new declaration is not allowed while it waits on a person)
        assert (
            api.put(f"/v1/org-claims/{claim}/screening", headers=owner, json=BODY).status_code
            == 409
        )


def _case(rt_url: str, tenant: str, claim: str) -> str:
    async def go() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return str(
                (
                    await c.execute(
                        text("SELECT case_id FROM verify.screening_case WHERE kyb_case_id = :k"),
                        {"k": uuid.UUID(claim)},
                    )
                ).scalar_one()
            )

    return asyncio.run(go())


def test_the_watch_notices_a_changed_register_and_the_next_pass_screens_again_and_says_why(
    rt_url: str, owner_url: str
) -> None:
    now = fresh_lists(rt_url)
    register = WithPeople(OWNER)
    _o, api, tenant, owner = make(rt_url, register)
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        assert (
            api.post(f"/v1/org-claims/{claim}/screening", headers=owner, json=BODY).status_code
            == 201
        )
        case_id = _case(rt_url, tenant, claim)

        async def watch_then_screen() -> tuple[
            dict[str, int], dict[str, int], dict[str, int], dict[str, int]
        ]:
            async with engine(owner_url) as worker:
                async with worker.begin() as c:
                    nothing = await register_watch_pass(
                        c, {"companies-house": register}, now
                    )  # the register is as the case saw it
                register.names.append(
                    LATE
                )  # a new person with significant control appears on the register
                async with worker.begin() as c:
                    changed = await register_watch_pass(c, {"companies-house": register}, now)
                async with worker.begin() as c:
                    screened = await rescreen_pass(c, CFG, sign, now)
                async with worker.begin() as c:
                    again = await register_watch_pass(
                        c, {"companies-house": register}, now
                    )  # nothing new since
                return nothing, changed, screened, again

        results = asyncio.run(watch_then_screen())
        # the passes are global and the test database is shared: look only at this tenant
        nothing, changed, screened, again = (
            {k: v for k, v in r.items() if k == tenant} for r in results
        )
        assert (nothing, again) == ({}, {}), (nothing, changed, screened, again)
        assert changed == {tenant: 1} and screened.get(tenant) == 1

        async def history() -> list[Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                return await service.history(c, tenant, case_id)

        hist = asyncio.run(history())
        assert [h.version for h in hist] == [1, 2] and hist[-1].body["triggers"] == [
            "REGISTER_CHANGED"
        ]
        assert any(r.startswith("REGISTER_PSC_UNDECLARED") for r in hist[-1].reasons), (
            "the person who appeared is asked for, as a task"
        )
        assert hist[0].body["register"]["seq"] == 1 and hist[-1].body["register"]["seq"] == 2

        # a register that cannot be read changes nothing: it is never read as "no one"
        register.down_people = True

        async def outage() -> dict[str, int]:
            async with engine(owner_url) as worker, worker.begin() as c:
                return await register_watch_pass(c, {"companies-house": register}, now)

        assert tenant not in asyncio.run(outage())


def test_an_appeal_over_http_goes_back_to_a_person_who_did_not_decide_before_and_the_statement_stays_private(
    rt_url: str, owner_url: str
) -> None:
    fresh_lists(rt_url)
    o, api, tenant, owner = make(
        rt_url, WithPeople(OWNER), extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES
    )
    second = o.bearer(o.second)
    secret = "ZQXWVHTTPSTATEMENT"
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]
        assert (
            api.post(f"/v1/org-claims/{claim}/screening", headers=owner, json=BODY).status_code
            == 201
        )
        case_id = _case(rt_url, tenant, claim)
        statement = {
            "kind": "APPEAL",
            "statement": f"The details on file are wrong and I can prove it. {secret}",
        }

        # an appeal is for a rejection; before one, the same words are a correction
        r = api.post(f"/v1/org-claims/{claim}/appeal", headers=owner, json=statement)
        assert r.status_code == 409 and error(r) == "APPEAL_NOT_AVAILABLE"
        ruled = api.post(
            f"/v1/reviews/screening-cases/{case_id}/rulings",
            headers=second,
            json={"outcome": "REJECTED", "rationale": "The authority cannot be established."},
        )
        assert ruled.status_code == 200 and ruled.json()["status"] == "REJECTED"

        r = api.post(
            f"/v1/org-claims/{claim}/appeal",
            headers=owner,
            json={**statement, "statement": "short"},
        )
        assert r.status_code == 422 and error(r) == "STATEMENT_INVALID"
        r = api.post(f"/v1/org-claims/{claim}/appeal", headers=owner, json=statement)
        assert r.status_code == 201, r.text
        assert r.json()["screening"]["status"] == "REVIEW_REQUIRED" and secret not in r.text
        assert (
            api.post(f"/v1/org-claims/{claim}/appeal", headers=owner, json=statement).status_code
            == 409
        )

        # the reviewer who rejected it cannot decide the appeal; the person who asked cannot either
        again = {"outcome": "VERIFIED", "rationale": "A second look at the same documents."}
        assert (
            error(
                api.post(
                    f"/v1/reviews/screening-cases/{case_id}/rulings", headers=second, json=again
                )
            )
            == "REVIEWER_CONFLICT"
        )
        assert (
            error(
                api.post(
                    f"/v1/reviews/screening-cases/{case_id}/rulings", headers=owner, json=again
                )
            )
            == "REVIEWER_CONFLICT"
        )
        detail = api.get(f"/v1/reviews/screening-cases/{case_id}", headers=second).json()
        assert [a["statement"] for a in detail["appeals"]] == [statement["statement"]], (
            "the reviewer reads the statement"
        )
        assert secret not in scan(owner_url, tenant)

        # another organization cannot appeal this case: the same 404 as one that does not exist
        _o2, api2, _t2, owner2 = make(rt_url, WithPeople(OWNER))
        with api2:
            for target in (claim, str(uuid.uuid4())):
                assert (
                    api2.post(
                        f"/v1/org-claims/{target}/appeal", headers=owner2, json=statement
                    ).status_code
                    == 404
                )


def test_restricted_person_records_are_stored_only_where_the_organizations_region_allows(
    rt_url: str, owner_url: str
) -> None:
    fresh_lists(rt_url)
    _o, api, tenant, owner = make(rt_url, WithPeople(OWNER))
    token = "Zebulon Refusedname"
    with api:
        claim = api.post("/v1/org-claims", headers={**owner, **key()}, json=CLAIM).json()[
            "claim_id"
        ]

        async def forget_residency() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text("DELETE FROM platform.tenant_residency WHERE tenant_id = :t"),
                    {"t": uuid.UUID(tenant)},
                )

        asyncio.run(
            forget_residency()
        )  # the organization has no region to hold restricted records in
        r = api.post(
            f"/v1/org-claims/{claim}/screening",
            headers=owner,
            json={**BODY, "owners": [{"ref": "o1", "name": token}]},
        )
        assert r.status_code == 403 and error(r) == "SCREENING_RESIDENCY_DENIED", r.text
        assert token not in r.text

        async def stored() -> tuple[int, int, int]:
            async with engine(owner_url) as eng, eng.begin() as c:
                parties = (
                    await c.execute(
                        text("SELECT count(*) FROM verify.screening_party WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
                cases = (
                    await c.execute(
                        text("SELECT count(*) FROM verify.screening_case WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
                refused = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'verification.screening.refused'"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
            return int(parties), int(cases), int(refused)

        assert asyncio.run(stored()) == (0, 0, 1), (
            "nothing was stored, and the refusal is on record"
        )
        assert "Zebulon" not in scan(owner_url, tenant)
