"""Release gates G0 to G3 over HTTP, operator plane (WF06 section 22, ENG-20, QA-053; CR-113), against PostgreSQL with real operator sessions.

The role someone signs a gate in is the capability they hold; a role signs a gate once; a scope is GO only with every named role signed, no open P0 defect and no hold; a defect or a hold stops it at once; a hold
is released by someone other than its placer; a signature is withdrawn only by its signer; and every step is a protected audit record."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.modules.screening.domain import release_gates as rg
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
BASE = "/v1/screening/gates"
ROLES = (
    "founder",
    "legal",
    "dpo",
    "aml",
    "engineering",
    "security",
    "qa",
    "cto",
    "ciso",
    "product",
)


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
            operator_tenant_id=o.tenant, operator_client_ids=(o.client,),
        ),
        keyring=o.ring,
    )  # fmt: skip


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = (*ROLES, "opener", "holder", "releaser", "stranger")
    who = {n: uid() for n in names} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    two = CAPABILITIES[capability].two_person
    approvers = (who["grant_1"], who["grant_2"]) if two else (who["grant_1"],)
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _audit(owner_url: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code FROM evidence.audit_record WHERE tenant_id IS NULL AND event_type LIKE 'screening.gate.%' ORDER BY created_at, audit_id"
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    return asyncio.run(read())


def test_a_module_is_go_only_with_every_gate_signed_no_open_defect_and_no_hold(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    market, module = "GB", "SANCTIONS"
    url = f"{BASE}/{market}/{module}"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert api.get(url).status_code == 401
        assert call("GET", url, "stranger").status_code == 403
        for role in ROLES:
            _grant(api, o, who, role, f"screening.gate.sign_{role}")
        _grant(api, o, who, "opener", "screening.gate.defect")
        _grant(api, o, who, "holder", "screening.gate.hold")
        _grant(api, o, who, "holder", "screening.gate.release_hold")
        _grant(api, o, who, "releaser", "screening.gate.release_hold")

        start = call("GET", url, "legal").json()
        assert (
            start["go"] is False
            and len(start["blockers"]) == 4
            and start["missing"]["G3"] == sorted(rg.GATES["G3"])
        )

        def sign(person: str, gate: str, **kw: Any) -> Any:
            return call(
                "POST",
                f"{url}:sign",
                person,
                json={"gate": gate, "evidence_ref": f"ev-{person}-{gate}", **kw},
            )

        # a role signs only the gates that name it, once, and a person signs in one role
        wrong = sign("legal", "G1")
        assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "ROLE_NOT_FOR_GATE"
        assert sign("legal", "G0").status_code == 200
        again = sign("legal", "G0")
        assert again.status_code == 409 and again.json()["error"]["code"] == "ROLE_ALREADY_SIGNED"
        assert (
            call(
                "POST",
                f"{url}:sign",
                "legal",
                json={"gate": "G0", "evidence_ref": "", "valid_days": 30},
            ).status_code
            == 422
        )
        assert (
            call(
                "POST", f"{url}:sign", "stranger", json={"gate": "G0", "evidence_ref": "e"}
            ).status_code
            == 403
        )

        for gate, roles in rg.GATES.items():
            for role in sorted(roles):
                if (gate, role) == ("G0", "LEGAL"):
                    continue
                r = sign(role.lower(), gate)
                assert r.status_code == 200, (gate, role, r.text)
        done = call("GET", url, "legal").json()
        assert done["go"] is True and done["blockers"] == []

        # another market or module is not touched by these signatures
        assert call("GET", f"{BASE}/DE/{module}", "legal").json()["go"] is False
        assert call("GET", f"{BASE}/{market}/PEP_RCA", "legal").json()["go"] is False

        # an open P0 defect stops every gate, whatever is signed; closing it restores it
        d = call("POST", f"{url}/defects", "opener", json={"ref": "SEC-1001 cross-tenant read"})
        assert (
            d.status_code == 201
            and d.json()["go"] is False
            and d.json()["blockers"] == ["OPEN_P0_DEFECTS:1"]
        )
        rid = d.json()["record_id"]
        assert call("POST", f"{BASE}/records/{rid}:close", "opener").json()["go"] is True
        assert call("POST", f"{BASE}/records/{rid}:close", "opener").status_code == 409

        # a kill switch is one person's act and is released by another
        h = call("POST", f"{url}/holds", "holder", json={"ref": "provider incident in the region"})
        assert (
            h.status_code == 201
            and h.json()["go"] is False
            and h.json()["blockers"][0].startswith("HELD:")
        )
        hid = h.json()["record_id"]
        own = call("POST", f"{BASE}/records/{hid}:release", "holder")
        assert (
            own.status_code == 403
            and own.json()["error"]["code"] == "HOLD_RELEASED_BY_ITS_OWN_PLACER"
        )
        assert call("POST", f"{BASE}/records/{hid}:release", "releaser").json()["go"] is True

        # only the signer withdraws a signature
        signatures = [r for r in _signoff_ids(owner_url) if r[1] == "DPO" and r[2] == "G3"]
        sid = signatures[0][0]
        other = call("POST", f"{BASE}/records/{sid}:rescind", "legal")
        assert (
            other.status_code == 403 and other.json()["error"]["code"] == "ONLY_THE_SIGNER_RESCINDS"
        )
        gone = call("POST", f"{BASE}/records/{sid}:rescind", "dpo")
        assert (
            gone.status_code == 200
            and gone.json()["go"] is False
            and gone.json()["missing"]["G3"] == ["DPO"]
        )
        assert call("GET", f"{BASE}/{market}/BANANA", "legal").status_code == 422

    kinds = {(e["event_type"], e["outcome"]) for e in _audit(owner_url)}
    for expected in (
        ("screening.gate.signed", "SUCCESS"),
        ("screening.gate.defect_opened", "SUCCESS"),
        ("screening.gate.defect_closed", "SUCCESS"),
        ("screening.gate.hold_placed", "SUCCESS"),
        ("screening.gate.hold_released", "SUCCESS"),
        ("screening.gate.rescinded", "SUCCESS"),
        ("screening.gate.refused", "DENIED"),
    ):
        assert expected in kinds, expected


def _signoff_ids(owner_url: str) -> list[tuple[str, str, str]]:
    async def read() -> list[tuple[str, str, str]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT record_id, role, gate FROM verify.screening_gate_record WHERE kind = 'SIGNOFF' AND market = 'GB' AND module = 'SANCTIONS' AND closed_at IS NULL"
                    )
                )
            ).all()
        return [(str(r.record_id), r.role, r.gate) for r in rows]

    return asyncio.run(read())
