"""Step-up challenges, proofs and their use (ZID-ENG-DEC-009, slice L-9).

Decision sequence the record asks for, as far as this module goes: classify the action, require fresh proof, verify the proof and the
action digest, execute once. The proof is minted only by a passkey assertion over a challenge derived from the server-held digest of the
exact command (so editing the command afterwards invalidates it), is bound to one actor, tenant and session, expires on the server's
clock, and for S2 is consumed atomically inside the transaction that executes the command: a second use, in parallel or later, finds it
used. Nothing here grants anything; permission, entitlement, residency and approval are decided by their own gates.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from webauthn import generate_authentication_options, options_to_json
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

from zid_platform.modules.authentication.application.webauthn import (
    WebAuthnSettings,
    assert_credential,
    challenge_of,
)
from zid_platform.modules.authentication.domain.authenticator import (
    AuthnError,
    credential_allow_list,
)
from zid_platform.modules.authentication.domain.step_up import (
    Binding,
    Catalogue,
    Method,
    StepUpError,
    Tier,
    canonical_json,
    check_binding,
    classify,
    command_hash,
    describe,
    fresh_enough,
    human_only,
    method_satisfies,
    webauthn_challenge,
)
from zid_platform.modules.authentication.infrastructure import authenticator_store as authn_store
from zid_platform.modules.authentication.infrastructure import step_up_store as store
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.principal.ports import load_principal

CEREMONY = "ACTION_STEP_UP"
ASSURANCE_LABEL = {Tier.S1: "FRESH_STRONG", Tier.S2: "PHISHING_RESISTANT_ACTION_BOUND"}


@dataclass(frozen=True, slots=True)
class Who:
    """The caller as the server established them from the token, never from the request body."""

    tenant_id: str
    actor_id: str
    session_id: str | None
    delegated_actor: str | None = None
    workload_bound: bool = False
    security_state: str = "NORMAL"


async def _audit(
    conn: AsyncConnection,
    who: Who,
    event: str,
    *,
    target: str,
    outcome: str,
    now: datetime,
    details: Mapping[str, Any],
    reason: str | None = None,
) -> None:
    await record_audit(
        conn,
        who.tenant_id,
        AuditFacts(
            event_type=f"authentication.step_up.{event}",
            actor_type="HUMAN",
            action=event,
            target_type="step_up",
            target_ref=target[:128],
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=who.actor_id,
            reason_code=reason,
            source_service="authentication",
            details={
                k: v for k, v in details.items() if isinstance(v, str | int | bool | type(None))
            },
        ),
    )


def _id(value: str, code: str) -> str:
    """An id from the outside is a UUID or it is refused: a malformed one is a refusal, never a crash."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise StepUpError(code, "malformed id") from e


async def record_refusal(
    conn: AsyncConnection, who: Who, *, action_id: str, code: str, now: datetime, target: str
) -> None:
    """A refused step-up leaves its own evidence. Ids and a reason code only."""
    await _audit(
        conn, who, "refused", target=target, outcome="DENIED", now=now,
        details={"action": action_id, "reason": code}, reason=code,
    )  # fmt: skip


