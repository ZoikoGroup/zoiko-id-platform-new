"""Credential configurations, issuer trust, RP registrations, presentations, issued credentials.

ZID-DATA-001 §6.3; ZID-VC-001 §4-5, §13, §15, §18. Inside the caller's tenant transaction (RLS).
Presentation nonce/state are stored as hashes; disclosed claims are kept only until the relying
party collects them, then removed (§7.1: no raw credential payloads retained by default).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.domain.credentials import (
    Configuration,
    CredentialError,
    IssuerTrust,
)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


async def create_configuration(conn: AsyncConnection, tenant_id: str, c: dict[str, Any]) -> str:
    cid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.credential_configuration (configuration_id, tenant_id, credential_type, "
            "format, adapter_version, required_claims, optional_claims, holder_binding_required, "
            "status_method, status_mandatory, risk_class, assurance_profile_id, issuance_enabled) "
            "VALUES (:id, :t, :ty, :f, :a, :r, :o, :hb, :sm, :smand, :risk, :ap, :ie)"
        ),
        {
            "id": uuid.UUID(cid),
            "t": uuid.UUID(tenant_id),
            "ty": c["credential_type"],
            "f": c["format"],
            "a": c["adapter_version"],
            "r": c["required_claims"],
            "o": c.get("optional_claims", []),
            "hb": c.get("holder_binding_required", True),
            "sm": c["status_method"],
            "smand": c.get("status_mandatory", True),
            "risk": c.get("risk_class", 2),
            "ap": uuid.UUID(c["assurance_profile_id"]) if c.get("assurance_profile_id") else None,
            "ie": c.get("issuance_enabled", False),
        },
    )
    return cid


async def activate_configuration(conn: AsyncConnection, configuration_id: str) -> bool:
    r = await conn.execute(
        text(
            "UPDATE cred.credential_configuration SET status = 'ACTIVE' "
            "WHERE configuration_id = :c AND status = 'DRAFT'"
        ),
        {"c": uuid.UUID(configuration_id)},
    )
    return r.rowcount == 1


async def load_configuration(conn: AsyncConnection, configuration_id: str) -> Configuration:
    try:
        cid = uuid.UUID(configuration_id)
    except ValueError as e:
        raise CredentialError("CONFIGURATION_NOT_FOUND") from e
    row = (
        await conn.execute(
            text("SELECT * FROM cred.credential_configuration WHERE configuration_id = :c"),
            {"c": cid},
        )
    ).first()
    if row is None:
        raise CredentialError("CONFIGURATION_NOT_FOUND")
    m = row._mapping
    return Configuration(
        configuration_id=str(m["configuration_id"]),
        credential_type=m["credential_type"],
        format=m["format"],
        adapter_version=m["adapter_version"],
        required_claims=tuple(m["required_claims"]),
        optional_claims=tuple(m["optional_claims"]),
        holder_binding_required=m["holder_binding_required"],
        status_method=m["status_method"],
        status_mandatory=m["status_mandatory"],
        risk_class=m["risk_class"],
        status=m["status"],
        assurance_profile_id=str(m["assurance_profile_id"]) if m["assurance_profile_id"] else None,
        issuance_enabled=m["issuance_enabled"],
    )


async def create_trust(conn: AsyncConnection, tenant_id: str, t: dict[str, Any]) -> str:
    tid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.issuer_trust (trust_id, tenant_id, issuer_identifier, credential_types, "
            "jurisdictions, trust_source, jwks, iaca_certificates, trust_anchor_id, algorithms, "
            "valid_to) VALUES (:id, :t, :iss, :types, :j, :src, CAST(:jwks AS jsonb), :iaca, :anchor, "
            ":algs, :vt)"
        ),
        {
            "id": uuid.UUID(tid),
            "t": uuid.UUID(tenant_id),
            "iss": t["issuer_identifier"],
            "types": t["credential_types"],
            "j": t.get("jurisdictions", []),
            "src": t["trust_source"],
            "jwks": json.dumps(t["jwks"]) if t.get("jwks") is not None else None,
            "iaca": t.get("iaca_certificates"),
            "anchor": uuid.UUID(t["trust_anchor_id"]) if t.get("trust_anchor_id") else None,
            "algs": t.get("algorithms", ["ES256"]),
            "vt": t["valid_to"],
        },
    )
    return tid


async def load_trust(
    conn: AsyncConnection, issuer: str
) -> tuple[IssuerTrust, dict[str, Any]] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM cred.issuer_trust WHERE issuer_identifier = :i"), {"i": issuer}
        )
    ).first()
    if row is None:
        return None
    m = dict(row._mapping)
    return (
        IssuerTrust(
            issuer_identifier=m["issuer_identifier"],
            credential_types=frozenset(m["credential_types"]),
            jurisdictions=frozenset(m["jurisdictions"]),
            trust_source=m["trust_source"],
            status=m["status"],
            valid_to=m["valid_to"],
        ),
        m,
    )


async def trust_for_type(conn: AsyncConnection, credential_type: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM cred.issuer_trust WHERE :t = ANY(credential_types) AND status = 'ACTIVE'"
            ),
            {"t": credential_type},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def set_trust_status(conn: AsyncConnection, issuer: str, status: str) -> bool:
    r = await conn.execute(
        text(
            "UPDATE cred.issuer_trust SET status = :s, revision = revision + 1 "
            "WHERE issuer_identifier = :i"
        ),
        {"i": issuer, "s": status},
    )
    return r.rowcount == 1


async def rotate_trust_keys(conn: AsyncConnection, issuer: str, jwks: dict[str, Any]) -> bool:
    """Replace the pinned key set of a PINNED issuer (rollover); the revision moves with it."""
    r = await conn.execute(
        text(
            "UPDATE cred.issuer_trust SET jwks = CAST(:j AS jsonb), revision = revision + 1 "
            "WHERE issuer_identifier = :i AND trust_source = 'PINNED' AND jwks IS NOT NULL"
        ),
        {"i": issuer, "j": json.dumps(jwks)},
    )
    return r.rowcount == 1


async def create_registration(conn: AsyncConnection, tenant_id: str, r: dict[str, Any]) -> str:
    rid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.rp_registration (registration_id, tenant_id, client_id, purpose, "
            "allowed_attributes, retention_days, valid_to) VALUES (:id, :t, :c, :p, :a, :rd, :vt)"
        ),
        {
            "id": uuid.UUID(rid),
            "t": uuid.UUID(tenant_id),
            "c": r["client_id"],
            "p": r["purpose"],
            "a": r["allowed_attributes"],
            "rd": r.get("retention_days", 0),
            "vt": r["valid_to"],
        },
    )
    return rid


async def registration(
    conn: AsyncConnection, client_id: str, purpose: str, now: datetime
) -> list[str] | None:
    row = (
        await conn.execute(
            text(
                "SELECT allowed_attributes FROM cred.rp_registration WHERE client_id = :c "
                "AND purpose = :p AND status = 'ACTIVE' AND valid_to > :now"
            ),
            {"c": client_id, "p": purpose, "now": now},
        )
    ).first()
    return list(row.allowed_attributes) if row else None


async def create_presentation(conn: AsyncConnection, tenant_id: str, p: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO cred.presentation_record (presentation_id, tenant_id, configuration_id, "
            "relying_client_id, purpose, requested_claims, subject_principal_id, jurisdiction, nonce, "
            "state_hash, request_jwt, status, created_at, expires_at, response_mode, response_key) "
            "VALUES (:id, :t, :c, :rc, :p, :claims, :subj, :j, :n, :sh, :jwt, 'REQUESTED', :now, "
            ":exp, :mode, CAST(:rkey AS jsonb))"
        ),
        {
            "id": uuid.UUID(p["presentation_id"]),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(p["configuration_id"]),
            "rc": p["client_id"],
            "p": p["purpose"],
            "claims": p["claims"],
            "subj": uuid.UUID(p["subject_principal_id"]) if p.get("subject_principal_id") else None,
            "j": p["jurisdiction"],
            "n": p["nonce"],
            "sh": sha256(p["state"]),
            "jwt": p["request_jwt"],
            "now": p["created_at"],
            "exp": p["expires_at"],
            "mode": p.get("response_mode", "direct_post"),
            "rkey": json.dumps(p["response_key"]) if p.get("response_key") else None,
        },
    )


async def load_presentation(
    conn: AsyncConnection, presentation_id: str, *, lock: bool = False
) -> dict[str, Any]:
    try:
        pid = uuid.UUID(presentation_id)
    except ValueError as e:
        raise CredentialError("PRESENTATION_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                "SELECT * FROM cred.presentation_record WHERE presentation_id = :p"
                + (" FOR UPDATE" if lock else "")
            ),
            {"p": pid},
        )
    ).first()
    if row is None:
        raise CredentialError("PRESENTATION_NOT_FOUND")
    return dict(row._mapping)


async def by_state(conn: AsyncConnection, state: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM cred.presentation_record WHERE state_hash = :h FOR UPDATE"),
            {"h": sha256(state)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def mark_request_fetched(conn: AsyncConnection, presentation_id: Any, now: datetime) -> bool:
    r = await conn.execute(
        text(
            "UPDATE cred.presentation_record SET request_fetched_at = :now WHERE presentation_id = :p "
            "AND request_fetched_at IS NULL AND status = 'REQUESTED'"
        ),
        {"p": presentation_id, "now": now},
    )
    return r.rowcount == 1


async def finish_presentation(
    conn: AsyncConnection,
    presentation_id: Any,
    *,
    status: str,
    reasons: list[str],
    issuer: str | None,
    credential_status: str | None,
    commitments: dict[str, str] | None,
    disclosed: dict[str, Any] | None,
    now: datetime,
    basis: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.presentation_record SET status = :st, reason_codes = :r, "
            "issuer_identifier = :iss, credential_status = :cs, "
            "claim_commitments = CAST(:cc AS jsonb), disclosed_claims = CAST(:dc AS jsonb), "
            "verification_basis = CAST(:vb AS jsonb), "
            "completed_at = :now, response_key = NULL WHERE presentation_id = :p"
        ),
        {
            "p": presentation_id,
            "st": status,
            "r": reasons,
            "iss": issuer,
            "cs": credential_status,
            "cc": json.dumps(commitments) if commitments is not None else None,
            "dc": json.dumps(disclosed, default=str) if disclosed is not None else None,
            "vb": json.dumps(basis) if basis is not None else None,
            "now": now,
        },
    )


async def collect_claims(conn: AsyncConnection, presentation_id: Any) -> dict[str, Any] | None:
    """Hand the disclosed claims to the relying party once, then forget them."""
    claims = (
        await conn.execute(
            text(
                "SELECT disclosed_claims FROM cred.presentation_record WHERE presentation_id = :p "
                "FOR UPDATE"
            ),
            {"p": presentation_id},
        )
    ).scalar()
    if not claims:
        return None
    await conn.execute(
        text(
            "UPDATE cred.presentation_record SET disclosed_claims = NULL WHERE presentation_id = :p"
        ),
        {"p": presentation_id},
    )
    return dict(claims)


# -------------------------------------------------------------------------------- issuance


async def status_list_for(conn: AsyncConnection, tenant_id: str) -> tuple[str, int]:
    """The tenant's current list and the next free index (a new list when full)."""
    row = (
        await conn.execute(
            text(
                "SELECT list_id, next_index, size FROM cred.status_list WHERE next_index < size "
                "ORDER BY created_at LIMIT 1 FOR UPDATE"
            )
        )
    ).first()
    if row is None:
        list_id, size = uuid.uuid4(), 16384
        await conn.execute(
            text(
                "INSERT INTO cred.status_list (list_id, tenant_id, bits, size, statuses) "
                "VALUES (:id, :t, 2, :size, :s)"
            ),
            {"id": list_id, "t": uuid.UUID(tenant_id), "size": size, "s": bytes(size * 2 // 8)},
        )
        idx = 0
    else:
        list_id, idx = row.list_id, row.next_index
    await conn.execute(
        text("UPDATE cred.status_list SET next_index = :n WHERE list_id = :id"),
        {"id": list_id, "n": idx + 1},
    )
    return str(list_id), int(idx)


async def load_status_list(conn: AsyncConnection, list_id: str) -> dict[str, Any] | None:
    try:
        lid = uuid.UUID(list_id)
    except ValueError:
        return None
    row = (
        await conn.execute(text("SELECT * FROM cred.status_list WHERE list_id = :l"), {"l": lid})
    ).first()
    return dict(row._mapping) if row else None


async def write_status(conn: AsyncConnection, list_id: str, statuses: bytes) -> int:
    """Returns the list's new revision (monotonic: also orders the credential status events)."""
    return int(
        (
            await conn.execute(
                text(
                    "UPDATE cred.status_list SET statuses = :s, revision = revision + 1 "
                    "WHERE list_id = :l RETURNING revision"
                ),
                {"l": uuid.UUID(list_id), "s": statuses},
            )
        ).scalar_one()
    )


async def record_issued(conn: AsyncConnection, tenant_id: str, r: dict[str, Any]) -> bool:
    """Idempotent per issuance transaction: a retry never mints a second credential."""
    result = await conn.execute(
        text(
            "INSERT INTO cred.credential_record (credential_id, tenant_id, subject_principal_id, "
            "issuer_identifier, credential_type, format, configuration_id, status, status_ref, "
            "status_list_id, status_index, holder_key_thumbprint, issuance_transaction, issued_at, "
            "expires_at) VALUES (:id, :t, :s, :iss, :ty, 'SD-JWT', :c, 'VALID', :ref, :lid, :idx, "
            ":jkt, :tx, :now, :exp) ON CONFLICT (tenant_id, issuance_transaction) DO NOTHING"
        ),
        {
            "id": uuid.UUID(r["credential_id"]),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(r["subject_principal_id"]),
            "iss": r["issuer"],
            "ty": r["credential_type"],
            "c": uuid.UUID(r["configuration_id"]),
            "ref": r["status_ref"],
            "lid": uuid.UUID(r["status_list_id"]),
            "idx": r["status_index"],
            "jkt": r["holder_jkt"],
            "tx": r["transaction"],
            "now": r["issued_at"],
            "exp": r["expires_at"],
        },
    )
    return result.rowcount == 1


async def issued(conn: AsyncConnection, credential_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM cred.credential_record WHERE credential_id = :c FOR UPDATE"),
            {"c": uuid.UUID(credential_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def set_issued_status(conn: AsyncConnection, credential_id: str, status: str) -> None:
    await conn.execute(
        text("UPDATE cred.credential_record SET status = :s WHERE credential_id = :c"),
        {"c": uuid.UUID(credential_id), "s": status},
    )


# ------------------------------------------------------------------------------ administration
# Listing views carry identifiers, states, limits and times only: no pinned key material, holder
# key thumbprints, status-list positions or claim payloads.


async def list_issued(
    conn: AsyncConnection, *, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT credential_id, configuration_id, credential_type, format, issuer_identifier, "
                "subject_principal_id, status, issued_at, expires_at FROM cred.credential_record "
                + ("WHERE status = :s " if status is not None else "")
                + "ORDER BY issued_at DESC, credential_id DESC LIMIT :lim"
            ),
            {"s": status, "lim": limit},
        )
    ).all()
    return [
        {
            "credential_id": str(r.credential_id),
            "configuration_id": str(r.configuration_id),
            "credential_type": r.credential_type,
            "format": r.format,
            "issuer_identifier": r.issuer_identifier,
            "subject_principal_id": str(r.subject_principal_id) if r.subject_principal_id else None,
            "status": r.status,
            "issued_at": r.issued_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rows
    ]


async def list_configurations(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT configuration_id, credential_type, format, adapter_version, required_claims, "
                "optional_claims, holder_binding_required, status_method, status_mandatory, "
                "risk_class, assurance_profile_id, issuance_enabled, status, created_at "
                "FROM cred.credential_configuration ORDER BY created_at DESC, configuration_id"
            )
        )
    ).all()
    return [
        {
            "configuration_id": str(r.configuration_id),
            "credential_type": r.credential_type,
            "format": r.format,
            "adapter_version": r.adapter_version,
            "required_claims": list(r.required_claims),
            "optional_claims": list(r.optional_claims),
            "holder_binding_required": r.holder_binding_required,
            "status_method": r.status_method,
            "status_mandatory": r.status_mandatory,
            "risk_class": r.risk_class,
            "assurance_profile_id": str(r.assurance_profile_id) if r.assurance_profile_id else None,
            "issuance_enabled": r.issuance_enabled,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def list_trust(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT trust_id, issuer_identifier, credential_types, jurisdictions, trust_source, "
                "trust_anchor_id, algorithms, status, valid_to, revision, created_at "
                "FROM cred.issuer_trust ORDER BY created_at DESC, trust_id"
            )
        )
    ).all()
    return [
        {
            "trust_id": str(r.trust_id),
            "issuer_identifier": r.issuer_identifier,
            "credential_types": list(r.credential_types),
            "jurisdictions": list(r.jurisdictions),
            "trust_source": r.trust_source,
            "trust_anchor_id": str(r.trust_anchor_id) if r.trust_anchor_id else None,
            "algorithms": list(r.algorithms),
            "status": r.status,
            "valid_to": r.valid_to.isoformat(),
            "revision": r.revision,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def list_registrations(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT registration_id, client_id, purpose, allowed_attributes, retention_days, "
                "status, valid_to FROM cred.rp_registration ORDER BY client_id, purpose"
            )
        )
    ).all()
    return [
        {
            "registration_id": str(r.registration_id),
            "client_id": r.client_id,
            "purpose": r.purpose,
            "allowed_attributes": list(r.allowed_attributes),
            "retention_days": r.retention_days,
            "status": r.status,
            "valid_to": r.valid_to.isoformat(),
        }
        for r in rows
    ]
