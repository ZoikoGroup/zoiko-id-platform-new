"""Shared Signals Framework: SET receiver and transmitter (ZID-SIGNALS-001 §5-11, §21; RFC 8417,
RFC 8935 push, RFC 8936 poll, RFC 9493 subject identifiers).

Receiver (§8): stream credential -> explicit `secevent+jwt` type -> signature with the stream's pinned
keys and algorithms -> exact issuer and audience -> iat window -> jti -> exactly one allowlisted
event -> subject (iss_sub via a federation link, or stream-scoped opaque id; never email) -> the
critical-subject rule -> normalization -> trust policy -> ingest. A SET is never an access token
and an access token is never a SET (distinct `typ`, distinct verifier, SIG-04).

Transmitter (§21): internal events Zoiko is competent to assert become SETs for each active stream
that requested them, with a stream-scoped pairwise opaque subject, queued for poll delivery.
"""

from __future__ import annotations

import hmac
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext, uuid7
from zid_crypto import CryptoError, KeyRing, verify_external_jws
from zid_db import tenant_transaction
from zid_outbox import CloudEvent

from zid_platform.modules.authentication.ports import find_external_link
from zid_platform.modules.signals.application.ingest import CanonicalSignal, IngestResult, ingest
from zid_platform.modules.signals.domain.rules import (
    CAEP,
    RULES,
    Action,
    SignalError,
    normalize_event,
)
from zid_platform.modules.signals.infrastructure import signal_store as store

SET_TYP = "secevent+jwt"
SET_CONTENT_TYPE = "application/secevent+jwt"
MAX_SET_BYTES = 64 * 1024
SET_MAX_AGE = timedelta(days=1)
LEEWAY = timedelta(seconds=60)
VERIFICATION_EVENT = "https://schemas.openid.net/secevent/ssf/event-type/verification"
TRUST_CONFIDENCE = {"INFORMATIONAL": "LOW", "TRUSTED": "HIGH", "AUTHORITATIVE": "AUTHORITATIVE"}
# What an informational source may do: raise attention, never suspend or revoke (§6 source trust).
INFORMATIONAL_ACTIONS = frozenset({Action.NONE, Action.REEVALUATE, Action.STEP_UP})


class SsfError(Exception):
    """RFC 8935 §2.3 error: `err` code, safe description, HTTP status."""

    def __init__(self, err: str, description: str, status: int = 400) -> None:
        super().__init__(f"{err}: {description}")
        self.err, self.description, self.status = err, description, status


def parse_stream_ref(ref: str) -> tuple[str, str]:
    """`<tenant uuid>.<stream uuid>`: the push URL names its tenant so RLS can be set first."""
    tenant, _, stream = ref.partition(".")
    try:
        return str(uuid.UUID(tenant)), str(uuid.UUID(stream))
    except ValueError as e:
        raise SsfError("authentication_failed", "unknown stream", 401) from e


@dataclass(frozen=True, slots=True)
class Received:
    jti: str
    event_uri: str
    result: IngestResult | None


async def receive_push(
    engine: AsyncEngine,
    *,
    stream_ref: str,
    authorization: str | None,
    content_type: str | None,
    body: bytes,
    base_ctx: RequestContext,
    now: datetime,
) -> Received:
    tenant, stream_id = parse_stream_ref(stream_ref)
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        stream = await store.load_stream(conn, stream_id)
        if stream is None or stream.direction != "RECEIVER" or stream.push_token_hash is None:
            raise SsfError("authentication_failed", "unknown stream", 401)
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            store.sha256(token.strip()), stream.push_token_hash
        ):
            raise SsfError("authentication_failed", "stream credential invalid", 401)
        if stream.status != "ACTIVE":
            raise SsfError("access_denied", "STREAM_DISABLED", 403)
        if (content_type or "").split(";")[0].strip().lower() != SET_CONTENT_TYPE:
            raise SsfError("invalid_request", "content type must be application/secevent+jwt")
        if len(body) > MAX_SET_BYTES:
            raise SsfError("invalid_request", "SET too large")
        claims = _verify_set(body, stream, now)
        jti = claims["jti"]
        ((uri, event),) = claims["events"].items()
        if uri == VERIFICATION_EVENT:
            return Received(jti, uri, None)
        result = await _apply(conn, stream, claims, uri, event, body, now)
    return Received(jti, uri, result)


