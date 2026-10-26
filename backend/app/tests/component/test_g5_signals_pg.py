"""Security-signal acceptance rows closed in group g5 (PostgreSQL, real RLS).

SIG-09 same e-mail at different issuers/tenants never merges principals
SIG-11 CAEP credential revoke revokes the authenticator and forces re-authentication
SIG-13 non-compliant device restricts the subject: R2/R3 are denied by the PDP
SIG-15 recycled identifier severs the old mapping and demands re-proofing
SIG-23 a bulk informational flood cannot starve priority (R3) propagation
SIG-26 a quarantined transmitter stream stops affecting state; history remains
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
from joserfc import jws
from joserfc.jwk import RSAKey
from sqlalchemy import text
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_outbox import emit, relay_once
from zid_platform.modules.authentication.domain.authenticator import (
    Authenticator,
    AuthenticatorStatus,
)
from zid_platform.modules.authentication.infrastructure.authenticator_store import (
    add_authenticator,
)
from zid_platform.modules.authentication.infrastructure.authenticator_store import (
    load as load_authenticator,
)
from zid_platform.modules.authentication.ports import (
    create_external_link,
    find_external_link,
)
from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.decision.ports import (
    AuthnFact,
    DecisionRequest,
    Outcome,
    action_spec,
    evaluate_request,
)
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    development_runtime,
)
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import (
    Session,
    SessionPolicy,
    SessionStatus,
    check,
)
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.signals.application.ingest import CanonicalSignal, ingest
from zid_platform.modules.signals.application.ssf import SsfError, receive_push
from zid_platform.modules.signals.application.state import pdp_security_state
from zid_platform.modules.signals.infrastructure import signal_store as store
from zid_platform.protected import catalog

RISC = "https://schemas.openid.net/secevent/risc/event-type/"
SET_CT = "application/secevent+jwt"
TX_ISS, IDP_A, IDP_B = (
    "https://ssf.idp.example",
    "https://login.a.example",
    "https://login.b.example",
)
LOCAL_ISS = "https://id.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


def _sig(
    tenant: str, event: str, subject: str, *, stype: str = "PRINCIPAL", src: str = "risk", **kw: Any
) -> CanonicalSignal:
    now = datetime.now(UTC)
    return CanonicalSignal(
        tenant_id=tenant,
        subject_type=stype,
        subject_ref=subject,
        source_id=src,
        source_event_id=uid(),
        source_event_type=f"zoiko:{event}",
        event_type=event,
        effective_at=now,
        observed_at=now,
        confidence=kw.pop("confidence", "HIGH"),
        **kw,
    )


async def _principal(c: Any, tenant: str, pid: str) -> None:
    await create_principal(
        c, Principal(pid, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
    )


async def _session(c: Any, tenant: str, pid: str) -> Session:
    now = datetime.now(UTC)
    principal, _ = await load_principal(c, pid)
    s = Session(
        str(uuid.uuid4()),
        tenant,
        pid,
        now - timedelta(seconds=30),
        "native:webauthn",
        2,
        principal.security_revision,
        1,
        SessionStatus.ACTIVE,
        now + timedelta(hours=8),
    )
    await create_session(c, s)
    return s


def test_credential_revoke_signal_revokes_the_authenticator_and_blocks_old_sessions(
    rt_url: str,
) -> None:
    """SIG-11: a credential-change/revoke signal (CAEP, normalized to credential.revoked) revokes
    that authenticator at once, moves the security state to REAUTH_REQUIRED and increments the
    principal's security revision, so the session the credential backed fails its next check."""
    tenant, alice = uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _principal(c, tenant, alice)
            auth_id = uid()
            await add_authenticator(
                c,
                Authenticator(
                    auth_id,
                    tenant,
                    alice,
                    "PASSKEY",
                    AuthenticatorStatus.ACTIVE,
                    now,
                    credential_id=uuid.uuid4().bytes,
                    public_key_cose=b"k",
                ),
                aal=2,
            )
            session = await _session(c, tenant, alice)
            policy = SessionPolicy()
            principal, _ = await load_principal(c, alice)
            assert check(
                session,
                principal_revision=principal.security_revision,
                principal_active=True,
                now=now,
                policy=policy,
            ).ok
            r = await ingest(
                c,
                _sig(tenant, "credential.revoked", auth_id, stype="CREDENTIAL", principal_id=alice),
                now=now,
            )
            assert r.status == "APPLIED" and r.state == "REAUTH_REQUIRED", r
            assert (await load_authenticator(c, auth_id)).status is AuthenticatorStatus.REVOKED
            principal2, _ = await load_principal(c, alice)
            assert principal2.security_revision > principal.security_revision
            blocked = check(
                session,
                principal_revision=principal2.security_revision,
                principal_active=True,
                now=now,
                policy=policy,
            )
            assert not blocked.ok and blocked.code == "SESSION_REAUTH_REQUIRED"
            # a plain "credential changed" (not a revoke) re-evaluates but revokes nothing
            other = uid()
            await add_authenticator(
                c,
                Authenticator(
                    other,
                    tenant,
                    alice,
                    "PASSKEY",
                    AuthenticatorStatus.ACTIVE,
                    now,
                    credential_id=uuid.uuid4().bytes,
                    public_key_cose=b"k",
                ),
                aal=2,
            )
            r = await ingest(
                c,
                _sig(tenant, "credential.changed", other, stype="CREDENTIAL", principal_id=alice),
                now=now,
            )
            assert r.status == "APPLIED" and r.state is None
            assert (await load_authenticator(c, other)).status is AuthenticatorStatus.ACTIVE

    asyncio.run(run())


