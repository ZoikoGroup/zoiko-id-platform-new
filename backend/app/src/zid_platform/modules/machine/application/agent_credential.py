"""Issuing and verifying an agent's credential (ZID-CP-WF07 v2.0 .09; A07-13; D08; CR-169). See `domain.agent_credential` for what it does and does not say.

Issue: only for an ACTIVE agent whose sponsor is active, bound to the agent's own public key, optionally naming one of that agent's mandates by id and hash. Verify: the signature, the live status of the credential and of the agent
(a stopped, frozen or unsponsored agent's credential is not good), and, when a presentation is given, that it was signed by the bound key for THIS relying party and nonce, recently, once. The answer keeps identity, validity and key
possession apart and states that authority was not evaluated.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, jwk_thumbprint, verify_external_jws

from zid_platform.modules.evidence.ports import EvidenceClass
from zid_platform.modules.machine.application import governance
from zid_platform.modules.machine.application.agents import _record, sponsor_is_active
from zid_platform.modules.machine.domain import agent_credential as d
from zid_platform.modules.machine.domain.agents import AgentStatus, MachineError
from zid_platform.modules.machine.infrastructure import credential_store, machine_store
from zid_platform.modules.mandate.ports import canonical_hash_of, mandates_executed_by

Sign = Callable[[Mapping[str, Any], str], str]
Verify = Callable[[str], Mapping[str, Any]]


def _iso(x: datetime | None) -> str | None:
    return x.isoformat() if x else None


def view(c: Mapping[str, Any], *, with_token: bool = False) -> dict[str, Any]:
    out = {
        "credential_id": str(c["credential_id"]), "agent_id": str(c["agent_principal_id"]), "key_thumbprint": c["jkt"], "audience": c["audience"], "mandate_id": str(c["mandate_id"]) if c["mandate_id"] else None,
        "mandate_hash": c["mandate_hash"], "evidence_class": c["evidence_class"], "status": c["status"], "issued_at": _iso(c["issued_at"]), "expires_at": _iso(c["expires_at"]), "revoked_at": _iso(c["revoked_at"]),
        "authority_evaluated": False, "limits": d.LIMITS,
    }  # fmt: skip
    if with_token:
        out["credential"] = c["token"]
    return out


async def issue(
    conn: AsyncConnection, tenant_id: str, actor: str, agent_id: str, *, public_key: Any, audience: str | None, mandate_id: str | None, evidence_class: str | None, ttl_hours: int | None,
    sign: Sign, issuer: str, status_url: str, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    now = now or datetime.now(UTC)
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise MachineError("AGENT_NOT_FOUND", "no such agent")
    if agent.status is not AgentStatus.ACTIVE:
        raise MachineError(
            "AGENT_CREDENTIAL_NOT_ISSUABLE", "only an active agent is given a credential"
        )
    if not agent.sponsor_principal_id or not await sponsor_is_active(
        conn, agent.sponsor_principal_id
    ):
        raise MachineError(
            "AGENT_CREDENTIAL_NOT_ISSUABLE",
            "an agent whose sponsor is not active is given no credential",
        )
    if await governance.frozen_codes(conn, [agent_id]):
        raise MachineError("AGENT_CREDENTIAL_NOT_ISSUABLE", "a frozen agent is given no credential")
    jwk = d.check_public_key(public_key)
    ttl, ev = d.check_ttl(ttl_hours), d.check_evidence_class(evidence_class)
    aud = (audience or "").strip() or None
    if aud is not None and not 3 <= len(aud) <= 300:
        raise MachineError(
            "AGENT_CREDENTIAL_INVALID",
            "an audience is the relying party's own name, 3 to 300 characters",
        )
    mhash: str | None = None
    if mandate_id:
        mine = {
            str(m["mandate_id"]) for m in await mandates_executed_by(conn, [agent_id], limit=100)
        }
        if mandate_id not in mine:
            raise MachineError(
                "AGENT_CREDENTIAL_INVALID", "name a live mandate this agent executes, or none"
            )
        mhash = await canonical_hash_of(conn, mandate_id)
    cid = str(uuid.uuid4())
    expires = now + ttl
    claims = d.credential_claims(
        issuer=issuer, credential_id=cid, agent_id=agent_id, organization_id=agent.owning_organization_id, mandate_id=mandate_id, mandate_hash=mhash, evidence_class=ev,
        jkt=jwk_thumbprint(jwk), audience=aud, status_url=status_url, issued_at=now, expires_at=expires,
    )  # fmt: skip
    token = sign(claims, d.CREDENTIAL_TYP)
    await credential_store.insert(
        conn, tenant_id, credential_id=cid, agent_id=agent_id, jkt=jwk_thumbprint(jwk), public_key=jwk, audience=aud, mandate_id=mandate_id, mandate_hash=mhash, evidence_class=ev, token=token,
        issued_by=actor, issued_at=now, expires_at=expires,
    )  # fmt: skip
    await _record(
        conn, tenant_id, name="agent.credential_issued", event=False, aggregate_type="agent", aggregate_id=agent_id, actor=actor, evidence=EvidenceClass.E2,
        data={"credential_id": cid, "evidence_class": ev, "mandate_id": mandate_id or ""},
    )  # fmt: skip
    got = await credential_store.get(conn, cid)
    assert got is not None
    return view(got, with_token=True)


async def revoke(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    credential_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await credential_store.get(conn, credential_id, lock=True)
    if c is None:
        raise MachineError("AGENT_CREDENTIAL_NOT_FOUND", "no such credential")
    if not await credential_store.revoke(conn, credential_id, by=actor, now=now):
        raise MachineError("AGENT_CREDENTIAL_STATE_INVALID", "this credential is already revoked")
    await _record(
        conn,
        tenant_id,
        name="agent.credential_revoked",
        event=False,
        aggregate_type="agent",
        aggregate_id=str(c["agent_principal_id"]),
        actor=actor,
        evidence=EvidenceClass.E2,
        data={"credential_id": credential_id},
    )
    after = await credential_store.get(conn, credential_id)
    assert after is not None
    return view(after)


async def list_for(conn: AsyncConnection, agent_id: str | None) -> dict[str, Any]:
    return {"items": [view(c) for c in await credential_store.for_agent(conn, agent_id, limit=100)]}


async def verify(
    conn: AsyncConnection, tenant_id: str, actor: str, *, credential: str, presentation: str | None, relying_party: str, nonce: str | None, verify_own: Verify, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Always answers: a failed check is a reason in the answer, never an exception, so a relying party always learns why."""
    now = now or datetime.now(UTC)
    codes: list[str] = []
    claims: Mapping[str, Any] = {}
    try:
        claims = verify_own(credential)
    except Exception:  # noqa: BLE001 - a credential this issuer did not sign, or one that was altered
        codes.append("CREDENTIAL_SIGNATURE_INVALID")
    record = None
    agent_codes: list[str] = []
    if not codes:
        record = await credential_store.get(conn, str(claims.get("jti", "")))
        codes += d.credential_problems(claims, record=record, now=now)
        agent_id = str(claims.get("sub", "")).removeprefix("urn:zoiko:agent:")
        agent = await machine_store.load_agent(conn, agent_id) if record is not None else None
        if record is not None and agent is None:
            agent_codes.append("AGENT_UNKNOWN")
        elif agent is not None:
            sponsor_ok = bool(
                agent.sponsor_principal_id
                and await sponsor_is_active(conn, agent.sponsor_principal_id)
            )
            agent_codes = d.agent_problems(
                status=agent.status.value,
                frozen=bool(await governance.frozen_codes(conn, [agent_id])),
                sponsor_active=sponsor_ok,
            )
    possession: bool | None = None
    pcodes: list[str] = []
    if presentation is not None and record is not None:
        if not nonce:
            pcodes.append("PRESENTATION_NONCE_MISMATCH")
        else:
            try:
                _h, pclaims = verify_external_jws(
                    presentation,
                    keys=[record["public_key"]],
                    algorithms=["ES256", "EdDSA"],
                    typ=d.PRESENTATION_TYP,
                )
                pcodes = d.presentation_problems(
                    pclaims,
                    credential_id=str(record["credential_id"]),
                    relying_party=relying_party,
                    nonce=nonce,
                    now=now,
                )
                if not pcodes and not await credential_store.take_presentation(
                    conn,
                    tenant_id,
                    str(record["credential_id"]),
                    _digest(str(pclaims["jti"])),
                    relying_party,
                    now,
                ):
                    pcodes.append("PRESENTATION_REPLAYED")
            except CryptoError:
                pcodes.append("PRESENTATION_SIGNATURE_INVALID")
        possession = not pcodes
    elif presentation is not None:
        possession = False
    credential_ok = not codes
    ind = d.indicators(credential_ok=credential_ok, agent_ok=not agent_codes, possession=possession)
    out = {
        "verdict": d.verdict(ind), "indicators": ind, "reason_codes": d.reasons_unique(codes, agent_codes, pcodes), "credential_id": str(claims.get("jti")) if claims else None,
        "relying_party": relying_party, "as_of": now.isoformat(), "checked_live": True,
    }  # fmt: skip
    await _record(
        conn, tenant_id, name="agent.credential_verified", event=False, aggregate_type="agent", aggregate_id=str(record["agent_principal_id"]) if record else tenant_id, actor=actor, evidence=EvidenceClass.E2,
        outcome="SUCCESS" if out["verdict"] != "NOT_VALID" else "DENIED", data={"credential_id": out["credential_id"] or "", "verdict": out["verdict"], "relying_party": relying_party[:100]},
    )  # fmt: skip
    return out


def _digest(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
