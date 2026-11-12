"""Directory sync administration on real routes and a real database: connect, view, run, pause, resume and replace the secret.

Who may do it is the same as for a SCIM source (the protected action `provisioning.source.manage`, R3). The secret is write-only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from scim_fixture import make_scim_client
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
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
SECRET = "dir-" + "x" * 32  # a stand-in value: the tests check it is never stored or returned
SETTINGS = {"domain": "acme-test.us.auth0.com", "client_id": "test-client-id-0001"}


def uid() -> str:
    return str(uuid.uuid4())


def test_directory_sync_administration(rt_url: str) -> None:
    configure_runtime(development_runtime("uk-dir-api"))
    tenant, admin, reader = uid(), uid(), uid()
    client = f"hr-{tenant[:8]}"
    ring = KeyRing(LocalSigner("issuer-1"))
    source_id = uid()

    async def seed() -> tuple[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, reader):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(
                c,
                tenant,
                admin,
                ["provisioning.source.manage", "provisioning.source.read"],
                ["provisioning_source:*"],
            )
            await make_scim_client(c, tenant, client)
            await c.execute(
                text(
                    "INSERT INTO platform.provisioning_source (source_id, tenant_id, client_id, display_name, status) "
                    "VALUES (:s, :t, :c, 'HR', 'ACTIVE')"
                ),
                {"s": uuid.UUID(source_id), "t": uuid.UUID(tenant), "c": client},
            )
            sids = []
            for who in (admin, reader):
                sid, now = uid(), datetime.now(UTC)
                p, _ = await load_principal(c, who)
                await create_session(
                    c,
                    Session(
                        sid,
                        tenant,
                        who,
                        now - timedelta(seconds=10),
                        "native:webauthn",
                        2,
                        p.security_revision,
                        1,
                        SessionStatus.ACTIVE,
                        now + timedelta(hours=1),
                        now,
                    ),
                )
                sids.append(sid)
            return sids[0], sids[1]

    admin_sid, reader_sid = asyncio.run(seed())
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

    def bearer(sub: str, sid: str) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="console",
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, other = bearer(admin, admin_sid), bearer(reader, reader_sid)
    body = {
        "source_id": source_id,
        "provider": "AUTH0",
        "display_name": "Acme Auth0",
        "settings": SETTINGS,
        "secret": SECRET,
    }

    with TestClient(app, base_url=ISS) as http:
        # someone without the authority cannot connect a directory or even read the list
        assert (
            http.post("/provisioning/directory-syncs", headers=other, json=body).status_code == 403
        )
        assert http.get("/provisioning/directory-syncs", headers=other).status_code == 403

        r = http.post("/provisioning/directory-syncs", headers=adm, json=body)
        assert r.status_code == 201, r.text
        made = r.json()
        assert (
            made["status"] == "ACTIVE"
            and made["settings"] == SETTINGS
            and made["last_outcome"] is None
        )
        assert SECRET not in r.text  # write-only
        sync_id = made["sync_id"]

        listed = http.get("/provisioning/directory-syncs", headers=adm)
        assert [s["sync_id"] for s in listed.json()["items"]] == [
            sync_id
        ] and SECRET not in listed.text

        # one sync per source; a source that does not exist, a bad domain and unknown extras are refused
        assert http.post("/provisioning/directory-syncs", headers=adm, json=body).status_code == 409
        assert (
            http.post(
                "/provisioning/directory-syncs", headers=adm, json={**body, "source_id": uid()}
            ).status_code
            == 409
        )
        bad = {**body, "settings": {**SETTINGS, "domain": "evil.example.com"}}
        assert http.post("/provisioning/directory-syncs", headers=adm, json=bad).status_code in (
            409,
            422,
        )
        assert (
            http.post(
                "/provisioning/directory-syncs", headers=adm, json={**body, "extra": 1}
            ).status_code
            == 422
        )

        assert (
            http.post(
                f"/provisioning/directory-syncs/{sync_id}:run", headers=adm, json={}
            ).status_code
            == 202
        )
        paused = http.post(f"/provisioning/directory-syncs/{sync_id}:pause", headers=adm, json={})
        assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
        assert (
            http.post(
                f"/provisioning/directory-syncs/{sync_id}:resume", headers=adm, json={}
            ).json()["status"]
            == "ACTIVE"
        )

        rotated = http.put(
            f"/provisioning/directory-syncs/{sync_id}/credential",
            headers=adm,
            json={"secret": SECRET + "-v2"},
        )
        assert (
            rotated.status_code == 200
            and rotated.json()["version"] == 2
            and SECRET not in rotated.text
        )
        assert (
            http.post(
                f"/provisioning/directory-syncs/{uid()}:run", headers=adm, json={}
            ).status_code
            == 404
        )

    configure_runtime(None)

    async def stored() -> list[bytes]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                await c.execute(text("SELECT envelope FROM platform.connector_credential"))
            ).all()
            return [bytes(r.envelope) for r in rows]

    envelopes: list[Any] = asyncio.run(stored())
    assert len(envelopes) == 2 and all(SECRET.encode() not in e for e in envelopes)
