"""Controlled credential issuer (ZID-VC-001 §6, §13, §16; OID4VCI 1.0; DEC-E2 "build second").

Role ISSUER_NON_QUALIFIED only, for configurations explicitly enabled for issuance. What Zoiko
issues is an assurance attestation (dc+sd-jwt): the subject's identity-proofing level and the
*names* of verified claims, each selectively disclosable - never claim values, which Zoiko keeps
only as commitments. Holder binding comes from the wallet's OID4VCI key proof; issuance is
idempotent per proof; every credential gets an index in a Token Status List that Zoiko publishes
(herd privacy: verifiers fetch the whole list, the issuer never learns who checks).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, KeyRing, jwk_thumbprint, peek_header, verify_jws
from zid_outbox import emit

from zid_platform.modules.commercial.ports import key_vc_issued, record_billable
from zid_platform.modules.credential.domain.credentials import (
    STATUS_LIST_TYP,
    CredentialError,
    b64e,
    encode_status_list,
    require_role,
    set_status,
)
from zid_platform.modules.credential.domain.reliance import RELIANCE
from zid_platform.modules.credential.infrastructure import credential_store as store
from zid_platform.modules.verification.ports import assurance

PROOF_TYP = "openid4vci-proof+jwt"
NONCE_TYP = "zid-c-nonce+jwt"
NONCE_TTL = timedelta(minutes=5)
CREDENTIAL_TTL = timedelta(days=365)
STATUS_TOKEN_TTL = timedelta(hours=1)
STATUS_CODES = {"VALID": 0, "REVOKED": 1, "SUSPENDED": 2}


@dataclass(frozen=True, slots=True)
class IssuerSettings:
    issuer: str
    keyring: KeyRing

    def status_uri(self, tenant_id: str, list_id: str) -> str:
        return f"{self.issuer.rstrip('/')}/credentials/status-lists/{tenant_id}.{list_id}"


def c_nonce(settings: IssuerSettings, now: datetime) -> str:
    iat = int(now.timestamp())
    return settings.keyring.sign(
        {
            "iss": settings.issuer,
            "iat": iat,
            "exp": iat + int(NONCE_TTL.total_seconds()),
            "jti": secrets.token_urlsafe(16),
        },
        typ=NONCE_TYP,
    )


def _check_nonce(settings: IssuerSettings, nonce: object, now: datetime) -> None:
    try:
        _, claims = settings.keyring.verify(str(nonce), typ=NONCE_TYP)
    except CryptoError as e:
        raise CredentialError("INVALID_NONCE") from e
    if claims.get("iss") != settings.issuer or now.timestamp() >= float(claims.get("exp", 0)):
        raise CredentialError("INVALID_NONCE")


def _holder_key(settings: IssuerSettings, proof: str, now: datetime) -> dict[str, Any]:
    """OID4VCI 1.0 §8.2.1.1: the proof is signed by the key the credential will be bound to."""
    try:
        header = peek_header(proof)
        jwk = header.get("jwk")
        if header.get("typ") != PROOF_TYP or not isinstance(jwk, dict):
            raise CredentialError("INVALID_PROOF", "typ/jwk")
        _, claims = verify_jws(proof, keys=[jwk], typ=PROOF_TYP)
    except CryptoError as e:
        raise CredentialError("INVALID_PROOF", e.code) from e
    if claims.get("aud") != settings.issuer:
        raise CredentialError("INVALID_PROOF", "aud")
    iat = claims.get("iat")
    if not isinstance(iat, int | float) or abs(now.timestamp() - iat) > NONCE_TTL.total_seconds():
        raise CredentialError("INVALID_PROOF", "iat")
    _check_nonce(settings, claims.get("nonce"), now)
    return jwk


def _disclosure(name: str, value: Any) -> tuple[str, str]:
    d = b64e(json.dumps([secrets.token_urlsafe(16), name, value]).encode())
    return d, b64e(hashlib.sha256(d.encode("ascii")).digest())


async def issue(
    conn: AsyncConnection,
    settings: IssuerSettings,
    *,
    tenant_id: str,
    subject_principal_id: str,
    configuration_id: str,
    proof: str,
    now: datetime,
) -> dict[str, Any]:
    require_role("ISSUER_NON_QUALIFIED")
    config = await store.load_configuration(conn, configuration_id)
    if config.status != "ACTIVE" or not config.issuance_enabled or config.format != "SD_JWT_VC":
        raise CredentialError("ISSUANCE_NOT_ENABLED")
    holder = _holder_key(settings, proof, now)
    facts = await assurance(conn, subject_principal_id, now)
    proofing = facts["identity_proofing"]
    if proofing["level"] == "IAL-Z0":
        raise CredentialError("ASSURANCE_NOT_MET")
    values: dict[str, Any] = {
        "ial": proofing["level"],
        "verified_claims": sorted(proofing["claims"]),
    }
    missing = [c for c in config.required_claims if c not in values]
    if missing:
        raise CredentialError("CLAIMS_UNAVAILABLE", ",".join(missing))
    transaction = hashlib.sha256(proof.encode()).hexdigest()
    list_id, idx = await store.status_list_for(conn, tenant_id)
    disclosures, digests = [], []
    for name in (*config.required_claims, *config.optional_claims):
        if name in values:
            d, digest = _disclosure(name, values[name])
            disclosures.append(d)
            digests.append(digest)
    iat = int(now.timestamp())
    credential_id = str(uuid.uuid4())
    issuer_jwt = settings.keyring.sign(
        {
            "iss": settings.issuer,
            "vct": config.credential_type,
            "iat": iat,
            "exp": iat + int(CREDENTIAL_TTL.total_seconds()),
            "cnf": {"jwk": holder},
            "status": {"status_list": {"idx": idx, "uri": settings.status_uri(tenant_id, list_id)}},
            "_sd": sorted(digests),
            "_sd_alg": "sha-256",
        },
        typ="dc+sd-jwt",
    )
    fresh = await store.record_issued(
        conn,
        tenant_id,
        {
            "credential_id": credential_id,
            "subject_principal_id": subject_principal_id,
            "issuer": settings.issuer,
            "credential_type": config.credential_type,
            "configuration_id": configuration_id,
            "status_ref": settings.status_uri(tenant_id, list_id),
            "status_list_id": list_id,
            "status_index": idx,
            "holder_jkt": jwk_thumbprint(holder),
            "transaction": transaction,
            "issued_at": now,
            "expires_at": now + CREDENTIAL_TTL,
        },
    )
    if not fresh:  # the same proof again: the logical issuance already happened
        raise CredentialError("ISSUANCE_ALREADY_COMPLETED")
    await record_billable(
        conn,
        tenant_id=tenant_id,
        meter="VC_ISSUED",
        key=key_vc_issued(credential_id),
        at=now,
        principal_class="HUMAN",
    )
    await emit(
        conn,
        name="credential.issued",
        tenant_id=tenant_id,
        aggregate_type="credential",
        aggregate_id=credential_id,
        aggregate_version=1,
        data={
            "credential_id": credential_id,
            "credential_type": config.credential_type,
            "subject_principal_id": subject_principal_id,
        },
    )
    return {
        "credentials": [{"credential": issuer_jwt + "~" + "".join(d + "~" for d in disclosures)}],
        "credential_id": credential_id,
    }


async def status_list_token(
    conn: AsyncConnection, settings: IssuerSettings, *, tenant_id: str, list_id: str, now: datetime
) -> str:
    row = await store.load_status_list(conn, list_id)
    if row is None:
        raise CredentialError("STATUS_LIST_NOT_FOUND")
    iat = int(now.timestamp())
    return settings.keyring.sign(
        {
            "sub": settings.status_uri(tenant_id, list_id),
            "iat": iat,
            "exp": iat + int(STATUS_TOKEN_TTL.total_seconds()),
            "ttl": 300,
            "status_list": {"bits": row["bits"], "lst": encode_status_list(bytes(row["statuses"]))},
        },
        typ=STATUS_LIST_TYP,
    )


async def change_status(
    conn: AsyncConnection, *, tenant_id: str, credential_id: str, status: str, now: datetime
) -> None:
    record = await store.issued(conn, credential_id)
    if record is None:
        raise CredentialError("CREDENTIAL_NOT_FOUND")
    if record["status"] == "REVOKED":
        raise CredentialError("CREDENTIAL_REVOKED")  # revocation is terminal
    row = await store.load_status_list(conn, str(record["status_list_id"]))
    assert row is not None
    statuses = bytearray(row["statuses"])
    set_status(statuses, row["bits"], record["status_index"], STATUS_CODES[status])
    revision = await store.write_status(conn, str(record["status_list_id"]), bytes(statuses))
    await store.set_issued_status(conn, credential_id, status)
    RELIANCE.invalidate_containing(
        str(record["status_list_id"])
    )  # SIG-21: our own change is seen at once
    await emit(
        conn,
        name="credential.revoked" if status == "REVOKED" else "credential.status.changed",
        tenant_id=tenant_id,
        aggregate_type="credential",
        aggregate_id=credential_id,
        aggregate_version=revision,
        data={"credential_id": credential_id, "status": status},
    )
