"""OpenID Federation 1.1 trust-chain resolution (ZID-AUTH-001 §12, §17, §20; AUTH-W8).

    resolve(anchor, entity_id) -> validated chain + policy-applied metadata + chain expiry

Each step: fetch the entity configuration (EC) -> for each authority hint, fetch the superior's EC
and its subordinate statement about us from its federation_fetch_endpoint -> the statement is
signed by the superior's keys and the EC below is signed by the keys the statement vouches for
(never by the keys an entity claims for itself) -> the top EC is verified with the anchor's
*pinned* keys. Fetching is SSRF-safe: https only, the anchor's trust domains only, no redirects,
size and time bounded, and private network addresses refused.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from zid_crypto import CryptoError, KeyRing, peek_header, verify_external_jws

from zid_platform.modules.federation.domain.openid_federation import (
    ENTITY_TYP,
    MAX_CHAIN,
    Statement,
    TrustChainError,
    apply_policy,
    check_links,
    merge_policies,
    parse,
)

MAX_BYTES = 64 * 1024
ALGS = ("ES256", "Ed25519", "RS256", "PS256")


@dataclass(frozen=True, slots=True)
class Anchor:
    anchor_id: str
    entity_id: str
    jwks: Mapping[str, Any]
    allowed_domains: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class Resolved:
    entity_id: str
    anchor: str
    chain: tuple[str, ...]  # entity ids leaf -> anchor
    metadata: dict[str, Any]
    expires_at: datetime


def _allowed(url: str, domains: Sequence[str]) -> str:
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or p.username or p.password:
        raise TrustChainError("FEDERATION_FETCH_REFUSED", "https only")
    if not any(host == d.lower() or host.endswith("." + d.lower()) for d in domains):
        raise TrustChainError("FEDERATION_FETCH_REFUSED", f"{host} outside the trust domains")
    return host


def _public(host: str) -> None:
    """Refuse loopback, private, link-local and other non-global targets (SSRF)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise TrustChainError("FEDERATION_FETCH_FAILED", "dns") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise TrustChainError("FEDERATION_FETCH_REFUSED", "non-public address")


async def _fetch(client: httpx.AsyncClient, url: str, anchor: Anchor, *, check_dns: bool) -> str:
    host = _allowed(url, anchor.allowed_domains)
    if check_dns:
        _public(host)
    try:
        r = await client.get(url, headers={"Accept": "application/entity-statement+jwt"})
    except httpx.HTTPError as e:
        raise TrustChainError("FEDERATION_FETCH_FAILED", host) from e
    if r.status_code != 200 or len(r.content) > MAX_BYTES:
        raise TrustChainError("FEDERATION_FETCH_FAILED", f"{host} {r.status_code}")
    return r.text.strip()


def _verify(token: str, keys: Mapping[str, Any] | None, *, now: datetime) -> Statement:
    try:
        _, claims = verify_external_jws(
            token, keys=(keys or {}).get("keys", []), algorithms=ALGS, typ=ENTITY_TYP
        )
    except CryptoError as e:
        raise TrustChainError("ENTITY_STATEMENT_SIGNATURE_INVALID", e.code) from e
    return parse(claims, now=now)


def _self_claims(token: str) -> Mapping[str, Any]:
    """The keys an EC claims for itself, used only to check self-consistency, never as trust."""
    try:
        peek_header(token)
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))  # type: ignore[no-any-return]
    except (ValueError, IndexError, CryptoError) as e:
        raise TrustChainError("ENTITY_STATEMENT_INVALID") from e


async def resolve(
    anchor: Anchor,
    entity_id: str,
    *,
    now: datetime,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Resolved:
    if anchor.status != "ACTIVE":
        raise TrustChainError("TRUST_ANCHOR_QUARANTINED")
    check_dns = transport is None
    async with httpx.AsyncClient(
        transport=transport, timeout=5.0, follow_redirects=False
    ) as client:

        async def ec(entity: str) -> tuple[str, Statement]:
            token = await _fetch(
                client,
                entity.rstrip("/") + "/.well-known/openid-federation",
                anchor,
                check_dns=check_dns,
            )
            own = _self_claims(token).get("jwks")
            statement = _verify(token, own if isinstance(own, dict) else None, now=now)
            if not statement.self_signed or statement.sub != entity:
                raise TrustChainError("ENTITY_CONFIGURATION_INVALID", entity)
            return token, statement

        async def walk(
            entity: str, token: str, statement: Statement, depth: int
        ) -> list[Statement]:
            if depth > MAX_CHAIN:
                raise TrustChainError("TRUST_CHAIN_LENGTH")
            for hint in statement.authority_hints:
                try:
                    _allowed(hint, anchor.allowed_domains)
                    sup_token, sup = await ec(hint)
                    if hint == anchor.entity_id:  # the anchor is trusted only through pinned keys
                        sup = _verify(sup_token, anchor.jwks, now=now)
                    fetch = (sup.metadata.get("federation_entity") or {}).get(
                        "federation_fetch_endpoint"
                    )
                    if not isinstance(fetch, str):
                        raise TrustChainError("FEDERATION_FETCH_ENDPOINT_MISSING", hint)
                    sub_token = await _fetch(
                        client,
                        fetch + ("&" if "?" in fetch else "?") + urlencode({"sub": entity}),
                        anchor,
                        check_dns=check_dns,
                    )
                    subordinate = _verify(sub_token, sup.jwks, now=now)
                    if subordinate.iss != hint or subordinate.sub != entity:
                        raise TrustChainError("TRUST_CHAIN_BROKEN", entity)
                    _verify(token, subordinate.jwks, now=now)  # EC below, by the vouched-for keys
                    if hint == anchor.entity_id:
                        return [subordinate, sup]
                    return [subordinate, *await walk(hint, sup_token, sup, depth + 1)]
                except TrustChainError:
                    continue  # try the next authority hint
            raise TrustChainError("TRUST_CHAIN_UNRESOLVED", entity)

        leaf_token, leaf = await ec(entity_id)
        upward = await walk(entity_id, leaf_token, leaf, 1)
    chain = [leaf, *upward]
    expires = check_links(chain, anchor=anchor.entity_id)
    subordinates = chain[1:-1]
    policy = merge_policies([s.metadata_policy for s in reversed(subordinates)])
    metadata = dict(leaf.metadata)
    for entity_type, values in (subordinates[0].metadata if subordinates else {}).items():
        metadata[entity_type] = {**(metadata.get(entity_type) or {}), **values}
    return Resolved(
        entity_id=entity_id,
        anchor=anchor.entity_id,
        chain=(leaf.sub, *(s.iss for s in subordinates)),
        metadata=apply_policy(metadata, policy),
        expires_at=expires,
    )


def entity_configuration(
    keyring: KeyRing,
    *,
    issuer: str,
    authority_hints: Sequence[str],
    metadata: Mapping[str, Any],
    now: datetime,
    lifetime_hours: int = 24,
) -> str:
    """Zoiko's own self-signed entity configuration (§18 /.well-known/openid-federation)."""
    iat = int(now.timestamp())
    return keyring.sign(
        {
            "iss": issuer,
            "sub": issuer,
            "iat": iat,
            "exp": iat + lifetime_hours * 3600,
            "jwks": keyring.jwks(),
            "authority_hints": list(authority_hints),
            "metadata": dict(metadata),
        },
        typ=ENTITY_TYP,
    )
