"""The governed CSCA trust store through the full app on PostgreSQL (ZID-DTV-WF-01 section 13, O2; CR-107).

WF01 T05  an empty store: a chip is INDETERMINATE, never certified
WF01 T06  a list that is present but not approved is not trusted; a list that does not verify is rejected
WF01 T26  a roll back restores the earlier package and the right trust is used again
and: the stager can neither approve nor activate, the database keeps that rule too, a retired store trusts nothing, another tenant sees none of it.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography.hazmat.primitives.serialization import Encoding
from epassport_fixture import integer, make_chip, sod_cms
from fastapi.testclient import TestClient
from pdf_signing import NOW, der, ec_key, make_cert
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application import csca_master_list as ml
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
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
ACTIONS = ["evidence.trust.propose", "evidence.trust.approve", "evidence.read", "evidence.verify"]
RESOURCES = ["trust-store:*", "document:*"]
BASE = "/v2/trust-store/csca"


def uid() -> str:
    return str(uuid.uuid4())


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def list_for(csca_cert, *, anchor=None):
    """A master list signed under `anchor` that carries `csca_cert`."""
    anchor = anchor or make_cert(
        "Utopia ML anchor",
        ec_key(),
        ca=True,
        not_before=NOW - timedelta(days=3000),
        not_after=NOW + timedelta(days=3000),
    )
    signer = make_cert(
        "ML signer",
        ec_key(),
        anchor,
        not_before=NOW - timedelta(days=30),
        not_after=NOW + timedelta(days=300),
    )
    econtent = der(0x30, integer(0) + der(0x31, csca_cert.public_bytes(Encoding.DER)))
    return sod_cms(econtent, signer, content_type=ml.OID_MASTER_LIST, wrap=False), anchor


def stage_body(
    blob: bytes, anchor, *, fingerprints=None, uri="https://example.test/masterlist.ml"
) -> dict:
    fp = ml.fingerprint(anchor.cert)
    return {
        "master_list_base64": b64(blob),
        "anchors_base64": [b64(anchor.cert.public_bytes(Encoding.PEM))],
        "confirmed_anchor_sha256": fingerprints or [fp],
        "source_uri": uri,
        "source_note": "downloaded from the country's own publication",
    }


def chip_body(chip) -> dict:
    return {
        "sod_base64": b64(chip.sod),
        "data_groups": {str(k): b64(v) for k, v in chip.data_groups.items()},
    }


@pytest.fixture
def world(rt_url: str):
    tenant, other_tenant = uid(), uid()
    stager, approver, outsider = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-trust-store"))

    async def seed(t: str, people: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t)) as c:
            for p in people:
                await create_principal(
                    c, Principal(p, t, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, t, people, ACTIONS, RESOURCES)
            for p in people:
                sid, now = uid(), datetime.now(UTC)
                principal, _ = await load_principal(c, p)
                await create_session(
                    c,
                    Session(
                        sid, t, p, now - timedelta(seconds=30), "native:webauthn", 2,
                        principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                    ),
                )  # fmt: skip
                out[p] = sid
        return out

    sessions = asyncio.run(seed(tenant, [stager, approver]))
    other_sessions = asyncio.run(seed(other_tenant, [outsider]))

    def bearer(sub: str, t: str, sid: str) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=t, client_id="admin-console",
            now=datetime.now(UTC), session_id=sid, scope=frozenset(),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"),
        keyring=ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as http:
        yield {
            "http": http,
            "tenant": tenant,
            "stager": bearer(stager, tenant, sessions[stager]),
            "approver": bearer(approver, tenant, sessions[approver]),
            "outsider": bearer(outsider, other_tenant, other_sessions[outsider]),
            "stager_id": stager,
            "approver_id": approver,
            "rt_url": rt_url,
        }


def chip_status(w, who: str, chip) -> dict:
    r = w["http"].post("/v2/documents/chip-verify", headers=w[who], json=chip_body(chip))
    assert r.status_code == 200, r.text
    return r.json()


def test_the_lifecycle_needs_two_people_and_a_package_is_trusted_only_when_active(world) -> None:
    w = world
    http, a, b = w["http"], w["stager"], w["approver"]
    chip = make_chip()
    blob, anchor = list_for(chip.csca.cert)

    # T05: nothing configured, nothing governed: the chip is not certified
    before = http.get(f"{BASE}/active", headers=a).json()
    assert (
        before["governed"] is False
        and before["source"] == "CONFIG_FOLDER"
        and before["certificates"] == 0
    )
    assert chip_status(w, "stager", chip)["status"] == "INDETERMINATE"

    # the stager must have compared the anchors: wrong fingerprints are refused and nothing is stored
    bad = http.post(
        f"{BASE}/packages", headers=a, json=stage_body(blob, anchor, fingerprints=["0" * 64])
    )
    assert bad.status_code == 422 and bad.json()["code"] == "anchor_fingerprint_mismatch", bad.text
    assert http.get(f"{BASE}/packages", headers=a).json()["items"] == []

    staged = http.post(f"{BASE}/packages", headers=a, json=stage_body(blob, anchor))
    assert staged.status_code == 201, staged.text
    pkg = staged.json()
    pid = pkg["package_id"]
    assert pkg["state"] == "STAGED" and pkg["seq"] == 1 and pkg["staged_by"] == w["stager_id"]
    assert pkg["certificate_count"] == 0 and pkg["manifest_digest"] is None
    for secret in ("package_der", "anchor_pems", "BEGIN CERTIFICATE", "der_b64"):
        assert (
            secret not in staged.text
        )  # the list, the anchors and the certificates are never returned

    # a staged package is not a candidate and not trusted
    assert http.post(f"{BASE}/packages/{pid}:approve", headers=b).status_code == 409
    assert http.post(f"{BASE}/packages/{pid}:activate", headers=b).status_code == 409
    assert chip_status(w, "approver", chip)["status"] == "INDETERMINATE"
    staged_test = http.post(
        f"{BASE}/packages/{pid}:check-chip", headers=a, json=chip_body(chip)
    ).json()
    assert staged_test["status"] == "INDETERMINATE" and staged_test["certificates_used"] == 0

    # cryptographic validation reads the stored list against the stored anchors
    valid = http.post(f"{BASE}/packages/{pid}:validate", headers=a)
    assert valid.status_code == 200, valid.text
    v = valid.json()
    assert (
        v["state"] == "VALIDATED" and v["list_status"] == "VERIFIED" and v["certificate_count"] == 1
    )
    assert len(v["manifest_digest"]) == 64
    assert http.post(f"{BASE}/packages/{pid}:validate", headers=a).status_code == 409  # not twice

    # the fixture: the package works for a lab check, and is still not trusted by production
    lab = http.post(f"{BASE}/packages/{pid}:check-chip", headers=a, json=chip_body(chip)).json()
    assert lab["status"] == "VERIFIED_LIMITED" and lab["trusted_for_production"] is False
    assert (
        chip_status(w, "stager", chip)["status"] == "INDETERMINATE"
    )  # T06: present but not approved is not trusted

    # two-person rule
    own = http.post(f"{BASE}/packages/{pid}:approve", headers=a)
    assert own.status_code == 403 and own.json()["code"] == "self_approval"
    assert http.post(f"{BASE}/packages/{pid}:approve", headers=b).json()["state"] == "APPROVED"
    own_activate = http.post(f"{BASE}/packages/{pid}:activate", headers=a)
    assert own_activate.status_code == 403 and own_activate.json()["code"] == "self_activation"
    assert (
        chip_status(w, "approver", chip)["status"] == "INDETERMINATE"
    )  # approved is still not active

    active = http.post(f"{BASE}/packages/{pid}:activate", headers=b)
    assert active.status_code == 200 and active.json()["state"] == "ACTIVE", active.text
    now_trusted = chip_status(w, "approver", chip)
    assert now_trusted["status"] == "VERIFIED_LIMITED" and now_trusted["csca_configured"] is True
    assert (
        now_trusted["trust"]["source"] == "TRUST_STORE"
        and now_trusted["trust"]["package_id"] == pid
    )
    assert now_trusted["trust"]["manifest_digest"] == v["manifest_digest"]

    # another tenant sees none of it and is still ungoverned
    assert http.get(f"{BASE}/packages/{pid}", headers=w["outsider"]).status_code == 404
    assert http.get(f"{BASE}/packages", headers=w["outsider"]).json()["items"] == []
    assert http.get(f"{BASE}/active", headers=w["outsider"]).json()["governed"] is False
    assert chip_status(w, "outsider", chip)["status"] == "INDETERMINATE"


def test_activating_another_package_supersedes_and_a_rollback_restores_the_first(world) -> None:
    w = world
    http, a, b = w["http"], w["stager"], w["approver"]
    chip1, chip2 = make_chip(), make_chip()
    first, anchor1 = list_for(chip1.csca.cert)
    second, anchor2 = list_for(chip2.csca.cert)

    def promote(blob, anchor) -> str:
        pid = http.post(f"{BASE}/packages", headers=a, json=stage_body(blob, anchor)).json()[
            "package_id"
        ]
        assert (
            http.post(f"{BASE}/packages/{pid}:validate", headers=a).json()["state"] == "VALIDATED"
        )
        assert http.post(f"{BASE}/packages/{pid}:approve", headers=b).status_code == 200
        done = http.post(f"{BASE}/packages/{pid}:activate", headers=b)
        assert done.status_code == 200, done.text
        return pid

    p1 = promote(first, anchor1)
    p2 = promote(second, anchor2)
    states = {
        i["package_id"]: i["state"] for i in http.get(f"{BASE}/packages", headers=a).json()["items"]
    }
    assert states == {p1: "SUPERSEDED", p2: "ACTIVE"}
    assert chip_status(w, "stager", chip2)["status"] == "VERIFIED_LIMITED"
    assert (
        chip_status(w, "stager", chip1)["status"] == "INDETERMINATE"
    )  # only the active package is trusted

    # a rollback needs a reason
    assert http.post(f"{BASE}/rollback", headers=b, json={"reason": "  "}).status_code == 422
    back = http.post(
        f"{BASE}/rollback", headers=b, json={"reason": "the second list was withdrawn"}
    )
    assert (
        back.status_code == 200
        and back.json()["package_id"] == p1
        and back.json()["state"] == "ACTIVE"
    ), back.text
    states = {
        i["package_id"]: i["state"] for i in http.get(f"{BASE}/packages", headers=a).json()["items"]
    }
    assert states == {p1: "ACTIVE", p2: "ROLLED_BACK"}
    ended = http.get(f"{BASE}/packages/{p2}", headers=a).json()
    assert ended["ended_reason"] == "the second list was withdrawn"
    assert (
        chip_status(w, "stager", chip1)["status"] == "VERIFIED_LIMITED"
    )  # T26: the right trust is used again
    assert chip_status(w, "stager", chip2)["status"] == "INDETERMINATE"
    # a rolled back package is never a rollback target, and there is nothing earlier left
    assert (
        http.post(
            f"{BASE}/rollback", headers=b, json={"reason": "again", "to_package_id": p2}
        ).status_code
        == 422
    )
    assert http.post(f"{BASE}/rollback", headers=b, json={"reason": "again"}).status_code == 409

    # retire: the store keeps governing, so nothing is certified until another package is activated
    assert http.post(f"{BASE}/retire", headers=b, json={"reason": "  "}).status_code == 422
    retired = http.post(f"{BASE}/retire", headers=b, json={"reason": "country list under review"})
    assert retired.status_code == 200 and retired.json()["state"] == "RETIRED"
    gone = chip_status(w, "stager", chip1)
    assert (
        gone["status"] == "INDETERMINATE"
        and gone["csca_configured"] is False
        and gone["trust"]["source"] == "TRUST_STORE"
    )
    assert http.get(f"{BASE}/active", headers=a).json()["governed"] is True
    assert (
        http.post(f"{BASE}/retire", headers=b, json={"reason": "nothing left"}).status_code == 409
    )


def test_a_list_that_does_not_verify_is_rejected_and_the_database_keeps_the_two_person_rule(
    world,
) -> None:
    w = world
    http, a, b = w["http"], w["stager"], w["approver"]
    chip = make_chip()
    blob, _real_anchor = list_for(chip.csca.cert)
    stranger = make_cert(
        "Some other anchor",
        ec_key(),
        ca=True,
        not_before=NOW - timedelta(days=3000),
        not_after=NOW + timedelta(days=3000),
    )
    pid = http.post(f"{BASE}/packages", headers=a, json=stage_body(blob, stranger)).json()[
        "package_id"
    ]
    done = http.post(f"{BASE}/packages/{pid}:validate", headers=a).json()
    assert done["state"] == "REJECTED" and done["list_status"] == "INDETERMINATE"
    assert done["certificate_count"] == 0 and done["list_reasons"]
    assert (
        http.post(f"{BASE}/packages/{pid}:approve", headers=b).status_code == 409
    )  # a list that did not verify is never approved

    # a person may reject a staged package, with a reason
    other = http.post(f"{BASE}/packages", headers=a, json=stage_body(blob, stranger)).json()[
        "package_id"
    ]
    assert (
        http.post(f"{BASE}/packages/{other}:reject", headers=b, json={"reason": ""}).status_code
        == 422
    )
    rej = http.post(f"{BASE}/packages/{other}:reject", headers=b, json={"reason": "wrong country"})
    assert (
        rej.status_code == 200
        and rej.json()["state"] == "REJECTED"
        and rej.json()["decision_reason"] == "wrong country"
    )

    # even a direct write cannot make the stager the approver
    async def forge() -> None:
        async with engine(w["rt_url"]) as eng, tenant_transaction(eng, ctx(w["tenant"])) as c:
            await c.execute(
                text(
                    "UPDATE evidence.trust_package SET decided_by = staged_by WHERE package_id = :i"
                ),
                {"i": uuid.UUID(pid)},
            )

    with pytest.raises(DBAPIError):
        asyncio.run(forge())

    # every change left an event the evidence module turns into an audit record: staged, rejected, rejected
    async def events() -> list[str]:
        async with engine(w["rt_url"]) as eng, tenant_transaction(eng, ctx(w["tenant"])) as c:
            rows = await c.execute(
                text(
                    "SELECT event_type FROM events.outbox_event WHERE aggregate_type = 'trust_package' ORDER BY created_at"
                )
            )
            return [r[0] for r in rows]

    names = asyncio.run(events())
    assert names.count("com.zoiko.id.evidence.trust_package.staged.v1") == 2
    assert names.count("com.zoiko.id.evidence.trust_package.rejected.v1") == 2
