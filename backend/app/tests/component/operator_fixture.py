"""An operator tenant with registered operators, sessions and an operator client (G-W7/G-W8 tests)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.operator_access.application import service as operator_service
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.oauth import OAuthClient
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class Operators:
    tenant: str
    client: str
    ring: KeyRing
    sessions: dict[str, str] = field(default_factory=dict)

    def bearer(self, p: str) -> dict[str, str]:
        now = datetime.now(UTC)
        tok = issue_access_token(
            self.ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=self.tenant,
            client_id=self.client,
            now=now,
            session_id=self.sessions[p],
            auth_time=now - timedelta(seconds=5),
        )
        return {"Authorization": f"Bearer {tok}"}


def operators(
    rt_url: str, roles: Mapping[str, set[OperatorRole]], *, unregistered: tuple[str, ...] = ()
) -> Operators:
    """Principals in `roles` are registered operators; `unregistered` only have operator sessions."""
    o = Operators(uid(), f"zoiko-operator-{uid()[:8]}", KeyRing(LocalSigner(f"issuer-{uid()[:8]}")))

    async def seed() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_client(
                c,
                OAuthClient(
                    o.client,
                    o.tenant,
                    "PUBLIC",
                    "STANDARD",
                    ("https://op.example/cb",),
                    frozenset({"authorization_code"}),
                    frozenset({"openid"}),
                    (AUD,),
                    "none",
                    False,
                    "ACTIVE",
                    first_party=True,
                ),
            )
            for p in (*roles, *unregistered):
                await create_principal(
                    c, Principal(p, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
                principal, _ = await load_principal(c, p)
                sid = uid()
                await create_session(
                    c,
                    Session(
                        sid,
                        o.tenant,
                        p,
                        now - timedelta(seconds=5),
                        "native:webauthn",
                        2,
                        principal.security_revision,
                        1,
                        SessionStatus.ACTIVE,
                        now + timedelta(hours=1),
                        now,
                    ),
                )
                o.sessions[p] = sid
            for p, r in roles.items():
                await operator_service.register_operator(
                    c,
                    tenant_id=o.tenant,
                    principal_id=p,
                    jurisdiction="GB",
                    roles=frozenset(r),
                    now=now,
                )

    asyncio.run(seed())
    return o


def activate_grant(
    api: Any, o: Operators, requester: str, approvers: tuple[str, ...], **body: Any
) -> str:
    req = {
        "case_ref": "INC-OPS-1",
        "reason": "operational change under review",
        "duration_minutes": 60,
        **body,
    }
    r = api.post("/operator/access-requests", json=req, headers=o.bearer(requester))
    assert r.status_code == 201, r.text
    gid = str(r.json()["jit_grant_id"])
    for a in approvers:
        assert (
            api.post(f"/operator/access-requests/{gid}:approve", headers=o.bearer(a)).status_code
            == 200
        )
    r = api.post(f"/operator/access-requests/{gid}:activate", headers=o.bearer(requester))
    assert r.status_code == 200 and r.json()["state"] == "ACTIVE", r.text
    return gid