async def _decide_r3(c: Any, tenant: str, actor: str, security_state: str) -> Any:
    now = datetime.now(UTC)
    return await evaluate_request(
        c,
        req=DecisionRequest(
            decision_id=uid(),
            tenant_id=tenant,
            action=action_spec("evidence.export", catalog()),
            resource_id="receipt:1",
            represented_principal_id=None,
            purpose=None,
            tool_id=None,
            at=now,
        ),
        executor_id=actor,
        authority_request=AuthorityRequest("evidence.export", "receipt:1", now, actor_assurance=3),
        authn=AuthnFact(3, now - timedelta(minutes=1)),
        security_state=security_state,
        catalog=catalog(),
    )


def test_noncompliant_device_restricts_and_recycled_identifier_severs_the_old_mapping(
    rt_url: str,
) -> None:
    """SIG-13: device.noncompliant moves the principal to RESTRICTED and the PDP then denies an
    R3 action it allowed a moment earlier (SECURITY_STATE_RESTRICTED); device.compliant alone does
    not lift it. SIG-15: identifier.recycled severs the (issuer, subject) link so the old identifier
    finds no principal, revokes sessions, and requires re-proofing (REPROOF_REQUIRED, R3 denied)."""
    configure_runtime(development_runtime("uk-1"))
    tenant, admin, victim = uid(), uid(), uid()

    async def run() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _principal(c, tenant, admin)
            await make_admin(c, tenant, admin, ["evidence.export"], ["receipt:*"])
            await _principal(c, tenant, victim)
            before = await _decide_r3(
                c, tenant, admin, await pdp_security_state(c, admin, auth_time=now)
            )
            assert before.outcome is Outcome.ALLOW, before.reason_codes

            # SIG-13
            r = await ingest(c, _sig(tenant, "device.noncompliant", admin), now=now)
            assert r.status == "APPLIED" and r.state == "RESTRICTED"
            state = await pdp_security_state(c, admin, auth_time=now + timedelta(minutes=5))
            assert state == "RESTRICTED"  # fresh authentication does not lift a restriction
            denied = await _decide_r3(c, tenant, admin, state)
            assert denied.outcome is Outcome.DENY and "SECURITY_STATE_RESTRICTED" in (
                denied.reason_codes
            )
            await ingest(c, _sig(tenant, "device.compliant", admin), now=now)
            assert await pdp_security_state(c, admin, auth_time=None) == "RESTRICTED"

            # SIG-15
            link = await create_external_link(
                c,
                tenant_id=tenant,
                principal_id=victim,
                issuer=IDP_A,
                subject="old-id",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=now,
            )
            session = await _session(c, tenant, victim)
            assert (await find_external_link(c, IDP_A, "old-id")).principal_id == victim
            r = await ingest(
                c,
                _sig(tenant, "identifier.recycled", victim, external_link=(IDP_A, "old-id")),
                now=now,
            )
            assert r.status == "APPLIED" and r.state == "REPROOF_REQUIRED"
            assert await find_external_link(c, IDP_A, "old-id") is None  # cannot authenticate
            row = (
                await c.execute(
                    text("SELECT status FROM authn.external_identity_link WHERE link_id = :l"),
                    {"l": uuid.UUID(link.link_id)},
                )
            ).scalar_one()
            assert row != "ACTIVE"
            revoked = (
                await c.execute(
                    text("SELECT status FROM authn.session WHERE session_id = :s"),
                    {"s": uuid.UUID(session.session_id)},
                )
            ).scalar_one()
            assert revoked == "REVOKED"
            assert await pdp_security_state(c, victim, auth_time=now) == "REPROOF_REQUIRED"

    asyncio.run(run())
    configure_runtime(None)


def _sign_set(key: RSAKey, claims: dict[str, Any], kid: str = "tx-1") -> bytes:
    header = {"alg": "RS256", "kid": kid, "typ": "secevent+jwt"}
    return jws.serialize_compact(
        header, json.dumps(claims).encode(), key, algorithms=["RS256"]
    ).encode()


