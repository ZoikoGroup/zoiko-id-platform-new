"""ZID-INT-002 (Document 02 v2.0): the /v2 facade through the full app on PostgreSQL.

T02  an idempotent POST repeated creates one check (and one receipt); the same key with a changed body is idempotency_conflict (409)
T03  an unsupported market is 422 unsupported_market and never VERIFIED
     every decision carries request_id, correlation_id, pack id and version, a manifest reference, checked_at, valid_until, dimensions
     RFC 9457 problems on /v2; the canonical envelope stays on /v1
T01  another tenant cannot read a result
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
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
ACTIONS = [
    "credential.claim.verify",
    "credential.read",
    "credential.eligibility.evaluate",
    "credential.outreach.manage",
    "evidence.read",
    "evidence.verify",
]
RESOURCES = [
    "credential-claim:*",
    "eligibility:*",
    "engagement:*",
    "outreach:*",
    "decision_receipt:*",
]
BODY = {
    "credential_type": "professional_license",
    "issuer_claim": {"name": "Sample State Licensing Board", "country": "US"},
    "credential_claim": {"license_ref": "L-1", "activity_scope": "legal.advice"},
    "jurisdiction": {"country": "US", "subdivision": "US-CA"},
}


def uid() -> str:
    return str(uuid.uuid4())


def test_the_v2_facade_is_idempotent_problem_detailed_and_never_collapses_a_result(
    rt_url: str,
) -> None:
    tenant, other_tenant, admin, outsider = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-v2"))

    async def seed(t: str, people: list[str], grant: bool) -> dict[str, str]:
        out: dict[str, str] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t)) as c:
            for p in people:
                await create_principal(
                    c, Principal(p, t, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            if grant:
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

    sessions = asyncio.run(seed(tenant, [admin], True))
    other_sessions = asyncio.run(seed(other_tenant, [outsider], True))

    def bearer(sub: str, t: str, sid: str) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=t, client_id="admin-console",
            now=datetime.now(UTC), session_id=sid, scope=frozenset(),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    mine = bearer(admin, tenant, sessions[admin])
    theirs = bearer(outsider, other_tenant, other_sessions[outsider])

    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as http:
        key = {"Idempotency-Key": "idem-key-0001"}

        # RFC 9457 on /v2: a missing Idempotency-Key is an invalid request
        no_key = http.post("/v2/credentials/verify", headers=mine, json=BODY)
        assert no_key.status_code == 422
        assert no_key.headers["content-type"].startswith("application/problem+json")
        assert no_key.json()["code"] == "invalid_request" and no_key.json()["trace_id"].startswith(
            "req_"
        )

        first = http.post("/v2/credentials/verify", headers={**mine, **key}, json=BODY)
        assert first.status_code == 201, first.text
        out: dict[str, Any] = first.json()
        for field in ("request_id", "correlation_id", "policy_pack_id", "policy_pack_version",
                      "provenance_manifest_ref", "checked_at", "valid_until", "dimensions", "assertion"):  # fmt: skip
            assert field in out, field
        dim = out["dimensions"][0]
        assert dim["dimension"] == "professional_license"
        assert dim["outcome"] in {
            "VERIFIED", "VERIFIED_LIMITED", "NOT_VERIFIED", "INDETERMINATE", "PENDING", "REVOKED", "EXPIRED", "UNAVAILABLE",
        }  # fmt: skip
        assert "decision" not in dim and "PASS" not in str(
            out["dimensions"]
        )  # one of eight statuses, never a global PASS
        assert out["assertion"]["signature"], "the five facts stay in the signed assertion"

        # T02: the same key and body replays the stored response: one check, one receipt
        again = http.post("/v2/credentials/verify", headers={**mine, **key}, json=BODY)
        assert again.status_code == 201 and again.json()["credential_ref"] == out["credential_ref"]

        async def counts() -> tuple[int, int]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                checks = (
                    await c.execute(text("SELECT count(*) FROM cred.claim_check"))
                ).scalar_one()
                receipts = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM evidence.decision_receipt WHERE decision_type = 'CREDENTIAL'"
                        )
                    )
                ).scalar_one()
                return checks, receipts

        assert asyncio.run(counts()) == (1, 1)

        # the same key with a changed body: 409 idempotency_conflict, and still nothing new
        changed = {
            **BODY,
            "credential_claim": {"license_ref": "L-2", "activity_scope": "legal.advice"},
        }
        clash = http.post("/v2/credentials/verify", headers={**mine, **key}, json=changed)
        assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"
        assert asyncio.run(counts()) == (1, 1)

        # T03: an unsupported market is 422 unsupported_market, never VERIFIED
        nowhere = {**BODY, "jurisdiction": {"country": "ZZ"}}
        refused = http.post(
            "/v2/credentials/verify",
            headers={**mine, "Idempotency-Key": "idem-key-0002"},
            json=nowhere,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "unsupported_market" and "VERIFIED" not in refused.text
        # an unknown credential type fails closed before any source is asked
        bad = http.post(
            "/v2/credentials/verify", headers={**mine, "Idempotency-Key": "idem-key-0003"},
            json={**BODY, "credential_type": "wizard_licence"},
        )  # fmt: skip
        assert bad.status_code == 422 and bad.json()["code"] == "invalid_request"

        # reading back gives the same outcome; another tenant sees nothing (T01)
        read = http.get(f"/v2/credentials/{out['credential_ref']}", headers=mine)
        assert read.status_code == 200 and read.json()["dimensions"][0]["outcome"] == dim["outcome"]
        assert (
            http.get(f"/v2/credentials/{out['credential_ref']}", headers=theirs).status_code == 404
        )
        assert http.get("/v2/credentials/not-a-reference", headers=mine).status_code == 422

        # discovery is honest: readiness is stated and nothing is called VERIFIED_IN_CODE for a market without a source
        caps = http.get("/v2/capabilities", headers=mine).json()
        assert caps["capabilities"] and all(
            c["readiness"] == "PROVIDER_DEPENDENT" for c in caps["capabilities"]
        )
        cov = http.get("/v2/coverage?country=ZZ", headers=mine).json()
        assert cov["packs"] == [] and set(cov["source_coverage"].values()) == {"NO_COVERAGE"}

        # an eligibility request with no policy for the scope makes no decision at all
        none = http.post(
            "/v2/eligibility/evaluate", headers={**mine, "Idempotency-Key": "idem-key-0004"},
            json={"jurisdiction": {"country": "ZZ"}, "profession_code": "x", "service_code": "y"},
        )  # fmt: skip
        assert none.status_code == 404 and none.headers["content-type"].startswith(
            "application/problem+json"
        )

        # /v1 keeps the canonical envelope (a major version never changes silently)
        v1 = http.get(f"/v1/credentials/assertions/{uid()}", headers=mine)
        assert v1.status_code == 404 and "error" in v1.json() and "code" not in v1.json()


@contextmanager
def world(rt_url: str) -> Iterator[tuple[TestClient, dict[str, str], str, str]]:
    """One tenant with an administrator who may run checks and decide eligibility, behind the full app."""
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-v2-world"))

    async def seed() -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(c, tenant, [admin], ACTIONS, RESOURCES)
            principal, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid, tenant, admin, now - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                ),
            )  # fmt: skip
        return sid

    sid = asyncio.run(seed())
    tok = issue_access_token(
        ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="admin-console",
        now=datetime.now(UTC), session_id=sid, scope=frozenset(),
    )  # fmt: skip
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"),
        keyring=ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as http:
        yield http, {"Authorization": f"Bearer {tok}"}, tenant, admin


ELIGIBILITY = {
    "jurisdiction": {"country": "US", "subdivision": "US-CA"},
    "profession_code": "LAW",
    "service_code": "legal.advice",
}
VOCAB = {"ELIGIBLE", "NOT_ELIGIBLE", "REVIEW_REQUIRED", "UNKNOWN"}


def test_the_v2_eligibility_routes_decide_from_the_check_and_can_be_read_back(rt_url: str) -> None:
    with world(rt_url) as (http, mine, _tenant, _admin):
        # before any check: no evidence is never an approval
        none = http.post(
            "/v2/eligibility/evaluate",
            headers={**mine, "Idempotency-Key": "elig-key-0001"},
            json=ELIGIBILITY,
        )
        assert none.status_code == 201, none.text
        assert none.json()["engagement_eligibility"] in {
            "NOT_ELIGIBLE",
            "UNKNOWN",
            "REVIEW_REQUIRED",
        }
        assert none.json()["engagement_eligibility"] != "ELIGIBLE"

        # a verified licence listing the service, then the decision is recomputed from it (never cached)
        check = http.post(
            "/v2/credentials/verify",
            headers={**mine, "Idempotency-Key": "cred-key-0001"},
            json=BODY,
        )
        assert (
            check.status_code == 201 and check.json()["dimensions"][0]["outcome"] == "VERIFIED"
        ), check.text
        done = http.post(
            "/v2/eligibility/evaluate",
            headers={**mine, "Idempotency-Key": "elig-key-0002"},
            json=ELIGIBILITY,
        )
        out = done.json()
        assert done.status_code == 201 and out["engagement_eligibility"] in VOCAB, done.text
        for field in ("request_id", "correlation_id", "policy_pack_id", "policy_pack_version",
                      "provenance_manifest_ref", "checked_at", "engagement_eligibility", "reason_codes"):  # fmt: skip
            assert field in out, field
        assert out["policy_pack_id"] == "sample-us-ca-law"

        # replay: the same key and body returns the same decision and makes no second one
        again = http.post(
            "/v2/eligibility/evaluate",
            headers={**mine, "Idempotency-Key": "elig-key-0002"},
            json=ELIGIBILITY,
        )
        assert again.json()["eligibility_ref"] == out["eligibility_ref"]

        read = http.get(f"/v2/eligibility/{out['eligibility_ref']}", headers=mine)
        assert (
            read.status_code == 200
            and read.json()["engagement_eligibility"] == out["engagement_eligibility"]
        )
        assert http.get(f"/v2/eligibility/elig_{uid()}", headers=mine).status_code == 404


def test_the_v2_zoikorum_check_is_signed_per_service_and_is_never_eligible_without_identity(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _tenant, admin):
        assert (
            http.post(
                "/v2/credentials/verify",
                headers={**mine, "Idempotency-Key": "cred-key-0002"},
                json=BODY,
            ).status_code
            == 201
        )
        body = {
            "professional_subject_ref": f"sub_{admin}",
            "engagement_ref": "eng_1",
            "profession_code": "LAW",
            "requested_services": ["legal.advice"],
            "jurisdiction": {"country": "US", "subdivision": "US-CA"},
            "client_ref": "org_1",
            "minimum_insurance_policy_ref": "ins_requirement_1",
        }
        r = http.post(
            "/v2/zoikorum/engagement-checks",
            headers={**mine, "Idempotency-Key": "eng-key-0001"},
            json=body,
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["consumer"] == "zoikorum" and out["signature"].count(".") == 2  # a compact JWS
        # identity was never verified and insurance was never checked: the engagement cannot be ELIGIBLE
        assert out["engagement_eligibility"] in VOCAB - {"ELIGIBLE"}
        svc = out["services"][0]
        assert svc["service"] == "legal.advice" and svc["dimensions"]["ELIGIBILITY"] == "VERIFIED"
        assert svc["dimensions"]["IDENTITY"] != "VERIFIED"

        # replay returns the same signed answer; a changed body under the same key is a conflict
        same = http.post(
            "/v2/zoikorum/engagement-checks",
            headers={**mine, "Idempotency-Key": "eng-key-0001"},
            json=body,
        )
        assert same.json()["signature"] == out["signature"]
        clash = http.post(
            "/v2/zoikorum/engagement-checks",
            headers={**mine, "Idempotency-Key": "eng-key-0001"},
            json={**body, "engagement_ref": "eng_2"},
        )
        assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"

        # several services: the overall answer is the worst of them, and an unknown service has no policy at all
        multi = http.post(
            "/v2/zoikorum/engagement-checks",
            headers={**mine, "Idempotency-Key": "eng-key-0002"},
            json={**body, "requested_services": ["legal.advice", "tax.advice"]},
        )
        assert multi.status_code == 404 and multi.headers["content-type"].startswith(
            "application/problem+json"
        )


def test_the_v2_issuer_check_shows_its_nonce_once_and_the_manifest_carries_commitments_only(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, tenant, _admin):
        h = {**mine, "Idempotency-Key": "ichk-key-0001"}
        body = {"issuer_ref": "sample-university", "credential_type": "academic_degree"}
        first = http.post("/v2/issuer-checks", headers=h, json=body)
        assert first.status_code == 201, first.text
        out = first.json()
        assert (
            out["state"] == "PENDING"
            and out["nonce"]
            and out["issuer_check_ref"].startswith("ichk_")
        )

        # replay: the same question, and the nonce is not given out a second time
        again = http.post("/v2/issuer-checks", headers=h, json=body)
        assert again.json()["issuer_check_ref"] == out["issuer_check_ref"]
        assert "nonce" not in again.json() and again.json()["nonce_shown_once"] is True

        async def stored_text() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                rows = await c.execute(
                    text("SELECT response_body::text FROM platform.idempotency_record")
                )
                return " ".join(r[0] or "" for r in rows)

        assert out["nonce"] not in asyncio.run(stored_text()), (
            "the stored replay record must never hold the nonce"
        )

        read = http.get(f"/v2/issuer-checks/{out['issuer_check_ref']}", headers=mine)
        assert (
            read.status_code == 200
            and read.json()["state"] == "PENDING"
            and read.json()["outcome"] is None
        )

        # an unknown or lookalike issuer is never contacted; an unsupported credential type fails closed
        for bad, code in (
            ({**body, "issuer_ref": "sample-universlty"}, "issuer_not_found"),
            ({**body, "credential_type": "wizard_licence"}, "invalid_request"),
        ):
            r = http.post(
                "/v2/issuer-checks", headers={**mine, "Idempotency-Key": "ichk-key-0002"}, json=bad
            )
            assert r.json()["code"] == code and r.headers["content-type"].startswith(
                "application/problem+json"
            )
        assert http.get(f"/v2/issuer-checks/ichk_{uid()}", headers=mine).status_code == 404

        # the evidence manifest a credential answer points at: commitments and a verification result
        check = http.post(
            "/v2/credentials/verify",
            headers={**mine, "Idempotency-Key": "cred-key-0009"},
            json=BODY,
        ).json()
        m = http.get(f"/v2/evidence/{check['provenance_manifest_ref']}/manifest", headers=mine)
        assert m.status_code == 200, m.text
        mj = m.json()
        assert (
            mj["receipt"]["decision_type"] == "CREDENTIAL"
            and mj["manifest_ref"] == check["provenance_manifest_ref"]
        )
        assert "L-1" not in m.text and "legal.advice" not in m.text, (
            "no claimed value in a manifest"
        )
        assert mj["verification"]
        assert http.get(f"/v2/evidence/manifest_{uid()}/manifest", headers=mine).status_code == 404
        assert http.get("/v2/evidence/not-a-ref/manifest", headers=mine).status_code == 422
