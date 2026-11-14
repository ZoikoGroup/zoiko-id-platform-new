"""The API agent's credential path (CR-094): a signed one-time assertion in, a short-lived token for the agent principal out.

An API client (CR-093) holds a private key the platform never sees. To act it signs a short assertion naming itself, the platform's token
address, one audience it was registered with and a single-use id. The platform:

  1. reads WHO claims to be signing (client id, and the company that owns it) only to choose where to look; nothing in it is trusted yet,
  2. verifies the signature against the PUBLIC keys stored with the approved build (never a key the assertion carries or points to),
  3. checks the claims exactly (issuer, audience, registered resource, a lifetime of a minute at most),
  4. burns the assertion id BEFORE deciding anything else, so a replay fails even when the first use was refused,
  5. checks the agent: active, not frozen, binding live, build approved and not revoked, release unchanged, and the attestation an API client
     can show (capped at API_ATTESTATION_CAP) against the floor of the risk class the token asks for,
  6. records evidence of the issue or the refusal. The route then mints the token.

A refusal is returned as a result so that the burned id and the evidence are committed; the route turns it into an error. Failures before the
signature verifies all look the same to the caller (one code), so the route is not an oracle for which clients exist.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, b64url_decode, jwk_thumbprint, peek_header, verify_external_jws

from zid_platform.modules.machine.application import agents as agent_service
from zid_platform.modules.machine.application import governance
from zid_platform.modules.machine.application.tool_gateway import load_chain
from zid_platform.modules.machine.domain import api_agents as d
from zid_platform.modules.machine.domain.agents import AgentStatus
from zid_platform.modules.machine.infrastructure import agent_store, build_store, machine_store

UNVERIFIED = "ASSERTION_INVALID"


def unverified_claims(token: str) -> dict[str, Any]:
    """The payload of a compact JWS WITHOUT checking it. Used only to choose which company and client to look up; never trusted."""
    try:
        parts = token.split(".")
        if len(parts) != 3 or len(token) > 4096:
            return {}
        payload = json.loads(b64url_decode(parts[1]))
    except (ValueError, TypeError, CryptoError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _now() -> datetime:
    return datetime.now(UTC)


async def exchange(
    conn: AsyncConnection,
    tenant_id: str,
    assertion: str,
    *,
    token_audience: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Either {"ok": True, agent_id, client_id, jkt, resource, expires_at, ...} or {"ok": False, "codes": [...], "verified": bool}."""
    now = now or _now()
    claims_hint = unverified_claims(assertion)
    client_id = claims_hint.get("iss")
    if not isinstance(client_id, str):
        return {"ok": False, "codes": [UNVERIFIED], "verified": False}
    binding = await agent_store.api_binding_by_client(conn, client_id)
    if binding is None:
        return {"ok": False, "codes": [UNVERIFIED], "verified": False}
    build = await build_store.get(conn, str(binding["build_id"]))
    desc = (build or {}).get("client_descriptor") or {}
    keys = (desc.get("jwks") or {}).get("keys") or []
    try:
        header = peek_header(assertion)
        _, claims = verify_external_jws(
            assertion, keys=keys, algorithms=d.ASSERTION_ALGORITHMS, typ=d.ASSERTION_TYP
        )
        signing = next(k for k in keys if k.get("kid") == header.get("kid"))
        jkt = jwk_thumbprint(signing)
    except (CryptoError, StopIteration, ValueError):
        return {"ok": False, "codes": [UNVERIFIED], "verified": False}

    agent_id = str(binding["agent_principal_id"])
    base = {"binding_id": str(binding["binding_id"]), "client_id": client_id}
    problems = d.assertion_problems(
        claims,
        client_id=client_id,
        token_audience=token_audience,
        resources=desc.get("audiences") or [],
        now_ts=int(now.timestamp()),
    )
    jti = claims.get("jti")
    if isinstance(jti, str) and not any(p == "ASSERTION_ID_REQUIRED" for p in problems):
        # Burn the id before the verdict: a refused assertion cannot be replayed once the reason for refusal has gone away.
        expires = now + timedelta(seconds=d.ASSERTION_MAX_LIFETIME_SECONDS + d.ASSERTION_SKEW_SECONDS)
        if not await agent_store.claim_api_assertion(
            conn, tenant_id, jti=jti, binding_id=base["binding_id"], expires_at=expires
        ):
            problems.append("ASSERTION_REPLAYED")
    if not problems:
        problems = await _agent_problems(conn, agent_id, binding, build, now)
    if problems:
        await agent_service._record(
            conn, tenant_id, name="agent_registry.api_credential_refused", event=False, aggregate_type="agent", aggregate_id=agent_id,
            actor=None, actor_type="MACHINE", outcome="DENIED", data={**base, "codes": sorted(set(problems))},
        )  # fmt: skip
        return {"ok": False, "codes": list(dict.fromkeys(problems)), "verified": True}
    expires_at = now + timedelta(seconds=d.API_TOKEN_LIFETIME_SECONDS)
    await agent_service._record(
        conn, tenant_id, name="agent_registry.api_credential_issued", event=False, aggregate_type="agent", aggregate_id=agent_id,
        actor=None, actor_type="MACHINE",
        data={**base, "resource": claims["resource"], "jkt": jkt, "expires_at": expires_at.isoformat(), "attestation_cap": d.API_ATTESTATION_CAP},
    )  # fmt: skip
    return {
        "ok": True, "agent_id": agent_id, "client_id": client_id, "jkt": jkt, "resource": claims["resource"],
        "expires_at": expires_at, "security_revision": int(binding["security_revision"]),
    }  # fmt: skip


async def _agent_problems(
    conn: AsyncConnection,
    agent_id: str,
    binding: dict[str, Any],
    build: dict[str, Any] | None,
    now: datetime,
) -> list[str]:
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        return ["AGENT_NOT_ACTIVE"]
    bp = await machine_store.load_blueprint(conn, agent.blueprint_id, agent.blueprint_version)
    chain_ids = [a.agent_principal_id for a in await load_chain(conn, agent_id)]
    release_build = (
        await build_store.by_digest(conn, bp.artifact_digest) if bp is not None else None
    )
    return d.api_credential_blockers(
        agent_active=agent.status is AgentStatus.ACTIVE,
        binding_status=str(binding["status"]),
        binding_valid=binding["valid_to"] > now,
        build_revoked=(build or {}).get("review_status") == "REVOKED"
        or (release_build or {}).get("review_status") == "REVOKED",
        digest_matches_release=bp is not None
        and bp.artifact_digest == binding["descriptor_digest"]
        and (build or {}).get("review_status") == "APPROVED",
        frozen=bool(await governance.frozen_codes(conn, chain_ids)),
    )
