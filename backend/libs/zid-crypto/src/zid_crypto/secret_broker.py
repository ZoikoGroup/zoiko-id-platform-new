"""Secrets broker (DEC-F5 §5; ZID-CRYPTO-MI-001 §15-16).

Secrets are referenced by logical name (`ref`) and held in memory only. Two implementations:

- `EnvSecretBroker`: local/test. `ref` -> environment variable ZID_SECRET__<REF> (upper-cased,
  '-' -> '_'). Refuses deployed environments.
- `OpenBaoSecretBroker`: deployed cells. KV v2 read from the cell's OpenBao. Authenticates with a
  token or by exchanging the workload's JWT-SVID (DEC-F7) at a JWT auth mount; the token is renewed
  by logging in again when its lease ends. Values are cached in memory for a bounded time.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from zid_crypto import CryptoError
from zid_crypto.providers import require_local_environment

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,199}(#[A-Za-z0-9_.\-]{1,64})?$")


class SecretError(CryptoError):
    """SECRET_UNAVAILABLE / SECRET_ACCESS_DENIED / SECRET_REF_INVALID. Never carries the value."""


class SecretBroker(Protocol):
    def get(self, ref: str) -> str: ...


class EnvSecretBroker:
    def __init__(
        self,
        *,
        environment: str = "local",
        prefix: str = "ZID_SECRET__",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        require_local_environment(environment, "EnvSecretBroker")
        self._prefix = prefix
        self._environ = environ if environ is not None else os.environ

    def get(self, ref: str) -> str:
        value = self._environ.get(self._prefix + ref.upper().replace("-", "_"))
        if not value:
            raise SecretError("SECRET_UNAVAILABLE", ref)
        return value


class OpenBaoSecretBroker:
    """KV v2: `ref` = "<path>" (field "value") or "<path>#<field>" under the configured mount."""

    def __init__(
        self,
        address: str,
        *,
        mount: str = "secret",
        token: Callable[[], str] | None = None,
        jwt: Callable[[], str] | None = None,
        jwt_role: str | None = None,
        jwt_mount: str = "jwt",
        namespace: str | None = None,
        cache_seconds: float = 300.0,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (token is None) == (jwt is None):
            raise ValueError("configure exactly one of token or jwt (workload JWT-SVID) auth")
        if jwt is not None and not jwt_role:
            raise ValueError("jwt auth needs the OpenBao role bound to this workload")
        self._base = address.rstrip("/")
        self._mount = mount.strip("/")
        self._token_source, self._jwt_source = token, jwt
        self._jwt_role, self._jwt_mount = jwt_role, jwt_mount.strip("/")
        self._namespace = namespace
        self._cache_seconds = cache_seconds
        self._client = client or httpx.Client(timeout=5.0, follow_redirects=False)
        self._clock = clock
        self._token: tuple[str, float] | None = None  # (token, expires at)
        self._cache: dict[str, tuple[str, float]] = {}

    def get(self, ref: str) -> str:
        if not _REF.match(ref) or ".." in ref:
            raise SecretError("SECRET_REF_INVALID")
        now = self._clock()
        cached = self._cache.get(ref)
        if cached and cached[1] > now:
            return cached[0]
        path, _, field = ref.partition("#")
        data = self._read(path)
        value = data.get(field or "value")
        if not isinstance(value, str) or not value:
            raise SecretError("SECRET_UNAVAILABLE", ref)
        self._cache[ref] = (value, now + self._cache_seconds)
        return value

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {"X-Vault-Request": "true"}
        if token:
            headers["X-Vault-Token"] = token
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        return headers

    def _read(self, path: str) -> dict[str, Any]:
        url = f"{self._base}/v1/{self._mount}/data/{path}"
        response = self._request("GET", url, token=self._current_token())
        if response.status_code == 403 and self._jwt_source is not None:
            self._token = None  # lease ended early: log in again once
            response = self._request("GET", url, token=self._current_token())
        if response.status_code == 403:
            raise SecretError("SECRET_ACCESS_DENIED", path)
        if response.status_code == 404:
            raise SecretError("SECRET_UNAVAILABLE", path)
        if response.status_code != 200:
            raise SecretError("SECRET_UNAVAILABLE", f"{path}: HTTP {response.status_code}")
        data = (response.json().get("data") or {}).get("data")
        if not isinstance(data, dict):
            raise SecretError("SECRET_UNAVAILABLE", path)
        return data

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = kwargs.pop("token", None)
        try:
            return self._client.request(method, url, headers=self._headers(token), **kwargs)
        except httpx.HTTPError as e:
            raise SecretError("SECRET_UNAVAILABLE", "secrets broker unreachable") from e

    def _current_token(self) -> str:
        if self._token_source is not None:
            return self._token_source()
        now = self._clock()
        if self._token and self._token[1] > now:
            return self._token[0]
        assert self._jwt_source is not None
        response = self._request(
            "POST",
            f"{self._base}/v1/auth/{self._jwt_mount}/login",
            json={"role": self._jwt_role, "jwt": self._jwt_source()},
        )
        if response.status_code != 200:
            raise SecretError("SECRET_ACCESS_DENIED", f"login HTTP {response.status_code}")
        auth = response.json().get("auth") or {}
        token = auth.get("client_token")
        if not isinstance(token, str) or not token:
            raise SecretError("SECRET_ACCESS_DENIED", "login returned no token")
        lease = float(auth.get("lease_duration") or 0)
        self._token = (token, now + (lease * 0.8 if lease > 0 else self._cache_seconds))
        return token


__all__ = ["EnvSecretBroker", "OpenBaoSecretBroker", "SecretBroker", "SecretError"]
