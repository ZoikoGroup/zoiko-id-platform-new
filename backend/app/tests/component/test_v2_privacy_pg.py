"""/v2 privacy control plane over HTTP against PostgreSQL (ZID-PRI-004): default-deny purpose decisions, notice acknowledgments, erasure jobs that are
complete only on signed receipts, default-deny transfers with subprocessor-change blocking, DPIA packages and release gates, reviews by another person,
breach clocks that never guess a deadline, and KPIs measured from the records."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.privacy.domain import control as rules
from zid_platform.settings import Settings

ACTIONS = (
    "privacy.read",
    "privacy.rights.manage",
    "privacy.rights.fulfill",
    "privacy.processing_activity.manage",
)
RESOURCES = (
    "purpose_decision:new", "transfer_decision:new", "erasure_job:*", "erasure_job:new", "processor:*", "subprocessor_change:*", "dpia_package:*",
    "dpia_package:new", "decision_review:*", "breach_case:*", "breach_case:new", "privacy_kpi:*",
)  # fmt: skip


@pytest.fixture(autouse=True)
def runtime() -> Any:
    configure_runtime(development_runtime("uk-1"))

    async def dispose(conn: Any, entry: Any, action: str) -> None:
        return None

    async def export(conn: Any, entry: Any) -> Any:
        return {"ref": entry.object_ref}

    privacy.register_store("dsr.records", disposer=dispose, exporter=export)
    yield
    configure_runtime(None)


def api_for(rt_url: str, o: Any, **state: Any) -> TestClient:
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    for k, v in state.items():
        setattr(app.state, k, v)
    return TestClient(app)


def code(r: Any) -> str:
    return str(r.json().get("code"))


def test_decisions_notices_transfers_and_provider_controls(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    owner, second = o.bearer(o.owner), o.bearer(o.second)

    async def setup() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await registry.register_processor(
                c, o.tenant, processor_id="veriff", name="Veriff", role="PROCESSOR", data_categories=["D1", "D2", "D3"], locations=["GB", "IE"],
                created_by=o.owner, transfer_mechanism="uk_idta", deletion_sla_days=30, contract_ref="DPA-1",
            )  # fmt: skip
            await c.execute(
                text(
                    "UPDATE privacy.processor SET status = 'APPROVED', approved_by = :a WHERE processor_id = 'veriff'"
                ),
                {"a": uuid.UUID(o.second)},
            )
            await registry.publish_notice(
                c,
                o.tenant,
                code="BIOMETRIC_NOTICE",
                audience="SUBJECT",
                content="We use your camera for liveness.",
                created_by=o.owner,
                approved_by=o.second,
            )
        return "BIOMETRIC_NOTICE@1"

    notice = asyncio.run(setup())
    with api_for(rt_url, o) as api:
        base = {
            "activity_code": "BIOMETRIC_CHECK",
            "purpose_code": "IDENTITY_PROOFING",
            "data_category": "D3",
            "country": "GB",
        }
        assert api.post("/v2/privacy/purpose-decisions", json=base).status_code == 401
        # default deny: no activity, no purpose, no notice -> denied with reasons and the non-biometric alternatives
        r = api.post("/v2/privacy/purpose-decisions", headers=owner, json=base)
        assert r.status_code == 200, r.text
        d = r.json()
        assert (
            d["allowed"] is False
            and "NOTICE_REQUIRED_FOR_SENSITIVE_DATA" in d["reason_codes"]
            and len(d["reason_codes"]) >= 2
        )
        assert {"ASSISTED_DOCUMENT_REVIEW", "IN_PERSON_REVIEW", "MANUAL_REVIEW"} <= set(
            d["alternative_modes"]
        ) and d["decision_id"].startswith("pdec_")
        assert "face" not in r.text.lower().replace("no face", "")
        r2 = api.post(
            "/v2/privacy/authorization-evaluations",
            headers=owner,
            json={
                **base,
                "notice_version": notice,
                "provider_service_ref": "veriff:liveness",
                "jurisdiction_pack_id": "uk-gdpr",
            },
        )
        assert r2.status_code == 200 and r2.json()["effective_policy_version"].startswith(
            "uk-gdpr@"
        )
        bad = api.post(
            "/v2/privacy/purpose-decisions",
            headers=owner,
            json={**base, "notice_version": "NOPE@9"},
        )
        assert (
            "PRIVACY_NOTICE_UNKNOWN" in bad.json()["reason_codes"] or bad.json()["allowed"] is False
        )
        assert (
            api.post(
                "/v2/privacy/purpose-decisions", headers=owner, json={**base, "data_category": "D9"}
            ).status_code
            == 422
        )

        # notice acknowledgment: exact version, idempotent, and the alternatives are always on offer
        a = api.post(
            "/v2/verification-sessions/ver_1/notices/acknowledgments",
            headers=owner,
            json={"notice_version": notice, "language": "en"},
        )
        assert a.status_code == 201, a.text
        again = api.post(
            "/v2/verification-sessions/ver_1/notices/acknowledgments",
            headers=owner,
            json={"notice_version": notice, "language": "en"},
        )
        assert again.json()["ack_id"] == a.json()["ack_id"], "a repeat returns the first record"
        assert (
            api.post(
                "/v2/verification-sessions/ver_1/notices/acknowledgments",
                headers=owner,
                json={"notice_version": "NOPE@9", "language": "en"},
            ).status_code
            == 422
        )
        alt = api.get("/v2/verification-sessions/ver_1/alternatives", headers=owner).json()
        assert (
            alt["notices_acknowledged"] == [notice]
            and all(m["biometric"] is False for m in alt["alternatives"])
            and len(alt["alternatives"]) >= 2
        )

        # transfers: default deny with named reasons; a subprocessor change blocks egress until someone else decides it
        t = {
            "processor_id": "veriff",
            "purpose_code": "IDENTITY_PROOFING",
            "data_category": "D3",
            "origin_country": "GB",
            "destination_country": "IE",
            "jurisdiction_pack_id": "uk-gdpr",
        }
        ok = api.post("/v2/privacy/transfer-decisions", headers=owner, json=t).json()
        assert ok["allowed"] is True and ok["mechanism"] == "uk_idta", ok
        us = api.post(
            "/v2/privacy/transfer-decisions", headers=owner, json={**t, "destination_country": "US"}
        ).json()
        assert (
            us["allowed"] is False and "DESTINATION_NOT_AN_APPROVED_LOCATION" in us["reason_codes"]
        )
        remote = api.post(
            "/v2/privacy/transfer-decisions",
            headers=owner,
            json={**t, "remote_access_countries": ["IN"]},
        ).json()
        assert "REMOTE_ACCESS_FROM_IN_NOT_APPROVED" in remote["reason_codes"]
        assert (
            "PROCESSOR_NOT_FOUND"
            in api.post(
                "/v2/privacy/transfer-decisions", headers=owner, json={**t, "processor_id": "ghost"}
            ).json()["reason_codes"]
        )
        ctrl = api.get("/v2/privacy/provider-controls/veriff?country=GB", headers=owner).json()
        assert ctrl["usable"] is True and ctrl["transfer_mechanism"] == "uk_idta"
        assert api.get("/v2/privacy/provider-controls/veriff?country=US", headers=owner).json()[
            "reasons"
        ] == ["COUNTRY_NOT_AN_APPROVED_LOCATION"]
        assert (
            api.get("/v2/privacy/provider-controls/ghost?country=GB", headers=owner).json()["known"]
            is False
        )

        ch = api.post(
            "/v2/privacy/subprocessor-changes",
            headers=owner,
            json={
                "processor_id": "veriff",
                "kind": "SUBPROCESSOR_ADDED",
                "detail": "AWS eu-west-1",
                "notified_at": datetime.now(UTC).isoformat(),
            },
        )
        assert ch.status_code == 201, ch.text
        blocked = api.post("/v2/privacy/transfer-decisions", headers=owner, json=t).json()
        assert (
            blocked["allowed"] is False and "SUBPROCESSOR_CHANGE_PENDING" in blocked["reason_codes"]
        )
        assert (
            "SUBPROCESSOR_CHANGE_PENDING"
            in api.get("/v2/privacy/provider-controls/veriff?country=GB", headers=owner).json()[
                "reasons"
            ]
        )
        own = api.post(
            f"/v2/privacy/subprocessor-changes/{ch.json()['change_id']}:decide",
            headers=owner,
            json={"approve": True},
        )
        assert own.status_code >= 400, "the person who raised the change cannot decide it"
        done = api.post(
            f"/v2/privacy/subprocessor-changes/{ch.json()['change_id']}:decide",
            headers=second,
            json={"approve": True},
        )
        assert done.status_code == 200 and done.json()["state"] == "APPROVED", done.text
        assert (
            api.post("/v2/privacy/transfer-decisions", headers=owner, json=t).json()["allowed"]
            is True
        )

        # KPIs are measured from the records
        k = api.get("/v2/privacy/kpis", headers=owner).json()
        assert (
            k["purpose_denial"]["decisions"] >= 3
            and k["purpose_denial"]["denied"] >= 2
            and 0 < k["purpose_denial"]["rate"] <= 1
        )
        assert k["dpia_currency"]["rate"] is None, "no use case, no rate: never a made-up zero"


def test_erasure_dpia_reviews_and_breaches(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    subject = str(uuid.uuid4())
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()

    async def setup() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await registry.register_processor(c, o.tenant, processor_id="veriff", name="Veriff", role="PROCESSOR", data_categories=["D1"], locations=["GB"],
                                              created_by=o.owner, deletion_sla_days=30, contract_ref="DPA-1")  # fmt: skip
            await c.execute(
                text(
                    "UPDATE privacy.processor SET status = 'APPROVED', approved_by = :a WHERE processor_id = 'veriff'"
                ),
                {"a": uuid.UUID(o.second)},
            )
            for store, ref, proc in (
                ("dsr.records", "rec-1", None),
                ("veriff.sessions", "sess-1", "veriff"),
            ):
                await privacy.register_personal_data(c, o.tenant, subject_principal_id=subject, store_name=store, object_type="profile", object_ref=ref,
                                                     purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None, processor_id=proc)  # fmt: skip

    asyncio.run(setup())
    clocks = {"EU": 72, "UK": "COUNSEL"}
    with api_for(rt_url, o, breach_clock_config=clocks) as api:
        # ------------------------------------------------------------------ erasure: complete only on verified receipts
        job = api.post(
            "/v2/privacy/erasure-jobs",
            headers=owner,
            json={
                "subject_ref": subject,
                "reason": "subject request",
                "legal_basis": "GDPR Art. 17",
            },
        )
        assert job.status_code == 201, job.text
        j = job.json()
        kinds = {(t["target_kind"], t["target_ref"]): t for t in j["tasks"]}
        assert kinds[("FIRST_PARTY_STORE", "dsr.records:rec-1")]["state"] == "ACKNOWLEDGED"
        prov = kinds[("PROVIDER", "veriff")]
        assert prov["state"] == "PENDING" and prov["due_at"], (
            "a provider task waits for the provider's signed receipt"
        )
        backup = kinds[("BACKUP", "cell-backups")]
        assert j["state"] == "PARTIAL"
        proof = api.get(f"/v2/privacy/erasure-jobs/{j['job_id']}/proof", headers=owner).json()
        assert proof["complete"] is False and len(proof["outstanding"]) == 2, (
            "never claimed complete early"
        )

        payload = rules.receipt_payload(prov["task_id"], j["job_id"], "2026-11-11T10:00:00Z", 4)
        receipt = {
            "job_id": j["job_id"],
            "task_id": prov["task_id"],
            "deleted_at": "2026-11-11T10:00:00Z",
            "object_count": 4,
            "signature": base64.b64encode(key.sign(payload)).decode(),
        }
        assert (
            api.post(
                "/v2/providers/veriff/deletion-receipts", headers=owner, json=receipt
            ).status_code
            == 409
        ), "no receipt key is registered yet"
        assert (
            api.post(
                "/v2/privacy/processors/veriff/receipt-key",
                headers=owner,
                json={"public_key": "not-a-key"},
            ).status_code
            == 422
        )
        assert (
            api.post(
                "/v2/privacy/processors/veriff/receipt-key", headers=owner, json={"public_key": pub}
            ).status_code
            == 200
        )
        forged = {
            **receipt,
            "signature": base64.b64encode(Ed25519PrivateKey.generate().sign(payload)).decode(),
        }
        assert (
            api.post(
                "/v2/providers/veriff/deletion-receipts", headers=owner, json=forged
            ).status_code
            == 403
        ), "a receipt signed by anyone else changes nothing"
        tampered = {**receipt, "object_count": 5}
        assert (
            api.post(
                "/v2/providers/veriff/deletion-receipts", headers=owner, json=tampered
            ).status_code
            == 403
        )
        assert (
            api.get(f"/v2/privacy/erasure-jobs/{j['job_id']}", headers=owner).json()["state"]
            == "PARTIAL"
        )
        good = api.post("/v2/providers/veriff/deletion-receipts", headers=owner, json=receipt)
        assert good.status_code == 200 and good.json()["state"] == "PARTIAL", good.text
        assert (
            api.post(
                "/v2/providers/veriff/deletion-receipts", headers=owner, json=receipt
            ).status_code
            == 409
        ), "a task is acknowledged once"
        # the backup task is attested by an operator, and only then is the job complete
        assert (
            api.post(
                f"/v2/privacy/erasure-jobs/{j['job_id']}/tasks/{backup['task_id']}:attest",
                headers=owner,
                json={"statement": "x"},
            ).status_code
            == 422
        )
        fin = api.post(
            f"/v2/privacy/erasure-jobs/{j['job_id']}/tasks/{backup['task_id']}:attest",
            headers=owner,
            json={"statement": "Backup lifecycle expired; restore journal reconciled."},
        )
        assert (
            fin.status_code == 200
            and fin.json()["state"] == "COMPLETE"
            and fin.json()["completed_at"]
        ), fin.text
        done = api.get(f"/v2/privacy/erasure-jobs/{j['job_id']}/proof", headers=owner).json()
        assert (
            done["complete"] is True
            and done["outstanding"] == []
            and len(done["receipts_commitment"]) == 64
        )
        assert api.get(f"/v2/privacy/erasure-jobs/{uuid.uuid4()}", headers=owner).status_code == 404

        # ------------------------------------------------------------------ DPIA package, gates and release
        pk = api.post(
            "/v2/privacy/dpia-packages",
            headers=owner,
            json={
                "market": "GB",
                "product": "id-verification",
                "version": "1.0",
                "controller": "Acme Ltd",
                "purpose_code": "IDENTITY_PROOFING",
            },
        )
        assert pk.status_code == 201, pk.text
        pid = pk.json()["package_id"]
        rg = api.get("/v2/privacy/release-gates/GB/id-verification/1.0", headers=owner).json()
        assert (
            rg["ready"] is False
            and "DPIA_NOT_APPROVED" in rg["blockers"]
            and "G3_SECURITY_CISO_NOT_SIGNED" in rg["blockers"]
            and rg["gates"]["G3"]["name"] == "Biometric security"
        )
        assert api.get("/v2/privacy/release-gates/GB/nothing/1", headers=owner).json()[
            "blockers"
        ] == ["NO_DPIA_PACKAGE"]
        risk = {
            "risk_id": "R01",
            "title": "Biometric leak",
            "mitigation": "Envelope encryption",
            "owner": "ciso",
            "inherent_likelihood": 5,
            "inherent_impact": 5,
            "residual_likelihood": 3,
            "residual_impact": 5,
        }
        assert (
            api.put(
                f"/v2/privacy/dpia-packages/{pid}/risks",
                headers=owner,
                json={"risks": [{**risk, "owner": ""}]},
            ).status_code
            == 422
        )
        withrisk = api.put(
            f"/v2/privacy/dpia-packages/{pid}/risks", headers=owner, json={"risks": [risk]}
        ).json()
        assert (
            withrisk["risks"][0]["residual_band"] == "HIGH"
            and "R01_RESIDUAL_HIGH_UNCONSULTED" in withrisk["release"]["blockers"]
        )
        # the author cannot sign a gate; a role signs once; sign-offs need evidence
        assert (
            api.post(
                f"/v2/privacy/dpia-packages/{pid}/gate-signoffs",
                headers=owner,
                json={"gate": "G3", "role": "SECURITY_CISO", "evidence_uri": "https://e/1"},
            ).status_code
            == 403
        )
        s1 = api.post(
            f"/v2/privacy/dpia-packages/{pid}/gate-signoffs",
            headers=second,
            json={"gate": "G3", "role": "SECURITY_CISO", "evidence_uri": "https://e/pentest"},
        )
        assert (
            s1.status_code == 200
            and "G3_SECURITY_CISO_NOT_SIGNED" not in s1.json()["release"]["blockers"]
        )
        assert (
            api.post(
                f"/v2/privacy/dpia-packages/{pid}/gate-signoffs",
                headers=second,
                json={"gate": "G3", "role": "SECURITY_CISO", "evidence_uri": "https://e/2"},
            ).status_code
            == 409
        )
        assert (
            api.post(
                f"/v2/privacy/dpia-packages/{pid}/gate-signoffs",
                headers=second,
                json={"gate": "G1", "role": "LEGAL", "evidence_uri": "https://e/3"},
            ).status_code
            == 409
        ), "one person cannot be two accountable parties"
        assert (
            api.post(
                f"/v2/privacy/dpia-packages/{pid}/gate-signoffs",
                headers=second,
                json={"gate": "G3", "role": "LEGAL", "evidence_uri": "https://e/3"},
            ).status_code
            == 422
        )
        # approval is refused while a no-release criterion stands, and the author cannot decide their own package
        blocked = api.post(
            f"/v2/privacy/dpia-packages/{pid}/decisions",
            headers=second,
            json={"approve": True, "rationale": "looks fine"},
        )
        assert blocked.status_code == 409 and code(blocked) == "release_blocked", blocked.text
        assert (
            api.post(
                f"/v2/privacy/dpia-packages/{pid}/decisions",
                headers=owner,
                json={"approve": False, "rationale": "own work"},
            ).status_code
            == 403
        )
        rej = api.post(
            f"/v2/privacy/dpia-packages/{pid}/decisions",
            headers=second,
            json={"approve": False, "rationale": "Residual high risk, no consultation."},
        )
        assert rej.status_code == 200 and rej.json()["state"] == "REJECTED"
        assert (
            api.put(
                f"/v2/privacy/dpia-packages/{pid}/risks", headers=owner, json={"risks": [risk]}
            ).status_code
            == 409
        ), "a decided package is not edited"

        # ------------------------------------------------------------------ appeals and human review
        ap = api.post(
            "/v2/decisions/dec_1/appeals",
            headers=owner,
            json={"statement": "The record is not mine."},
        )
        assert ap.status_code == 201 and ap.json()["state"] == "OPEN", ap.text
        hr = api.post(
            "/v2/decisions/dec_1/human-reviews",
            headers=owner,
            json={"statement": "Please have a person look."},
        )
        assert hr.status_code == 201
        rid = ap.json()["review_id"]
        assert (
            api.post(
                f"/v2/privacy/reviews/{rid}/decisions",
                headers=owner,
                json={"outcome": "UPHELD", "rationale": "self review"},
            ).status_code
            == 403
        ), "the requester never reviews their own appeal"
        assert (
            api.post(
                f"/v2/privacy/reviews/{rid}/decisions",
                headers=second,
                json={"outcome": "CORRECTED", "rationale": "Wrong record linked."},
            ).status_code
            == 422
        ), "a correction names its correction"
        dec = api.post(
            f"/v2/privacy/reviews/{rid}/decisions",
            headers=second,
            json={"outcome": "OVERTURNED", "rationale": "Checked the source: a different person."},
        )
        assert dec.status_code == 200 and dec.json()["state"] == "OVERTURNED", dec.text
        assert (
            api.post(
                f"/v2/privacy/reviews/{rid}/decisions",
                headers=second,
                json={"outcome": "UPHELD", "rationale": "again"},
            ).status_code
            == 409
        )
        assert (
            api.get(f"/v2/privacy/reviews/{rid}", headers=owner)
            .json()["rationale"]
            .startswith("Checked")
        )

        # ------------------------------------------------------------------ breach clocks: set by counsel, never guessed
        detected = datetime.now(UTC) - timedelta(hours=80)
        br = api.post(
            "/v2/privacy/breaches",
            headers=owner,
            json={
                "summary": "Provider bucket exposed",
                "severity": "HIGH",
                "jurisdictions": ["EU", "UK", "BR"],
                "detected_at": detected.isoformat(),
                "subjects_estimate": 120,
            },
        )
        assert br.status_code == 201, br.text
        by = {c["jurisdiction"]: c for c in br.json()["clocks"]}
        assert (
            by["EU"]["state"] == "OVERDUE"
            and by["UK"]["state"] == "NOT_SET_BY_COUNSEL"
            and by["BR"]["state"] == "NOT_SET_BY_COUNSEL"
        )
        bid = br.json()["breach_id"]
        assert api.post(f"/v2/privacy/breaches/{bid}/close", headers=owner).status_code == 409, (
            "an overdue clock is not closed quietly"
        )
        assert (
            api.post(
                f"/v2/privacy/breaches/{bid}/notifications",
                headers=owner,
                json={
                    "jurisdiction": "FR",
                    "notified_at": datetime.now(UTC).isoformat(),
                    "reference": "x1",
                },
            ).status_code
            == 422
        )
        n = api.post(
            f"/v2/privacy/breaches/{bid}/notifications",
            headers=owner,
            json={
                "jurisdiction": "EU",
                "notified_at": datetime.now(UTC).isoformat(),
                "reference": "EDPB-1",
            },
        )
        assert (
            n.status_code == 200
            and {c["jurisdiction"]: c["state"] for c in n.json()["clocks"]}["EU"] == "NOTIFIED"
        )
        assert (
            api.post(
                f"/v2/privacy/breaches/{bid}/notifications",
                headers=owner,
                json={
                    "jurisdiction": "EU",
                    "notified_at": datetime.now(UTC).isoformat(),
                    "reference": "again",
                },
            ).status_code
            == 409
        )
        closed = api.post(f"/v2/privacy/breaches/{bid}/close", headers=owner)
        assert closed.status_code == 200 and closed.json()["state"] == "CLOSED", closed.text

        # ------------------------------------------------------------------ KPIs now have something to measure
        k = api.get("/v2/privacy/kpis", headers=owner).json()
        assert (
            k["erasure"]["jobs"] == 1
            and k["erasure"]["complete"] == 1
            and k["erasure"]["completion_rate"] == 1.0
        )
        assert (
            k["dpia_currency"]["active_use_cases"] == 1
            and k["dpia_currency"]["with_current_approved_dpia"] == 0
        )
        assert (
            k["breaches"] == {"opened": 1, "open": 0}
            and "fraud false positives" in k["not_measured"]
        )