def _verify_set(body: bytes, stream: store.Stream, now: datetime) -> dict[str, Any]:
    try:
        token = body.decode("ascii").strip()
        _, claims = verify_external_jws(
            token,
            keys=(stream.jwks or {}).get("keys", []),
            algorithms=stream.algorithms,
            typ=SET_TYP,
        )
    except (CryptoError, UnicodeDecodeError) as e:
        raise SsfError("invalid_key", "SIGNAL_SIGNATURE_INVALID") from e
    if claims.get("iss") != stream.issuer:
        raise SsfError("invalid_issuer", "SIGNAL_ISSUER_MISMATCH")
    aud = claims.get("aud")
    if stream.audience not in (aud if isinstance(aud, list) else [aud]):
        raise SsfError("invalid_audience", "SIGNAL_AUDIENCE_INVALID")
    jti, iat = claims.get("jti"), claims.get("iat")
    if not isinstance(jti, str) or not 1 <= len(jti) <= 256:
        raise SsfError("invalid_request", "jti required")
    if not isinstance(iat, int | float) or isinstance(iat, bool):
        raise SsfError("invalid_request", "iat required")
    issued = datetime.fromtimestamp(iat, UTC)
    if issued > now + LEEWAY or now - issued > SET_MAX_AGE:
        raise SsfError("invalid_request", "iat outside the accepted window")
    if "exp" in claims or "cnf" in claims or "scope" in claims:
        raise SsfError("invalid_request", "not a security event token")  # JWT confusion (§8.1)
    events = claims.get("events")
    if not isinstance(events, dict) or len(events) != 1:
        raise SsfError("invalid_request", "exactly one event per SET")
    ((uri, event),) = events.items()
    if not isinstance(event, dict):
        raise SsfError("invalid_request", "SIGNAL_SCHEMA_INVALID")
    if uri != VERIFICATION_EVENT and uri not in stream.events:
        raise SsfError("invalid_request", "SIGNAL_EVENT_NOT_ALLOWED")
    return claims


async def _resolve(
    conn: AsyncConnection, stream: store.Stream, subject: dict[str, Any], critical: list[str]
) -> tuple[str | None, tuple[str, str] | None]:
    """Principal id (or None when unknown) and the federation link it came through, if any."""
    fmt = subject.get("format")
    if fmt == "complex":
        members = {k: v for k, v in subject.items() if k != "format"}
        for name in critical:
            if name not in ("user",):
                raise SsfError("invalid_request", "SIGNAL_CRITICAL_SUBJECT_UNSUPPORTED")  # SIG-07
        user = members.get("user")
        if not isinstance(user, dict):
            raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")
        return await _resolve(conn, stream, user, [])
    if fmt not in stream.subject_formats:
        raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")
    if fmt == "iss_sub":
        iss, sub = subject.get("iss"), subject.get("sub")
        if not isinstance(iss, str) or not isinstance(sub, str):
            raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")
        if iss != stream.federation_issuer:  # only subjects of the issuer this stream speaks for
            return None, None
        link = await find_external_link(conn, iss, sub)
        return (link.principal_id, (iss, sub)) if link else (None, None)
    if fmt == "opaque":
        oid = subject.get("id")
        if not isinstance(oid, str):
            raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")
        return await store.opaque_principal(conn, stream.stream_id, oid), None
    raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")


