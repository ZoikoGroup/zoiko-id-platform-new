"""A signed credential for an agent, and what a relying party may and may not conclude from it (ZID-CP-WF07 v2.0 .09; A07-13; D08; CR-169). Pure domain, no I/O.

The credential is a short signed JWT in the W3C Verifiable Credentials 2.0 data model (JWT form). It carries only the issuer, the agent, the organization, a reference to one mandate (its id and hash), a validity period and an evidence class,
and it is bound to the agent's own public key (`cnf`). It never carries personal data, KYB records, biometric data or any permission.

A relying party that reads it learns three separate things and one that is deliberately NOT answered:
  identity_verified       Zoiko iD vouches that this agent is registered under this organization
  credential_valid        the signature, the validity period and the live status all hold
  key_possession          whoever presented it holds the agent's key, for THIS relying party, now, once
  authority_evaluated     ALWAYS false: the credential says nothing about what the agent may do. That is decided, per call, at the gateway.
No verifier may call an agent "approved to spend" from this. No equivalence to vLEI or to a qualified signature is claimed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from zid_platform.modules.machine.domain.agents import MachineError

CREDENTIAL_TYP = "zid-agent-credential+jwt"
PRESENTATION_TYP = "zid-agent-presentation+jwt"
MAX_LIFETIME = timedelta(days=30)
DEFAULT_LIFETIME = timedelta(days=7)
PRESENTATION_MAX_AGE = 120  # seconds: a presentation is made for one moment
LEEWAY = 30
EVIDENCE_CLASSES = ("E1", "E2", "E3")
CONTEXT = ("https://www.w3.org/ns/credentials/v2",)
LIMITS = "A signature proves Zoiko iD issued this. Whether it is still good comes from the status check, and what the agent may do is not part of it."


def check_ttl(hours: int | None) -> timedelta:
    ttl = timedelta(hours=hours) if hours else DEFAULT_LIFETIME
    if not timedelta(hours=1) <= ttl <= MAX_LIFETIME:
        raise MachineError("AGENT_CREDENTIAL_INVALID", "a credential lasts from 1 hour to 30 days")
    return ttl


def check_evidence_class(value: str | None) -> str:
    if (value or "E2") not in EVIDENCE_CLASSES:
        raise MachineError("AGENT_CREDENTIAL_INVALID", "an evidence class is E1, E2 or E3")
    return value or "E2"


def check_public_key(jwk: Any) -> dict[str, Any]:
    """The agent's own PUBLIC key. A private key, a key with no id, or a remote reference is refused."""
    if not isinstance(jwk, dict) or not isinstance(jwk.get("kid"), str) or not jwk["kid"] or len(jwk["kid"]) > 100:
        raise MachineError("AGENT_CREDENTIAL_INVALID", "give the agent's public key as an object with a key id")
    if any(p in jwk for p in ("d", "p", "q", "dp", "dq", "qi", "k")):
        raise MachineError("AGENT_CREDENTIAL_INVALID", "give the public key only: this one holds private material")
    if any(p in jwk for p in ("jku", "x5u")):
        raise MachineError("AGENT_CREDENTIAL_INVALID", "a key is given, not fetched from an address")
    return {k: v for k, v in jwk.items() if isinstance(k, str) and len(k) <= 20}


def credential_claims(
    *, issuer: str, credential_id: str, agent_id: str, organization_id: str | None, mandate_id: str | None, mandate_hash: str | None, evidence_class: str, jkt: str, audience: str | None,
    status_url: str, issued_at: datetime, expires_at: datetime,
) -> dict[str, Any]:  # fmt: skip
    """Minimal claims. Everything is an identifier, a digest, a class or a time."""
    subject: dict[str, Any] = {"id": f"urn:zoiko:agent:{agent_id}", "organization": organization_id, "evidence_class": evidence_class}
    if mandate_id:
        subject["mandate"] = {"id": mandate_id, "hash": mandate_hash}
    claims: dict[str, Any] = {
        "iss": issuer, "sub": f"urn:zoiko:agent:{agent_id}", "jti": credential_id, "iat": int(issued_at.timestamp()), "nbf": int(issued_at.timestamp()), "exp": int(expires_at.timestamp()),
        "cnf": {"jkt": jkt},
        "vc": {
            "@context": list(CONTEXT), "type": ["VerifiableCredential", "ZoikoAgentCredential"], "credentialSubject": subject,
            "credentialStatus": {"id": status_url, "type": "ZoikoLiveStatus"}, "authority_evaluated": False,
        },
    }  # fmt: skip
    if audience:
        claims["aud"] = audience
    return claims


