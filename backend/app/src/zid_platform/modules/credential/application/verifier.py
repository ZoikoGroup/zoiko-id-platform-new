"""OID4VP 1.0 verifier / relying party (ZID-VC-001 §7-11, §13-15; HAIP 1.0; DEC-E2 baseline role).

    create()    purpose-bound request: registered RP + purpose, minimized DCQL claim set, nonce,
                state; a signed request object (oauth-authz-req+jwt) served once by request_uri
    respond()   direct_post or HAIP direct_post.jwt (ECDH-ES JWE to a per-presentation key)
                response: state -> single presentation -> format adapter ->
                issuer trust (typed) -> holder binding to this transaction -> status -> claims;
                then canonical assertions (verification module) where the configuration maps a
                verification profile. A presentation never creates authority.

Separate reason codes for proof, trust, binding, status and claims; nothing undisclosed and no
raw presentation is kept. UNKNOWN status is never VALID (fails closed when mandatory).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from joserfc import jwe
from joserfc.errors import JoseError
from joserfc.jwk import ECKey
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_app.resilience import budget_timeout
from zid_context import RequestContext
from zid_crypto import (
    CryptoError,
    KeyRing,
    b64url,
    peek_header,
    sign_jws,
    verify_external_jws,
)
from zid_db import tenant_transaction
from zid_outbox import emit

from zid_platform import egress
from zid_platform.modules.commercial.ports import key_vc_presented, record_billable
from zid_platform.modules.credential.domain import vcdm
from zid_platform.modules.credential.domain.credentials import (
    KB_TYP,
    MDOC_ADAPTER,
    SD_JWT_ADAPTER,
    SD_JWT_TYPS,
    STATUS_LIST_TYP,
    Configuration,
    CredentialError,
    b64d,
    build_basis,
    disclose,
    minimize,
    require_role,
    sd_hash,
    split_presentation,
    status_at,
    status_at_raw,
    status_decision,
    trust_decision,
    trust_entry,
)
from zid_platform.modules.credential.domain.reliance import RELIANCE
from zid_platform.modules.credential.infrastructure import credential_store as store
from zid_platform.modules.credential.infrastructure.mdoc import (
    STATUS_LIST_CWT_TYP,
    session_transcript,
    status_reference,
    verify_device_response,
    verify_status_list_cwt,
)
from zid_platform.modules.federation.ports import TrustChainError, resolve, trust_anchor
from zid_platform.modules.privacy.ports import enforce_processing
from zid_platform.modules.verification.ports import record_presentation

REQUEST_TTL = timedelta(minutes=10)
KB_MAX_AGE = timedelta(minutes=5)
SKEW = timedelta(seconds=60)
REQUEST_TYP = "oauth-authz-req+jwt"


PINNED_ADAPTERS = {
    "SD_JWT_VC": SD_JWT_ADAPTER,
    "MDOC": MDOC_ADAPTER,
    "VC_JWT": vcdm.VCDM_ADAPTER,
}


def require_pinned_adapter(config: Configuration) -> None:
    """VC §29: a configuration written for another draft is rejected, never parsed with whatever
    this build understands (the SD-JWT VC, status list and mdoc drafts are pinned)."""
    if PINNED_ADAPTERS.get(config.format) != config.adapter_version:
        raise CredentialError("PROFILE_VERSION_MISMATCH", config.adapter_version)


RESPONSE_ENC_VALUES = ("A128GCM", "A256GCM")
RESPONSE_MODES = ("direct_post", "direct_post.jwt")


@dataclass(frozen=True, slots=True)
class VerifierSettings:
    issuer: str
    keyring: KeyRing
    transport: httpx.AsyncBaseTransport | None = None
    x5c: tuple[str, ...] = ()  # HAIP verifier certificate chain (base64 DER), leaf first

    @property
    def client_id(self) -> str:
        """OID4VP 1.0 client identifier prefix: x509_hash (HAIP) once a verifier certificate is
        configured - the hash of the leaf that signs our request objects - else OpenID Federation."""
        if self.x5c:
            leaf = base64.b64decode(self.x5c[0])
            return "x509_hash:" + b64url(hashlib.sha256(leaf).digest())
        return f"openid_federation:{self.issuer}"

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
    if config.format == "VC_JWT":
        return {
            "credentials": [
                {
                    "id": "cred1",
                    "format": "jwt_vc_json",
                    "meta": {"type_values": [["VerifiableCredential", config.credential_type]]},
                    "claims": [{"path": ["credentialSubject", c]} for c in claims],
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
    response_mode: str = "direct_post",
) -> dict[str, Any]:
    require_role("VERIFIER")
    if response_mode not in RESPONSE_MODES:
        raise CredentialError("RESPONSE_MODE_UNSUPPORTED")
    config = await store.load_configuration(conn, configuration_id)
    if config.status != "ACTIVE":
        raise CredentialError("CONFIGURATION_NOT_ACTIVE")
    require_pinned_adapter(config)
    allowed = await store.registration(conn, client_id, purpose, now)
    if allowed is None:
        raise CredentialError("RP_NOT_REGISTERED_FOR_PURPOSE")
    # Disclosure to a relying party: a registered purpose and recipient class (PRIV-01, §23)
    await enforce_processing(
        conn,
        tenant_id,
        activity_code="CREDENTIAL_PRESENTATION",
        purpose=purpose,
        data_category="D3",
        subject_principal_id=subject_principal_id,
        recipient_class="relying_party",
    )
    requested = minimize(claims, config, allowed)
    pid, nonce, state = str(uuid.uuid4()), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    iat = int(now.timestamp())
    client_metadata: dict[str, Any] = {
        "vp_formats_supported": {
            "dc+sd-jwt": {"sd-jwt_alg_values": ["ES256"], "kb-jwt_alg_values": ["ES256"]},
            "mso_mdoc": {"issuerauth_alg_values": [-7], "deviceauth_alg_values": [-7]},
            "jwt_vc_json": {"alg_values": ["ES256"]},
        }
    }
    response_key = None
    if response_mode == "direct_post.jwt":  # HAIP: an ephemeral key for this response only
        key = ECKey.generate_key("P-256", private=True)
        response_key = {**key.as_dict(private=True), "kid": pid}
        public = {**key.as_dict(private=False), "kid": pid, "use": "enc", "alg": "ECDH-ES"}
        client_metadata["jwks"] = {"keys": [public]}
        client_metadata["encrypted_response_enc_values_supported"] = list(RESPONSE_ENC_VALUES)
    request_jwt = sign_jws(
        settings.keyring.active,
        {
            "iss": settings.client_id,
            "aud": "https://self-issued.me/v2",
            "client_id": settings.client_id,
            "response_type": "vp_token",
            "response_mode": response_mode,
            "response_uri": settings.response_uri(tenant_id),
            "nonce": nonce,
            "state": state,
            "dcql_query": _dcql(config, requested),
            "client_metadata": client_metadata,
            "iat": iat,
            "exp": iat + int(REQUEST_TTL.total_seconds()),
        },
        typ=REQUEST_TYP,
        extra_header={"x5c": list(settings.x5c)} if settings.x5c else None,
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
            "response_mode": response_mode,
            "response_key": response_key,
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
    basis: dict[str, Any]  # what the decision rested on (VC §28); no credential or claim values


def live_keys(jwks: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Bounded rollover overlap (VC §25): a pinned key may carry `not_after` (epoch seconds); once
    it has passed, the key no longer verifies anything, whether or not an administrator removed it."""
    keep = []
    for key in jwks.get("keys", []):
        cutoff = key.get("not_after")
        if cutoff is not None and (
            not isinstance(cutoff, int | float) or now.timestamp() >= cutoff
        ):
            continue
        keep.append({k: v for k, v in key.items() if k != "not_after"})
    return {"keys": keep}


