"""Company claims under hostile and repeated use (WF-13B acceptance AT-08, AT-09, AT-10).

AT-08: a single-person founder may open a provisional claim but cannot approve their own sensitive actions.
AT-09: a second organization claiming the same company learns nothing about the first, and cannot read, refresh or extend its claim.
AT-10: a replayed submit, a replayed ruling and concurrent refreshes never make a second trust decision."""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard, use_register_first_routing
from sqlalchemy import text
from test_approvals_pg import OWNER_TRANSFER, World, code, world  # noqa: F401  (world is a fixture)
from test_org_claims_api_pg import REVIEW_ACTIONS, REVIEW_RESOURCES, Register, error
from zid_platform.main import create_app
from zid_platform.modules.organization.domain.kyb import CompanyFacts
from zid_platform.modules.verification.domain.verification import ClaimResult, SessionStatus
from zid_platform.modules.verification.infrastructure import verification_store as vstore
from zid_platform.settings import Settings

CLAIM = {"country": "GB", "registration_number": "00123456", "legal_name": "Acme Widgets Limited"}


@pytest.fixture(autouse=True)
def _register_route(monkeypatch: pytest.MonkeyPatch) -> None:
    use_register_first_routing(monkeypatch)


def key(prefix: str = "claim") -> dict[str, str]:
    return {"Idempotency-Key": f"{prefix}-{uuid.uuid4().hex[:10]}"}


def app_for(o: Any, rt_url: str, register: Register) -> Any:
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.kyb_adapters = {"companies-house": register}
    return app


def test_a_single_person_founder_may_claim_provisionally_but_cannot_approve_their_own_sensitive_actions(
    world: World,  # noqa: F811
) -> None:
    w = world
    w.http.app.state.kyb_adapters = {"companies-house": Register()}
    solo = w.http.get("/v1/governance/solo-status", headers=w.h(w.o.owner)).json()
    assert solo["solo_mode"] is True and solo["independent_routes"] == []

    # the claim is allowed, and it is only ever provisional: opening it verifies nothing
    r = w.http.post("/v1/org-claims", headers={**w.h(w.o.owner), **key("solo")}, json=CLAIM)
    assert r.status_code == 201, r.text
    assert r.json()["outcome"] == "PROVISIONAL"

    # the same person asks for a critical change: it is held, they cannot approve it, and nothing runs
    target = w.person(None)
    case = w.create(w.o.owner, OWNER_TRANSFER, "t", {"principal_id": target}).json()
    assert case["hold"] == "BLOCKED_INSUFFICIENT_APPROVERS" and case["approvals_received"] == 0
    assert w.vote(w.o.owner, case, proof=False).status_code == 403
    assert code(w.execute(w.o.owner, case["case_id"])) == "APPROVAL_NOT_READY"

    # a sensitive (A1) change is the same: the requester is never the independent approval
    outsider = w.person("VIEWER")
    made = w.http.post(
        "/v1/governance/reviewers", headers=w.h(w.o.owner),
        json={"principal_id": outsider, "relationship": "TRUSTEE", "authority_basis": "Trustee under the trust deed",
              "independence_declaration": "No tie to the requester"},
    )  # fmt: skip
    assert made.status_code == 201, made.text
    a1 = made.json()["activation_case"]
    assert w.vote(w.o.owner, a1, proof=False).status_code == 403
    assert code(w.execute(w.o.owner, a1["case_id"])) == "APPROVAL_NOT_READY"

    # and the claim did not become anything more by waiting: the organization cannot activate on a provisional claim
    blocked = w.http.post(f"/v1/organizations/{w.o.tenant}/activation", headers=w.h(w.o.owner))
    assert blocked.status_code in (409, 422) and error(blocked) == "ACTIVATION_BLOCKED"
    status = w.http.get(
        f"/v1/org-claims/{r.json()['claim_id']}/status", headers=w.h(w.o.owner)
    ).json()
    assert status["outcome"] == "PROVISIONAL" and "authority" not in status["checks"]


