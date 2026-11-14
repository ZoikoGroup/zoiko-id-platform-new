"""Identity-proofing providers through the API (ZID-EDR-005): Veriff and Sumsub sessions are created server-side, the applicant
gets a handoff, a webhook is authenticated on its raw bytes, and only an authenticated, understood approval verifies."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
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
SECRETS = {
    "veriff-key": "vk", "veriff-secret": "vs", "sumsub-token": "st", "sumsub-secret": "ss", "sumsub-hook": "sh",
}  # fmt: skip


class Broker:
    def get(self, name: str) -> str:
        return SECRETS[name]


def uid() -> str:
    return str(uuid.uuid4())


def mac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def rejected_callbacks(rt_url: str, tenant: str) -> tuple[list[Any], list[Any]]:
    """T35: what the platform recorded (audit) and raised (alerts) for refused provider callbacks."""

    async def rows() -> tuple[list[Any], list[Any]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            t = {"t": uuid.UUID(tenant)}
            audit = (
                await c.execute(
                    text(
                        "SELECT outcome, reason_code, target_ref, body::text AS body FROM evidence.audit_record "
                        "WHERE tenant_id = :t AND event_type = 'verification.callback.rejected' ORDER BY created_at"
                    ),
                    t,
                )
            ).all()
            alerts = (
                await c.execute(
                    text(
                        "SELECT severity, mandatory, occurrences, correlation_key FROM platform.notification_request "
                        "WHERE tenant_id = :t AND kind = 'verification.callback.rejected'"
                    ),
                    t,
                )
            ).all()
            return list(audit), list(alerts)

    return asyncio.run(rows())


@pytest.mark.parametrize("only", ["veriff", "sumsub"])
def test_veriff_and_sumsub_sessions_and_webhooks(rt_url: str, only: str) -> None:
    """EDR05: a forged or altered webhook decides nothing; an approval verifies once; a retry is a duplicate; Sumsub does not
    verify when the applicant's data cannot be read; a provider that fails to start a session leaves nothing pending."""
    tenant, alice, admin = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    sumsub_info = {"ok": True}

    def provider(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "api.veriff.example":
            return httpx.Response(
                201,
                json={
                    "verification": {
                        "id": "ver-1",
                        "url": "https://magic.veriff.example/s/x",
                        "sessionToken": "t",
                    }
                },
            )
        if path.startswith("/resources/applicants/") and path.endswith("/one"):
            if not sumsub_info["ok"]:
                return httpx.Response(500)
            return httpx.Response(
                200, json={"info": {"firstName": "Alice", "lastName": "Smith", "dob": "1980-02-01"}}
            )
        if path.startswith("/resources/applicants"):
            return httpx.Response(201, json={"id": "app-1"})
        return httpx.Response(200, json={"token": "_act-sbx"})

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
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
    app.state.verification_transport = httpx.MockTransport(provider)
    app.state.secret_broker = Broker()

    def bearer(
        scope: str = "", *, with_session: bool = False, client: str = "onboarding"
    ) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=client, now=datetime.now(UTC),
            session_id=sid if with_session else None, scope=frozenset(scope.split()),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    adm, svc = (
        bearer(with_session=True),
        bearer("verification:execute verification:read", client="onboarding-svc"),
    )

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

        def adapter(
            adapter_id: str, kind: str, base: str, group: str, config: dict[str, Any]
        ) -> Any:
            return http.post(
                "/verification-adapters", headers=adm,
                json={
                    "adapter_id": adapter_id, "provider": adapter_id, "mapping_version": "v1", "submit_endpoint": base,
                    "equivalence_group": group, "supported_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"], "jurisdictions": ["GB"],
                    "provider_kind": kind, "provider_config": config,
                },
            )  # fmt: skip

        # settings that carry a secret, or a certified flag on the wrong provider, are refused at creation
        assert (
            adapter(
                "bad",
                "VERIFF",
                "https://api.veriff.example",
                "g1",
                {"api_key_ref": "veriff-key", "shared_secret": "x"},
            ).status_code
            == 422
        )
        assert (
            adapter(
                "bad",
                "SUMSUB",
                "https://api.sumsub.example",
                "g1",
                {
                    "app_token_ref": "a",
                    "secret_key_ref": "b",
                    "webhook_secret_ref": "c",
                    "level_name": "lvl",
                    "uk_certified": True,
                },
            ).status_code
            == 422
        )
        if only == "veriff":
            r = adapter(
                "veriff",
                "VERIFF",
                "https://api.veriff.example",
                "g-veriff",
                {"api_key_ref": "veriff-key", "shared_secret_ref": "veriff-secret"},
            )
        else:
            r = adapter(
                "sumsub", "SUMSUB", "https://api.sumsub.example", "g-sumsub",
                {"app_token_ref": "sumsub-token", "secret_key_ref": "sumsub-secret", "webhook_secret_ref": "sumsub-hook", "level_name": "basic-kyc", "biometric_level": True},
            )  # fmt: skip
        assert r.status_code == 201, r.text

        def start() -> Any:
            return http.post(
                "/verifications", headers=svc,
                json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"},
            )  # fmt: skip

        cb = f"/verification-provider-callbacks/{tenant}."
        r = start()
        assert r.status_code == 201 and r.json()["status"] == "PENDING_EXTERNAL", r.text
        chosen, vid = r.json()["adapter_id"], r.json()["verification_id"]
        assert "handoff" in r.json() and r.json()["handoff"]  # a link or a token for the applicant
        assert (
            http.get(f"/verifications/{vid}", headers=svc).json().get("handoff") is None
        )  # never stored, never read back again

        assert chosen == only
        if chosen == "veriff":
            payload = {
                "status": "success",
                "verification": {
                    "id": "ver-1",
                    "attemptId": "a1",
                    "status": "approved",
                    "code": 9001,
                    "person": {
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "dateOfBirth": "1980-02-01",
                    },
                },
            }
            raw = json.dumps(payload).encode()
            good = {"X-AUTH-CLIENT": "vk", "X-HMAC-SIGNATURE": mac("vs", raw)}
            assert (
                http.post(
                    cb + "veriff",
                    content=raw,
                    headers={**good, "X-HMAC-SIGNATURE": mac("wrong", raw)},
                ).status_code
                == 401
            )
            assert (
                http.post(cb + "veriff", content=raw + b" ", headers=good).status_code == 401
            )  # altered in transit
            assert http.post(cb + "veriff", content=raw).status_code == 401  # unsigned
            # T35: every refusal is recorded as a DENIED audit event and raises one mandatory HIGH alert (repeats fold into it)
            audit, alerts = rejected_callbacks(rt_url, tenant)
            assert [(a.outcome, a.reason_code, a.target_ref) for a in audit] == [
                ("DENIED", "CALLBACK_SIGNATURE_INVALID", "veriff")
            ] * 3
            assert all(mac("vs", raw) not in a.body and "vk" not in a.body for a in audit)
            assert len(alerts) == 1
            assert (alerts[0].severity, alerts[0].mandatory, alerts[0].occurrences) == (
                "HIGH",
                True,
                3,
            )
            assert (
                http.get(f"/verifications/{vid}", headers=svc).json()["status"]
                == "PENDING_EXTERNAL"
            )  # nothing decided
            ok = http.post(cb + "veriff", content=raw, headers=good)
            assert ok.status_code == 200 and ok.json()["outcome"] == "VERIFIED", (
                ok.text
            )  # Veriff retries anything but 200
            assert (
                http.post(cb + "veriff", content=raw, headers=good).json()["outcome"] == "DUPLICATE"
            )
            assert http.get(f"/verifications/{vid}", headers=svc).json()["status"] == "VERIFIED"
            # T35 replay: the same event id re-sent with a different (correctly signed) body is refused, recorded and alerted
            replay = json.dumps({**payload, "note": "changed"}).encode()
            sent = http.post(
                cb + "veriff",
                content=replay,
                headers={"X-AUTH-CLIENT": "vk", "X-HMAC-SIGNATURE": mac("vs", replay)},
            )
            assert sent.status_code == 409 and sent.json()["error"] == "CALLBACK_CONFLICT"
            audit, alerts = rejected_callbacks(rt_url, tenant)
            assert audit[-1].reason_code == "CALLBACK_CONFLICT" and audit[-1].outcome == "DENIED"
            assert {a.correlation_key.rsplit(":", 1)[1] for a in alerts} == {
                "CALLBACK_SIGNATURE_INVALID",
                "CALLBACK_CONFLICT",
            }
        else:
            payload = {
                "type": "applicantReviewed",
                "applicantId": "app-1",
                "correlationId": "c1",
                "externalUserId": vid,
                "reviewStatus": "completed",
                "reviewResult": {"reviewAnswer": "GREEN"},
            }
            raw = json.dumps(payload).encode()
            good = {"X-Payload-Digest": mac("sh", raw), "X-Payload-Digest-Alg": "HMAC_SHA256_HEX"}
            assert (
                http.post(
                    cb + "sumsub",
                    content=raw,
                    headers={**good, "X-Payload-Digest": mac("wrong", raw)},
                ).status_code
                == 401
            )
            assert http.post(cb + "sumsub", content=raw).status_code == 401
            sumsub_info["ok"] = (
                False  # the applicant's data cannot be read: nothing is recorded, the provider retries
            )
            held = http.post(cb + "sumsub", content=raw, headers=good)
            assert held.status_code == 503 and "APPLICANT_DATA_UNAVAILABLE" in held.text
            assert (
                http.get(f"/verifications/{vid}", headers=svc).json()["status"]
                == "PENDING_EXTERNAL"
            )
            sumsub_info["ok"] = True
            ok = http.post(cb + "sumsub", content=raw, headers=good)
            assert ok.status_code == 200 and ok.json()["outcome"] == "VERIFIED", ok.text
            assert (
                http.post(cb + "sumsub", content=raw, headers=good).json()["outcome"] == "DUPLICATE"
            )
