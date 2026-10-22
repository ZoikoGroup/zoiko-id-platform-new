"""Registry source adapters: UK Companies House and GLEIF (ZID-ROOT-001 §4-6; G-W1).

Adapters normalize source facts but keep the source-native status, dates and revision (§4.1).
Transport rules (§4.1 "SSRF-safe, certificate/TLS validated, rate-limited, separately credentialed"):
the base URL is fixed per adapter (never caller input), TLS is verified, redirects are refused,
responses are size-bounded, and an outage is an explicit REGISTRY_UNAVAILABLE - missing data is
never read as revocation. The Companies House API key comes from the secrets broker by reference.
`transport` lets tests (and a sovereign cell's egress proxy) supply the HTTP transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from zid_platform.modules.organization.domain.registry import (
    RegistryEntity,
    RegistryError,
    RegistryOfficer,
    SourceType,
    check_identifier,
)

MAX_BYTES = 1_000_000
TIMEOUT_S = 5.0


class RegistryAdapter(Protocol):
    source_id: str
    namespace: str

    async def lookup(self, identifier: str, now: datetime) -> RegistryEntity: ...


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


async def _get(
    client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any], str]:
    try:
        r = await client.get(url, headers=headers or {})
    except httpx.HTTPError as e:
        raise RegistryError("REGISTRY_UNAVAILABLE", type(e).__name__) from e
    if r.status_code == 404:
        return 404, {}, ""
    if r.status_code != 200:
        raise RegistryError("REGISTRY_UNAVAILABLE", f"source answered {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise RegistryError("REGISTRY_UNAVAILABLE", "response too large")
    try:
        body = json.loads(r.content)
    except ValueError as e:
        raise RegistryError("REGISTRY_UNAVAILABLE", "response is not JSON") from e
    if not isinstance(body, dict):
        raise RegistryError("REGISTRY_UNAVAILABLE", "unexpected response shape")
    return 200, body, r.headers.get("etag", "")


def _client(base: str, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base, transport=transport, timeout=TIMEOUT_S, follow_redirects=False, verify=True
    )


@dataclass
class CompaniesHouse:
    """GET /company/{number} and /company/{number}/officers (Companies House Public Data API)."""

    api_key: Callable[[], str | None]  # resolved from the secrets broker at call time
    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://api.company-information.service.gov.uk"
    source_id: str = "companies-house"
    namespace: str = "GB-COH"

    async def lookup(self, identifier: str, now: datetime) -> RegistryEntity:
        number = check_identifier(self.namespace, identifier)
        key = self.api_key()
        if not key:
            raise RegistryError("REGISTRY_UNAVAILABLE", "no Companies House credential configured")
        auth = httpx.BasicAuth(key, "")
        async with _client(self.base_url, self.transport) as c:
            c.auth = auth
            status, profile, etag = await _get(c, f"/company/{quote(number)}")
            if status == 404:
                raise RegistryError("ORGANIZATION_NOT_FOUND", "no such company number")
            status, officers, _ = await _get(
                c, f"/company/{quote(number)}/officers?items_per_page=100"
            )
        items = officers.get("items") if status == 200 else []
        source_status = str(profile.get("company_status") or "unknown")
        return RegistryEntity(
            source_id=self.source_id,
            source_type=SourceType.NATIONAL_REGISTER,
            namespace=self.namespace,
            identifier=number,
            jurisdiction="GB",
            legal_name=str(profile.get("company_name") or ""),
            source_status=source_status,
            active=source_status == "active",
            incorporated_on=_date(profile.get("date_of_creation")),
            officers=tuple(
                RegistryOfficer(
                    name=str(o.get("name") or ""),
                    role=str(o.get("officer_role") or ""),
                    appointed_on=_date(o.get("appointed_on")),
                    resigned_on=_date(o.get("resigned_on")),
                )
                for o in (items if isinstance(items, list) else [])
                if isinstance(o, dict)
            ),
            source_revision=etag or str(profile.get("etag") or ""),
            observed_at=now,
        )


@dataclass
class Gleif:
    """GET /lei-records/{lei} (GLEIF API v1, JSON:API). Entity identity only; no roles."""

    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://api.gleif.org/api/v1"
    source_id: str = "gleif"
    namespace: str = "LEI"

    async def lookup(self, identifier: str, now: datetime) -> RegistryEntity:
        lei = check_identifier(self.namespace, identifier)
        async with _client(self.base_url, self.transport) as c:
            status, doc, etag = await _get(
                c, f"/lei-records/{quote(lei)}", {"Accept": "application/vnd.api+json"}
            )
        if status == 404:
            raise RegistryError("ORGANIZATION_NOT_FOUND", "no such LEI")
        attrs = (doc.get("data") or {}).get("attributes") or {}
        entity = attrs.get("entity") or {}
        registration = attrs.get("registration") or {}
        entity_status = str(entity.get("status") or "unknown")
        reg_status = str(registration.get("status") or "unknown")
        juris = str(entity.get("jurisdiction") or "")[:2].upper()
        return RegistryEntity(
            source_id=self.source_id,
            source_type=SourceType.GLEIF,
            namespace=self.namespace,
            identifier=lei,
            jurisdiction=juris,
            legal_name=str((entity.get("legalName") or {}).get("name") or ""),
            source_status=f"{entity_status}/{reg_status}",
            active=entity_status == "ACTIVE" and reg_status == "ISSUED",
            incorporated_on=_date(str(entity.get("creationDate") or "")[:10]),
            officers=(),
            source_revision=etag or str(registration.get("lastUpdateDate") or ""),
            observed_at=now,
        )


def adapter_for(
    namespace: str,
    *,
    transport: httpx.AsyncBaseTransport | None,
    companies_house_key: Callable[[], str | None],
) -> RegistryAdapter:
    if namespace == "GB-COH":
        return CompaniesHouse(companies_house_key, transport)
    if namespace == "LEI":
        return Gleif(transport)
    raise RegistryError("ORGANIZATION_SOURCE_UNSUPPORTED", f"no adapter for {namespace}")


def utcnow() -> datetime:
    return datetime.now(UTC)