async def _issuer_keys(
    conn: AsyncConnection, settings: VerifierSettings, row: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if row["trust_source"] == "PINNED":
        return live_keys(dict(row["jwks"] or {"keys": []}), now)
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
        await egress.guard(uri, resolve=settings.transport is None)  # SEC-19
    except egress.EgressError:
        return "UNKNOWN"
    cached = RELIANCE.get(uri, now)
    if cached is not None:
        claims = cached
    else:
        try:
            async with httpx.AsyncClient(
                transport=settings.transport, timeout=budget_timeout(5.0), follow_redirects=False
            ) as c:
                r = await c.get(uri, headers={"Accept": "application/statuslist+jwt"})
            if r.status_code != 200 or len(r.content) > 256 * 1024:
                return "UNKNOWN"
            _, claims = verify_external_jws(
                r.text.strip(),
                keys=keys.get("keys", []),
                algorithms=algorithms,
                typ=STATUS_LIST_TYP,
            )
        except (httpx.HTTPError, CryptoError):
            return "UNKNOWN"
        exp = claims.get("exp")
        if claims.get("sub") != uri or (isinstance(exp, int | float) and now.timestamp() >= exp):
            return "UNKNOWN"
        RELIANCE.put(uri, claims, now, claims.get("ttl"))
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
    jurisdiction: str,
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
        jurisdiction=jurisdiction,
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
    basis = build_basis(
        config,
        [trust_entry(row)],
        credential_status=status,
        holder_bound=bound,
        jurisdiction=jurisdiction,
        decided_at=now,
    )
    return Verified(issuer, {c: claims[c] for c in requested if c in claims}, status, bound, basis)


def _jwt_payload(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        header = peek_header(token)
        payload = json.loads(b64d(token.split(".")[1]))
    except (CryptoError, ValueError, IndexError) as e:
        raise CredentialError("VC_MALFORMED") from e
    if not isinstance(payload, dict):
        raise CredentialError("VC_MALFORMED")
    return header, payload


async def _vc_jwt(
    conn: AsyncConnection,
    settings: VerifierSettings,
    config: Configuration,
    presentation: str,
    *,
    nonce: str,
    requested: list[str],
    jurisdiction: str,
    now: datetime,
) -> Verified:
    """W3C VCDM 2.0: holder-signed vp+jwt enveloping one issuer-signed vc+jwt."""
    vp_header, vp_unverified = _jwt_payload(presentation)
    if vp_header.get("typ") != vcdm.VP_TYP:
        raise CredentialError("VP_MALFORMED", "typ")
    vc_jwt = vcdm.enveloped_credential(vp_unverified)
    vc_header, vc_unverified = _jwt_payload(vc_jwt)
    if vc_header.get("typ") != vcdm.VC_TYP:
        raise CredentialError("VC_MALFORMED", "typ")
    issuer = vcdm.issuer_of(vc_unverified)
    found = await store.load_trust(conn, issuer)
    trust_decision(
        found[0] if found else None,
        credential_type=config.credential_type,
        jurisdiction=jurisdiction,
        now=now,
    )
    assert found is not None
    row = found[1]
    keys = await _issuer_keys(conn, settings, row, now)
    try:
        _, vc = verify_external_jws(
            vc_jwt, keys=keys.get("keys", []), algorithms=row["algorithms"], typ=vcdm.VC_TYP
        )
    except CryptoError as e:
        raise CredentialError("CREDENTIAL_PROOF_INVALID", e.code) from e
    if vcdm.issuer_of(vc) != issuer:
        raise CredentialError("CREDENTIAL_PROOF_INVALID", "issuer")
    claims = vcdm.check_credential(vc, credential_type=config.credential_type, now=now)
    bound = False
    if config.holder_binding_required:
        jwk = (vc.get("cnf") or {}).get("jwk")
        if not isinstance(jwk, dict):
            raise CredentialError("HOLDER_BINDING_MISSING")
        try:
            _, vp = verify_external_jws(
                presentation, keys=[jwk], algorithms=["ES256", "Ed25519"], typ=vcdm.VP_TYP
            )
        except CryptoError as e:
            raise CredentialError("HOLDER_BINDING_INVALID", e.code) from e
        iat = vp.get("iat")
        if vp.get("aud") != settings.client_id or vp.get("nonce") != nonce:
            raise CredentialError("HOLDER_BINDING_INVALID", "audience/nonce")
        if (
            not isinstance(iat, int | float)
            or abs(now.timestamp() - iat) > KB_MAX_AGE.total_seconds()
        ):
            raise CredentialError("HOLDER_BINDING_INVALID", "iat")
        if vcdm.enveloped_credential(vp) != vc_jwt:
            raise CredentialError("HOLDER_BINDING_INVALID", "credential")
        bound = True
    status = (
        await _bitstring_status(settings, vc, issuer, keys, list(row["algorithms"]), now)
        if config.status_method == "BITSTRING_STATUS_LIST"
        else "VALID"
    )
    status_decision(status, config)
    missing = [c for c in requested if c in config.required_claims and c not in claims]
    if missing:
        raise CredentialError("CLAIMS_MISSING", ",".join(missing))
    basis = build_basis(
        config,
        [trust_entry(row)],
        credential_status=status,
        holder_bound=bound,
        jurisdiction=jurisdiction,
        decided_at=now,
    )
    return Verified(issuer, {c: claims[c] for c in requested if c in claims}, status, bound, basis)


async def _bitstring_status(
    settings: VerifierSettings,
    vc: dict[str, Any],
    issuer: str,
    keys: dict[str, Any],
    algorithms: list[str],
    now: datetime,
) -> str:
    """Every BitstringStatusListEntry is checked; any set bit wins, any failure is UNKNOWN."""
    try:
        entries = vcdm.status_entries(vc)
    except CredentialError:
        return "UNKNOWN"
    if not entries:
        return "UNKNOWN"
    issuer_host = urlsplit(issuer).hostname
    result = "VALID"
    async with httpx.AsyncClient(
        transport=settings.transport, timeout=budget_timeout(5.0), follow_redirects=False
    ) as c:
        for e in entries:
            if not e.list_credential.startswith("https://") or (
                urlsplit(e.list_credential).hostname != issuer_host
            ):
                return "UNKNOWN"  # status only from the issuer's own domain
            try:
                await egress.guard(e.list_credential, resolve=settings.transport is None)
            except egress.EgressError:
                return "UNKNOWN"
            try:
                r = await c.get(e.list_credential, headers={"Accept": "application/vc+jwt"})
                if r.status_code != 200 or len(r.content) > 256 * 1024:
                    return "UNKNOWN"
                _, doc = verify_external_jws(
                    r.text.strip(),
                    keys=keys.get("keys", []),
                    algorithms=algorithms,
                    typ=vcdm.VC_TYP,
                )
                bits = vcdm.status_list_bits(
                    doc, issuer=issuer, url=e.list_credential, purpose=e.purpose, now=now
                )
                if vcdm.bit_at(bits, e.index):
                    if vcdm.STATUS_PURPOSES[e.purpose] == "REVOKED":
                        return "REVOKED"
                    result = "SUSPENDED"
            except (httpx.HTTPError, CryptoError, CredentialError):
                return "UNKNOWN"
    return result


async def _mdoc(
    conn: AsyncConnection,
    settings: VerifierSettings,
    config: Configuration,
    presentation: str,
    *,
    tenant_id: str,
    nonce: str,
    requested: list[str],
    jurisdiction: str,
    now: datetime,
) -> Verified:
    rows = [
        r
        for r in await store.trust_for_type(conn, config.credential_type)
        if not r["jurisdictions"] or jurisdiction in r["jurisdictions"]  # typed trust (§4.1)
    ]
    roots = [pem for r in rows if r["valid_to"] > now for pem in (r["iaca_certificates"] or [])]
    if not roots:
        raise CredentialError("ISSUER_UNTRUSTED", "no IACA trusted for this document type")
    transcript = session_transcript(
        client_id=settings.client_id, nonce=nonce, response_uri=settings.response_uri(tenant_id)
    )
    claims, mso = verify_device_response(
        presentation,
        doc_type=config.credential_type,
        iaca_roots=roots,
        transcript=transcript,
        requested=requested,
        holder_binding_required=config.holder_binding_required,
        now=now,
    )
    status = (
        await _mdoc_status(settings, mso, roots, now)
        if config.status_method == "TOKEN_STATUS_LIST"
        else "VALID"
    )
    status_decision(status, config)
    missing = [c for c in requested if c in config.required_claims and c not in claims]
    if missing:
        raise CredentialError("CLAIMS_MISSING", ",".join(missing))
    basis = build_basis(
        config,
        [trust_entry(r) for r in rows],
        credential_status=status,
        holder_bound=config.holder_binding_required,
        jurisdiction=jurisdiction,
        decided_at=now,
    )
    return Verified(
        rows[0]["issuer_identifier"], claims, status, config.holder_binding_required, basis
    )


async def _mdoc_status(
    settings: VerifierSettings, mso: Mapping[str, Any], roots: list[str], now: datetime
) -> str:
    """Token Status List in CWT form; the list's signer must chain to the same pinned IACA."""
    ref = status_reference(mso)
    if ref is None:
        return "UNKNOWN"
    idx, uri = ref
    try:
        await egress.guard(uri, resolve=settings.transport is None)  # SEC-19: wallet-supplied URL
    except egress.EgressError:
        return "UNKNOWN"
    try:
        async with httpx.AsyncClient(
            transport=settings.transport, timeout=budget_timeout(5.0), follow_redirects=False
        ) as c:
            r = await c.get(uri, headers={"Accept": STATUS_LIST_CWT_TYP})
        if r.status_code != 200 or len(r.content) > 256 * 1024:
            return "UNKNOWN"
        bits, lst = verify_status_list_cwt(r.content, uri=uri, iaca_roots=roots, now=now)
        return status_at_raw(lst, bits, idx)
    except (httpx.HTTPError, CredentialError):
        return "UNKNOWN"


async def _decrypt_response(conn: AsyncConnection, jwe_value: str) -> dict[str, str]:
    """HAIP direct_post.jwt: ECDH-ES + A128GCM/A256GCM to this presentation's ephemeral key.

    The JWE kid names the presentation; its payload must carry that presentation's own state,
    so a response encrypted for one transaction can never answer another."""
    try:
        header = json.loads(b64d(jwe_value.split(".")[0]))
    except (ValueError, IndexError) as e:
        raise CredentialError("RESPONSE_DECRYPTION_FAILED") from e
    if header.get("alg") != "ECDH-ES" or header.get("enc") not in RESPONSE_ENC_VALUES:
        raise CredentialError("RESPONSE_DECRYPTION_FAILED", "algorithm")
    try:
        p = await store.load_presentation(conn, str(header.get("kid", "")))
    except CredentialError as e:
        raise CredentialError("RESPONSE_DECRYPTION_FAILED") from e
    if p["response_key"] is None or p["status"] != "REQUESTED":
        raise CredentialError("PRESENTATION_STATE_INVALID")
    try:
        key = ECKey.import_key(dict(p["response_key"]))
        plaintext = jwe.decrypt_compact(
            jwe_value, key, algorithms=["ECDH-ES", *RESPONSE_ENC_VALUES]
        ).plaintext
        payload = json.loads(plaintext or b"")
    except (JoseError, ValueError) as e:
        raise CredentialError("RESPONSE_DECRYPTION_FAILED") from e
    if not isinstance(payload, dict) or store.sha256(str(payload.get("state", ""))) != bytes(
        p["state_hash"]
    ):
        raise CredentialError("PRESENTATION_STATE_INVALID")
    return {
        k: v if isinstance(v, str) else json.dumps(v)
        for k, v in payload.items()
        if k in ("state", "vp_token", "error", "error_description")
    }


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
        encrypted = "response" in form
        if encrypted:
            form = await _decrypt_response(conn, form["response"])
        p = await store.by_state(conn, form.get("state", ""))
        if p is None or p["status"] != "REQUESTED":
            raise CredentialError("PRESENTATION_STATE_INVALID")
        if p["response_mode"] == "direct_post.jwt" and not encrypted:
            raise CredentialError("RESPONSE_ENCRYPTION_REQUIRED")
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
        require_pinned_adapter(config)
        if "error" in form:
            raise CredentialError("WALLET_ERROR", form["error"][:64])
        vp = json.loads(form.get("vp_token", "null"))
        items = vp.get("cred1") if isinstance(vp, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], str):
            raise CredentialError("VP_TOKEN_INVALID", "exactly one presentation for cred1")
        async with tenant_transaction(engine, ctx) as conn:
            if config.format == "SD_JWT_VC":
                verified = await _sd_jwt(
                    conn,
                    settings,
                    config,
                    items[0],
                    nonce=p["nonce"],
                    requested=requested,
                    jurisdiction=str(p["jurisdiction"]),
                    now=now,
                )
            elif config.format == "VC_JWT":
                verified = await _vc_jwt(
                    conn,
                    settings,
                    config,
                    items[0],
                    nonce=p["nonce"],
                    requested=requested,
                    jurisdiction=str(p["jurisdiction"]),
                    now=now,
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
                    jurisdiction=str(p["jurisdiction"]),
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
            await record_billable(
                conn,
                tenant_id=tenant_id,
                meter="VC_PRESENTED",
                key=key_vc_presented(str(p["presentation_id"])),
                at=now,
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
            basis=verified.basis,
        )
        await record_billable(
            conn,
            tenant_id=tenant_id,
            meter="VC_PRESENTED",
            key=key_vc_presented(str(p["presentation_id"])),
            at=now,
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
