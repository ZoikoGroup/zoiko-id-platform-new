"""The Zoiko iD Verify API client for a partner's SERVER. Standard library plus `cryptography`; `transport` is injectable for tests.

Rules the client keeps, all from the API contract:
- it holds a client id and EITHER a secret OR a private key (never both); it never logs or prints either, and it must run on your server, never in a web page or a mobile app;
- a private-key client signs a short, single-use assertion for every token request, so Zoiko iD holds nothing that could leak;
- the token is cached and renewed shortly before it ends; a 401 on a call renews it once and retries that call once, never in a loop;
- every POST that makes something takes a REQUIRED `idempotency_key`: a retry must reuse the same key, so the key is yours and is never generated behind your back;
- an error answer becomes a `VerifyError` with the API's own code (for example `INVALID_IDEMPOTENCY_REUSE`, `RATE_LIMITED`) and the HTTP status. A result is never guessed: only what the API says is returned.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

from zoiko_verify._jwt import load_private_key, sign_jws

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ASSERTION_LIFETIME = 120  # seconds; the API accepts at most 5 minutes
RENEW_BEFORE = 60  # renew a token this many seconds before it ends
PREFIX = "/v1/verify-api"


class VerifyError(Exception):
    """An error answer from the API. `code` is the API's public code, `status` the HTTP status."""

    def __init__(self, code: str, status: int, detail: str = "", retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.status, self.detail, self.retryable = code, status, detail, retryable


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], data: bytes | None, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _error(status: int, body: bytes) -> VerifyError:
    try:
        err = json.loads(body).get("error", {})
        return VerifyError(
            str(err.get("code", "ERROR")),
            status,
            str(err.get("message", "")),
            bool(err.get("retryable", status >= 500 or status == 429)),
        )
    except (ValueError, AttributeError):
        return VerifyError("ERROR", status, "The answer could not be read.", status >= 500)


