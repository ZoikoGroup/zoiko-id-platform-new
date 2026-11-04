"""Identity-proofing providers (ZID-EDR-005): a provider that asks the person to send the evidence again is its own state, not a pending one, and a
delayed webhook is reconciled by asking the provider for the decision. A forged, missing or not-yet-made decision decides nothing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import Principal, PrincipalStatus, PrincipalType
from zid_platform.modules.principal.infrastructure.principal_store import create_principal, load_principal
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.application import service
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
SECRETS = {"veriff-key": "vk", "veriff-secret": "vs", "sumsub-token": "st", "sumsub-secret": "ss", "sumsub-hook": "sh"}


class Broker:
    def get(self, name: str) -> str:
        return SECRETS[name]


def uid() -> str:
    return str(uuid.uuid4())


def mac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(status: str, code: int | None = None, ref: str = "ver-1") -> dict[str, Any]:
    return {
        "status": "success",
        "verification": {
            "id": ref, "attemptId": "a1", "status": status, "code": code,
            "person": {"firstName": "Alice", "lastName": "Smith", "dateOfBirth": "1980-02-01"},
        },
    }  # fmt: skip


def test_resubmission_is_its_own_state_and_a_late_webhook_is_reconciled_by_asking(rt_url: str, owner_url: str) -> None:
    tenant, alice, admin = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    ref = f"ver-{uid()[:8]}"  # the provider's reference is this run's own: the test database outlives a run, and event ids are remembered
    decision: dict[str, Any] = {"doc": None}  # what the provider's decision endpoint answers; None is "not decided yet" (404)
    asked: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={"verification": {"id": ref, "url": "https://magic.veriff.example/s/x", "sessionToken": "t"}})
        if request.url.path.endswith("/decision"):
            asked.append(request.url.path)
            return httpx.Response(404) if decision["doc"] is None else httpx.Response(200, json=decision["doc"])
        return httpx.Response(404)

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(
                c, tenant, [admin], ["verification.profile.manage", "verification.adapter.manage"],
                ["verification_profile:*", "verification_adapter:*"],
            )  # fmt: skip
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(sid, tenant, admin, now - timedelta(seconds=10), "native:webauthn", 2, pr.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=1), now),
            )  # fmt: skip
            return sid

    sid = asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"), keyring=ring)
    app.state.verification_transport = httpx.MockTransport(provider)
    app.state.secret_broker = Broker()

    def bearer(scope: str = "", *, with_session: bool = False, client: str = "onboarding") -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=client, now=datetime.now(UTC),
            session_id=sid if with_session else None, scope=frozenset(scope.split()),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    adm, svc = bearer(with_session=True), bearer("verification:execute verification:read", client="onboarding-svc")

    def age_session(vid: str) -> None:
        """The session has been waiting for an hour: the poll only asks about sessions older than its delay."""

        async def run() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.verification_session SET updated_at = now() - interval '1 hour' WHERE session_id = :s"), {"s": uuid.UUID(vid)})

        asyncio.run(run())

    def reconcile(now: datetime) -> dict[str, int]:
        async def run() -> dict[str, int]:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await service.reconcile_delayed(c, now, secrets=Broker().get, transport=httpx.MockTransport(provider), only_tenant=tenant)

        return asyncio.run(run())

    with TestClient(app, base_url=ISS) as http:
        profile = {
            "name": "uk-onboarding", "level": "IAL-Z2",
            "requirements": {
                "required_claims": ["legal_name", "birthdate"], "accepted_evidence": ["GOVERNMENT_ID"], "binding_required": True,
                "liveness_required": True, "biometric_jurisdictions": ["GB"], "jurisdictions": ["GB"], "freshness_days": 365,
                "manual_review": False, "review_approvals": 1, "reliance_purposes": ["onboarding"],
            },
        }  # fmt: skip
        pid = http.post("/verification-profiles", headers=adm, json=profile).json()["profile_id"]
        assert http.post(f"/verification-profiles/{pid}:activate", headers=adm).status_code == 200
        r = http.post(
            "/verification-adapters", headers=adm,
            json={
                "adapter_id": "veriff", "provider": "veriff", "mapping_version": "v1", "submit_endpoint": "https://api.veriff.example",
                "equivalence_group": "g-veriff", "supported_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"], "jurisdictions": ["GB"],
                "provider_kind": "VERIFF", "provider_config": {"api_key_ref": "veriff-key", "shared_secret_ref": "veriff-secret"},
            },
        )  # fmt: skip
        assert r.status_code == 201, r.text
        cb = f"/verification-provider-callbacks/{tenant}.veriff"

        def start() -> str:
            r = http.post("/verifications", headers=svc, json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"})
            assert r.status_code == 201 and r.json()["status"] == "PENDING_EXTERNAL", r.text
            return str(r.json()["verification_id"])

        def webhook(doc: dict[str, Any]) -> Any:
            raw = json.dumps(doc).encode()
            return http.post(cb, content=raw, headers={"X-AUTH-CLIENT": "vk", "X-HMAC-SIGNATURE": mac("vs", raw)})

        def status(vid: str) -> str:
            return str(http.get(f"/verifications/{vid}", headers=svc).json()["status"])

        # 1. the provider asks the person to send the evidence again: an open session in its own state, never a decision
        vid = start()
        resub = webhook(_payload("resubmission_requested", 2000, ref))
        assert resub.status_code == 200 and resub.json()["outcome"] == "RESUBMISSION_REQUIRED", resub.text
        assert status(vid) == "RESUBMISSION_REQUIRED"
        # the provider's next result still moves it on, and a repeat of the same request changes nothing
        assert status(vid) == "RESUBMISSION_REQUIRED"
        done = webhook(_payload("approved", 9001, ref))
        assert done.status_code == 200 and done.json()["outcome"] == "VERIFIED", done.text
        assert status(vid) == "VERIFIED"

        # 2. a late webhook: the poll asks only about a session that has waited, and a provider with no decision yet decides nothing
        # (the provider's reference is the same for the next session in this test, so it is recorded as a new transaction)
        async def forget_ref() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.verification_session SET provider_transaction_ref = :d WHERE session_id = :s"), {"s": uuid.UUID(vid), "d": f"done-{uid()[:8]}"})

        asyncio.run(forget_ref())
        vid2 = start()
        now = datetime.now(UTC)
        service._last_polled.clear()
        assert reconcile(now) == {"asked": 0, "applied": 0, "no_decision": 0, "unsupported": 0}  # not waiting long enough
        age_session(vid2)
        out = reconcile(now)
        assert out["asked"] == 1 and out["no_decision"] == 1 and out["applied"] == 0, out
        assert status(vid2) == "PENDING_EXTERNAL" and asked, "no decision yet: nothing is decided"
        out = reconcile(now + timedelta(minutes=1))
        assert out == {"asked": 0, "applied": 0, "no_decision": 0, "unsupported": 0}, "one session is asked about only every few minutes"

        # the provider now has the decision: the poll applies it exactly as the signed webhook would
        decision["doc"] = _payload("approved", 9001, ref)
        out = reconcile(now + service.RECONCILE_EVERY + timedelta(minutes=1))
        assert out["applied"] == 1, out
        assert status(vid2) == "VERIFIED"
        # the webhook that was late arrives after all: a result already applied changes nothing
        late = webhook(_payload("approved", 9001, ref))
        assert late.status_code == 200 and late.json()["outcome"] in ("IGNORED_TERMINAL", "DUPLICATE"), late.text
        assert status(vid2) == "VERIFIED"


def test_a_late_sumsub_review_is_reconciled_by_asking_and_an_unrecognised_answer_decides_nothing(rt_url: str, owner_url: str) -> None:
    """Sumsub is polled through the applicant call the platform already makes: no review on record, or a shape nobody recognises, is no decision; a
    completed GREEN review with the applicant's data verifies exactly as the signed webhook would."""
    tenant, alice, admin = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    app_id = f"app-{uid()[:8]}"  # this run's own applicant: the test database outlives a run
    applicant: dict[str, Any] = {"doc": {"id": app_id, "info": {"firstName": "Alice", "lastName": "Smith", "dob": "1980-02-01"}}}

    def provider(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/one"):
            return httpx.Response(200, json=applicant["doc"])
        if path.startswith("/resources/applicants"):
            return httpx.Response(201, json={"id": app_id})
        return httpx.Response(200, json={"token": "_act-sbx"})

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(
                c, tenant, [admin], ["verification.profile.manage", "verification.adapter.manage"],
                ["verification_profile:*", "verification_adapter:*"],
            )  # fmt: skip
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(sid, tenant, admin, now - timedelta(seconds=10), "native:webauthn", 2, pr.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=1), now),
            )  # fmt: skip
            return sid

    sid = asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"), keyring=ring)
    app.state.verification_transport = httpx.MockTransport(provider)
    app.state.secret_broker = Broker()

    def bearer(scope: str = "", *, with_session: bool = False, client: str = "onboarding") -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=client, now=datetime.now(UTC),
            session_id=sid if with_session else None, scope=frozenset(scope.split()),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    adm, svc = bearer(with_session=True), bearer("verification:execute verification:read", client="onboarding-svc")

    def reconcile(now: datetime) -> dict[str, int]:
        async def run() -> dict[str, int]:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await service.reconcile_delayed(c, now, secrets=Broker().get, transport=httpx.MockTransport(provider), only_tenant=tenant)

        return asyncio.run(run())

    with TestClient(app, base_url=ISS) as http:
        profile = {
            "name": "uk-onboarding", "level": "IAL-Z2",
            "requirements": {
                "required_claims": ["legal_name", "birthdate"], "accepted_evidence": ["GOVERNMENT_ID"], "binding_required": True,
                "liveness_required": True, "biometric_jurisdictions": ["GB"], "jurisdictions": ["GB"], "freshness_days": 365,
                "manual_review": False, "review_approvals": 1, "reliance_purposes": ["onboarding"],
            },
        }  # fmt: skip
        pid = http.post("/verification-profiles", headers=adm, json=profile).json()["profile_id"]
        assert http.post(f"/verification-profiles/{pid}:activate", headers=adm).status_code == 200
        r = http.post(
            "/verification-adapters", headers=adm,
            json={
                "adapter_id": "sumsub", "provider": "sumsub", "mapping_version": "v1", "submit_endpoint": "https://api.sumsub.example",
                "equivalence_group": "g-sumsub", "supported_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"], "jurisdictions": ["GB"],
                "provider_kind": "SUMSUB",
                "provider_config": {"app_token_ref": "sumsub-token", "secret_key_ref": "sumsub-secret", "webhook_secret_ref": "sumsub-hook", "level_name": "basic-kyc", "biometric_level": True},
            },
        )  # fmt: skip
        assert r.status_code == 201, r.text
        started = http.post("/verifications", headers=svc, json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"})
        assert started.status_code == 201 and started.json()["status"] == "PENDING_EXTERNAL", started.text
        vid = str(started.json()["verification_id"])

        def status() -> str:
            return str(http.get(f"/verifications/{vid}", headers=svc).json()["status"])

        async def age() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.verification_session SET updated_at = now() - interval '1 hour' WHERE session_id = :s"), {"s": uuid.UUID(vid)})

        asyncio.run(age())
        service._last_polled.clear()
        now = datetime.now(UTC)
        # no review on record, and then a shape nobody recognises: nothing is decided
        assert reconcile(now)["no_decision"] == 1 and status() == "PENDING_EXTERNAL"
        applicant["doc"] = {**applicant["doc"], "review": "completed"}
        assert reconcile(now + service.RECONCILE_EVERY + timedelta(minutes=1))["no_decision"] == 1 and status() == "PENDING_EXTERNAL"
        # a review that is not finished is not a decision either
        applicant["doc"] = {**applicant["doc"], "review": {"reviewStatus": "pending"}}
        reconcile(now + 2 * service.RECONCILE_EVERY + timedelta(minutes=2))
        assert status() == "PENDING_EXTERNAL"
        # a completed GREEN review, with the applicant's data, verifies as the signed webhook would
        applicant["doc"] = {**applicant["doc"], "review": {"reviewStatus": "completed", "reviewResult": {"reviewAnswer": "GREEN"}}}
        out = reconcile(now + 3 * service.RECONCILE_EVERY + timedelta(minutes=3))
        assert out["applied"] == 1, out
        assert status() == "VERIFIED"
