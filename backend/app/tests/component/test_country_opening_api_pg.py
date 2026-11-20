"""Opening a country for sign-up from the Operator Console (CR-127), against PostgreSQL with real operator sessions.

A country opens only with a complete record (six approvals with references, a date, a known cell); each decision is a new append-only row with a
protected audit record; the decision in force changes what the registry says, and closing shuts it again.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.main import create_app
from zid_platform.modules.onboarding.application import registration as signup
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.modules.verification import ports as v
from zid_platform.modules.verification.infrastructure import country_registry
from zid_platform.settings import Settings

URL = "/v1/regulatory/country-openings"
CAPABILITY = "regulatory.country_opening.record"
COUNTRY = "CA"  # listed in the registry file with DISCOVERY only: closed for sign-up until an operator opens it
APPROVALS = (
    "legal_assessment",
    "supplier_coverage",
    "privacy_and_data",
    "security_and_resilience",
    "local_support",
    "signoff",
)


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
            operator_tenant_id=o.tenant,
            operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )


def _row(view: dict[str, Any], country: str, capability: str = "REGISTRATION") -> dict[str, Any]:
    """One feature of one country: its file status, what is in force and the recorded decision."""
    row = next(c for c in view["countries"] if c["country"] == country)
    return next(f for f in row["features"] if f["capability"] == capability)


def _audit(owner_url: str, *like: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, target_ref FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND (target_ref LIKE ANY(:p)) ORDER BY created_at, audit_id"
                    ),
                    {"p": list(like)},
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


@pytest.fixture(autouse=True)
def _fresh_registry():
    country_registry.registry.cache_clear()  # no decision from another test, and none left behind
    yield
    country_registry.registry.cache_clear()


def test_a_country_is_opened_then_closed_by_an_operator(rt_url: str, owner_url: str) -> None:
    person, approver_1, approver_2 = uid(), uid(), uid()
    o = operators(
        rt_url,
        {
            person: {OperatorRole.REQUESTER},
            approver_1: {OperatorRole.APPROVER},
            approver_2: {OperatorRole.APPROVER},
        },
    )
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(person), **kw)

        # no token, no grant
        assert api.get(URL).status_code == 401
        denied = call("GET", URL)
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        activate_grant(api, o, person, (approver_1, approver_2), capability=CAPABILITY, resource_scope="regulatory:*")

        view = call("GET", URL).json()
        assert [a["key"] for a in view["approvals"]] == list(APPROVALS)
        assert [f["capability"] for f in view["features"]] == [
            "REGISTRATION",
            "INDIVIDUAL_VERIFICATION",
            "BUSINESS_VERIFICATION",
            "CREDENTIAL_ISSUANCE",
            "ENTERPRISE_USE",
        ]
        assert view["cells"], "the screen needs the cells this deployment knows"
        cell = view["cells"][0]["cell_id"]
        row = _row(view, COUNTRY)
        assert row["in_force"] == "DISABLED" and row["opening"] is None

        refs = {a: f"REF-{a}" for a in APPROVALS}
        effective = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        good = {"action": "OPEN", "effective_from": effective, "approvals": refs, "home_cells": [cell]}

        # an incomplete record, an unknown cell and an unlisted country are all refused, and nothing is stored
        missing = {**good, "approvals": {k: x for k, x in refs.items() if k != "signoff"}}
        r = call("PUT", f"{URL}/{COUNTRY}", json=missing)
        assert r.status_code == 422 and r.json()["error"]["code"] == "COUNTRY_OPENING_INCOMPLETE"
        r = call("PUT", f"{URL}/{COUNTRY}", json={**good, "home_cells": ["no-such-cell"]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CELL_UNKNOWN"
        r = call("PUT", f"{URL}/ZZ", json=good)
        assert r.status_code == 404 and r.json()["error"]["code"] == "COUNTRY_NOT_LISTED"
        assert _row(call("GET", URL).json(), COUNTRY)["opening"] is None

        # a complete record opens it, in force at once in this process
        r = call("PUT", f"{URL}/{COUNTRY}", json=good)
        assert r.status_code == 200, r.text
        assert r.json()["version"] == 1 and r.json()["status"] == "OPEN" and r.json()["home_cells"] == [cell]
        row = _row(call("GET", URL).json(), COUNTRY)
        assert row["in_force"] == "ENABLED" and row["opening"]["approvals"] == refs
        state = v.availability(v.registry(), COUNTRY, v.Capability.REGISTRATION, now=datetime.now(UTC), environment="production")
        assert state is v.Availability.ENABLED  # open in a deployed environment, where the file's sandbox never counts
        # organization claims are their own switch: still closed until they are opened too
        claims = v.availability(v.registry(), COUNTRY, v.Capability.BUSINESS_VERIFICATION, now=datetime.now(UTC), environment="production")
        assert claims is v.Availability.DISABLED
        other = v.availability(v.registry(), COUNTRY, v.Capability.INDIVIDUAL_VERIFICATION, now=datetime.now(UTC), environment="production")
        assert other is v.Availability.DISABLED  # nothing but REGISTRATION was opened
        # and sign-up itself follows the decision: open, held by the chosen cell
        here = signup.Env("t", cell, "v1", "production", None, {})
        assert signup.registration_open(here, COUNTRY, datetime.now(UTC))
        assert signup.route_country(here, COUNTRY).state == "HERE"

        # organization claims open with the same six approvals and no home cell, and leave sign-up as it was
        claims_in = {"capability": "BUSINESS_VERIFICATION", "action": "OPEN", "effective_from": effective, "approvals": refs}
        r = call("PUT", f"{URL}/{COUNTRY}", json=claims_in)
        assert r.status_code == 200, r.text
        assert r.json()["capability"] == "BUSINESS_VERIFICATION" and r.json()["version"] == 1 and r.json()["home_cells"] == []
        view = call("GET", URL).json()
        assert _row(view, COUNTRY, "BUSINESS_VERIFICATION")["in_force"] == "ENABLED"
        assert _row(view, COUNTRY, "REGISTRATION")["in_force"] == "ENABLED"
        assert _row(view, COUNTRY, "CREDENTIAL_ISSUANCE")["in_force"] == "DISABLED"
        claims = v.availability(v.registry(), COUNTRY, v.Capability.BUSINESS_VERIFICATION, now=datetime.now(UTC), environment="production")
        assert claims is v.Availability.ENABLED
        # a restart forgets what this process had loaded; the next request on ANY route (here an unrelated public one) brings the decisions back,
        # so a gate such as the organization search sees the opened feature without anyone opening sign-up or the operator page first
        country_registry.registry.cache_clear()
        assert v.availability(v.registry(), COUNTRY, v.Capability.BUSINESS_VERIFICATION, now=datetime.now(UTC), environment="production") is v.Availability.DISABLED
        assert api.get("/v1/countries").status_code == 200
        reloaded = v.availability(v.registry(), COUNTRY, v.Capability.BUSINESS_VERIFICATION, now=datetime.now(UTC), environment="production")
        assert reloaded is v.Availability.ENABLED
        # a capability that cannot be opened here is refused
        r = call("PUT", f"{URL}/{COUNTRY}", json={**claims_in, "capability": "DISCOVERY"})
        assert r.status_code == 422  # not one of the five features

        # closing is a new record
        r = call("PUT", f"{URL}/{COUNTRY}", json={"action": "CLOSE", "reason": "paused for a review"})
        assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["status"] == "CLOSED"
        row = _row(call("GET", URL).json(), COUNTRY)
        assert row["in_force"] == "DISABLED" and row["opening"]["status"] == "CLOSED"
        assert _row(call("GET", URL).json(), COUNTRY, "BUSINESS_VERIFICATION")["in_force"] == "ENABLED"  # closing sign-up left claims as they were
        assert not signup.registration_open(here, COUNTRY, datetime.now(UTC))

    # the table keeps both decisions and refuses to change either
    async def rows() -> list[Any]:
        async with engine(owner_url) as eng, eng.connect() as c:
            return list((await c.execute(text("SELECT version, status FROM verify.country_opening WHERE country = :c AND capability = 'REGISTRATION' ORDER BY version"), {"c": COUNTRY})).all())

    assert [(r.version, r.status) for r in asyncio.run(rows())] == [(1, "OPEN"), (2, "CLOSED")]

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE verify.country_opening SET status = 'OPEN' WHERE country = :c AND version = 2"), {"c": COUNTRY})

    with pytest.raises(DBAPIError):
        asyncio.run(tamper())

    # every change is audited, and the refusals too
    events = _audit(owner_url, f"{COUNTRY}:%", f"country:{COUNTRY}")
    assert [e["outcome"] for e in events if e["event_type"] == "regulatory.country_opening.recorded"] == ["SUCCESS", "SUCCESS", "SUCCESS"]
    assert sum(1 for e in events if e["event_type"] == "regulatory.country_opening.refused") == 2  # the incomplete record and the unknown cell
    assert "REF-signoff" not in json.dumps(events)  # the audit carries ids and codes, not the references
