"""Auth0 as a directory: read a company's users with a read-only machine-to-machine application (Management API).

The company gives its Auth0 domain, the application's client id and (write-only, sealed) its client secret. The application must hold exactly the
`read:users` permission: a wider grant is refused, so a leaked sync credential can never change the company's Auth0 tenant. A list this reader cannot
see completely is refused too (an incomplete list would look like people who left), see DIRECTORY_TOO_LARGE.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import httpx

from zid_platform import egress
from zid_platform.modules.provisioning.domain.directory import DirectoryError, DirectoryUser

PROVIDER = "AUTH0"
PAGE = 100
WINDOW = 1000  # Auth0's paged user list reaches no further than this
FIELDS = "user_id,email,name,given_name,family_name,blocked"
_DOMAIN = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.auth0\.com$"
)


def valid_domain(domain: str) -> bool:
    return bool(_DOMAIN.match(domain)) and len(domain) <= 253


def check_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """The only settings the provider takes: its tenant domain and the application's client id. Nothing here is a secret."""
    domain, client_id = (
        str(settings.get("domain", "")).strip().lower(),
        str(settings.get("client_id", "")).strip(),
    )
    if not valid_domain(domain):
        raise DirectoryError(
            "SETTINGS_INVALID", "domain must be an Auth0 tenant such as your-tenant.us.auth0.com"
        )
    if not 8 <= len(client_id) <= 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", client_id):
        raise DirectoryError("SETTINGS_INVALID", "client_id looks wrong")
    return {"domain": domain, "client_id": client_id}


def _failure(res: httpx.Response) -> DirectoryError:
    if res.status_code in (401, 403):
        return DirectoryError("CREDENTIAL_REJECTED")
    if res.status_code == 429:
        return DirectoryError("PROVIDER_RATE_LIMITED")
    return DirectoryError("PROVIDER_ERROR")


async def _call(http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        safe = await egress.guard(url)
    except egress.EgressError as e:
        raise DirectoryError("PROVIDER_ADDRESS_REFUSED") from e
    try:
        res = await http.request(method, safe, **kwargs)
    except httpx.HTTPError as e:
        raise DirectoryError("PROVIDER_UNREACHABLE") from e
    if res.status_code >= 400:
        raise _failure(res)
    return res


def to_user(raw: Mapping[str, Any]) -> DirectoryUser | None:
    user_id = raw.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    email = raw.get("email") if isinstance(raw.get("email"), str) and raw.get("email") else None
    return DirectoryUser(
        external_id=user_id,
        user_name=email or user_id,
        email=email,
        display_name=raw.get("name") or None,
        given_name=raw.get("given_name") or None,
        family_name=raw.get("family_name") or None,
        active=not bool(raw.get("blocked", False)),
    )


async def fetch_users(
    http: httpx.AsyncClient, settings: Mapping[str, Any], secret: str
) -> list[DirectoryUser]:
    s = check_settings(settings)
    audience = f"https://{s['domain']}/api/v2/"
    token = (
        await _call(
            http,
            "POST",
            f"https://{s['domain']}/oauth/token",
            json={
                "client_id": s["client_id"],
                "client_secret": secret,
                "audience": audience,
                "grant_type": "client_credentials",
            },
        )
    ).json()
    granted = set(str(token.get("scope", "")).split())
    if "read:users" not in granted:
        raise DirectoryError("CREDENTIAL_NOT_ALLOWED")
    if granted - {"read:users"}:
        raise DirectoryError("CREDENTIAL_TOO_BROAD")
    headers = {"authorization": f"Bearer {token['access_token']}"}
    users: list[DirectoryUser] = []
    page, total = 0, 0
    while True:
        body = (
            await _call(
                http,
                "GET",
                f"{audience}users",
                headers=headers,
                params={
                    "per_page": PAGE,
                    "page": page,
                    "include_totals": "true",
                    "fields": FIELDS,
                    "include_fields": "true",
                },
            )
        ).json()
        total = int(body["total"])
        if total > WINDOW:
            raise DirectoryError("DIRECTORY_TOO_LARGE")
        users += [u for u in (to_user(r) for r in body["users"]) if u is not None]
        if len(body["users"]) == 0 or (page + 1) * PAGE >= total:
            break
        page += 1
    if len(users) != total:
        raise DirectoryError(
            "DIRECTORY_INCOMPLETE"
        )  # fewer people than the provider said it has: never act on a partial list
    return users
