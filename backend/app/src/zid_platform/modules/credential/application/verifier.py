"""OID4VP 1.0 verifier / relying party (ZID-VC-001 §7-11, §13-15; HAIP 1.0; DEC-E2 baseline role).

    create()    purpose-bound request: registered RP + purpose, minimized DCQL claim set, nonce,
                state; a signed request object (oauth-authz-req+jwt) served once by request_uri
    respond()   direct_post response: state -> single presentation -> format adapter ->
                issuer trust (typed) -> holder binding to this transaction -> status -> claims;
                then canonical assertions (verification module) where the configuration maps a
                verification profile. A presentation never creates authority.

Separate reason codes for proof, trust, binding, status and claims; nothing undisclosed and no
raw presentation is kept. UNKNOWN status is never VALID (fails closed when mandatory).
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_crypto import CryptoError, KeyRing, peek_header, verify_external_jws
from zid_db import tenant_transaction
from zid_outbox import emit

from zid_platform.modules.credential.domain.credentials import (
    KB_TYP,
    SD_JWT_TYPS,
    STATUS_LIST_TYP,
    Configuration,
    CredentialError,
    b64d,
    disclose,
    minimize,
    require_role,
    sd_hash,
    split_presentation,
    status_at,
    status_decision,
    trust_decision,
)
from zid_platform.modules.credential.infrastructure import credential_store as store
from zid_platform.modules.credential.infrastructure.mdoc import (
    session_transcript,
    verify_device_response,
)
from zid_platform.modules.federation.ports import TrustChainError, resolve, trust_anchor
from zid_platform.modules.verification.ports import record_presentation

REQUEST_TTL = timedelta(minutes=10)
KB_MAX_AGE = timedelta(minutes=5)
SKEW = timedelta(seconds=60)
REQUEST_TYP = "oauth-authz-req+jwt"


@dataclass(frozen=True, slots=True)
class VerifierSettings:
    issuer: str
    keyring: KeyRing
    transport: httpx.AsyncBaseTransport | None = None

    @property
    def client_id(self) -> str:
        return f"openid_federation:{self.issuer}"  # OID4VP 1.0 client identifier prefix

    def response_uri(self, tenant_id: str) -> str:
        return f"{self.issuer.rstrip('/')}/presentations/response/{tenant_id}"

    def request_uri(self, tenant_id: str, presentation_id: str) -> str:
        return f"{self.issuer.rstrip('/')}/presentations/{tenant_id}.{presentation_id}/request"


def _dcql(config: Configuration, claims: tuple[str, ...]) -> dict[str, Any]:
    if config.format == "SD_JWT_VC":
        return {
            "credentials": [
                {
                    "id": "cred1",
                    "format": "dc+sd-jwt",
                    "meta": {"vct_values": [config.credential_type]},
                    "claims": [{"path": [c]} for c in claims],
                }
            ]
        }
    namespace = config.credential_type
    return {
        "credentials": [
            {
                "id": "cred1",
                "format": "mso_mdoc",
                "meta": {"doctype_value": config.credential_type},
                "claims": [{"path": [namespace, c]} for c in claims],
            }
        ]
    }


async def create(
    conn: AsyncConnection,
    settings: VerifierSettings,
    *,
    tenant_id: str,
    client_id: str,
    configuration_id: str,
    purpose: str,
    claims: list[str],
    subject_principal_id: str | None,
    jurisdiction: str,
    now: datetime,
) -> dict[str, Any]:
    require_role("VERIFIER")
    config = await store.load_configuration(conn, configuration_id)
    if config.status != "ACTIVE":
        raise CredentialError("CONFIGURATION_NOT_ACTIVE")
    allowed = await store.registration(conn, client_id, purpose, now)
    if allowed is None:
        raise CredentialError("RP_NOT_REGISTERED_FOR_PURPOSE")
    requested = minimize(claims, config, allowed)
    pid, nonce, state = str(uuid.uuid4()), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    iat = int(now.timestamp())
    request_jwt = settings.keyring.sign(
        {
            "iss": settings.client_id,
            "aud": "https://self-issued.me/v2",
            "client_id": settings.client_id,
            "response_type": "vp_token",
            "response_mode": "direct_post",
            "response_uri": settings.response_uri(tenant_id),
            "nonce": nonce,
            "state": state,
            "dcql_query": _dcql(config, requested),
            "client_metadata": {
                "vp_formats_supported": {
                    "dc+sd-jwt": {"sd-jwt_alg_values": ["ES256"], "kb-jwt_alg_values": ["ES256"]},
                    "mso_mdoc": {"issuerauth_alg_values": [-7], "deviceauth_alg_values": [-7]},
                }
            },
            "iat": iat,
            "exp": iat + int(REQUEST_TTL.total_seconds()),
        },
        typ=REQUEST_TYP,
    )
    await store.create_presentation(
        conn,
        tenant_id,
        {
            "presentation_id": pid,
            "configuration_id": configuration_id,
            "client_id": client_id,
            "purpose": purpose,
            "claims": list(requested),
            "subject_principal_id": subject_principal_id,
            "jurisdiction": jurisdiction,
            "nonce": nonce,
            "state": state,
            "request_jwt": request_jwt,
            "created_at": now,
            "expires_at": now + REQUEST_TTL,
        },
    )
    await emit(
        conn,
        name="presentation.requested",
        tenant_id=tenant_id,
        aggregate_type="presentation",
        aggregate_id=pid,
        aggregate_version=1,
        data={"presentation_id": pid, "purpose": purpose, "claims": list(requested)},
    )
    request_uri = settings.request_uri(tenant_id, pid)
    return {
        "presentation_id": pid,
        "request_uri": request_uri,
        "authorization_request": "openid4vp://?"
        + urlencode({"client_id": settings.client_id, "request_uri": request_uri}),
        "claims": list(requested),
        "expires_at": (now + REQUEST_TTL).isoformat(),
    }


async def request_object(conn: AsyncConnection, presentation_id: str, now: datetime) -> str:
    """Served once (request_uri is single-use) while the transaction is open."""
    p = await store.load_presentation(conn, presentation_id, lock=True)
    if p["status"] != "REQUESTED" or now >= p["expires_at"]:
        raise CredentialError("PRESENTATION_NOT_OPEN")
    if not await store.mark_request_fetched(conn, p["presentation_id"], now):
        raise CredentialError("REQUEST_URI_USED")
    return str(p["request_jwt"])


@dataclass(frozen=True, slots=True)
class Verified:
    issuer: str
    claims: dict[str, Any]
    credential_status: str
    holder_bound: bool


async def _issuer_keys(
    conn: AsyncConnection, settings: VerifierSettings, row: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if row["trust_source"] == "PINNED":
        return dict(row["jwks"] or {"keys": []})
    anchor = await trust_anchor(conn, str(row["trust_anchor_id"]))
    if anchor is None:
        raise CredentialError("ISSUER_UNTRUSTED", "anchor missing")
    try:
        resolved = await resolve(
            anchor, row["issuer_identifier"], now=now, transport=settings.transport
        )
    except TrustChainError as e:
        raise CredentialError("ISSUER_UNTRUSTED", e.code) from e
    md = resolved.metadata.get("openid_credential_issuer") or {}
    if not isinstance(md.get("jwks"), dict):
        raise CredentialError("ISSUER_UNTRUSTED", "issuer metadata has no keys")
    return dict(md["jwks"])


async def _status(
    settings: VerifierSettings,
    payload: dict[str, Any],
    keys: dict[str, Any],
    algorithms: list[str],
    now: datetime,
) -> str:
    ref = (payload.get("status") or {}).get("status_list") or {}
    idx, uri = ref.get("idx"), ref.get("uri")
    if not isinstance(idx, int) or not isinstance(uri, str):
        return "UNKNOWN"
    issuer_host = urlsplit(str(payload.get("iss", ""))).hostname
    if not uri.startswith("https://") or urlsplit(uri).hostname != issuer_host:
        return "UNKNOWN"  # status only from the issuer's own domain
    try:
        async with httpx.AsyncClient(
            transport=settings.transport, timeout=5.0, follow_redirects=False
        ) as c:
            r = await c.get(uri, headers={"Accept": "application/statuslist+jwt"})
        if r.status_code != 200 or len(r.content) > 256 * 1024:
            return "UNKNOWN"
        _, claims = verify_external_jws(
            r.text.strip(), keys=keys.get("keys", []), algorithms=algorithms, typ=STATUS_LIST_TYP
        )
    except (httpx.HTTPError, CryptoError):
        return "UNKNOWN"
    exp = claims.get("exp")
    if claims.get("sub") != uri or (isinstance(exp, int | float) and now.timestamp() >= exp):
        return "UNKNOWN"
    sl = claims.get("status_list") or {}
    try:
        return status_at(str(sl.get("lst", "")), int(sl.get("bits", 0)), idx)
    except CredentialError:
        return "UNKNOWN"


async def _sd_jwt(
    conn: AsyncConnection,
    settings: VerifierSettings,
    config: Configuration,
    presentation: str,
    *,
    nonce: str,
    requested: list[str],
    now: datetime,
) -> Verified:
    parts = split_presentation(presentation)
    try:
        header = peek_header(parts.issuer_jwt)
        unverified = json.loads(b64d(parts.issuer_jwt.split(".")[1]))
    except (CryptoError, ValueError) as e:
        raise CredentialError("SD_JWT_MALFORMED") from e
    if header.get("typ") not in SD_JWT_TYPS:
        raise CredentialError("SD_JWT_TYP_INVALID")
    issuer = str(unverified.get("iss", ""))
    found = await store.load_trust(conn, issuer)
    trust_decision(
        found[0] if found else None,
        credential_type=config.credential_type,
        jurisdiction=None,
        now=now,
    )
    assert found is not None
    row = found[1]
    keys = await _issuer_keys(conn, settings, row, now)
    try:
        _, payload = verify_external_jws(
            parts.issuer_jwt,
            keys=keys.get("keys", []),
            algorithms=row["algorithms"],
            typ=str(header.get("typ")),
        )
    except CryptoError as e:
        raise CredentialError("CREDENTIAL_PROOF_INVALID", e.code) from e
    if payload.get("vct") != config.credential_type:
        raise CredentialError("CREDENTIAL_TYPE_MISMATCH")
    exp, nbf = payload.get("exp"), payload.get("nbf")
    if isinstance(exp, int | float) and now.timestamp() >= exp + SKEW.total_seconds():
        raise CredentialError("CREDENTIAL_EXPIRED")
    if isinstance(nbf, int | float) and nbf > now.timestamp() + SKEW.total_seconds():
        raise CredentialError("CREDENTIAL_NOT_YET_VALID")
    claims = disclose(payload, parts.disclosures)
    bound = False
    if config.holder_binding_required:
        jwk = (payload.get("cnf") or {}).get("jwk")
        if not isinstance(jwk, dict) or parts.kb_jwt is None:
            raise CredentialError("HOLDER_BINDING_MISSING")
        try:
            _, kb = verify_external_jws(
                parts.kb_jwt, keys=[jwk], algorithms=["ES256", "Ed25519"], typ=KB_TYP
            )
        except CryptoError as e:
            raise CredentialError("HOLDER_BINDING_INVALID", e.code) from e
        iat = kb.get("iat")
        if kb.get("aud") != settings.client_id or kb.get("nonce") != nonce:
            raise CredentialError("HOLDER_BINDING_INVALID", "audience/nonce")  # replay/forwarding
        if (
            not isinstance(iat, int | float)
            or abs(now.timestamp() - iat) > KB_MAX_AGE.total_seconds()
        ):
            raise CredentialError("HOLDER_BINDING_INVALID", "iat")
        if kb.get("sd_hash") != sd_hash(parts.sd_hash_input):
            raise CredentialError("HOLDER_BINDING_INVALID", "sd_hash")
        bound = True
    status = (
        await _status(settings, payload, keys, list(row["algorithms"]), now)
        if config.status_method == "TOKEN_STATUS_LIST"
        else "VALID"
    )
    status_decision(status, config)
    missing = [c for c in requested if c in config.required_claims and c not in claims]
    if missing:
        raise CredentialError("CLAIMS_MISSING", ",".join(missing))
    return Verified(issuer, {c: claims[c] for c in requested if c in claims}, status, bound)


async def _mdoc(
    conn: AsyncConnection,
    settings: VerifierSettings,
    config: Configuration,
    presentation: str,
    *,
    tenant_id: str,
    nonce: str,
    requested: list[str],
    now: datetime,
) -> Verified:
    rows = await store.trust_for_type(conn, config.credential_type)
    roots = [pem for r in rows if r["valid_to"] > now for pem in (r["iaca_certificates"] or [])]
    if not roots:
        raise CredentialError("ISSUER_UNTRUSTED", "no IACA trusted for this document type")
    transcript = session_transcript(
        client_id=settings.client_id, nonce=nonce, response_uri=settings.response_uri(tenant_id)
    )
    claims, _ = verify_device_response(
        presentation,
        doc_type=config.credential_type,
        iaca_roots=roots,
        transcript=transcript,
        requested=requested,
        holder_binding_required=config.holder_binding_required,
        now=now,
    )
    if config.status_method != "NONE":
        status_decision("UNKNOWN", config)  # mdoc status lists: adapter not enabled in this release
    missing = [c for c in requested if c in config.required_claims and c not in claims]
    if missing:
        raise CredentialError("CLAIMS_MISSING", ",".join(missing))
    return Verified(rows[0]["issuer_identifier"], claims, "VALID", config.holder_binding_required)


async def respond(
    engine: AsyncEngine,
    settings: VerifierSettings,
    *,
    tenant_id: str,
    form: dict[str, str],
    base_ctx: RequestContext,
    now: datetime,
) -> str:
    ctx = base_ctx.with_identity(tenant_id, None)
    async with tenant_transaction(engine, ctx) as conn:
        p = await store.by_state(conn, form.get("state", ""))
        if p is None or p["status"] != "REQUESTED":
            raise CredentialError("PRESENTATION_STATE_INVALID")
        if now >= p["expires_at"]:
            await store.finish_presentation(
                conn,
                p["presentation_id"],
                status="EXPIRED",
                reasons=["EXPIRED"],
                issuer=None,
                credential_status=None,
                commitments=None,
                disclosed=None,
                now=now,
            )
            return "EXPIRED"
        config = await store.load_configuration(conn, str(p["configuration_id"]))
    requested = list(p["requested_claims"])
    try:
        if "error" in form:
            raise CredentialError("WALLET_ERROR", form["error"][:64])
        vp = json.loads(form.get("vp_token", "null"))
        items = vp.get("cred1") if isinstance(vp, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], str):
            raise CredentialError("VP_TOKEN_INVALID", "exactly one presentation for cred1")
        async with tenant_transaction(engine, ctx) as conn:
            if config.format == "SD_JWT_VC":
                verified = await _sd_jwt(
                    conn, settings, config, items[0], nonce=p["nonce"], requested=requested, now=now
                )
            else:
                verified = await _mdoc(
                    conn,
                    settings,
                    config,
                    items[0],
                    tenant_id=tenant_id,
                    nonce=p["nonce"],
                    requested=requested,
                    now=now,
                )
    except (CredentialError, ValueError) as e:
        code = e.code if isinstance(e, CredentialError) else "VP_TOKEN_INVALID"
        async with tenant_transaction(engine, ctx) as conn:
            await store.finish_presentation(
                conn,
                p["presentation_id"],
                status="REJECTED",
                reasons=[code],
                issuer=None,
                credential_status=None,
                commitments=None,
                disclosed=None,
                now=now,
            )
            await emit(
                conn,
                name="presentation.rejected",
                tenant_id=tenant_id,
                aggregate_type="presentation",
                aggregate_id=str(p["presentation_id"]),
                aggregate_version=2,
                data={"presentation_id": str(p["presentation_id"]), "reason": code},
            )
        raise CredentialError(code) from e
    async with tenant_transaction(engine, ctx) as conn:
        assertion_ids: list[str] = []
        reasons = ["PRESENTATION_VERIFIED"]
        if config.assurance_profile_id and p["subject_principal_id"]:
            outcome, assertion_ids = await record_presentation(
                conn,
                tenant_id=tenant_id,
                subject_principal_id=str(p["subject_principal_id"]),
                profile_id=config.assurance_profile_id,
                claims=verified.claims,
                holder_bound=verified.holder_bound,
                reference=str(p["presentation_id"]),
                jurisdiction=str(p["jurisdiction"]),
                now=now,
            )
            reasons.append(f"ASSURANCE:{outcome}")
        commitments = {
            c: store.sha256(f"{p['presentation_id']}|{c}|{v}").hex()
            for c, v in verified.claims.items()
        }
        await store.finish_presentation(
            conn,
            p["presentation_id"],
            status="VERIFIED",
            reasons=reasons,
            issuer=verified.issuer,
            credential_status=verified.credential_status,
            commitments=commitments,
            disclosed=verified.claims,
            now=now,
        )
        await emit(
            conn,
            name="presentation.verified",
            tenant_id=tenant_id,
            aggregate_type="presentation",
            aggregate_id=str(p["presentation_id"]),
            aggregate_version=2,
            data={
                "presentation_id": str(p["presentation_id"]),
                "issuer": verified.issuer,
                "claims": sorted(verified.claims),
                "assertion_ids": assertion_ids,
            },
        )
    return "VERIFIED"


__all__ = ["VerifierSettings", "create", "request_object", "respond"]
