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
ACTIONS = ["credential.claim.verify", "credential.read", "credential.eligibility.evaluate"]
RESOURCES = ["credential-claim:*", "eligibility:*"]
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