async def _apply(
    conn: AsyncConnection,
    stream: store.Stream,
    claims: dict[str, Any],
    uri: str,
    event: dict[str, Any],
    body: bytes,
    now: datetime,
) -> IngestResult:
    subject = claims.get("sub_id") or event.get("subject")
    if not isinstance(subject, dict):
        raise SsfError("invalid_request", "SIGNAL_SUBJECT_INVALID")
    critical = claims.get("critical_subject_members") or []
    principal_id, link = await _resolve(conn, stream, subject, list(critical))
    try:
        event_type = normalize_event(uri, event)
    except SignalError as e:
        raise SsfError("invalid_request", e.code) from e
    if stream.source_trust == "INFORMATIONAL" and RULES[event_type].action not in (
        INFORMATIONAL_ACTIONS
    ):
        event_type = "risk.elevated"
    ts = event.get("event_timestamp")
    effective = (
        datetime.fromtimestamp(ts, UTC)
        if isinstance(ts, int | float) and not isinstance(ts, bool)
        else datetime.fromtimestamp(claims["iat"], UTC)
    )
    subject_commitment = store.sha256(json.dumps(subject, sort_keys=True))
    if principal_id is None:  # unknown subject: recorded for reconciliation, not revealed (§9)
        await store.insert_signal(
            conn,
            store.SignalRow(
                signal_id=str(uuid7()),
                tenant_id=stream.tenant_id,
                subject_type="PRINCIPAL",
                subject_ref="unresolved",
                source_id=f"ssf:{stream.stream_id}",
                source_event_id=claims["jti"],
                source_event_type=uri,
                event_type=event_type,
                severity="INFO",
                confidence=TRUST_CONFIDENCE[stream.source_trust],
                required_action="NONE",
                risk_class_floor=0,
                effective_at=effective,
                observed_at=now,
                normalized_at=now,
                dedupe_key_hash=store.sha256(f"ssf:{stream.stream_id}|{claims['jti']}"),
                payload_commitment=store.sha256(body),
                source_subject_commitment=subject_commitment,
                status="UNRESOLVED",
                reason_code="SIGNAL_SUBJECT_UNRESOLVED",
                state_revision=None,
            ),
        )
        return IngestResult("", "UNRESOLVED", None, None, "SIGNAL_SUBJECT_UNRESOLVED")
    return await ingest(
        conn,
        CanonicalSignal(
            tenant_id=stream.tenant_id,
            subject_type="PRINCIPAL",
            subject_ref=principal_id,
            source_id=f"ssf:{stream.stream_id}",
            source_event_id=claims["jti"],
            source_event_type=uri,
            event_type=event_type,
            effective_at=effective,
            observed_at=now,
            confidence=TRUST_CONFIDENCE[stream.source_trust],
            external_link=link,
            payload_commitment=store.sha256(body),
            source_subject_commitment=subject_commitment,
        ),
        now=now,
    )


# ------------------------------------------------------------------------------------ transmitter

# Internal event -> (external event URI, event body). Only what Zoiko is authoritative for (§21).
OUTBOUND: dict[str, tuple[str, dict[str, Any]]] = {
    "com.zoiko.id.session.revoked.v1": (CAEP + "session-revoked", {}),
    "com.zoiko.id.authenticator.revoked.v1": (
        CAEP + "credential-change",
        {"credential_type": "fido2-roaming", "change_type": "revoke"},
    ),
}
TRANSMITTER_CONSUMER = "signals.ssf_transmitter.v1"


def build_set(
    keyring: KeyRing,
    *,
    issuer: str,
    audience: str,
    event_uri: str,
    subject: dict[str, Any],
    event: dict[str, Any],
    now: datetime,
) -> tuple[str, str]:
    jti = str(uuid7())
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "jti": jti,
        "sub_id": subject,
        "events": {event_uri: {**event, "event_timestamp": int(now.timestamp())}},
    }
    return jti, keyring.sign(claims, typ=SET_TYP)


async def transmit(
    conn: AsyncConnection, keyring: KeyRing, issuer: str, event: CloudEvent, now: datetime
) -> int:
    """Queue one SET per active transmitter stream that asked for this event type."""
    mapping = OUTBOUND.get(event.type)
    principal = event.data.get("principal_id")
    if mapping is None or not isinstance(principal, str):
        return 0
    uri, body = mapping
    queued = 0
    for stream in await store.transmitter_streams(conn, uri):
        opaque = await store.opaque_for(conn, stream.stream_id, principal)
        if opaque is None:  # pairwise per stream: receivers cannot correlate across streams
            opaque = secrets.token_urlsafe(16)
            await store.map_subject(
                conn,
                stream_id=stream.stream_id,
                tenant_id=stream.tenant_id,
                opaque_id=opaque,
                principal_id=principal,
            )
        jti, jwt = build_set(
            keyring,
            issuer=issuer,
            audience=stream.audience,
            event_uri=uri,
            subject={"format": "opaque", "id": opaque},
            event=dict(body),
            now=now,
        )
        await store.enqueue_set(
            conn,
            stream_id=stream.stream_id,
            tenant_id=stream.tenant_id,
            jti=jti,
            set_jwt=jwt,
            committed_at=event.time,  # the authoritative commit, not when the consumer got to it
            risk_floor=3 if event.type.endswith("session.revoked.v1") else 2,
        )
        queued += 1
    return queued