def test_another_organization_claiming_the_same_company_learns_nothing_and_reaches_nothing(
    rt_url: str,
) -> None:
    a, b = onboard(rt_url), onboard(rt_url)
    api_a = TestClient(app_for(a, rt_url, Register()))
    api_b = TestClient(app_for(b, rt_url, Register()))
    with api_a, api_b:
        first = api_a.post("/v1/org-claims", headers={**a.bearer(a.owner), **key()}, json=CLAIM)
        assert first.status_code == 201, first.text
        mine = first.json()["claim_id"]

        # the second organization claims the very same company: an ordinary answer, no sign that anyone else did
        second = api_b.post("/v1/org-claims", headers={**b.bearer(b.owner), **key()}, json=CLAIM)
        assert second.status_code == 201, second.text
        theirs = second.json()["claim_id"]
        assert theirs != mine
        for leaked in (a.tenant, mine, "already", "duplicate", "another organization"):
            assert leaked not in second.text, leaked
        search = api_b.post(
            "/v1/org-claims/search",
            headers=b.bearer(b.owner),
            json={"country": "GB", "query": "Acme"},
        )
        assert search.status_code == 200 and a.tenant not in search.text

        # reading, refreshing or extending the first claim from the second organization is the same 404 as a claim that never existed
        ghost = str(uuid.uuid4())
        for method, path, body in (
            ("get", "/v1/org-claims/{}/status", None),
            ("post", "/v1/org-claims/{}/refresh", None),
            (
                "post",
                "/v1/org-claims/{}/screening",
                {
                    "representative": {"ref": "r", "name": "Alice Smith"},
                    "owners": [],
                    "holdings": [],
                },
            ),
        ):
            kw: dict[str, Any] = {"json": body} if body else {}
            foreign = getattr(api_b, method)(path.format(mine), headers=b.bearer(b.owner), **kw)
            absent = getattr(api_b, method)(path.format(ghost), headers=b.bearer(b.owner), **kw)
            assert foreign.status_code == absent.status_code == 404, (
                path,
                foreign.status_code,
                absent.status_code,
            )
            assert (
                error(foreign) == error(absent)
                and foreign.json()["error"]["message"] == absent.json()["error"]["message"]
            )
            assert a.tenant not in foreign.text and mine not in foreign.text.replace(
                path.format(mine), ""
            )

        # and each organization still sees only its own claim
        assert (
            api_a.get(f"/v1/org-claims/{mine}/status", headers=a.bearer(a.owner)).json()["claim_id"]
            == mine
        )
        assert (
            api_b.get(f"/v1/org-claims/{theirs}/status", headers=b.bearer(b.owner)).json()[
                "claim_id"
            ]
            == theirs
        )
        assert (
            api_a.get(f"/v1/org-claims/{theirs}/status", headers=a.bearer(a.owner)).status_code
            == 404
        )


def counts(owner_url: str, claim_id: str) -> tuple[int, list[int], int]:
    """(cases, evidence versions, decision events) for one claim, seen by the worker role."""

    async def go() -> tuple[int, list[int], int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            cases = (
                await c.execute(
                    text("SELECT count(*) FROM org.kyb_case WHERE case_id = :c"), {"c": claim_id}
                )
            ).scalar_one()
            versions = [
                int(r.version)
                for r in (
                    await c.execute(
                        text(
                            "SELECT version FROM org.kyb_evidence WHERE case_id = :c ORDER BY version"
                        ),
                        {"c": claim_id},
                    )
                ).all()
            ]
            events = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE event_type = 'com.zoiko.id.organization.kyb.decided.v1' AND aggregate_id = :c"
                    ),
                    {"c": claim_id},
                )
            ).scalar_one()
        return int(cases), versions, int(events)

    return asyncio.run(go())


def test_replays_and_concurrent_requests_never_make_a_second_trust_decision(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES)
    register = Register()
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with TestClient(app_for(o, rt_url, register)) as api:
        # a submit replayed many times at once is one claim, one case, one decision
        k = key("replay")
        with ThreadPoolExecutor(8) as pool:
            answers = list(
                pool.map(
                    lambda _: api.post("/v1/org-claims", headers={**owner, **k}, json=CLAIM),
                    range(8),
                )
            )
        assert {r.status_code for r in answers} == {201}, [
            r.text for r in answers if r.status_code != 201
        ]
        ids = {r.json()["claim_id"] for r in answers}
        assert len(ids) == 1
        claim = ids.pop()
        assert counts(owner_url, claim) == (1, [1], 1)

        # concurrent refreshes each append their own version: no version is shared and none is skipped
        with ThreadPoolExecutor(6) as pool:
            refreshed = list(
                pool.map(
                    lambda _: api.post(f"/v1/org-claims/{claim}/refresh", headers=owner), range(6)
                )
            )
        assert {r.status_code for r in refreshed} == {200}, [
            r.text for r in refreshed if r.status_code != 200
        ]
        cases, versions, events = counts(owner_url, claim)
        assert cases == 1 and versions == list(range(1, 8)) and events == 7, (versions, events)

        # a ruling replayed is refused, and the case in review ends with exactly one ruling
        register.name = (
            "Acme Global Industries Limited"  # the register now contradicts the name given
        )
        other = {**CLAIM, "registration_number": "00777777"}
        held = api.post("/v1/org-claims", headers={**owner, **key("review")}, json=other).json()[
            "claim_id"
        ]
        ruling = {
            "outcome": "VERIFIED",
            "rationale": "The change of name is confirmed by the certificate of change.",
        }
        with ThreadPoolExecutor(4) as pool:
            rulings = list(
                pool.map(
                    lambda _: api.post(
                        f"/v1/reviews/kyb-cases/{held}/rulings", headers=second, json=ruling
                    ),
                    range(4),
                )
            )
        assert sorted(r.status_code for r in rulings) == [200, 409, 409, 409], [
            (r.status_code, r.text) for r in rulings
        ]
        _, versions, events = counts(owner_url, held)
        assert versions == [1, 2] and events == 2, "one decision, one ruling, nothing duplicated"


