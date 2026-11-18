"""Screening requests over HTTP for a tenant (WF06 S01 to S03; CR-113), against PostgreSQL with a real owner session.

Screening is off until a signed market policy names the tenant; a refusal names every reason in words and stores nothing; the identity a screening rests on comes from the platform, not the caller; the
caller reads a minimal status that never names a list, a candidate or a reason; a person without a tenant role is refused."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.screening.application import screening as lists_service
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.infrastructure.market_policies import parse
from zid_platform.settings import Settings

NOW = datetime.now(UTC)
SDN = b'1,"DOE, Norman","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1950"\n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK1,Pavel,Nobody,Primary Name\n"
EU = b'<export><sanctionEntity logicalId="1"><subjectType code="person"/><nameAlias wholeName="Quentin Imaginary"/></sanctionEntity></export>'
UN = (
    b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><DATAID>900</DATAID><FIRST_NAME>Eric</FIRST_NAME><SECOND_NAME>Badege</SECOND_NAME>"
    b"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>"
)
BASE = "/v1/screening"


def pack(*tenants: str) -> dict[str, Any]:
    return parse(
        {
            "markets": {
                "GB": {
                    "version": "gb-1",
                    "purposes": ["KYB"],
                    "regions": ["uk-1"],
                    "subject_types": ["BUSINESS", "UBO"],
                    "classes": {"SANCTIONS": "OFFICIAL"},
                    "legal_basis": "lawful-basis-ref-1",
                    "signed_by": ["legal-owner", "aml-owner"],
                    "valid_from": "2026-01-01T00:00:00Z",
                    "enabled_tenants": list(tenants),
                }
            }
        }
    )


def _load_lists(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            for list_id, files in (
                (ListId.OFAC_SDN, {"sdn": SDN}),
                (ListId.OFAC_NON_SDN, {"sdn": SDN}),
                (ListId.UK_SANCTIONS, {"file": UK}),
                (ListId.EU_CONSOLIDATED, {"file": EU}),
                (ListId.UN_SC, {"file": UN}),
            ):
                await lists_service.ingest_list(
                    c, list_id, files, fetched_at=NOW - timedelta(hours=1), source_ref="t", now=NOW
                )

    asyncio.run(run())


def _count(owner_url: str) -> int:
    async def run() -> int:
        async with engine(owner_url) as eng, eng.connect() as c:
            return int(
                (
                    await c.execute(text("SELECT count(*) FROM verify.screening_request"))
                ).scalar_one()
            )

    return asyncio.run(run())


def _case(subject: str, name: str = "Alice Wonderland", **kw: Any) -> dict[str, Any]:
    body = {
        "market": "GB",
        "purpose": "KYB",
        "subject_type": "UBO",
        "subject_ref": subject,
        "name": name,
    }
    body.update(kw)
    return body


def test_a_tenant_screens_a_subject_under_a_signed_policy_and_reads_a_minimal_status(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    _load_lists(rt_url)
    subject = str(uuid.uuid4())
    claims: dict[str, Any] = {"claim": ("IDENTITY_VERIFIED_FOR_SCOPE", NOW - timedelta(days=2))}

    async def lookup(
        conn: Any, tenant: str, subject_type: Any, subject_ref: str, now: datetime
    ) -> Any:
        return claims.get("claim") if subject_ref == subject else None

    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.screening_market_packs = pack(o.tenant)
    app.state.screening_identity_lookup = lookup
    with TestClient(app, base_url=ISS) as http:
        owner = o.bearer(o.owner)
        before = _count(owner_url)
        q = f"?market=GB&purpose=KYB&subject_type=UBO&subject_ref={subject}"
        assert http.get(f"{BASE}/eligibility{q}").status_code == 401

        # with no release gates signed for the market, nothing is available: the default fails closed, and says why
        closed = http.get(f"{BASE}/eligibility{q}", headers=owner).json()
        assert closed["eligible"] is False and closed["classes"][0]["available"] is False
        assert any(r["code"] == "RELEASE_GATES_NOT_SIGNED" for r in closed["classes"][0]["reasons"])
        app.state.screening_enforce_gates = (
            False  # the rest of this test is about the case flow, not the gates
        )

        # eligible: the class is available at the official tier, and the note says what this is not
        r = http.get(f"{BASE}/eligibility{q}", headers=owner)
        assert r.status_code == 200, r.text
        e = r.json()
        assert (
            e["eligible"] is True
            and e["classes"][0]["available"] is True
            and e["classes"][0]["tier"] == "OFFICIAL"
        )
        assert "not an AML clearance" in e["note"] and e["region"] == "uk-1"

        # not eligible: every reason is in words, and nothing is stored
        none = http.get(
            f"{BASE}/eligibility?market=DE&purpose=KYB&subject_type=UBO&subject_ref={subject}",
            headers=owner,
        ).json()
        assert (
            none["eligible"] is False
            and none["blocked"][0]["code"] == "NO_POLICY_FOR_MARKET"
            and none["blocked"][0]["text"].endswith(".")
        )
        stranger = http.get(
            f"{BASE}/eligibility?market=GB&purpose=KYB&subject_type=UBO&subject_ref={uuid.uuid4()}",
            headers=owner,
        ).json()
        assert stranger["pending_identity"] is True and any(
            b["code"] == "PENDING_IDENTITY" for b in stranger["blocked"]
        )
        refused = http.post(
            f"{BASE}/cases",
            json=_case(str(uuid.uuid4())),
            headers={**owner, "Idempotency-Key": "refused-key-1"},
        )
        assert (
            refused.status_code == 422
            and refused.json()["error"]["code"] == "SCREENING_NOT_ELIGIBLE"
        )
        assert any(d["issue"] == "PENDING_IDENTITY" for d in refused.json()["error"]["details"])
        assert _count(owner_url) == before  # a refusal stores nothing

        # open a case: a clear subject reads calibrated, and the same key returns the same case
        key = {"Idempotency-Key": "clear-key-0001"}
        r = http.post(f"{BASE}/cases", json=_case(subject), headers={**owner, **key})
        assert r.status_code == 201, r.text
        clear = r.json()
        assert (
            clear["finding"] == "No candidate was found in the checked sources"
            and clear["pep_rca"] == "Not enabled"
        )
        assert cov.unsafe_wording(clear) == []
        assert (
            http.post(f"{BASE}/cases", json=_case(subject), headers={**owner, **key}).json()[
                "request_id"
            ]
            == clear["request_id"]
        )
        assert http.post(f"{BASE}/cases", json=_case(subject)).status_code in (401, 422)
        assert (
            http.get(f"{BASE}/cases/{clear['request_id']}", headers=owner).json()["request_id"]
            == clear["request_id"]
        )

        # a subject that matches a list entry holds the case, and the caller learns nothing about why or where
        hit = http.post(
            f"{BASE}/cases",
            json=_case(subject, "Eric Badege"),
            headers={**owner, "Idempotency-Key": "hit-key-00001"},
        ).json()
        assert hit["finding"] == "Review pending"
        assert "Badege" not in str(hit) and "UN_SC" not in str(hit) and "score" not in str(hit)
        asked = http.post(
            f"{BASE}/cases/{hit['request_id']}:request-review",
            json={"note": "Please look at this again, the name is common."},
            headers=owner,
        )
        assert asked.status_code == 202 and "does not change the result" in asked.json()["status"]
        assert (
            http.post(
                f"{BASE}/cases/{hit['request_id']}:request-review",
                json={"note": "short"},
                headers=owner,
            ).status_code
            == 422
        )
        assert http.get(f"{BASE}/cases/{uuid.uuid4()}", headers=owner).status_code == 404
