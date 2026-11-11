"""The /v2 presentation-request facade over HTTP (ZID-INT-002 section 12): same verifier rules, a shorter life only, RFC 9457 problems."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from softcred import Mdoc
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


def uid() -> str:
    return str(uuid.uuid4())


def test_the_v2_presentation_request_keeps_the_verifier_rules_and_only_shortens_its_life(
    rt_url: str,
) -> None:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("verifier-v2"))
    mdl = Mdoc()

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c, tenant, admin, ["credential.trust.manage"],
                ["credential_configuration:*", "credential_issuer:*", "rp_registration:*"],
            )  # fmt: skip
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid, tenant, admin, now - timedelta(seconds=5), "native:webauthn", 2,
                    pr.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=1), now,
                ),
            )  # fmt: skip
            return sid

    admin_sid = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", verifier_client_id_prefix="x509_hash",
        ),
        keyring=ring,
    )  # fmt: skip

    def bearer(client: str, scope: str = "", sid: str | None = None) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=client,
            now=datetime.now(UTC), session_id=sid, scope=frozenset(scope.split()),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    adm, rp = bearer("console", sid=admin_sid), bearer("rp-bank", "presentation:request")
    no_scope = bearer("rp-other")
    until = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    with TestClient(app, base_url=ISS) as http:

        def admin_post(path: str, body: dict[str, Any]) -> Any:
            r = http.post(path, headers=adm, json=body)
            assert r.status_code in (200, 201), r.text
            return r.json()

        cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": mdl.doc_type, "format": "MDOC", "required_claims": ["age_over_18"],
                "status_method": "NONE", "status_mandatory": False,
            },
        )["configuration_id"]  # fmt: skip
        admin_post(f"/credential-configurations/{cfg}:activate", {})
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": "https://mdl.dvla.example", "credential_types": [mdl.doc_type],
                "trust_source": "PINNED", "iaca_certificates": [mdl.iaca_pem()], "valid_to": until,
            },
        )  # fmt: skip
        admin_post(
            "/relying-party-registrations",
            {"client_id": "rp-bank", "purpose": "age", "allowed_attributes": ["age_over_18"], "valid_to": until},
        )  # fmt: skip

        body = {
            "configuration_id": cfg, "recipient_ref": "zoikorum_tenant_1", "purpose": "age",
            "requested_claims": ["age_over_18"], "jurisdiction": {"country": "GB"},
            "expires_in_seconds": 120, "consent_ref": "consent_1",
        }  # fmt: skip
        h = {**rp, "Idempotency-Key": "pres-key-0001"}

        made = http.post("/v2/presentations/requests", headers=h, json=body)
        assert made.status_code == 201, made.text
        out = made.json()
        assert (
            out["presentation_ref"].startswith("pres_")
            and out["request_uri"]
            and out["claims"] == ["age_over_18"]
        )
        life = datetime.fromisoformat(out["expires_at"]) - datetime.now(UTC)
        assert timedelta(seconds=60) < life <= timedelta(seconds=121), (
            "a shorter life was asked for and given"
        )

        # replay returns the same request; a changed body under the same key is a conflict
        assert (
            http.post("/v2/presentations/requests", headers=h, json=body).json()["presentation_ref"]
            == out["presentation_ref"]
        )
        clash = http.post(
            "/v2/presentations/requests", headers=h, json={**body, "purpose": "other"}
        )
        assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"

        # a longer life than the verifier allows is refused: more exposure is never an option
        longer = http.post(
            "/v2/presentations/requests", headers={**rp, "Idempotency-Key": "pres-key-0002"},
            json={**body, "expires_in_seconds": 3600},
        )  # fmt: skip
        assert longer.status_code == 422 and longer.headers["content-type"].startswith(
            "application/problem+json"
        )

        # no scope, no registered purpose, and an unknown configuration are all refused by the same verifier rules
        assert (
            http.post(
                "/v2/presentations/requests",
                headers={**no_scope, "Idempotency-Key": "pres-key-0003"},
                json=body,
            ).status_code
            == 403
        )
        wrong = http.post(
            "/v2/presentations/requests", headers={**rp, "Idempotency-Key": "pres-key-0004"}, json={**body, "purpose": "marketing"}
        )  # fmt: skip
        assert wrong.status_code >= 400 and wrong.json()["code"] != "idempotency_conflict"
        missing = http.post(
            "/v2/presentations/requests", headers={**rp, "Idempotency-Key": "pres-key-0005"},
            json={k: v for k, v in body.items() if k != "configuration_id"},
        )  # fmt: skip
        assert missing.status_code == 422 and missing.json()["code"] == "invalid_request"

        # reading it back: only the relying party that asked can see it
        read = http.get(f"/v2/presentations/{out['presentation_ref']}", headers=rp)
        assert read.status_code == 200 and read.json()["status"] == "REQUESTED"
        assert (
            http.get(
                f"/v2/presentations/{out['presentation_ref']}",
                headers=bearer("rp-other", "presentation:request"),
            ).status_code
            == 404
        )