async def create_challenge(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    cat: Catalogue,
    who: Who,
    *,
    action_id: str,
    resource: str,
    change: Mapping[str, Any],
    critical: bool = False,
    now: datetime,
) -> dict[str, Any]:
    """Open a challenge for an immutable command draft. The server decides the tier and the method; the person is shown the summary the
    server wrote from the same fields it hashed."""
    principal, _ = await load_principal(conn, who.actor_id)
    refusal = human_only(
        principal_type=principal.principal_type.value,
        delegated_actor=who.delegated_actor,
        workload_bound=who.workload_bound,
    )
    if refusal:
        raise StepUpError(
            refusal, "an agent, a service or a delegated token cannot perform a human step-up"
        )
    cls = classify(cat, action_id, security_state=who.security_state, critical=critical)
    if cls.tier is Tier.S0:
        return {
            "required": False,
            "tier": Tier.S0.value,
            "action": action_id,
            "reasons": list(cls.reasons),
        }
    if who.session_id is None:
        raise StepUpError("STEP_UP_SESSION_REQUIRED", "a step-up belongs to a signed-in session")
    digest = command_hash(
        tenant_id=who.tenant_id, action_id=action_id, resource=resource, change=change
    )
    canonical_json(
        change
    )  # a payload that has no single reading is refused before anyone is asked to approve it
    challenge_id = str(uuid.uuid4())
    nonce = secrets.token_bytes(32)
    wa_challenge = webauthn_challenge(nonce, digest, challenge_id)
    mine = await authn_store.for_principal(conn, who.actor_id, active_only=True)
    allow = [PublicKeyCredentialDescriptor(id=cid) for cid in credential_allow_list(mine)]
    if not allow:  # never replaced by email or SMS: the person goes through governed recovery
        raise StepUpError(
            "NO_STRONG_AUTHENTICATOR", "no passkey is available; use governed recovery"
        )
    expires = now + cat.challenge_ttl
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        challenge=wa_challenge,
        timeout=int(cat.challenge_ttl.total_seconds() * 1000),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await authn_store.save_challenge(
        conn,
        tenant_id=who.tenant_id,
        challenge=wa_challenge,
        ceremony=CEREMONY,
        principal_id=who.actor_id,
        session_id=who.session_id,
        ticket_hash=None,
        user_verification="required",
        expires_at=expires,
    )
    summary = describe(cls.action, resource, change)
    row = store.ChallengeRow(
        challenge_id=challenge_id,
        tenant_id=who.tenant_id,
        actor_id=who.actor_id,
        session_id=who.session_id,
        action_id=action_id,
        family=cls.action.family,
        tier=cls.tier.value,
        resource_id=resource,
        command_hash=digest,
        summary=summary,
        requested_assurance=ASSURANCE_LABEL[cls.tier],
        nonce=nonce,
        policy_version=cat.version,
        status="PENDING",
        created_at=now,
        expires_at=expires,
    )
    await store.insert_challenge(conn, row)
    await _audit(
        conn, who, "challenge_created", target=challenge_id, outcome="SUCCESS", now=now,
        details={"action": action_id, "tier": cls.tier.value, "command_hash": digest, "policy_version": cat.version},
    )  # fmt: skip
    return {
        "required": True,
        "challenge_id": challenge_id,
        "tier": cls.tier.value,
        "action": action_id,
        "resource": resource,
        "summary": summary,
        "command_hash": digest,
        "requested_assurance": ASSURANCE_LABEL[cls.tier],
        "reasons": list(cls.reasons),
        "expires_at": expires.isoformat(),
        "policy_version": cat.version,
        "why": cls.action.control,
        "public_key": json.loads(options_to_json(options)),
    }


