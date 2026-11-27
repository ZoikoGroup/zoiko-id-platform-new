"""Outside identity provider bindings (ZID-CP-WF07 v2.0 .03; A07-11, A07-12; CR-169): evidence, never authority.

A company allowlists a provider for ONE agent: the exact issuer, the audience, the provider's own subject for the agent and the PUBLIC keys it pins. Later a token from that provider can be PRESENTED to prove the binding still
holds: the signature is checked against the pinned keys only (a key named in the token is never fetched), then issuer, audience, subject, expiry, lifetime and a one-time id. A token that passes refreshes what the provider says
about the agent, kept as an outside claim. It changes nothing the agent may do: it cannot activate an agent, assert a corporate authority or create a mandate. A binding whose provider has gone quiet turns STALE and is not relied on.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, verify_external_jws

from zid_platform.modules.evidence.ports import EvidenceClass
from zid_platform.modules.machine.application.agents import _record
from zid_platform.modules.machine.domain import idp_binding as d
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.infrastructure import idp_store, machine_store

STALE_AFTER = int(
    timedelta(hours=24).total_seconds()
)  # a provider not heard from in a day is not relied on


def _iso(x: datetime | None) -> str | None:
    return x.isoformat() if x else None


def view(b: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    prof = d.PROFILES[b["profile"]]
    return {
        "binding_id": str(b["binding_id"]), "agent_id": str(b["agent_principal_id"]), "profile": b["profile"], "profile_label": prof["label"], "certified": prof["certified"], "issuer": b["issuer"],
        "subject": b["subject"], "audience": b["audience"], "environment": b["environment"], "algorithms": list(b["algs"]), "key_ids": [k["kid"] for k in b["keys"]], "keys_fingerprint": b["keys_fingerprint"],
        "state": d.binding_state(status=b["status"], expires_at=b["expires_at"], now=now, last_ok_at=b["last_ok_at"], stale_after_s=STALE_AFTER), "last_ok_at": _iso(b["last_ok_at"]),
        "last_failure": b["last_failure"], "external_claims": b["external_claims"], "claims_seen_at": _iso(b["claims_seen_at"]), "created_at": _iso(b["created_at"]), "expires_at": _iso(b["expires_at"]),
        "note": prof.get("note"), "is_authority": False,  # what a provider says is evidence: it never grants anything
    }  # fmt: skip


async def create(
    conn: AsyncConnection, tenant_id: str, actor: str, agent_id: str, *, profile: str, issuer: str, subject: str, audience: str, keys: Any, algorithms: list[str] | None, environment: str,
    expires_at: datetime | None, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    now = now or datetime.now(UTC)
    if await machine_store.load_agent(conn, agent_id) is None:
        raise MachineError("AGENT_NOT_FOUND", "no such agent")
    if environment not in ("SANDBOX", "PRODUCTION"):
        raise MachineError("IDP_BINDING_INVALID", "an environment is SANDBOX or PRODUCTION")
    if expires_at is not None and expires_at <= now:
        raise MachineError("IDP_BINDING_INVALID", "a binding cannot expire in the past")
    clean_keys, algs = d.check_keys(keys, algorithms or [])
    bid = str(uuid.uuid4())
    made = await idp_store.insert(
        conn, tenant_id, binding_id=bid, agent_id=agent_id, profile=d.check_profile(profile), issuer=d.check_issuer(issuer), subject=d.check_subject(subject), audience=d.check_audience(audience),
        environment=environment, keys=clean_keys, algs=algs, fingerprint=d.keys_fingerprint(clean_keys), created_by=actor, expires_at=expires_at,
    )  # fmt: skip
    if not made:
        raise MachineError(
            "IDP_BINDING_DUPLICATE",
            "this provider's identifier for an agent is already bound, in this environment. A name or an e-mail never merges two agents",
        )
    await _record(
        conn, tenant_id, name="agent.idp_bound", event=False, aggregate_type="agent", aggregate_id=agent_id, actor=actor, evidence=EvidenceClass.E2,
        data={"binding_id": bid, "profile": profile, "issuer": issuer, "environment": environment},
    )  # fmt: skip
    got = await idp_store.get(conn, bid)
    assert got is not None
    return view(got, now)


async def present(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    binding_id: str,
    token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check a token from the provider against what the company pinned. Always returns an answer: a failed check is recorded on the binding and reported, never raised, so the reason is not lost."""
    now = now or datetime.now(UTC)
    b = await idp_store.get(conn, binding_id, lock=True)
    if b is None:
        raise MachineError("IDP_BINDING_NOT_FOUND", "no such binding")
    state = d.binding_state(
        status=b["status"],
        expires_at=b["expires_at"],
        now=now,
        last_ok_at=now,
        stale_after_s=STALE_AFTER,
    )  # staleness is about the provider going quiet, not about this very check
    if state != "ACTIVE":
        return {
            "accepted": False,
            "reason_codes": ["IDP_BINDING_" + state],
            "binding": view(b, now),
        }
    prof = d.PROFILES[b["profile"]]
    codes: list[str] = []
    claims: Mapping[str, Any] = {}
    try:
        _header, claims = verify_external_jws(token, keys=b["keys"], algorithms=list(b["algs"]))
    except CryptoError as e:
        codes = [
            "IDP_SIGNATURE_INVALID"
            if e.code in ("SIGNATURE_INVALID", "KEY_UNKNOWN", "ALG_KEY_MISMATCH")
            else "IDP_TOKEN_REJECTED"
        ]
    if not codes:
        codes = d.claim_problems(
            claims,
            issuer=b["issuer"],
            audience=b["audience"],
            subject=b["subject"],
            now=now,
            needs_jti=bool(prof["needs_jti"]),
        )
    if (
        not codes
        and claims.get("jti")
        and not await idp_store.take_proof(
            conn, tenant_id, binding_id, d.token_digest(str(claims["jti"])), now
        )
    ):
        codes = ["IDP_TOKEN_REPLAYED"]
    if codes:
        await idp_store.record_failure(conn, binding_id, codes[0])
        await _record(
            conn, tenant_id, name="agent.idp_token_refused", event=False, aggregate_type="agent", aggregate_id=str(b["agent_principal_id"]), actor=actor, evidence=EvidenceClass.E2, outcome="DENIED",
            data={"binding_id": binding_id, "reason_codes": ",".join(codes)},
        )  # fmt: skip
        again = await idp_store.get(conn, binding_id)
        assert again is not None
        return {"accepted": False, "reason_codes": codes, "binding": view(again, now)}
    await idp_store.record_ok(conn, binding_id, d.external_claims(b["profile"], claims), now)
    await _record(
        conn, tenant_id, name="agent.idp_token_accepted", event=False, aggregate_type="agent", aggregate_id=str(b["agent_principal_id"]), actor=actor, evidence=EvidenceClass.E2,
        data={"binding_id": binding_id, "proof_digest": d.token_digest(token)},
    )  # fmt: skip
    ok = await idp_store.get(conn, binding_id)
    assert ok is not None
    return {
        "accepted": True,
        "reason_codes": [],
        "binding": view(ok, now),
        "grants_authority": False,
    }


