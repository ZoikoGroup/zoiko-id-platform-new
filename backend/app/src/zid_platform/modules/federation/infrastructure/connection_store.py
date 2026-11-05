"""Federation connections and login transactions in PostgreSQL (ZID-AUTH-001 §9, §10, §17).

Inside the caller's tenant transaction (RLS). Every configuration change increments
`config_revision` and emits federation.metadata.changed; a login transaction remembers the
revision it started under, so a rotation in between invalidates it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.federation.domain.connection import Connection

_COLS = (
    "connection_id, tenant_id, protocol, display_name, issuer, status, config_revision, "
    "metadata_expires_at, acr_levels, trust_amr, link_policy, client_id, client_auth, "
    "client_secret_ref, authorization_endpoint, token_endpoint, jwks, algorithms, scopes, sso_url, "
    "idp_certificates, sp_entity_id, allow_idp_initiated, trust_anchor_id, federation_entity_id, "
    "idp_initiated_targets, sp_decryption_key_ref, require_encrypted_assertions"
)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _conn(r: object) -> Connection:
    m = r._mapping  # type: ignore[attr-defined]
    return Connection(
        connection_id=str(m["connection_id"]),
        tenant_id=str(m["tenant_id"]),
        protocol=m["protocol"],
        display_name=m["display_name"],
        issuer=m["issuer"],
        status=m["status"],
        config_revision=m["config_revision"],
        metadata_expires_at=m["metadata_expires_at"],
        acr_levels=dict(m["acr_levels"]),
        trust_amr=m["trust_amr"],
        link_policy=m["link_policy"],
        client_id=m["client_id"],
        client_auth=m["client_auth"],
        client_secret_ref=m["client_secret_ref"],
        authorization_endpoint=m["authorization_endpoint"],
        token_endpoint=m["token_endpoint"],
        jwks=m["jwks"],
        algorithms=tuple(m["algorithms"]),
        scopes=tuple(m["scopes"]),
        sso_url=m["sso_url"],
        idp_certificates=tuple(m["idp_certificates"] or ()),
        sp_entity_id=m["sp_entity_id"],
        allow_idp_initiated=m["allow_idp_initiated"],
        trust_anchor_id=str(m["trust_anchor_id"]) if m["trust_anchor_id"] else None,
        federation_entity_id=m["federation_entity_id"],
        idp_initiated_targets=tuple(m["idp_initiated_targets"] or ()),
        sp_decryption_key_ref=m["sp_decryption_key_ref"],
        require_encrypted_assertions=m["require_encrypted_assertions"],
    )


def _params(c: Connection) -> dict[str, object]:
    return {
        "id": uuid.UUID(c.connection_id),
        "t": uuid.UUID(c.tenant_id),
        "protocol": c.protocol,
        "name": c.display_name,
        "iss": c.issuer,
        "status": c.status,
        "rev": c.config_revision,
        "exp": c.metadata_expires_at,
        "acr": json.dumps(dict(c.acr_levels)),
        "amr": c.trust_amr,
        "lp": c.link_policy,
        "cid": c.client_id,
        "cauth": c.client_auth,
        "sref": c.client_secret_ref,
        "authz": c.authorization_endpoint,
        "token": c.token_endpoint,
        "jwks": json.dumps(dict(c.jwks)) if c.jwks is not None else None,
        "algs": list(c.algorithms),
        "scopes": list(c.scopes),
        "sso": c.sso_url,
        "certs": list(c.idp_certificates) or None,
        "sp": c.sp_entity_id,
        "idpinit": c.allow_idp_initiated,
        "anchor": uuid.UUID(c.trust_anchor_id) if c.trust_anchor_id else None,
        "entity": c.federation_entity_id,
        "targets": list(c.idp_initiated_targets),
        "deckey": c.sp_decryption_key_ref,
        "enc": c.require_encrypted_assertions,
    }


async def create(conn: AsyncConnection, c: Connection) -> None:
    await conn.execute(
        text(
            f"INSERT INTO authn.federation_connection ({_COLS}) VALUES (:id, :t, :protocol, :name, "
            ":iss, :status, :rev, :exp, CAST(:acr AS jsonb), :amr, :lp, :cid, :cauth, :sref, "
            ":authz, :token, CAST(:jwks AS jsonb), :algs, :scopes, :sso, :certs, :sp, :idpinit, "
            ":anchor, :entity, :targets, :deckey, :enc)"
        ),
        _params(c),
    )
    await _changed(conn, c, "created")


async def _changed(conn: AsyncConnection, c: Connection, change: str) -> None:
    await emit(
        conn,
        name="federation.metadata.changed",
        tenant_id=c.tenant_id,
        aggregate_type="federation_connection",
        aggregate_id=c.connection_id,
        aggregate_version=c.config_revision,
        data={
            "connection_id": c.connection_id,
            "issuer": c.issuer,
            "change": change,
            "status": c.status,
            "config_revision": c.config_revision,
        },
    )


async def load(
    conn: AsyncConnection, connection_id: str, *, lock: bool = False
) -> Connection | None:
    try:
        cid = uuid.UUID(connection_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.federation_connection WHERE connection_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": cid},
        )
    ).first()
    return _conn(row) if row else None


async def by_issuer(conn: AsyncConnection, issuer: str) -> Connection | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.federation_connection WHERE issuer = :i"),
            {"i": issuer},
        )
    ).first()
    return _conn(row) if row else None


async def list_all(conn: AsyncConnection) -> list[Connection]:
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.federation_connection ORDER BY created_at")
        )
    ).all()
    return [_conn(r) for r in rows]


async def replace(conn: AsyncConnection, old: Connection, new: Connection, change: str) -> None:
    """Optimistic update: `new` must carry old.config_revision + 1."""
    assert new.config_revision == old.config_revision + 1
    p = _params(new)
    result = await conn.execute(
        text(
            "UPDATE authn.federation_connection SET display_name = :name, sp_entity_id = :sp, "
            "status = :status, config_revision = :rev, "
            "metadata_expires_at = :exp, acr_levels = CAST(:acr AS jsonb), trust_amr = :amr, "
            "link_policy = :lp, client_auth = :cauth, client_secret_ref = :sref, "
            "authorization_endpoint = :authz, token_endpoint = :token, jwks = CAST(:jwks AS jsonb), "
            "algorithms = :algs, scopes = :scopes, sso_url = :sso, idp_certificates = :certs, "
            "allow_idp_initiated = :idpinit, idp_initiated_targets = :targets, "
            "sp_decryption_key_ref = :deckey, require_encrypted_assertions = :enc, "
            "updated_at = now() "
            "WHERE connection_id = :id AND config_revision = :old"
        ),
        {**p, "old": old.config_revision},
    )
    if result.rowcount != 1:
        raise ValueError("CONCURRENT_MODIFICATION")
    await _changed(conn, new, change)


@dataclass(frozen=True, slots=True)
class Login:
    connection_id: str
    config_revision: int
    nonce: str | None
    code_verifier: str | None
    request_id: str | None
    client_id: str | None
    return_to: str | None
    link_principal_id: str | None
    expires_at: datetime
    force_reauth: bool = False


async def start_login(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    c: Connection,
    nonce: str | None,
    code_verifier: str | None,
    request_id: str | None,
    client_id: str | None,
    return_to: str | None,
    link_principal_id: str | None,
    expires_at: datetime,
    force_reauth: bool = False,
) -> str:
    """Returns the `state` / RelayState: `<tenant>.<random>` (the callback needs the tenant for RLS)."""
    state = f"{tenant_id}.{secrets.token_urlsafe(32)}"
    await conn.execute(
        text(
            "INSERT INTO authn.federation_login (state_hash, tenant_id, connection_id, "
            "config_revision, nonce, code_verifier, request_id, client_id, return_to, "
            "link_principal_id, expires_at, force_reauth) VALUES (:h, :t, :c, :rev, :n, :v, :rid, :cid, :rt, :lp, "
            ":exp, :fr)"
        ),
        {
            "h": sha256(state),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(c.connection_id),
            "rev": c.config_revision,
            "n": nonce,
            "v": code_verifier,
            "rid": request_id,
            "cid": client_id,
            "rt": return_to,
            "lp": uuid.UUID(link_principal_id) if link_principal_id else None,
            "exp": expires_at,
            "fr": force_reauth,
        },
    )
    return state


async def finish_login(conn: AsyncConnection, state: str, now: datetime) -> Login | None:
    row = (
        await conn.execute(
            text(
                "UPDATE authn.federation_login SET used_at = :now WHERE state_hash = :h "
                "AND used_at IS NULL AND expires_at > :now RETURNING connection_id, config_revision, "
                "nonce, code_verifier, request_id, client_id, return_to, link_principal_id, expires_at, force_reauth"
            ),
            {"h": sha256(state), "now": now},
        )
    ).first()
    if row is None:
        return None
    return Login(
        connection_id=str(row.connection_id),
        config_revision=row.config_revision,
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        request_id=row.request_id,
        client_id=row.client_id,
        return_to=row.return_to,
        link_principal_id=str(row.link_principal_id) if row.link_principal_id else None,
        expires_at=row.expires_at,
        force_reauth=bool(row.force_reauth),
    )


def state_tenant(state: str) -> str | None:
    tenant, _, rest = state.partition(".")
    try:
        return str(uuid.UUID(tenant)) if rest else None
    except ValueError:
        return None


async def record_assertion(
    conn: AsyncConnection, *, tenant_id: str, issuer: str, assertion_id: str, expires_at: datetime
) -> bool:
    """True the first time an assertion is seen; False is a replay (AUTH-10)."""
    result = await conn.execute(
        text(
            "INSERT INTO authn.saml_assertion_replay (tenant_id, issuer, assertion_id, expires_at) "
            "VALUES (:t, :i, :a, :exp) ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "i": issuer, "a": assertion_id, "exp": expires_at},
    )
    return result.rowcount == 1


async def create_anchor(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    entity_id: str,
    jwks: dict[str, object],
    allowed_domains: list[str],
) -> str:
    aid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO authn.federation_trust_anchor (anchor_id, tenant_id, entity_id, jwks, "
            "allowed_domains) VALUES (:id, :t, :e, CAST(:j AS jsonb), :d)"
        ),
        {
            "id": uuid.UUID(aid),
            "t": uuid.UUID(tenant_id),
            "e": entity_id,
            "j": json.dumps(jwks),
            "d": allowed_domains,
        },
    )
    await emit(
        conn,
        name="federation.anchor.changed",
        tenant_id=tenant_id,
        aggregate_type="federation_trust_anchor",
        aggregate_id=aid,
        aggregate_version=1,
        data={"anchor_id": aid, "entity_id": entity_id, "status": "ACTIVE"},
    )
    return aid


async def load_anchor(conn: AsyncConnection, anchor_id: str) -> dict[str, object] | None:
    row = (
        await conn.execute(
            text(
                "SELECT anchor_id, entity_id, jwks, allowed_domains, status, revision "
                "FROM authn.federation_trust_anchor WHERE anchor_id = :a"
            ),
            {"a": uuid.UUID(anchor_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def list_anchors(conn: AsyncConnection) -> list[dict[str, object]]:
    rows = (
        await conn.execute(
            text(
                "SELECT anchor_id, entity_id, jsonb_array_length(jwks -> 'keys') AS pinned_keys, "
                "allowed_domains, status, revision, created_at FROM authn.federation_trust_anchor "
                "ORDER BY created_at DESC, anchor_id"
            )
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def set_anchor_status(conn: AsyncConnection, anchor_id: str, status: str) -> int | None:
    current = (
        await conn.execute(
            text(
                "SELECT status, revision FROM authn.federation_trust_anchor WHERE anchor_id = :a "
                "FOR UPDATE"
            ),
            {"a": uuid.UUID(anchor_id)},
        )
    ).first()
    if current is not None and current.status == status:
        return int(current.revision)  # already there: no second revision, no second event
    row = (
        await conn.execute(
            text(
                "UPDATE authn.federation_trust_anchor SET status = :s, revision = revision + 1 "
                "WHERE anchor_id = :a AND status <> 'RETIRED' RETURNING tenant_id, entity_id, revision"
            ),
            {"a": uuid.UUID(anchor_id), "s": status},
        )
    ).first()
    if row is None:
        return None
    await emit(
        conn,
        name="federation.anchor.changed",
        tenant_id=str(row.tenant_id),
        aggregate_type="federation_trust_anchor",
        aggregate_id=anchor_id,
        aggregate_version=row.revision,
        data={"anchor_id": anchor_id, "entity_id": row.entity_id, "status": status},
    )
    return int(row.revision)