def _risc(event: str, issuer: str, sub: str) -> dict[str, Any]:
    return {
        "iss": TX_ISS,
        "aud": LOCAL_ISS,
        "iat": int(time.time()),
        "jti": uid(),
        "sub_id": {"format": "iss_sub", "iss": issuer, "sub": sub},
        "events": {event: {}},
    }


def test_same_email_at_two_issuers_or_tenants_never_merges_principals_and_email_subjects_fail(
    rt_url: str, owner_url: str
) -> None:
    """SIG-09: the same person-shaped identity (same e-mail) is asserted by two issuers and in two
    tenants. Signals address subjects only by (issuer, sub) through a federation link or an opaque
    stream id: an account-disabled for issuer A's subject suspends only A's principal, the namesake
    behind issuer B and in the other tenant are untouched, an issuer the stream does not speak
    for resolves to nothing, and an e-mail subject is refused outright."""
    t1, t2 = uid(), uid()
    alice_a, alice_b, alice_t2 = uid(), uid(), uid()
    key = RSAKey.generate_key(2048)
    jwk = {**key.as_dict(private=False), "kid": "tx-1", "use": "sig"}
    stream_id, token = uid(), "stream-credential"

    async def seed() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t1)) as c:
            for p in (alice_a, alice_b):
                await _principal(c, t1, p)
            # both external accounts carry the same e-mail at their IdPs; links never use e-mail
            await create_external_link(
                c,
                tenant_id=t1,
                principal_id=alice_a,
                issuer=IDP_A,
                subject="alice@corp.example",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=now,
            )
            await create_external_link(
                c,
                tenant_id=t1,
                principal_id=alice_b,
                issuer=IDP_B,
                subject="alice@corp.example",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=now,
            )
            await store.create_stream(
                c,
                store.Stream(
                    stream_id,
                    t1,
                    "RECEIVER",
                    TX_ISS,
                    LOCAL_ISS,
                    {"keys": [jwk]},
                    ("RS256",),
                    (RISC + "account-disabled",),
                    ("iss_sub", "opaque"),
                    "push",
                    store.sha256(token),
                    "AUTHORITATIVE",
                    IDP_A,
                    None,
                    "ACTIVE",
                    1,
                ),
            )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t2)) as c:
            await _principal(c, t2, alice_t2)
            await create_external_link(
                c,
                tenant_id=t2,
                principal_id=alice_t2,
                issuer=IDP_A + "/t2",
                subject="alice@corp.example",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=now,
            )

    async def push(claims: dict[str, Any]) -> Any:
        async with engine(rt_url) as eng:
            return await receive_push(
                eng,
                stream_ref=f"{t1}.{stream_id}",
                authorization=f"Bearer {token}",
                content_type=SET_CT,
                body=_sign_set(key, claims),
                base_ctx=RequestContext("req_g5", "cor_g5", "test", "uk-1"),
                now=datetime.now(UTC),
            )

    async def statuses() -> dict[str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT principal_id::text AS p, status FROM core.principal "
                        "WHERE principal_id = ANY(:ids)"
                    ),
                    {"ids": [uuid.UUID(x) for x in (alice_a, alice_b, alice_t2)]},
                )
            ).all()
        return {r.p: r.status for r in rows}

    async def run() -> None:
        await seed()
        # an e-mail subject identifier is never accepted, even for an e-mail that exists
        bad = _risc(RISC + "account-disabled", IDP_A, "x")
        bad["sub_id"] = {"format": "email", "email": "alice@corp.example"}
        try:
            await push(bad)
            raise AssertionError("e-mail subject must be refused")
        except SsfError as e:
            assert e.description == "SIGNAL_SUBJECT_INVALID"
        # issuer B's namesake, sent down the stream that speaks for issuer A: resolves to nobody
        r = await push(_risc(RISC + "account-disabled", IDP_B, "alice@corp.example"))
        assert r.result is not None and r.result.status == "UNRESOLVED"
        assert set((await statuses()).values()) == {"ACTIVE"}
        # issuer A's subject: exactly one principal is affected
        r = await push(_risc(RISC + "account-disabled", IDP_A, "alice@corp.example"))
        assert r.result is not None and r.result.status == "APPLIED"
        assert await statuses() == {
            alice_a: "SUSPENDED",
            alice_b: "ACTIVE",
            alice_t2: "ACTIVE",
        }

    asyncio.run(run())


