"""Security signals end to end (ZID-SIGNALS-001 §5-11, §21-22, §26; SIG-01..08, 10, 14, 16).

A transmitter signs real SETs (RS256, pinned key); the push receiver validates them, resolves the
subject through a federation link, applies the deterministic rules and the consequences land in
principal, session and security state. Stream management goes through the real PDP with a real
authority grant. The transmitter queues SETs for a poll receiver.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from joserfc import jws
from joserfc.jwk import RSAKey
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.main import create_app
from zid_platform.modules.authentication.ports import create_external_link
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
from zid_platform.modules.signals.application.ssf import transmit
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
TX_ISS, IDP_ISS = "https://ssf.idp.example", "https://login.idp.example"
RISC = "https://schemas.openid.net/secevent/risc/event-type/"
CAEP = "https://schemas.openid.net/secevent/caep/event-type/"
SET_CT = {"Content-Type": "application/secevent+jwt"}


def uid() -> str:
    return str(uuid.uuid4())


def test_ssf_receiver_rules_state_and_transmitter(rt_url: str, owner_url: str) -> None:
    tenant, alice, admin, bob = uid(), uid(), uid(), uid()
    tx_key = RSAKey.generate_key(2048)
    tx_jwk = {**tx_key.as_dict(private=False), "kid": "tx-1", "use": "sig"}
    rogue_key = RSAKey.generate_key(2048)
    ring = KeyRing(LocalSigner("issuer-1"))

    async def new_session(p: str, aal: int = 2) -> str:
        sid = uid()
        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)  # observe the current security revision
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    p,
                    now - timedelta(seconds=30),
                    "native:webauthn",
                    aal,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                ),
            )
        return sid

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin, bob):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_external_link(
                c,
                tenant_id=tenant,
                principal_id=alice,
                issuer=IDP_ISS,
                subject="u-1",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=datetime.now(UTC),
            )
            await make_admin(
                c,
                tenant,
                admin,
                ["signals.stream.manage", "security_state.override"],
                ["ssf_stream:*", "security_state:*"],
            )

    asyncio.run(seed())
    admin_sid, alice_sid = asyncio.run(new_session(admin)), asyncio.run(new_session(alice))
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

    def bearer(
        sub: str, sid: str | None, client_id: str = "admin-console", scope: str = ""
    ) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id=client_id,
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    def sign_set(claims: dict[str, Any], key: RSAKey = tx_key, typ: str = "secevent+jwt") -> bytes:
        header = {"alg": "RS256", "kid": "tx-1", "typ": typ}
        return jws.serialize_compact(
            header, json.dumps(claims).encode(), key, algorithms=["RS256"]
        ).encode()

    def risc(
        event: str, sub: dict[str, Any] | None = None, *, ts: int | None = None, **top: Any
    ) -> dict[str, Any]:
        now = int(time.time())
        body: dict[str, Any] = {} if ts is None else {"event_timestamp": ts}
        return {
            "iss": TX_ISS,
            "aud": ISS,
            "iat": now,
            "jti": uid(),
            "sub_id": sub or {"format": "iss_sub", "iss": IDP_ISS, "sub": "u-1"},
            "events": {event: body},
            **top,
        }

    with TestClient(app, base_url=ISS) as http:
        adm = bearer(admin, admin_sid)
        events = [
            RISC + "account-disabled",
            RISC + "account-enabled",
            RISC + "credential-compromise",
            CAEP + "session-revoked",
        ]
        r = http.post(
            "/ssf/streams",
            headers=adm,
            json={
                "direction": "RECEIVER",
                "issuer": TX_ISS,
                "audience": ISS,
                "jwks": {"keys": [tx_jwk]},
                "algorithms": ["RS256"],
                "events": events,
                "source_trust": "AUTHORITATIVE",
                "federation_issuer": IDP_ISS,
            },
        )
        assert r.status_code == 201, r.text
        stream = r.json()
        push_auth = stream["push_authorization"]
        url = f"/ssf/events/push/{stream['stream_ref']}"

        def push(body: bytes, auth: str = push_auth, **hdr: str) -> Any:
            return http.post(url, content=body, headers={**SET_CT, "Authorization": auth, **hdr})

        assert push(sign_set(risc(RISC + "account-disabled"))).status_code == 403  # not enabled yet
        assert (
            http.post(f"/ssf/streams/{stream['stream_id']}:enable", headers=adm).status_code == 200
        )

        # a principal without authority cannot manage streams, whatever their assurance
        r = http.post(
            f"/ssf/streams/{stream['stream_id']}:disable",
            headers=bearer(bob, asyncio.run(new_session(bob))),
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "PROTECTED_ACTION_DENIED"

        # SIG-01..04: signature, issuer, audience, type confusion; stream credential and transport
        r = push(sign_set(risc(RISC + "account-disabled"), key=rogue_key))
        assert r.status_code == 400 and r.json()["err"] == "invalid_key"
        r = push(sign_set({**risc(RISC + "account-disabled"), "iss": "https://other"}))
        assert r.json()["err"] == "invalid_issuer"
        r = push(sign_set({**risc(RISC + "account-disabled"), "aud": "https://another-receiver"}))
        assert r.json()["err"] == "invalid_audience"
        r = push(sign_set(risc(RISC + "account-disabled"), typ="JWT"))
        assert r.json()["err"] == "invalid_key"
        access = bearer(alice, alice_sid)["Authorization"].split()[1].encode()
        assert push(access).json()["err"] == "invalid_key"  # an access token is never a SET
        assert (
            push(sign_set(risc(RISC + "account-disabled")), auth="Bearer wrong").status_code == 401
        )
        r = http.post(
            url,
            content=sign_set(risc(RISC + "account-disabled")),
            headers={"Content-Type": "application/jwt", "Authorization": push_auth},
        )
        assert r.status_code == 400
        r = push(sign_set(risc(RISC + "account-purged")))
        assert r.json()["description"] == "SIGNAL_EVENT_NOT_ALLOWED"
        r = push(sign_set(risc(RISC + "account-disabled", {"format": "email", "email": "a@b.c"})))
        assert r.json()["description"] == "SIGNAL_SUBJECT_INVALID"
        complex_sub = {
            "format": "complex",
            "user": {"format": "iss_sub", "iss": IDP_ISS, "sub": "u-1"},
            "device": {"format": "opaque", "id": "d1"},
        }
        r = push(
            sign_set(
                risc(RISC + "account-disabled", complex_sub, critical_subject_members=["device"])
            )
        )
        assert r.json()["description"] == "SIGNAL_CRITICAL_SUBJECT_UNSUPPORTED"  # SIG-07
        assert (
            http.get(f"/security-state/PRINCIPAL/{alice}", headers=bearer(alice, alice_sid)).json()[
                "state"
            ]
            == "TRUSTED"
        )

        # account disabled at the IdP: principal suspended, sessions revoked, state SUSPENDED
        t0 = int(time.time())
        disabled = sign_set(risc(RISC + "account-disabled", ts=t0))
        assert push(disabled).status_code == 202
        assert push(disabled).status_code == 202  # SIG-05: replay accepted, no second effect
        r = http.get("/sessions", headers=bearer(alice, alice_sid))
        assert r.status_code == 401  # sessions revoked / principal suspended

        # SIG-06: an older "enabled" cannot undo the newer "disabled"
        assert push(sign_set(risc(RISC + "account-enabled", ts=t0 - 60))).status_code == 202
        # unknown subject, or a subject of an issuer this stream does not speak for: accepted, no effect
        assert (
            push(
                sign_set(
                    risc(
                        RISC + "account-disabled",
                        {"format": "iss_sub", "iss": IDP_ISS, "sub": "nobody"},
                    )
                )
            ).status_code
            == 202
        )
        assert (
            push(
                sign_set(
                    risc(
                        RISC + "account-disabled",
                        {"format": "iss_sub", "iss": "https://elsewhere", "sub": "u-1"},
                    )
                )
            ).status_code
            == 202
        )

        async def principal_state() -> tuple[str, int]:
            async with engine(owner_url) as eng, eng.begin() as c:
                row = (
                    await c.execute(
                        text(
                            "SELECT p.status, s.state FROM core.principal p LEFT JOIN signals.security_state s "
                            "ON s.subject_id = p.principal_id::text AND s.subject_type = 'PRINCIPAL' "
                            "WHERE p.principal_id = :p"
                        ),
                        {"p": uuid.UUID(alice)},
                    )
                ).first()
            return row.status, row.state

        assert asyncio.run(principal_state()) == ("SUSPENDED", "SUSPENDED")

        # SIG-16: a newer authoritative "enabled" re-enables, but old sessions stay revoked
        assert push(sign_set(risc(RISC + "account-enabled", ts=t0 + 5))).status_code == 202
        assert asyncio.run(principal_state()) == ("ACTIVE", "REAUTH_REQUIRED")
        assert http.get("/sessions", headers=bearer(alice, alice_sid)).status_code == 401
        fresh = asyncio.run(new_session(alice))  # a new authentication after the state change
        r = http.get(f"/security-state/PRINCIPAL/{alice}", headers=bearer(alice, fresh))
        assert r.status_code == 200 and r.json()["state"] == "REAUTH_REQUIRED"

        # SIG-14: credential compromise -> COMPROMISED, sessions revoked, revision moves
        assert push(sign_set(risc(RISC + "credential-compromise"))).status_code == 202
        assert http.get("/sessions", headers=bearer(alice, fresh)).status_code == 401
        later = asyncio.run(new_session(alice))
        assert (
            http.get(f"/security-state/PRINCIPAL/{alice}", headers=bearer(alice, later)).json()[
                "state"
            ]
            == "COMPROMISED"
        )
        # and the PDP sees it: alice may not manage anything at R3 even with authority
        # governed override back to TRUSTED by the admin (R3, recorded as a signal)
        r = http.post(
            f"/security-state/PRINCIPAL/{alice}:reevaluate",
            headers=adm,
            json={"state": "TRUSTED", "reason": "investigated: false positive"},
        )
        assert r.status_code == 200 and r.json()["state"] == "TRUSTED"
        r = http.post(
            f"/security-state/PRINCIPAL/{alice}:reevaluate",
            headers=bearer(bob, asyncio.run(new_session(bob))),
            json={"state": "TRUSTED", "reason": "trying"},
        )
        assert r.status_code == 403

        # trusted internal source: revoke one session (SIG-10 for an internal signal)
        target = asyncio.run(new_session(alice))
        pub = bearer(admin, None, client_id="risk-engine", scope="signals:publish")
        r = http.post(
            "/signals",
            headers=pub,
            json={
                "event_type": "session.revoked",
                "subject_type": "SESSION",
                "subject_id": target,
                "source_event_id": "risk-1",
            },
        )
        assert r.status_code == 202 and r.json()["status"] == "APPLIED", r.text
        assert http.get("/sessions", headers=bearer(alice, target)).status_code == 401
        r = http.post(
            "/signals",
            headers=bearer(admin, None, client_id="risk-engine"),
            json={
                "event_type": "session.revoked",
                "subject_type": "SESSION",
                "subject_id": target,
                "source_event_id": "risk-2",
            },
        )
        assert r.status_code == 403  # no signals:publish scope

        # transmitter: a poll receiver gets a pairwise-subject SET for a revoked session
        r = http.post(
            "/ssf/streams",
            headers=adm,
            json={
                "direction": "TRANSMITTER",
                "audience": "https://rp.example",
                "events": [CAEP + "session-revoked"],
                "receiver_client_id": "rp-client",
            },
        )
        assert r.status_code == 201, r.text
        tx = r.json()
        http.post(f"/ssf/streams/{tx['stream_id']}:enable", headers=adm)

        async def emit_revocation() -> int:
            event = CloudEvent(
                id=uid(),
                type="com.zoiko.id.session.revoked.v1",
                source="urn:zoiko:id:session",
                subject=target,
                time=datetime.now(UTC),
                tenantid=tenant,
                cellid="c1",
                aggregatetype="session",
                aggregateid=target,
                aggregateversion=2,
                data={"session_id": target, "principal_id": alice, "reason": "x"},
                correlationid="c",
            )
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                return await transmit(c, ring, ISS, event, datetime.now(UTC))

        assert asyncio.run(emit_revocation()) == 1
        rp = bearer(admin, None, client_id="rp-client")
        r = http.post(f"/ssf/poll/{tx['stream_ref']}", headers=rp, json={"maxEvents": 5})
        assert r.status_code == 200, r.text
        sets = r.json()["sets"]
        assert len(sets) == 1 and r.json()["moreAvailable"] is False
        ((jti, jwt),) = sets.items()
        _, claims = verify_jws(jwt, keys=ring.jwks()["keys"], typ="secevent+jwt")
        assert claims["aud"] == "https://rp.example" and claims["iss"] == ISS
        assert claims["sub_id"]["format"] == "opaque" and alice not in jwt
        assert list(claims["events"]) == [CAEP + "session-revoked"]
        r = http.post(f"/ssf/poll/{tx['stream_ref']}", headers=rp, json={"ack": [jti]})
        assert r.json()["sets"] == {}
        # FR-SIG-003/004: commit -> receiver acknowledgement, measured against the R3 target
        stats = http.get(f"/ssf/streams/{tx['stream_id']}/propagation", headers=adm).json()
        assert stats["acknowledged"] == 1 and stats["pending"] == 0, stats
        assert stats["p99_ms"] is not None and stats["slo_breaches"] == 0
        assert stats["slo_ms_by_risk"]["3"] == 5000
        r = http.post(
            f"/ssf/poll/{tx['stream_ref']}", headers=bearer(admin, None, client_id="other"), json={}
        )
        assert r.status_code == 404  # only the stream's receiver client may poll

        assert http.get("/.well-known/ssf-configuration").json()["issuer"] == ISS

    async def ledger() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT status, count(*) FROM signals.security_signal WHERE tenant_id = :t GROUP BY 1"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return dict(rows)

    counts = asyncio.run(ledger())
    # disabled, enabled(new), compromise, override, internal session revoke = APPLIED; old enable
    # = OUT_OF_ORDER; two unresolved subjects; the replay added nothing
    assert counts == {"APPLIED": 5, "OUT_OF_ORDER": 1, "UNRESOLVED": 2}, counts
