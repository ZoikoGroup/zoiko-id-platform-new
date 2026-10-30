"""Organization claims over HTTP (WF-13B; ZID-ED-03, ZID-ED-04): a provisional claim that the server decides, four independent
checks and one outcome, nothing from a sanctions list shown to the applicant, and reviewer routes that a role never grants."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.modules.organization.domain.kyb import (
    Candidate,
    Capabilities,
    CompanyFacts,
    KybError,
    SourceKind,
)
from zid_platform.modules.screening.application import screening as screening_service
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.settings import Settings

NOW = datetime.now(UTC)
REVIEW_ACTIONS = (
    "verification.screening.read_restricted",
    "verification.screening.review",
    "organization.kyb.review",
)
REVIEW_RESOURCES = ("screening_case:*", "kyb_case:*")


class Register:
    """A stand-in for Companies House: one answer, or an outage."""

    adapter_id, kind = "companies-house", SourceKind.OFFICIAL_REGISTER
    capabilities = Capabilities(frozenset({"GB"}))

    def __init__(self) -> None:
        self.down = False
        self.name = "Acme Widgets Limited"

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        return [
            Candidate(
                self.adapter_id,
                "GB",
                "companies-house",
                "00123456",
                self.name,
                "active",
                "https://x",
                0.97,
            ),
            Candidate(
                self.adapter_id,
                "GB",
                "companies-house",
                "00123457",
                self.name + " 2",
                "active",
                "https://x",
                0.9,
            ),
        ]

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        if self.down:
            raise KybError("SOURCE_UNAVAILABLE")
        return CompanyFacts(
            self.adapter_id, self.kind, "GB", "companies-house", number, self.name, "ltd", "active", True,
            "1 High St, London", f"https://x/{number}", now, "c" * 64,
        )  # fmt: skip


UN = (
    b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><DATAID>900</DATAID><FIRST_NAME>Eric</FIRST_NAME>"
    b"<SECOND_NAME>Badege</SECOND_NAME><UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL><INDIVIDUAL><DATAID>901</DATAID>"
    b"<FIRST_NAME>Aaa</FIRST_NAME><SECOND_NAME>Bbb</SECOND_NAME></INDIVIDUAL></INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>"
)
SDN = b'1,"DOE, Norman","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1950"\n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK1,Pavel,Nobody,Primary Name\n"
EU = b'<export><sanctionEntity logicalId="1"><subjectType code="person"/><nameAlias wholeName="Quentin Imaginary"/></sanctionEntity></export>'


async def load_lists(url: str) -> None:
    async with engine(url) as eng, eng.begin() as c:
        for lid, files in (
            (ListId.OFAC_SDN, {"sdn": SDN}),
            (ListId.OFAC_NON_SDN, {"sdn": SDN}),
            (ListId.UK_SANCTIONS, {"file": UK}),
            (ListId.EU_CONSOLIDATED, {"file": EU}),
            (ListId.UN_SC, {"file": UN}),
        ):
            await screening_service.ingest_list(
                c, lid, files, fetched_at=NOW, source_ref="t", now=NOW
            )


def error(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_a_claim_is_decided_by_the_server_screened_and_reviewed_without_leaking_a_list(
    rt_url: str,
) -> None:
    asyncio.run(load_lists(rt_url))
    o = onboard(rt_url, extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES)
    register = Register()
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.kyb_adapters = {"companies-house": register}
    owner, second = o.bearer(o.owner), o.bearer(o.second)

    with TestClient(app) as api:
        # who may ask: an OWNER or ADMIN, and the country must be open
        assert (
            api.post("/v1/org-claims/search", json={"country": "GB", "query": "Acme"}).status_code
            == 401
        )
        r = api.post(
            "/v1/org-claims/search", headers=second, json={"country": "GB", "query": "Acme"}
        )
        assert (
            r.status_code == 403 and error(r) == "ROLE_NOT_PERMITTED"
        )  # a grant is not a tenant role
        r = api.post(
            "/v1/org-claims/search", headers=owner, json={"country": "FR", "query": "Acme"}
        )
        assert r.status_code == 422 and error(r) == "COUNTRY_CAPABILITY_UNAVAILABLE"

        # 01 find the company: ranked candidates, none chosen, no supplier named
        r = api.post(
            "/v1/org-claims/search", headers=owner, json={"country": "GB", "query": "Acme Widgets"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["registration_number"] for c in body["candidates"]] == ["00123456", "00123457"]
        assert (
            body["manual_review_available"] is True and "creditsafe" not in r.text.lower()
        )  # no supplier is shown as a trust badge

        # 02 open a provisional claim for ONE selected company: the server re-reads the register and decides
        claim = {
            "country": "GB",
            "registration_number": "00123456",
            "legal_name": "Acme Widgets Limited",
        }
        key = {"Idempotency-Key": f"claim-key-{uuid.uuid4().hex[:8]}"}
        r = api.post("/v1/org-claims", headers={**owner, **key}, json=claim)
        assert r.status_code == 201, r.text
        status = r.json()
        claim_id = status["claim_id"]
        assert status["checks"]["company"] == "COMPLETED"
        assert (
            status["checks"]["person_assurance"] == "PENDING"
        )  # identity verification is its own step
        assert (
            status["outcome"] == "PROVISIONAL"
        )  # a registry match is never a verified organization
        again = api.post("/v1/org-claims", headers={**owner, **key}, json=claim)
        assert again.json()["claim_id"] == claim_id  # the same key is the same claim
        assert (
            api.post(
                "/v1/org-claims",
                headers={**owner, "Idempotency-Key": key["Idempotency-Key"]},
                json={**claim, "registration_number": "00999999"},
            ).status_code
            == 409
        )
        assert (
            api.post(
                "/v1/org-claims",
                headers={**owner, "Idempotency-Key": "claim-bad-0001"},
                json={**claim, "registration_number": "12"},
            ).status_code
            == 422
        )
        assert (
            api.post("/v1/org-claims", headers=owner, json=claim).status_code == 422
        )  # no idempotency key

        # the register goes down: a refresh is pending, never a made-up pass, and the claim is still provisional
        register.down = True
        r = api.post(f"/v1/org-claims/{claim_id}/refresh", headers=owner)
        assert r.status_code == 200 and r.json()["checks"]["company"] == "PENDING"
        register.down = False
        assert (
            api.post(f"/v1/org-claims/{claim_id}/refresh", headers=owner).json()["checks"][
                "company"
            ]
            == "COMPLETED"
        )
        assert (
            api.get(f"/v1/org-claims/{claim_id}/status", headers=owner).json()["claim_id"]
            == claim_id
        )
        assert api.get(f"/v1/org-claims/{uuid.uuid4()}/status", headers=owner).status_code == 404

        # 03 declare who to screen: a listed person among the owners, and no authority root for this person
        screening = {
            "representative": {"ref": "rep", "name": "Alice Smith"},
            "owners": [{"ref": "owner1", "name": "Eric Badege"}],
            "holdings": [{"owner": "owner1", "shares_pct": "60"}],
        }
        r = api.post(f"/v1/org-claims/{claim_id}/screening", headers=owner, json=screening)
        assert r.status_code == 201, r.text
        shown = r.json()
        assert shown["checks"]["risk"] == "HELD" and shown["checks"]["authority"] == "PENDING"
        assert shown["outcome"] == "IN_REVIEW"
        assert (
            "Badege" not in r.text and "UN_SC" not in r.text and "ALERT" not in r.text
        )  # nothing from a list reaches the applicant
        screening_id = case_of(rt_url, o.tenant, claim_id)

        # reviewers: a tenant role never grants these routes, a protected action does
        bad = api.get(f"/v1/reviews/screening-cases/{screening_id}")
        assert bad.status_code == 401
        detail = api.get(f"/v1/reviews/screening-cases/{screening_id}", headers=second)
        assert detail.status_code == 200, detail.text
        assert (
            detail.json()["alerts"][0]["matched_name"] == "Eric Badege"
            and detail.json()["alerts"][0]["list_id"] == "UN_SC"
        )
        ruling = {
            "outcome": "VERIFIED",
            "rationale": "Namesake: the passport shows a different birth year and country.",
        }
        # the person who opened the claim cannot decide it
        r = api.post(
            f"/v1/reviews/screening-cases/{screening_id}/rulings", headers=owner, json=ruling
        )
        assert r.status_code == 403 and error(r) == "REVIEWER_CONFLICT"
        assert (
            api.post(
                f"/v1/reviews/screening-cases/{screening_id}/rulings",
                headers=second,
                json={"outcome": "VERIFIED", "rationale": "fine"},
            ).status_code
            == 422
        )
        r = api.post(
            f"/v1/reviews/screening-cases/{screening_id}/rulings", headers=second, json=ruling
        )
        assert (
            r.status_code == 200 and r.json()["status"] == "VERIFIED" and r.json()["version"] == 2
        )
        assert (
            api.post(
                f"/v1/reviews/screening-cases/{screening_id}/rulings", headers=second, json=ruling
            ).status_code
            == 409
        )
        # a company case that is not in review cannot be ruled on; one that is can, by someone other than the requester
        r = api.post(f"/v1/reviews/kyb-cases/{claim_id}/rulings", headers=second, json=ruling)
        assert r.status_code == 409 and error(r) == "CASE_NOT_IN_REVIEW"
        register.name = "Acme Global Industries Limited"  # the register now contradicts the name the applicant gave
        other = {**claim, "registration_number": "00777777"}
        r = api.post(
            "/v1/org-claims", headers={**owner, "Idempotency-Key": "claim-review-001"}, json=other
        )
        review_id = r.json()["claim_id"]
        assert r.json()["checks"]["company"] == "HELD" and r.json()["outcome"] == "IN_REVIEW"
        r = api.post(f"/v1/reviews/kyb-cases/{review_id}/rulings", headers=owner, json=ruling)
        assert r.status_code == 403 and error(r) == "REVIEWER_IS_REQUESTER"
        r = api.post(
            f"/v1/reviews/kyb-cases/{review_id}/rulings",
            headers=second,
            json={**ruling, "outcome": "REVIEW_REQUIRED"},
        )
        assert (
            r.status_code == 422
        )  # that outcome belongs to screening: the company one is NEEDS_REVIEW
        r = api.post(f"/v1/reviews/kyb-cases/{review_id}/rulings", headers=second, json=ruling)
        assert (
            r.status_code == 200 and r.json()["status"] == "VERIFIED" and r.json()["version"] == 2
        )
        assert (
            api.get(f"/v1/org-claims/{review_id}/status", headers=owner).json()["checks"]["company"]
            == "COMPLETED"
        )
        done = api.get(f"/v1/org-claims/{claim_id}/status", headers=owner).json()
        assert (
            done["checks"]["risk"] == "COMPLETED" and done["outcome"] == "PROVISIONAL"
        )  # authority and identity still pending


def case_of(rt_url: str, tenant: str, claim_id: str) -> str:
    from conftest import ctx
    from sqlalchemy import text
    from zid_db import tenant_transaction

    async def go() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return str(
                (
                    await c.execute(
                        text("SELECT case_id FROM verify.screening_case WHERE kyb_case_id = :k"),
                        {"k": uuid.UUID(claim_id)},
                    )
                ).scalar_one()
            )

    return asyncio.run(go())


def test_the_reviewer_routes_are_refused_to_an_owner_without_the_protected_action(
    rt_url: str,
) -> None:
    o = onboard(rt_url)  # no review grants
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app) as api:
        case = str(uuid.uuid4())
        for method, path, body in (
            ("get", f"/v1/reviews/screening-cases/{case}", None),
            (
                "post",
                f"/v1/reviews/screening-cases/{case}/rulings",
                {"outcome": "VERIFIED", "rationale": "A written reason here."},
            ),
            (
                "post",
                f"/v1/reviews/kyb-cases/{case}/rulings",
                {"outcome": "VERIFIED", "rationale": "A written reason here."},
            ),
        ):
            r = getattr(api, method)(
                path, headers=o.bearer(o.owner), **({"json": body} if body else {})
            )
            assert r.status_code in (403, 401), (
                path,
                r.status_code,
            )  # being the OWNER is not being a reviewer