@dataclass(frozen=True, slots=True)
class Reconciliation:
    detected: int  # SETs past their propagation SLO with no acknowledgement
    repaired: int  # re-issued from authoritative state
    withdrawn: int  # authoritative state no longer holds the fact: superseded, not resent


def _own_claims(set_jwt: str) -> dict[str, Any]:
    """Claims of a SET this cell signed itself (read for repair only; nothing here is trusted input)."""
    import base64

    payload = set_jwt.split(".")[1]
    return dict(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))


async def reconcile_outbound(
    conn: AsyncConnection,
    keyring: KeyRing,
    issuer: str,
    stream: store.Stream,
    now: datetime,
    still_holds: Callable[[str, str], Awaitable[bool]] | None = None,
) -> Reconciliation:
    """SIG-25: a SET the receiver has not acknowledged inside its SLO is treated as dropped.

    It is replaced by a freshly signed SET for the same subject and event, keeping the original commit time so the
    lateness stays visible in the propagation measures. `still_holds(principal_id, event_uri)` asks the
    authoritative source whether the fact is still true; when it is not, the stale SET is withdrawn and nothing is
    sent. The old SET is superseded either way, so a receiver never gets both."""
    repaired = withdrawn = 0
    stalled = await store.stalled_sets(conn, stream.stream_id, now)
    for row in stalled:
        claims = _own_claims(row["set_jwt"])
        events = claims.get("events") or {}
        subject = (claims.get("sub_id") or {}).get("id")
        if len(events) != 1 or not isinstance(subject, str):
            continue
        uri, body = next(iter(events.items()))
        principal = await store.opaque_principal(conn, stream.stream_id, subject)
        if principal is None:
            continue
        if still_holds is not None and not await still_holds(principal, uri):
            await store.supersede_set(conn, stream.stream_id, row["jti"], now)
            withdrawn += 1
            continue
        jti, jwt = build_set(
            keyring,
            issuer=issuer,
            audience=stream.audience,
            event_uri=uri,
            subject={"format": "opaque", "id": subject},
            event={k: v for k, v in body.items() if k != "event_timestamp"},
            now=now,
        )
        await store.enqueue_set(
            conn,
            stream_id=stream.stream_id,
            tenant_id=stream.tenant_id,
            jti=jti,
            set_jwt=jwt,
            committed_at=row["event_committed_at"],
            risk_floor=int(row["risk_floor"]),
            repair_of=row["jti"],
            repaired_at=now,
        )
        await store.supersede_set(conn, stream.stream_id, row["jti"], now)
        repaired += 1
    return Reconciliation(len(stalled), repaired, withdrawn)


def ssf_configuration(issuer: str) -> dict[str, Any]:
    base = issuer.rstrip("/")
    return {
        "spec_version": "1_0",
        "issuer": issuer,
        "jwks_uri": f"{base}/ssf/jwks",  # SET keys are separate from token keys (§21)
        "delivery_methods_supported": ["urn:ietf:rfc:8936", "urn:ietf:rfc:8935"],
        "configuration_endpoint": f"{base}/ssf/streams",
        "authorization_schemes": [{"spec_urn": "urn:ietf:rfc:6749"}],
        "default_subjects": "NONE",
        "events_supported": sorted({uri for uri, _ in OUTBOUND.values()}),
        "critical_subject_members": ["user"],
    }


__all__ = [
    "Received",
    "SsfError",
    "build_set",
    "receive_push",
    "reconcile_outbound",
    "ssf_configuration",
    "transmit",
]


def transmitter_handler(engine: AsyncEngine, keyring: KeyRing, issuer: str, cell_id: str):  # type: ignore[no-untyped-def]
    """Outbox consumer: each mapped internal event becomes SETs, once (inbox dedupe)."""
    from zid_context import new_id
    from zid_outbox import consume

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        await transmit(conn, keyring, issuer, event, datetime.now(UTC))

    async def handle(event: CloudEvent) -> None:
        if event.type not in OUTBOUND:
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid
        )
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, TRANSMITTER_CONSUMER, event, apply)

    return handle


TRANSMITTER_TOPICS = (("session", "priority"), ("authenticator", "priority"))  # (domain, lane)