async def revoke(
    conn: AsyncConnection, tenant_id: str, actor: str, binding_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    b = await idp_store.get(conn, binding_id, lock=True)
    if b is None:
        raise MachineError("IDP_BINDING_NOT_FOUND", "no such binding")
    if not await idp_store.revoke(conn, binding_id, by=actor, now=now):
        raise MachineError("IDP_BINDING_STATE_INVALID", "this binding is already revoked")
    await _record(
        conn,
        tenant_id,
        name="agent.idp_revoked",
        event=False,
        aggregate_type="agent",
        aggregate_id=str(b["agent_principal_id"]),
        actor=actor,
        evidence=EvidenceClass.E2,
        data={"binding_id": binding_id},
    )
    after = await idp_store.get(conn, binding_id)
    assert after is not None
    return view(after, now)


async def list_for(conn: AsyncConnection, agent_id: str | None) -> dict[str, Any]:
    rows = (
        await idp_store.for_agent(conn, agent_id)
        if agent_id
        else await idp_store.list_all(conn, limit=100)
    )
    return {
        "items": [view(b) for b in rows],
        "profiles": [
            {"profile": k, "label": v["label"], "certified": v["certified"], "note": v.get("note")}
            for k, v in d.PROFILES.items()
        ],
    }


async def read(conn: AsyncConnection, binding_id: str) -> dict[str, Any]:
    b = await idp_store.get(conn, binding_id)
    if b is None:
        raise MachineError("IDP_BINDING_NOT_FOUND", "no such binding")
    return view(b)