class VerifyClient:
    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        client_secret: str | None = None,
        private_key_pem: str | bytes | None = None,
        key_id: str | None = None,
        scope: str | None = None,
        transport: Transport | None = None,
        timeout: float = 15.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (client_secret is None) == (private_key_pem is None):
            raise ValueError("give exactly one of client_secret or private_key_pem")
        self._base = base_url.rstrip("/")
        self._client_id = client_id
        self._secret = client_secret
        self._key = load_private_key(private_key_pem) if private_key_pem is not None else None
        self._key_id = key_id
        self._scope = scope
        self._transport = transport or _urllib_transport
        self._timeout = timeout
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def __repr__(self) -> str:  # never shows a secret or a token
        return f"VerifyClient(base_url={self._base!r}, client_id={self._client_id!r})"

    # ------------------------------------------------------------------ the token

    def _token_request(self) -> bytes:
        body: dict[str, str] = {"grant_type": "client_credentials"}
        if self._scope:
            body["scope"] = self._scope
        if self._key is not None:
            now = int(self._clock())
            body["client_assertion_type"] = ASSERTION_TYPE
            body["client_assertion"] = sign_jws(
                self._key,
                {
                    "iss": self._client_id,
                    "sub": self._client_id,
                    "aud": f"{self._base}{PREFIX}/oauth2/token",
                    "iat": now,
                    "exp": now + ASSERTION_LIFETIME,
                    "jti": uuid.uuid4().hex,  # a new one every time: each assertion works once
                },
                key_id=self._key_id,
            )
        else:
            body["client_id"] = self._client_id
            body["client_secret"] = self._secret or ""
        return json.dumps(body).encode()

    def _ensure_token(self, *, force: bool = False) -> str:
        if not force and self._token and self._clock() < self._expires_at - RENEW_BEFORE:
            return self._token
        status, raw = self._transport(
            "POST",
            f"{self._base}{PREFIX}/oauth2/token",
            {"Content-Type": "application/json", "Accept": "application/json"},
            self._token_request(),
            self._timeout,
        )
        if status != 200:
            raise _error(status, raw)
        answer = json.loads(raw)
        self._token = str(answer["access_token"])
        self._expires_at = self._clock() + int(answer.get("expires_in", 900))
        return self._token

    # ------------------------------------------------------------------ calls

    def _call(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}{PREFIX}{path}"
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            headers = {
                "Authorization": f"Bearer {self._ensure_token(force=attempt == 1)}",
                "Accept": "application/json",
            }
            if data is not None:
                headers["Content-Type"] = "application/json"
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            status, raw = self._transport(method, url, headers, data, self._timeout)
            if status == 401 and attempt == 0:
                self._token = None  # renew once and retry that call once
                continue
            if status >= 400:
                raise _error(status, raw)
            return json.loads(raw) if raw else {}
        raise VerifyError("AUTH_INVALID", 401, "The token was refused after it was renewed.")  # pragma: no cover

    def whoami(self) -> dict[str, Any]:
        """What the token is: application, environment, scopes. A quick check that your credentials work."""
        return self._call("GET", "/whoami")  # type: ignore[no-any-return]

    def workflows(self) -> dict[str, Any]:
        return self._call("GET", "/workflows")  # type: ignore[no-any-return]

    def products(self) -> dict[str, Any]:
        return self._call("GET", "/products")  # type: ignore[no-any-return]

    def create_session(
        self,
        *,
        idempotency_key: str,
        customer_reference: str,
        country: str,
        purpose_code: str,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        region: str | None = None,
        locale: str | None = None,
        return_origin: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Start a verification. `launch_url` is in this answer ONCE: send it to your customer. A created session is not an identity approval."""
        body: dict[str, Any] = {
            "customer_reference": customer_reference,
            "country": country,
            "purpose_code": purpose_code,
        }
        for k, v in (
            ("workflow_id", workflow_id),
            ("workflow_version", workflow_version),
            ("region", region),
            ("locale", locale),
            ("return_origin", return_origin),
            ("metadata", dict(metadata) if metadata else None),
        ):
            if v is not None:
                body[k] = v
        return self._call("POST", "/verification-sessions", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._call("GET", f"/verification-sessions/{session_id}")  # type: ignore[no-any-return]

    def get_decision(self, session_id: str) -> dict[str, Any]:
        """The minimized decision: outcome, assurance, checks, reason codes, version. Never photographs or document data."""
        return self._call("GET", f"/verification-sessions/{session_id}/decision")  # type: ignore[no-any-return]

    def list_sessions(
        self,
        *,
        status: str | None = None,
        customer_reference: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Newest first. Pass `next_cursor` from one page to get the next."""
        query = {
            "status": status,
            "customer_reference": customer_reference,
            "created_from": created_from,
            "created_to": created_to,
            "cursor": cursor,
            "limit": limit,
        }
        return self._call("GET", "/verification-sessions", query=query)  # type: ignore[no-any-return]

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        return self._call("POST", f"/verification-sessions/{session_id}:cancel", {})  # type: ignore[no-any-return]

    def resume_token(self, session_id: str) -> dict[str, Any]:
        """A fresh launch link for a customer who left. The old link stops working."""
        return self._call("POST", f"/verification-sessions/{session_id}/resume-token", {})  # type: ignore[no-any-return]

    def recheck(self, session_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """A new verification that follows a finished one. The original is left untouched."""
        return self._call("POST", f"/verification-sessions/{session_id}:recheck", {}, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ sandbox only

    def sandbox_session(
        self, outcome: str, *, idempotency_key: str, customer_reference: str | None = None
    ) -> dict[str, Any]:
        """Sandbox: a session already at a simulated outcome (approved, declined, review, ...). Refused on a production application."""
        body = {"customer_reference": customer_reference} if customer_reference else {}
        return self._call("POST", f"/sandbox/fixtures/{outcome}/sessions", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def simulate(self, session_id: str, outcome: str) -> dict[str, Any]:
        """Sandbox: move an open session to a simulated outcome."""
        return self._call("POST", f"/sandbox/sessions/{session_id}:simulate", {"outcome": outcome})  # type: ignore[no-any-return]