class Changing(Register):
    """A register that can start saying the company is dissolved."""

    dissolved = False

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        facts = await super().get(number, country, now)
        assert facts is not None
        return (
            replace(facts, registration_status="dissolved", active=False)
            if self.dissolved
            else facts
        )


def test_a_company_that_is_dissolved_at_its_next_refresh_drops_out_of_every_view_and_the_history_stays(
    rt_url: str, owner_url: str
) -> None:
    """AT-11: the claim, the readiness checklist and the audit trail agree; nothing is rewritten."""
    o = onboard(rt_url)
    register = Changing()
    owner = o.bearer(o.owner)
    with TestClient(app_for(o, rt_url, register)) as api:
        r = api.post("/v1/org-claims", headers={**owner, **key("lost")}, json=CLAIM)
        assert r.status_code == 201 and r.json()["checks"]["company"] == "COMPLETED"
        claim = r.json()["claim_id"]
        setup = api.get(f"/v1/organizations/{o.tenant}/onboarding", headers=owner).json()
        before = {i["key"]: i["status"] for i in setup["trust"]}["company"]

        register.dissolved = True  # the official register now shows the company as dissolved
        again = api.post(f"/v1/org-claims/{claim}/refresh", headers=owner).json()
        assert again["checks"]["company"] != "COMPLETED" and again["outcome"] != "VERIFIED"
        assert again["company"]["status"] in {"NEEDS_REVIEW", "REJECTED"}

        setup = api.get(f"/v1/organizations/{o.tenant}/onboarding", headers=owner).json()
        after = {i["key"]: i["status"] for i in setup["trust"]}["company"]
        assert after != before and after != "VERIFIED", (before, after)
        assert setup["can_activate"] is False

    cases, versions, decided = counts(owner_url, claim)
    assert (cases, versions, decided) == (1, [1, 2], 2), "both decisions are still on the ledger"

    async def lost() -> int:
        async with engine(owner_url) as eng, eng.begin() as c:
            return int(
                (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM events.outbox_event WHERE event_type = 'com.zoiko.id.organization.kyb.verification_lost.v1' AND aggregate_id = :c"
                        ),
                        {"c": claim},
                    )
                ).scalar_one()
            )

    assert asyncio.run(lost()) == 1


def give_identity(w: World, principal: str, *, performed_at: datetime) -> None:
    """One identity check result for a person: a profile, a verified session and its assertion, as the verification service writes them."""

    async def work(c: Any) -> None:
        pid, _ = await vstore.create_profile(
            c, tenant_id=w.o.tenant, name=f"kyc-{uuid.uuid4().hex[:8]}", level="IAL-Z2",
            requirements={"required_claims": ["legal_name"], "accepted_evidence": ["GOVERNMENT_ID"], "binding_required": True, "jurisdictions": ["GB"]},
        )  # fmt: skip
        profile = await vstore.load_profile(c, pid)
        s = vstore.VSession(
            str(uuid.uuid4()), w.o.tenant, pid, principal, "onboarding", "GB", SessionStatus.VERIFIED, "sim",
            f"tx-{uuid.uuid4().hex[:8]}", (), "VERIFIED", None, 1, performed_at, performed_at + timedelta(days=1),
        )  # fmt: skip
        await vstore.create_session(c, s)
        await vstore.insert_assertions(
            c, s, profile, (ClaimResult("legal_name", True, "Jane Mary Doe", "doc", ("GOVERNMENT_ID",)),),
            adapter_id="sim", reasons=("VERIFIED",), evidence_commitment=None, now=performed_at,
        )  # fmt: skip

    w.run(work)


def test_the_applicants_identity_check_completes_only_from_their_own_valid_identity_check(
    world: World,  # noqa: F811
) -> None:
    """WF-13B-03 'Your identity': read from the verification service for the person who made the claim. A registry match, a declaration, someone
    else's check or an expired one completes nothing, and on its own it never makes the company VERIFIED."""
    w = world
    w.http.app.state.kyb_adapters = {"companies-house": Register()}
    claim = w.http.post(
        "/v1/org-claims", headers={**w.h(w.o.owner), **key("identity")}, json=CLAIM
    ).json()["claim_id"]

    def status() -> dict[str, Any]:
        r = w.http.get(f"/v1/org-claims/{claim}/status", headers=w.h(w.o.owner))
        assert r.status_code == 200, r.text
        return dict(r.json())

    assert status()["checks"]["person_assurance"] == "PENDING"
    give_identity(
        w, w.person("ADMIN"), performed_at=datetime.now(UTC)
    )  # another person's identity check is not the applicant's
    assert status()["checks"]["person_assurance"] == "PENDING"
    give_identity(
        w, w.o.owner, performed_at=datetime.now(UTC) - timedelta(days=3650)
    )  # an expired one is not current
    assert status()["checks"]["person_assurance"] == "PENDING"

    give_identity(
        w, w.o.owner, performed_at=datetime.now(UTC)
    )  # the applicant's own, valid and unexpired
    done = status()
    assert done["checks"]["person_assurance"] == "COMPLETED"
    assert (
        "authority" not in done["checks"] and done["outcome"] == "PROVISIONAL"
    )  # identity alone verifies no company