def credential_problems(claims: Mapping[str, Any], *, record: Mapping[str, Any] | None, now: datetime) -> list[str]:
    """Why a credential whose signature verified is still not good: expired, unknown to this issuer, revoked, or issued for another agent or key."""
    out: list[str] = []
    ts = now.timestamp()
    exp, nbf = claims.get("exp"), claims.get("nbf")
    if not isinstance(exp, (int, float)) or exp + LEEWAY <= ts:
        out.append("CREDENTIAL_EXPIRED")
    if isinstance(nbf, (int, float)) and nbf - LEEWAY > ts:
        out.append("CREDENTIAL_NOT_YET_VALID")
    if record is None:
        out.append("CREDENTIAL_UNKNOWN")  # a signature from this issuer for something this issuer has no record of
        return out
    if record["status"] != "ACTIVE":
        out.append("CREDENTIAL_REVOKED")
    if str(record["agent_principal_id"]) != str(claims.get("sub", "")).removeprefix("urn:zoiko:agent:"):
        out.append("CREDENTIAL_SUBJECT_MISMATCH")
    if str(record["jkt"]) != str((claims.get("cnf") or {}).get("jkt")):
        out.append("CREDENTIAL_KEY_MISMATCH")
    return out


def agent_problems(*, status: str, frozen: bool, sponsor_active: bool) -> list[str]:
    """The live standing of the agent itself. A suspended, revoked, frozen or unsponsored agent's credential is not good, whatever its dates say."""
    out: list[str] = []
    if status != "ACTIVE":
        out.append(f"AGENT_{status}")
    if frozen:
        out.append("AGENT_FROZEN")
    if not sponsor_active:
        out.append("SPONSOR_NOT_ACTIVE")
    return out


def presentation_problems(claims: Mapping[str, Any], *, credential_id: str, relying_party: str, nonce: str, now: datetime) -> list[str]:
    """Why a presentation, signed by the key the credential is bound to, does not prove possession for THIS relying party now."""
    out: list[str] = []
    if claims.get("aud") != relying_party:
        out.append("PRESENTATION_AUDIENCE_MISMATCH")  # made for another relying party: a relayed presentation
    if claims.get("nonce") != nonce:
        out.append("PRESENTATION_NONCE_MISMATCH")
    if claims.get("cred") != credential_id:
        out.append("PRESENTATION_FOR_ANOTHER_CREDENTIAL")
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or isinstance(iat, bool):
        out.append("PRESENTATION_NO_ISSUE_TIME")
    elif now.timestamp() - iat > PRESENTATION_MAX_AGE + LEEWAY:
        out.append("PRESENTATION_TOO_OLD")
    elif iat - LEEWAY > now.timestamp():
        out.append("PRESENTATION_FROM_THE_FUTURE")
    if not (isinstance(claims.get("jti"), str) and claims["jti"]):
        out.append("PRESENTATION_NO_ID")
    return out


def indicators(*, credential_ok: bool, agent_ok: bool, possession: bool | None) -> dict[str, Any]:
    """The separate things a relying party learns. `authority_evaluated` is a constant: this answer never says what an agent may do."""
    return {
        "identity_verified": credential_ok and agent_ok,
        "credential_valid": credential_ok and agent_ok,
        "key_possession": possession,  # None when no presentation was made: offline-style checking proves nothing about who holds the credential
        "authority_evaluated": False,
        "may_spend": None,  # never answered here
        "limits": LIMITS,
    }


def verdict(ind: Mapping[str, Any]) -> str:
    """One plain word for a relying party's screen. Never a permission."""
    if not ind["credential_valid"]:
        return "NOT_VALID"
    if ind["key_possession"] is True:
        return "IDENTITY_VERIFIED_AUTHORITY_NOT_EVALUATED"
    if ind["key_possession"] is False:
        return "NOT_VALID"
    return "VALID_BUT_POSSESSION_NOT_SHOWN"


def reasons_unique(*groups: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for g in groups:
        for c in g:
            if c not in seen:
                seen.append(c)
    return seen