async def verify_challenge(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    cat: Catalogue,
    who: Who,
    *,
    challenge_id: str,
    credential: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Verify the passkey assertion and mint the proof. The proof is an opaque reference usable only by this actor, in this session, for
    this command: never an unrestricted token."""
    challenge_id = _id(challenge_id, "STEP_UP_CHALLENGE_INVALID")
    ch = await store.load_challenge(conn, challenge_id)
    if ch is None or ch.tenant_id != who.tenant_id:
        raise StepUpError("STEP_UP_CHALLENGE_INVALID", "no such challenge")
    if ch.actor_id != who.actor_id:
        raise StepUpError("STEP_UP_ACTOR_MISMATCH", "this challenge belongs to someone else")
    if ch.session_id != who.session_id:
        raise StepUpError("STEP_UP_SESSION_MISMATCH", "this challenge belongs to another session")
    if ch.status != "PENDING":
        raise StepUpError("STEP_UP_CHALLENGE_INVALID", f"this challenge is {ch.status.lower()}")
    if now >= ch.expires_at:
        await store.resolve_challenge(conn, challenge_id, "EXPIRED", now)
        raise StepUpError("STEP_UP_CHALLENGE_EXPIRED", "this challenge has expired")
    expected = webauthn_challenge(ch.nonce, ch.command_hash, ch.challenge_id)
    if (
        challenge_of(credential) != expected
    ):  # the passkey signed some other challenge, so it did not sign this command
        raise StepUpError("STEP_UP_BINDING_MISMATCH", "the assertion is not for this command")
    claimed = await authn_store.claim_challenge(conn, expected, ceremonies=(CEREMONY,), now=now)
    if claimed is None:
        raise StepUpError("STEP_UP_CHALLENGE_INVALID", "this challenge was already used")
    try:
        asserted = await assert_credential(
            conn,
            settings,
            credential=credential,
            challenge=expected,
            bound_principal_id=claimed.principal_id,
            user_verification=claimed.user_verification,
            now=now,
        )
    except AuthnError:
        # the passkey challenge is single-use whether or not the assertion was good (bounded retries), so this one is finished
        await store.resolve_challenge(conn, challenge_id, "CANCELLED", now)
        raise
    tier = Tier(ch.tier)
    reason = method_satisfies(
        cat,
        tier,
        Method.PASSKEY,
        user_verified=asserted.user_verified,
        phishing_resistant=int(asserted.assurance.aal) >= 2,
    )
    if reason:
        await store.resolve_challenge(conn, challenge_id, "CANCELLED", now)
        raise StepUpError("STEP_UP_ASSURANCE_INSUFFICIENT", reason)
    policy = cat.tiers[tier]
    assert policy.max_age is not None
    proof_id = str(uuid.uuid4())
    expires = now + policy.max_age
    binding = canonical_json(
        {
            "tenant": ch.tenant_id,
            "actor": ch.actor_id,
            "session": ch.session_id,
            "action": ch.action_id,
            "resource": ch.resource_id,
            "command": ch.command_hash,
            "policy": ch.policy_version,
        }
    )
    await store.insert_proof(
        conn,
        proof_id=proof_id,
        challenge=ch,
        authenticator_id=asserted.authenticator.authenticator_id,
        method=Method.PASSKEY.value,
        assurance={
            "aal": int(asserted.assurance.aal),
            "amr": list(asserted.assurance.amr),
            "uv": asserted.user_verified,
        },
        auth_time=now,
        verified_at=now,
        binding_hash=sha256(binding).hexdigest(),
        expires_at=expires,
        single_use=policy.single_use,
    )
    await store.resolve_challenge(conn, challenge_id, "VERIFIED", now)
    await _audit(
        conn, who, "verified", target=proof_id, outcome="SUCCESS", now=now,
        details={"challenge_id": challenge_id, "tier": ch.tier, "action": ch.action_id, "single_use": policy.single_use},
    )  # fmt: skip
    return {
        "proof_id": proof_id,
        "tier": ch.tier,
        "action": ch.action_id,
        "resource": ch.resource_id,
        "single_use": policy.single_use,
        "verified_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }


async def cancel_challenge(
    conn: AsyncConnection, who: Who, challenge_id: str, now: datetime
) -> dict[str, Any]:
    challenge_id = _id(challenge_id, "STEP_UP_CHALLENGE_INVALID")
    ch = await store.load_challenge(conn, challenge_id)
    if ch is None or ch.tenant_id != who.tenant_id or ch.actor_id != who.actor_id:
        raise StepUpError("STEP_UP_CHALLENGE_INVALID", "no such challenge")
    done = await store.resolve_challenge(conn, challenge_id, "CANCELLED", now)
    if done:
        await _audit(
            conn,
            who,
            "cancelled",
            target=challenge_id,
            outcome="SUCCESS",
            now=now,
            details={"action": ch.action_id},
        )
    return {
        "challenge_id": challenge_id,
        "status": "CANCELLED" if done else ch.status,
        "grants_nothing": True,
    }


async def check_and_consume(
    conn: AsyncConnection,
    cat: Catalogue,
    who: Who,
    *,
    proof_id: str,
    action_id: str,
    resource: str,
    change: Mapping[str, Any],
    critical: bool = False,
    now: datetime,
) -> dict[str, Any]:
    """Re-check the proof against the command that is about to run, and for S2 spend it. Call it inside the transaction that executes the
    command, so a command that fails does not spend a proof it did not use and a command that succeeds cannot run twice on it."""
    cls = classify(cat, action_id, security_state=who.security_state, critical=critical)
    if cls.tier is Tier.S0:
        return {"required": False, "tier": "S0"}
    proof_id = _id(proof_id, "STEP_UP_PROOF_INVALID")
    proof = await store.load_proof(conn, proof_id)
    if proof is None or proof.challenge.tenant_id != who.tenant_id:
        raise StepUpError("STEP_UP_PROOF_INVALID", "no such proof")
    ch = proof.challenge
    proven_tier = Tier(ch.tier)
    if proven_tier.rank < cls.tier.rank:
        raise StepUpError(
            "STEP_UP_TIER_INSUFFICIENT",
            f"{cls.tier.value} is required, this proof is {proven_tier.value}",
        )
    if not fresh_enough(cat.tiers[proven_tier], proof.verified_at, now) or now >= proof.expires_at:
        raise StepUpError("STEP_UP_PROOF_EXPIRED", "the proof has expired")
    expected = Binding(
        who.tenant_id, who.actor_id, who.session_id, action_id, cls.action.family, resource,
        command_hash(tenant_id=who.tenant_id, action_id=action_id, resource=resource, change=change),
    )  # fmt: skip
    proven = Binding(
        ch.tenant_id,
        ch.actor_id,
        ch.session_id,
        ch.action_id,
        ch.family,
        ch.resource_id,
        ch.command_hash,
    )
    mismatch = check_binding(cat, cls.tier, expected, proven)
    if mismatch:
        raise StepUpError(mismatch, "the proof is not for this command")
    spent = False
    if proof.single_use:
        spent = await store.consume_proof(conn, proof_id, f"{action_id}|{resource}", now)
        if not spent:  # used already, in parallel or earlier
            raise StepUpError("STEP_UP_PROOF_CONSUMED", "the proof has already been used")
    await _audit(
        conn, who, "consumed", target=proof_id, outcome="SUCCESS", now=now,
        details={"action": action_id, "tier": cls.tier.value, "spent": spent, "policy_version": cat.version},
    )  # fmt: skip
    return {"required": True, "tier": cls.tier.value, "proof_id": proof_id, "spent": spent}
