"""Market release and attestation API, operator plane (ZID-RCG-003 RCG-G10, RCG-G02; CR-072, CR-073), against PostgreSQL with real operator sessions.

Opening, each evidence gate owner, each approver role and launching are separate operator capabilities. The author signs nothing, a person
holds one signing role, the release freezes the packs and supplier certifications it relies on, and a launched release is what opens the market.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import _draft
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
ATTESTORS = ("legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt")
APPROVERS = ("legal", "security_grc", "qa", "product_owner")
GATE_OWNER = {
    "RG-01": "legal",
    "RG-02": "regulatory_ops",
    "RG-03": "security",
    "RG-04": "privacy",
    "RG-05": "qa",
    "RG-06": "security",
    "RG-07": "vendor_mgmt",
}
BASE = "/v1/regulatory/market-releases"


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


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    names = (
        "author",
        "recorder",
        "launcher",
        *(f"att_{r}" for r in ATTESTORS),
        *(f"app_{r}" for r in APPROVERS),
    )
    who = {n: uid() for n in names} | {"approver_1": uid(), "approver_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in names}
    roles[who["approver_1"]] = {OperatorRole.APPROVER}
    roles[who["approver_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    one = capability in ("regulatory.release.author",) or capability.startswith(
        ("regulatory.release.attest_", "regulatory.attestation.")
    )
    approvers = (who["approver_1"],) if one else (who["approver_1"], who["approver_2"])
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _seed_pack(rt_url: str, product: str) -> None:
    async def go() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _publish(
                c,
                _draft(
                    country="GB",
                    product=product,
                    purpose="STANDARD",
                    subdivision=None,
                    profession=None,
                ),
            )

    asyncio.run(go())


def _audit(owner_url: str, *like: str) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code, target_ref, body FROM evidence.audit_record "
                        "WHERE tenant_id IS NULL AND (target_ref LIKE ANY(:p)) ORDER BY created_at, audit_id"
                    ),
                    {"p": list(like)},
                )
            ).all()
        out = [dict(r._mapping) for r in rows]
        for r in out:
            if isinstance(r["body"], str):
                r["body"] = json.loads(r["body"])
        return out

    return asyncio.run(read())


def _attestation(
    scope: dict[str, str], status: str = "ACTIVE", ago: timedelta = timedelta(hours=1)
) -> dict[str, Any]:
    return {
        **scope,
        "scheme_version": "2026.1",
        "certificate_ref": "CERT-1",
        "status": status,
        "valid_from": "2025-01-01",
        "register_url": "https://register.example.test/entry/1",
        "register_entry_id": "ENTRY-1",
        "cab": "CAB-X",
        "fetched_at": (datetime.now(UTC) - ago).isoformat(),
        "proof_hash": uuid.uuid4().hex + uuid.uuid4().hex,
        "evidence_locator": "vault:attestations/x",
    }


def test_a_release_goes_from_draft_to_launched_and_opens_its_market(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    product = f"P{uuid.uuid4().hex[:10].upper()}"
    market = {"country": "GB", "product": product, "purpose": "STANDARD"}
    rid = f"r-{uuid.uuid4().hex[:12]}"
    supplier = {
        "entity": f"PARTNER-{uuid.uuid4().hex[:8]}",
        "scheme": "UK_DVS",
        "country": "GB",
        "service_code": "RTR",
    }
    _seed_pack(rt_url, product)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        # nobody without a token, and nobody without the grant
        assert api.post(BASE, json={**market, "release_id": rid}).status_code == 401
        r = call("POST", BASE, "author", json={**market, "release_id": rid})
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        assert call("GET", BASE, "author").status_code == 403

        _grant(api, o, who, "author", "regulatory.release.author")
        _grant(api, o, who, "recorder", "regulatory.attestation.record")
        _grant(api, o, who, "launcher", "regulatory.release.launch")
        for role in ATTESTORS:
            _grant(api, o, who, f"att_{role}", f"regulatory.release.attest_{role}")
        for role in APPROVERS:
            _grant(api, o, who, f"app_{role}", f"regulatory.release.approve_{role}")

        # the market is closed before anything exists
        status = call("GET", "/v1/regulatory/market-status", "author", params=market).json()
        assert (status["open"], status["reason"]) == (False, "no_release")

        # an official-register attestation: recorded with its proof, then visible as certified
        r = call("POST", "/v1/regulatory/attestations", "recorder", json=_attestation(supplier))
        assert r.status_code == 201, r.text
        assert (r.json()["outcome"], r.json()["reason"]) == ("CERTIFIED", r.json()["reason"])
        assert (
            call(
                "POST", "/v1/regulatory/attestations", "author", json=_attestation(supplier)
            ).status_code
            == 403
        )
        r = call(
            "POST",
            "/v1/regulatory/attestations",
            "recorder",
            json=_attestation(supplier, ago=timedelta(days=2)),
        )  # an older reading than the one already recorded
        assert r.status_code == 409 and r.json()["error"]["code"] == "ATTESTATION_OUT_OF_ORDER"
        listed = call(
            "GET", "/v1/regulatory/attestations", "recorder", params={"entity": supplier["entity"]}
        ).json()["items"]
        assert [(i["service_code"], i["outcome"]) for i in listed] == [("RTR", "CERTIFIED")]

        r = call("POST", BASE, "author", json={**market, "release_id": rid})
        assert r.status_code == 201, r.text
        rel = r.json()
        assert (rel["version"], rel["status"], rel["author"]) == (1, "DRAFT", who["author"])
        assert len(rel["gates"]) == 7 and rel["release_hash"] is None
        assert (
            call("POST", BASE, "author", json={**market, "release_id": rid}).json()["error"]["code"]
            == "RELEASE_EXISTS"
        )

        one = f"{BASE}/{rid}/versions/1"
        # freezing needs all seven gates
        r = call("POST", f"{one}:freeze", "author", json={"suppliers": [supplier]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "RELEASE_NOT_FREEZABLE"

        # a gate is attested only by its owner role; the capability IS the role
        body = {"gate": "RG-01", "role": "legal", "status": "PASSED", "evidence_ref": "DOC-LEGAL-1"}
        r = call("POST", f"{one}:attest", "att_qa", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        r = call("POST", f"{one}:attest", "att_qa", json={**body, "role": "qa"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "RELEASE_ROLE_MISMATCH"
        assert call("POST", f"{one}:attest", "author", json=body).status_code == 403

        for gate, role in GATE_OWNER.items():
            r = call(
                "POST",
                f"{one}:attest",
                f"att_{role}",
                json={
                    "gate": gate,
                    "role": role,
                    "status": "PASSED",
                    "evidence_ref": f"DOC-{gate}",
                },
            )
            assert r.status_code == 200, r.text
        assert r.json()["blockers"] == []

        r = call("POST", f"{one}:freeze", "author", json={"suppliers": [supplier]})
        assert r.status_code == 200, r.text
        frozen = r.json()
        digest = frozen["release_hash"]
        assert frozen["status"] == "FROZEN" and digest and len(frozen["pack_refs"]) == 1
        assert [s["service_code"] for s in frozen["supplier_scopes"]] == ["RTR"]
        assert frozen["missing_approvals"] == list(APPROVERS)

        # an approval signs the hash it was shown, in the role of its own capability, and the author signs nothing
        def approve(person: str, role: str, signed: str) -> Any:
            return call(
                "POST", f"{one}:approve", person, json={"role": role, "signed_hash": signed}
            )

        assert approve("author", "legal", digest).status_code == 403
        r = approve("app_legal", "legal", "0" * 64)
        assert (
            r.status_code == 409 and r.json()["error"]["code"] == "RELEASE_APPROVAL_HASH_MISMATCH"
        )
        assert (
            approve("app_legal", "qa", digest).status_code == 403
        )  # legal's capability does not sign as qa
        assert (
            call("POST", f"{one}:launch", "launcher").json()["error"]["code"]
            == "RELEASE_NOT_RELEASEABLE"
        )

        for role in APPROVERS[:3]:
            r = approve(f"app_{role}", role, digest)
            assert r.status_code == 200, r.text
        assert r.json()["status"] == "FROZEN" and r.json()["missing_approvals"] == ["product_owner"]
        assert (
            call("POST", f"{one}:launch", "launcher").status_code == 409
        )  # three of four is not enough
        r = approve("app_product_owner", "product_owner", digest)
        assert r.status_code == 200 and r.json()["status"] == "RELEASEABLE"

        assert (
            call("POST", f"{one}:launch", "author").status_code == 403
        )  # the author cannot launch either
        r = call("POST", f"{one}:launch", "launcher")
        assert r.status_code == 200 and r.json()["status"] == "LAUNCHED", r.text

        status = call("GET", "/v1/regulatory/market-status", "author", params=market).json()
        assert (status["open"], status["reason"]) == (True, "release_launched")
        assert call("GET", one, "author").json()["status"] == "LAUNCHED"
        assert [
            x["release_id"]
            for x in call(
                "GET", BASE, "author", params={"country": "GB", "status": "LAUNCHED"}
            ).json()["items"]
            if x["release_id"] == rid
        ] == [rid]

        # losing the supplier's certification closes the market on its own: no release is edited
        r = call(
            "POST",
            "/v1/regulatory/attestations",
            "recorder",
            json=_attestation(supplier, "WITHDRAWN", timedelta(minutes=5)),
        )
        assert r.status_code == 201 and r.json()["outcome"] == "HOLD"
        status = call("GET", "/v1/regulatory/market-status", "author", params=market).json()
        assert status["open"] is False and status["reason"].startswith("supplier_hold:")

        # withdrawing closes the market at once; a revision is a new draft with nothing carried over
        r = call("POST", f"{one}:withdraw", "launcher")
        assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"
        assert (
            call("POST", f"{one}:withdraw", "launcher").json()["error"]["code"]
            == "RELEASE_NOT_WITHDRAWABLE"
        )
        r = call("POST", f"{BASE}/{rid}:revise", "author")
        assert r.status_code == 201
        revised = r.json()
        assert (revised["version"], revised["status"]) == (2, "DRAFT")
        assert all(g["status"] is None for g in revised["gates"]) and revised["approvals"] == {}
        status = call("GET", "/v1/regulatory/market-status", "author", params=market).json()
        assert status["open"] is False and status["reason"] == "withdrawn"

        assert (
            call("GET", f"{BASE}/{rid}/versions/9", "author").json()["error"]["code"]
            == "RELEASE_NOT_FOUND"
        )

    # ---- the audit trail: one record per change, a refusal record for every refusal, ids and hashes only
    records = _audit(owner_url, rid, f"{rid}:v%", f"{supplier['entity']}/%")
    events = [r["event_type"] for r in records]
    for expected in (
        "regulatory.release.opened",
        "regulatory.release.gate_attested",
        "regulatory.release.frozen",
        "regulatory.release.approved",
        "regulatory.release.launched",
        "regulatory.release.withdrawn",
        "regulatory.release.revised",
        "regulatory.attestation.recorded",
        "regulatory.release.refused",
    ):
        assert expected in events, expected
    assert events.count("regulatory.release.gate_attested") == 7
    assert events.count("regulatory.release.approved") == 4
    frozen_record = next(r for r in records if r["event_type"] == "regulatory.release.frozen")
    assert frozen_record["body"]["details"]["release_hash"] == digest
    refused = [r for r in records if r["event_type"] == "regulatory.release.refused"]
    assert {r["reason_code"] for r in refused} >= {
        "OPERATOR_CAPABILITY_REQUIRED",
        "RELEASE_NOT_FREEZABLE",
        "RELEASE_ROLE_MISMATCH",
        "RELEASE_APPROVAL_HASH_MISMATCH",
        "RELEASE_NOT_RELEASEABLE",
        "RELEASE_EXISTS",
    }
    assert all(r["outcome"] == "DENIED" for r in refused)
    everything = " ".join(str(r["body"]) for r in records)
    assert "DOC-RG-01" not in everything and "register.example.test" not in everything


def test_whoever_authors_a_release_cannot_also_hold_a_signing_role(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        _grant(api, o, who, "author", "regulatory.release.author")
        for capability in ("regulatory.release.approve_legal", "regulatory.release.attest_qa"):
            r = api.post(
                "/operator/access-requests",
                json={
                    "capability": capability,
                    "resource_scope": SCOPE,
                    "case_ref": "INC-OPS-1",
                    "reason": "operational change under review",
                    "duration_minutes": 60,
                },
                headers=o.bearer(who["author"]),
            )
            assert r.status_code in (403, 409, 422), r.text
            assert "SOD" in r.text, r.text


def test_a_bad_request_is_refused_before_it_reaches_the_release(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        _grant(api, o, who, "author", "regulatory.release.author")
        _grant(api, o, who, "recorder", "regulatory.attestation.record")

        def post(url: str, person: str, body: dict[str, Any]) -> int:
            return api.post(url, json=body, headers=o.bearer(who[person])).status_code

        market = {"country": "GB", "product": "IDV", "purpose": "STANDARD"}
        assert post(BASE, "author", {**market, "release_id": "Upper_Case"}) == 422
        assert post(BASE, "author", {**market, "release_id": "ok", "country": "gb"}) == 422
        assert post(BASE, "author", {**market, "release_id": "ok", "extra": 1}) == 422
        supplier = {"entity": "P", "scheme": "UK_DVS", "country": "GB", "service_code": "RTR"}
        insecure = _attestation(supplier) | {"register_url": "http://register.example.test/x"}
        assert post("/v1/regulatory/attestations", "recorder", insecure) == 422
        assert (
            post(
                "/v1/regulatory/attestations",
                "recorder",
                _attestation(supplier) | {"proof_hash": "nothex"},
            )
            == 422
        )
        naive = _attestation(supplier) | {"fetched_at": "2026-01-01T00:00:00"}  # no time zone
        assert post("/v1/regulatory/attestations", "recorder", naive) == 422