def test_bulk_informational_flood_cannot_starve_priority_security_events(
    rt_url: str, owner_url: str
) -> None:
    """SIG-23: 600 informational events are queued first and a security.state.changed event last.
    With a tiny relay batch the priority event is still in the very first batch published; and a
    relay assigned only the priority lane never touches the flood at all."""
    tenant = uid()

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []

        async def publish(self, topic: str, key: str, event: Any) -> None:
            self.sent.append((topic, event.type))

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i in range(600):
                await emit(
                    c,
                    name="mandate.reservation.created",
                    tenant_id=tenant,
                    aggregate_type="g5_flood",
                    aggregate_id=f"f{i}",
                    aggregate_version=1,
                    data={"i": i},
                )
            await emit(
                c,
                name="security.state.changed",
                tenant_id=tenant,
                aggregate_type="g5_flood",
                aggregate_id="urgent",
                aggregate_version=1,
                data={"current_state": "COMPROMISED"},
            )
        async with engine(owner_url) as eng, eng.begin() as c:  # park other tests' backlog
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = now() "
                    "WHERE published_at IS NULL AND tenant_id <> :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        only_priority = Recorder()
        async with engine(owner_url) as eng, eng.begin() as c:
            n = await relay_once(
                c, only_priority, env="test", region="uk-1", batch=10, lanes=("priority",)
            )
        assert n == 1 and only_priority.sent[0][1].endswith("security.state.changed.v1")
        # a relay that serves every lane with a batch of 10 under the same flood: priority first
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = NULL, attempts = 0 "
                    "WHERE aggregate_type = 'g5_flood' AND aggregate_id = 'urgent'"
                )
            )
        everything = Recorder()
        async with engine(owner_url) as eng, eng.begin() as c:
            n = await relay_once(c, everything, env="test", region="uk-1", batch=10)
        assert n == 10 and everything.sent[0][0].endswith(".priority")
        assert everything.sent[0][1].endswith("security.state.changed.v1")

    asyncio.run(run())


def test_quarantined_transmitter_stops_affecting_state_but_history_remains(
    rt_url: str, owner_url: str
) -> None:
    """SIG-26: after the stream is quarantined (disabled) a validly signed SET from that
    transmitter is refused (403) and changes nothing; the signal already applied, its state row and
    its append-only record remain; the stream credential and keys are not what re-enables it."""
    tenant, alice = uid(), uid()
    key = RSAKey.generate_key(2048)
    jwk = {**key.as_dict(private=False), "kid": "tx-1", "use": "sig"}
    stream_id, token = uid(), "stream-credential"

    async def push(claims: dict[str, Any]) -> Any:
        async with engine(rt_url) as eng:
            return await receive_push(
                eng,
                stream_ref=f"{tenant}.{stream_id}",
                authorization=f"Bearer {token}",
                content_type=SET_CT,
                body=_sign_set(key, claims),
                base_ctx=RequestContext("req_g5", "cor_g5", "test", "uk-1"),
                now=datetime.now(UTC),
            )

    async def run() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _principal(c, tenant, alice)
            await create_external_link(
                c,
                tenant_id=tenant,
                principal_id=alice,
                issuer=IDP_A,
                subject="u-1",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=now,
            )
            await store.create_stream(
                c,
                store.Stream(
                    stream_id,
                    tenant,
                    "RECEIVER",
                    TX_ISS,
                    LOCAL_ISS,
                    {"keys": [jwk]},
                    ("RS256",),
                    (RISC + "account-disabled", RISC + "credential-compromise"),
                    ("iss_sub", "opaque"),
                    "push",
                    store.sha256(token),
                    "AUTHORITATIVE",
                    IDP_A,
                    None,
                    "ACTIVE",
                    1,
                ),
            )
        r = await push(_risc(RISC + "credential-compromise", IDP_A, "u-1"))
        assert r.result is not None and r.result.state == "COMPROMISED"
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await store.set_stream_status(c, stream_id, "DISABLED")  # quarantine
        try:
            await push(_risc(RISC + "account-disabled", IDP_A, "u-1"))
            raise AssertionError("a quarantined stream must not be accepted")
        except SsfError as e:
            assert (e.status, e.description) == (403, "STREAM_DISABLED")
        async with engine(owner_url) as eng, eng.begin() as c:
            p = (
                await c.execute(
                    text("SELECT status FROM core.principal WHERE principal_id = :p"),
                    {"p": uuid.UUID(alice)},
                )
            ).scalar_one()
            state = (
                await c.execute(
                    text(
                        "SELECT state, revision FROM signals.security_state "
                        "WHERE subject_id = :s AND subject_type = 'PRINCIPAL'"
                    ),
                    {"s": alice},
                )
            ).one()
            signals = (
                await c.execute(
                    text(
                        "SELECT event_type, status FROM signals.security_signal "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        assert p == "ACTIVE"  # the refused account-disabled had no effect
        assert (state.state, state.revision) == ("COMPROMISED", 1)
        assert [(s.event_type, s.status) for s in signals] == [
            ("credential.compromised", "APPLIED")
        ]

    asyncio.run(run())
