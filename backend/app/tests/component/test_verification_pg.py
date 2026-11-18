"""Identity verification end to end (ZID-VERIFY-001 §3-5, §9-10, §13-17, §21-22; FR-VER-001..008).

Provider simulators behind an httpx MockTransport; providers call back with signed results.
Covered: profile coherence and activation, server-side provider choice with failover only inside
the equivalence group and a circuit breaker, signed + deduplicated + mapping-pinned callbacks,
deterministic per-claim decisions (values stored only as commitments), biometric jurisdiction
gating, fraud -> manual review with dual control and a fraud signal, reliance decisions, assurance
facts for policy and assertion revocation; quarantine never falls back to a weaker method.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, sign_jws
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
NAME, DOB = "Barbara Jensen", "1980-02-01"


def uid() -> str:
    return str(uuid.uuid4())


def test_verification_profiles_providers_review_reliance(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VERIFY-07 (a repeated provider callback is deduplicated), API-11 (invalid, stale, replayed and
    wrong-source or wrong-tenant callbacks are refused), VERIFY-10 (a manual review needs two named
    reviewers and is audited; nothing sets VERIFIED directly), VERIFY-19 VERIFY-20 (verification, including a
    biometric match, creates no authority) and VERIFY-17 (a re-verification supersedes and keeps history)."""
    tenant, alice, admin, admin2 = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    provider_key = LocalSigner("beta-cb-1")
    failing = {"acme": True}
    counter = {"n": 0}

    def simulator(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "acme.kyc.example" and failing["acme"]:
            return httpx.Response(503)
        counter["n"] += 1
        return httpx.Response(202, json={"transaction_ref": f"{host}-tx-{counter['n']}"})

    async def seed() -> dict[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin, admin2):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(
                c,
                tenant,
                [admin, admin2],
                [
                    "verification.profile.manage",
                    "verification.adapter.manage",
                    "verification.review.decide",
                    "verification.assertion.revoke",
                ],
                [
                    "verification_profile:*",
                    "verification_adapter:*",
                    "verification_review_case:*",
                    "verification_assertion:*",
                ],
            )
            sids = {}
            for p in (admin, admin2):
                sid, now = uid(), datetime.now(UTC)
                pr, _ = await load_principal(c, p)
                await create_session(
                    c,
                    Session(
                        sid,
                        tenant,
                        p,
                        now - timedelta(seconds=10),
                        "native:webauthn",
                        2,
                        pr.security_revision,
                        1,
                        SessionStatus.ACTIVE,
                        now + timedelta(hours=1),
                        now,
                    ),
                )
                sids[p] = sid
            return sids

    sids = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )
    app.state.verification_transport = httpx.MockTransport(simulator)

    def bearer(
        sub: str, *, sid: str | None = None, scope: str = "", client: str = "onboarding"
    ) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id=client,
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, adm2 = bearer(admin, sid=sids[admin]), bearer(admin2, sid=sids[admin2])
    app_client = bearer(
        admin, scope="verification:execute verification:read", client="onboarding-svc"
    )

    def result(
        ref: str,
        adapter: str = "beta",
        *,
        event_id: str | None = None,
        key: LocalSigner = provider_key,
        mapping_version: str = "v3",
        iat: int | None = None,
        **overrides: Any,
    ) -> bytes:
        body = {
            "status": "COMPLETED",
            "binding": "BIOMETRIC",
            "liveness_passed": True,
            "fraud_signals": [],
            "claims": [
                {
                    "claim": "legal_name",
                    "verified": True,
                    "value": NAME,
                    "method": "document+biometric",
                    "evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"],
                },
                {
                    "claim": "birthdate",
                    "verified": True,
                    "value": DOB,
                    "method": "document",
                    "evidence": ["GOVERNMENT_ID"],
                },
            ],
            "evidence_commitment": "doc-hash-1",
        }
        claims = {
            "iss": adapter,
            "mapping_version": mapping_version,
            "event_id": event_id or uid(),
            "transaction_ref": ref,
            "iat": int(time.time()) if iat is None else iat,
            "result": {**body, **overrides},
        }
        return sign_jws(key, claims, typ="verification-result+jwt").encode()

    with TestClient(app, base_url=ISS) as http:
        cb = f"/verification-provider-callbacks/{tenant}."

        # a profile must be coherent; activation is a protected R3 change
        bad = {
            "name": "uk-onboarding",
            "level": "IAL-Z2",
            "requirements": {
                "required_claims": ["legal_name"],
                "accepted_evidence": ["GOVERNMENT_ID"],
                "jurisdictions": ["GB"],
            },
        }
        assert (
            http.post("/verification-profiles", headers=adm, json=bad).status_code == 422
        )  # Z2 needs binding
        profile = {
            "name": "uk-onboarding",
            "level": "IAL-Z2",
            "requirements": {
                "required_claims": ["legal_name", "birthdate"],
                "accepted_evidence": ["GOVERNMENT_ID"],
                "binding_required": True,
                "liveness_required": True,
                "biometric_jurisdictions": ["GB"],
                "jurisdictions": ["GB", "FR"],
                "freshness_days": 365,
                "manual_review": True,
                "review_approvals": 2,
                "reliance_purposes": ["onboarding", "recovery"],
            },
        }
        assert (
            http.post("/verification-profiles", headers=bearer(alice), json=profile).status_code
            == 403
        )
        r = http.post("/verification-profiles", headers=adm, json=profile)
        assert r.status_code == 201, r.text
        pid = r.json()["profile_id"]
        assert (
            http.post(
                "/verifications",
                headers=app_client,
                json={
                    "profile_id": pid,
                    "subject_principal_id": alice,
                    "purpose": "onboarding",
                    "jurisdiction": "GB",
                },
            ).status_code
            == 422
        )  # still DRAFT
        assert http.post(f"/verification-profiles/{pid}:activate", headers=adm).status_code == 200

        jwks = {"keys": [provider_key.public_jwk()]}
        for adapter_id, host, group, evidence in (
            ("acme", "acme.kyc.example", "doc-bio-eu", ["GOVERNMENT_ID", "BIOMETRIC_BINDING"]),
            ("beta", "beta.kyc.example", "doc-bio-eu", ["GOVERNMENT_ID", "BIOMETRIC_BINDING"]),
            ("cheap", "cheap.db.example", "db-only", ["AUTHORITATIVE_SOURCE"]),
        ):
            r = http.post(
                "/verification-adapters",
                headers=adm,
                json={
                    "adapter_id": adapter_id,
                    "provider": adapter_id.title(),
                    "mapping_version": "v3",
                    "submit_endpoint": f"https://{host}/sessions",
                    "callback_jwks": jwks,
                    "callback_algorithms": ["ES256"],
                    "equivalence_group": group,
                    "supported_evidence": evidence,
                    "jurisdictions": ["GB", "FR"],
                },
            )
            assert r.status_code == 201, r.text

        def start(jurisdiction: str = "GB", level: str | None = None) -> Any:
            body = {
                "profile_id": pid,
                "subject_principal_id": alice,
                "purpose": "onboarding",
                "jurisdiction": jurisdiction,
            } | ({"required_level": level} if level else {})
            return http.post("/verifications", headers=app_client, json=body)

        assert start(level="IAL-Z3").status_code == 422  # never weaker than the caller's floor
        # EDR-001: a country the registry has not enabled is refused, truthfully, never "verified"
        r = start("NG")
        assert (
            r.status_code == 422 and r.json()["error"]["code"] == "COUNTRY_CAPABILITY_UNAVAILABLE"
        )
        listed = http.get("/v1/countries").json()["countries"]
        assert {c["country"]: c["capabilities"]["INDIVIDUAL_VERIFICATION"] for c in listed}[
            "NG"
        ] == "DISABLED"
        # plan feature "bulk verification": the same country gate and scope, one result per person, nobody's failure hides another's
        bulk = {
            "profile_id": pid,
            "subject_principal_ids": [uid(), uid()],
            "purpose": "onboarding",
            "jurisdiction": "GB",
        }
        assert (
            http.post(
                "/verifications:bulk", headers=app_client, json={**bulk, "jurisdiction": "NG"}
            ).status_code
            == 422
        )
        assert http.post("/verifications:bulk", headers=bearer(admin), json=bulk).status_code == 403
        assert (
            http.post(
                "/verifications:bulk",
                headers=app_client,
                json={**bulk, "subject_principal_ids": []},
            ).status_code
            == 422
        )
        many = http.post("/verifications:bulk", headers=app_client, json=bulk)
        assert many.status_code == 207, many.text
        got = many.json()
        assert (
            got["requested"] == 2 and len(got["items"]) == 2 and got["started"] + got["failed"] == 2
        )
        assert all(
            ("verification_id" in i) if i["started"] else ("code" in i) for i in got["items"]
        )
        assert (
            http.post(
                "/verifications",
                headers=bearer(admin),
                json={
                    "profile_id": pid,
                    "subject_principal_id": alice,
                    "purpose": "x",
                    "jurisdiction": "GB",
                },
            ).status_code
            == 403
        )

        # acme is down: failover inside the equivalence group to beta; never to the db-only group
        r = start()
        assert r.status_code == 201 and r.json()["status"] == "PENDING_EXTERNAL", r.text
        assert r.json()["adapter_id"] == "beta"
        vid = r.json()["verification_id"]
        ref = f"beta.kyc.example-tx-{counter['n']}"

        # signed, mapping-pinned, deduplicated callbacks
        assert (
            http.post(cb + "beta", content=result(ref, key=LocalSigner("rogue"))).status_code == 401
        )
        r = http.post(cb + "beta", content=result(ref, mapping_version="v2"))
        assert r.status_code == 400
        # API-11: a stale or future-dated signed result, a result for another source, and one sent to another
        # tenant's callback are all refused
        stale = http.post(cb + "beta", content=result(ref, iat=int(time.time()) - 1200))
        assert stale.status_code == 400 and "CALLBACK_STALE" in stale.text
        assert (
            http.post(cb + "beta", content=result(ref, iat=int(time.time()) + 1200)).status_code
            == 400
        )
        assert (
            http.post(cb + "acme", content=result(ref)).status_code != 202
        )  # signed by beta's key, sent as acme
        elsewhere = f"/verification-provider-callbacks/{uid()}.beta"
        assert (
            http.post(elsewhere, content=result(ref)).status_code != 202
        )  # another tenant has no such provider
        # EDR-001: a result for a country switched off since the session began is held (503, retryable) and
        # records nothing; once the country is open again the very same event is applied, not a duplicate
        from zid_platform.modules.verification.infrastructure.country_registry import parse

        with monkeypatch.context() as m:
            m.setattr("zid_platform.country_gate.registry", lambda: parse({"version": "off"}))
            held = http.post(cb + "beta", content=result(ref, event_id="evt-1"))
            assert held.status_code == 503 and "COUNTRY_CAPABILITY_UNAVAILABLE" in held.text
            assert http.get(f"/verifications/{vid}", headers=app_client).json()["status"] == (
                "PENDING_EXTERNAL"
            )
        first = result(ref, event_id="evt-1")
        r = http.post(cb + "beta", content=first)
        assert r.status_code == 202 and r.json()["outcome"] == "VERIFIED", r.text
        assert http.post(cb + "beta", content=first).json()["outcome"] == "DUPLICATE"
        assert (
            http.post(
                cb + "beta", content=result(ref, event_id="evt-1", status="FAILED")
            ).status_code
            == 409
        )
        assert http.post(cb + "beta", content=result(ref)).json()["outcome"] == "IGNORED_TERMINAL"
        assert (
            http.post(cb + "beta", content=result("unknown-tx")).json()["outcome"] == "QUARANTINED"
        )
        r = http.get(f"/verifications/{vid}", headers=app_client)
        assert r.json()["status"] == "VERIFIED" and r.json()["outcome"] == "VERIFIED"

        # biometric binding is not permitted in FR by this profile
        r = start("FR")
        ref_fr = f"beta.kyc.example-tx-{counter['n']}"
        assert http.post(cb + "beta", content=result(ref_fr)).json()["outcome"] == "FAILED"
        assert http.get(f"/verifications/{r.json()['verification_id']}", headers=app_client).json()[
            "outcome"
        ] == ("BIOMETRIC_NOT_PERMITTED")

        # VERIFY-17: a new verification supersedes the old one; the old session and its outcome stay on record
        again = http.post(f"/verifications/{vid}:reverify", headers=app_client)
        assert again.status_code == 201 and again.json()["supersedes"] == vid, again.text
        assert again.json()["verification_id"] != vid
        old = http.get(f"/verifications/{vid}", headers=app_client).json()
        assert old["status"] == "VERIFIED" and old["outcome"] == "VERIFIED"

        # fraud: never auto-pass; manual review with dual control, and a fraud signal
        r = start()
        vid3, ref3 = r.json()["verification_id"], f"beta.kyc.example-tx-{counter['n']}"
        assert (
            http.post(
                cb + "beta", content=result(ref3, fraud_signals=["DOC_TEMPLATE_MISMATCH"])
            ).json()["outcome"]
            == "MANUAL_REVIEW"
        )

        async def case_and_state() -> tuple[str, str | None]:
            async with engine(owner_url) as eng, eng.begin() as c:
                case = (
                    await c.execute(
                        text("SELECT case_id FROM verify.review_case WHERE session_id = :s"),
                        {"s": uuid.UUID(vid3)},
                    )
                ).scalar_one()
                state = (
                    await c.execute(
                        text("SELECT state FROM signals.security_state WHERE subject_id = :p"),
                        {"p": alice},
                    )
                ).scalar()
            return str(case), state

        case_id, state = asyncio.run(case_and_state())
        assert state == "ELEVATED_RISK"
        decision = {"decision": "APPROVE", "reason_codes": ["DOC_RECHECKED_BY_REVIEWER"]}
        assert (
            http.post(
                f"/verification-review-cases/{case_id}/decisions",
                headers=bearer(alice),
                json=decision,
            ).status_code
            == 403
        )
        r = http.post(f"/verification-review-cases/{case_id}/decisions", headers=adm, json=decision)
        assert r.json()["outcome"] == "PENDING_SECOND_REVIEWER", r.text
        assert (
            http.post(
                f"/verification-review-cases/{case_id}/decisions", headers=adm, json=decision
            ).status_code
            == 409
        )
        r = http.post(
            f"/verification-review-cases/{case_id}/decisions", headers=adm2, json=decision
        )
        assert r.json()["outcome"] == "APPROVED"
        assert http.get(f"/verifications/{vid3}", headers=app_client).json()["status"] == "VERIFIED"
        # WF01 (CR-107): the facts say a person reviewed it, and that the scope holds only because the profile's own review path approved it
        facts = http.get(f"/verifications/{vid3}/proofing", headers=app_client)
        assert facts.status_code == 200, facts.text
        held = facts.json()
        assert held["state"] == "VERIFIED_FOR_SCOPE" and held["decision"] == "verified_for_scope"
        assert (
            "DOCUMENT_REVIEWED" in held["assertions"]
            and "IDENTITY_VERIFIED_FOR_SCOPE" in held["assertions"]
        )
        assert (
            "DOCUMENT_CHIP_CRYPTO_VALID" not in held["assertions"]
        )  # a provider's own read is not our cryptographic proof

        # reliance is an explicit decision per purpose, level and claim
        def rely(**kw: Any) -> str:
            body = {
                "subject_principal_id": alice,
                "purpose": "onboarding",
                "required_level": "IAL-Z2",
                "claims": ["legal_name"],
            } | kw
            return http.post(
                "/verification-reliance-decisions", headers=app_client, json=body
            ).json()["outcome"]

        assert rely() == "REUSE_ALLOWED"
        assert rely(purpose="marketing") == "REVERIFY_REQUIRED"
        assert rely(required_level="IAL-Z3") == "REVERIFY_REQUIRED"
        assert rely(claims=["legal_name", "address"]) == "SUPPLEMENT_REQUIRED"

        # assurance facts for policy: levels and freshness, never values or evidence
        facts = http.get(f"/principals/{alice}/assurance", headers=bearer(alice)).json()
        assert facts["identity_proofing"]["level"] == "IAL-Z2"
        assert set(facts["identity_proofing"]["claims"]) == {"legal_name", "birthdate"}
        assert NAME not in str(facts) and DOB not in str(facts)
        assert http.get(f"/principals/{admin}/assurance", headers=bearer(alice)).status_code == 403

        # revoking assertions changes reliance at once
        items = http.get(
            f"/principals/{alice}/verification-assertions", headers=bearer(alice)
        ).json()["items"]
        for a in items:
            if a["claim_type"] == "legal_name" and a["status"] == "VALID":
                assert (
                    http.post(
                        f"/verification-assertions/{a['assertion_id']}:revoke", headers=adm
                    ).status_code
                    == 200
                )
        assert rely() == "REVERIFY_REQUIRED"

        # circuit breaker on acme and quarantine of beta: no equivalent provider left -> fail closed
        assert http.post("/verification-adapters/beta:quarantine", headers=adm).status_code == 200
        assert http.post(cb + "beta", content=result(ref3)).status_code == 401
        r = start()
        assert r.json()["status"] == "FAILED" and r.json()["outcome"] == "PROVIDER_UNAVAILABLE"

    async def authority_created() -> tuple[int, int, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            t = {"t": uuid.UUID(tenant), "p": uuid.UUID(alice)}
            grants = await c.execute(
                text("SELECT count(*) FROM authz.authority_grant WHERE grantee_principal_id = :p"),
                t,
            )
            roots = await c.execute(
                text(
                    "SELECT count(*) FROM org.authority_root_assertion "
                    "WHERE tenant_id = :t AND holder_principal_id = :p"
                ),
                t,
            )
            mandates = await c.execute(
                text("SELECT count(*) FROM agent.mandate WHERE tenant_id = :t"), t
            )
            return (
                int(grants.scalar_one()),
                int(roots.scalar_one()),
                int(mandates.scalar_one()),
            )

    async def evidence() -> tuple[int, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            dump = (
                await c.execute(
                    text(
                        "SELECT string_agg(t::text, '|') FROM verify.verification_assertion t WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            acme = (
                await c.execute(
                    text(
                        "SELECT consecutive_failures FROM verify.provider_adapter WHERE adapter_id = 'acme' "
                        "AND tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
        return acme, dump

    # VERIFY-19 / VERIFY-20: a verified identity (document, KYB-style entity checks, a successful face match) made
    # alice more assured and gave her no authority: no grant to her, no organizational root held by her, no mandate
    assert asyncio.run(authority_created()) == (0, 0, 0)
    acme_failures, dump = asyncio.run(evidence())
    assert NAME not in dump and DOB not in dump  # claim values exist only as commitments
    assert acme_failures >= 3
