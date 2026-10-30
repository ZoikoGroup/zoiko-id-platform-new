"""HTTP clients for Veriff and Sumsub (ZID-EDR-005): create a session server-side and hand the applicant a short-lived link.

Only an opaque reference leaves Zoiko iD (the session id as the vendor's own reference): no name, number, email or address is
sent, because the provider collects the evidence from the applicant. Every call passes the platform's outbound guard (https, a
DNS name, a standard port, public addresses only), follows no redirects, bounds the response and returns None on any failure so
the circuit breaker and the equivalence-group failover act, and a failed call is never read as a pass.

Secrets come from the secrets broker by reference at call time; none is kept on the client.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from zid_platform.egress import EgressError, guard
from zid_platform.modules.verification.domain.providers import (
    sumsub_sign,
    veriff_decision_signature,
    veriff_sign,
)

MAX_BYTES = 200_000
TIMEOUT_S = 5.0
Secrets = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class Handoff:
    """What the applicant needs to start: never stored, never logged."""

    provider_ref: str
    url: str | None = None
    token: str | None = None


async def _call(
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
) -> dict[str, Any] | None:
    try:
        await guard(base_url, resolve=transport is None)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=TIMEOUT_S,
            follow_redirects=False,
            verify=True,
            base_url=base_url,
        ) as c:
            r = await c.request(method, path, headers=headers, content=body)
    except (httpx.HTTPError, EgressError):
        return None
    if r.status_code not in (200, 201) or len(r.content) > MAX_BYTES:
        return None
    try:
        doc = json.loads(r.content)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


@dataclass
class VeriffClient:
    """POST /v1/sessions (CONFIRMED): `X-AUTH-CLIENT` is the API key, the body is signed with the shared secret.

    The base address (`https://stationapi.veriff.com` in Veriff's guides) is configuration and is checked on first use."""

    base_url: str
    secrets: Secrets
    api_key_ref: str
    shared_secret_ref: str
    transport: httpx.AsyncBaseTransport | None = None

    def _keys(self) -> tuple[str, str] | None:
        key, secret = self.secrets(self.api_key_ref), self.secrets(self.shared_secret_ref)
        return (key, secret) if key and secret else None

    async def create_session(self, session_ref: str, callback_url: str | None) -> Handoff | None:
        keys = self._keys()
        if keys is None:
            return None
        verification: dict[str, Any] = {"vendorData": session_ref}
        if callback_url:
            verification["callback"] = callback_url
        body = json.dumps({"verification": verification}, separators=(",", ":")).encode()
        doc = await _call(
            self.transport,
            self.base_url,
            "POST",
            "/v1/sessions",
            headers={
                "content-type": "application/json",
                "x-auth-client": keys[0],
                "x-hmac-signature": veriff_sign(body, keys[1]),
            },
            body=body,
        )
        v = (doc or {}).get("verification")
        if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not v["id"]:
            return None
        url = (
            v.get("url")
            if isinstance(v.get("url"), str) and v["url"].startswith("https://")
            else None
        )
        return Handoff(
            v["id"],
            url=url,
            token=v.get("sessionToken") if isinstance(v.get("sessionToken"), str) else None,
        )

    async def decision(self, session_id: str) -> dict[str, Any] | None:
        """GET /v1/sessions/{id}/decision: the polling channel that reconciles a delayed webhook."""
        keys = self._keys()
        if keys is None:
            return None
        return await _call(
            self.transport,
            self.base_url,
            "GET",
            f"/v1/sessions/{session_id}/decision",
            headers={
                "x-auth-client": keys[0],
                "x-hmac-signature": veriff_decision_signature(session_id, keys[1]),
            },
        )


@dataclass
class SumsubClient:
    """Applicant creation and an SDK access token, signed per request (CONFIRMED). The base address is configuration."""

    base_url: str
    secrets: Secrets
    app_token_ref: str
    secret_key_ref: str
    level_name: str
    transport: httpx.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.time

    def _keys(self) -> tuple[str, str] | None:
        token, secret = self.secrets(self.app_token_ref), self.secrets(self.secret_key_ref)
        return (token, secret) if token and secret else None

    def _headers(
        self, keys: tuple[str, str], method: str, path: str, body: bytes
    ) -> dict[str, str]:
        ts = int(self.clock())
        return {
            "x-app-token": keys[0],
            "x-app-access-ts": str(ts),
            "x-app-access-sig": sumsub_sign(keys[1], ts, method, path, body),
            "content-type": "application/json",
            "accept": "application/json",
        }

    async def _signed(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        keys = self._keys()
        if keys is None:
            return None
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        return await _call(
            self.transport,
            self.base_url,
            method,
            path,
            headers=self._headers(keys, method, path, body),
            body=body or None,
        )

    async def create_session(self, session_ref: str, callback_url: str | None) -> Handoff | None:
        del callback_url  # Sumsub's webhook address is set in its dashboard, not per applicant
        applicant = await self._signed(
            "POST",
            f"/resources/applicants?levelName={self.level_name}",
            {"externalUserId": session_ref},
        )
        applicant_id = (applicant or {}).get("id")
        if not isinstance(applicant_id, str) or not applicant_id:
            return None
        token = await self._signed(
            "POST",
            "/resources/accessTokens/sdk",
            {"ttlInSecs": 600, "userId": session_ref, "levelName": self.level_name},
        )
        value = (token or {}).get("token")
        return Handoff(applicant_id, token=value if isinstance(value, str) else None)

    async def applicant_info(self, applicant_id: str) -> dict[str, Any] | None:
        """The applicant's data, for the claims (the response shape is NOT confirmed: the name and birth date are read from `info`)."""
        doc = await self._signed("GET", f"/resources/applicants/{applicant_id}/one")
        info = (doc or {}).get("info")
        return info if isinstance(info, dict) else None
