"""Company-verification source adapters (ZID-ED-03 §02, §06): Companies House, GLEIF and Creditsafe.

Each adapter answers two questions and states what it can do: `search` returns ranked candidates the applicant
chooses from, `get` returns the normalized facts of one identified company or None when the source has no such record.
An outage, a refusal or an unreadable answer is `KybError("SOURCE_UNAVAILABLE")`: it is never read as "not found" and
never as success. Transport rules are those of the registry adapters (ROOT-001 §4.1): a fixed base URL per adapter,
verified TLS, no redirects, bounded responses, credentials resolved by reference at call time, and an injectable
transport for tests and for a sovereign cell's egress proxy.

Creditsafe has NOT been run against a live account. Checked against its published documentation (doc.creditsafe.com,
30 Oct 2026): `POST /authenticate` with `username` and `password` returns `token`; the company search is
`GET /companies` with a required `countries` and optional `regNo`, `name` and `status` (Active, NonActive, Pending,
Other), answered as `{correlationId, messages, totalSize, companies}`; the token goes in `Authorization: Bearer`; a
sandbox host exists. Not confirmed by the documentation: the field names inside each `companies` item (`id`, `regNo`,
`name`, `status`, `address`), so those are still unverified until a sandbox account exists (ED-03 §07). It stays
DISABLED_UNTIL_CONTRACT in `config/kyb/kyb-routing.yaml`.

The French source is the public Annuaire des Entreprises search API (INSEE Sirene and the RNE), open and without a
key; its parameters and result fields below are from its OpenAPI document (recherche-entreprises.api.gouv.fr).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from zid_platform.modules.organization.domain.kyb import (
    Candidate,
    Capabilities,
    CompanyFacts,
    KybError,
    RegisterPersonFact,
    RegisterRead,
    SourceKind,
    name_similarity,
    response_hash,
)

MAX_BYTES = 1_000_000
TIMEOUT_S = 5.0
MAX_CANDIDATES = 10


class KybAdapter(Protocol):
    adapter_id: str
    kind: SourceKind
    capabilities: Capabilities

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]: ...

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None: ...


def _client(base: str, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base, transport=transport, timeout=TIMEOUT_S, follow_redirects=False, verify=True
    )


async def _fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, bytes, dict[str, Any]]:
    """(status, raw bytes, parsed JSON); 404 gives an empty document, anything else unusable is unavailable."""
    try:
        r = await client.request(method, url, headers=headers or {}, json=json_body)
    except httpx.HTTPError as e:
        raise KybError("SOURCE_UNAVAILABLE", type(e).__name__) from e
    if r.status_code == 404:
        return 404, b"", {}
    if r.status_code != 200:
        raise KybError("SOURCE_UNAVAILABLE", f"source answered {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise KybError("SOURCE_UNAVAILABLE", "response too large")
    try:
        body = json.loads(r.content)
    except ValueError as e:
        raise KybError("SOURCE_UNAVAILABLE", "response is not JSON") from e
    if not isinstance(body, dict):
        raise KybError("SOURCE_UNAVAILABLE", "unexpected response shape")
    return 200, r.content, body


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _list(doc: dict[str, Any], key: str) -> list[Any]:
    value = doc.get(key)
    return value if isinstance(value, list) else []


def _address(parts: Any) -> str | None:
    if isinstance(parts, str):
        return parts or None
    if isinstance(parts, dict):
        joined = ", ".join(_text(v) for v in parts.values() if _text(v))
        return joined or None
    return None


@dataclass
class CompaniesHouseKyb:
    """Companies House Public Data API: GET /company/{number}, GET /search/companies. The GB source of record."""

    api_key: Callable[[], str | None]
    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://api.company-information.service.gov.uk"
    adapter_id: str = "companies-house"
    kind: SourceKind = SourceKind.OFFICIAL_REGISTER
    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(countries=frozenset({"GB"}))
    )

    def _auth(self) -> httpx.BasicAuth:
        key = self.api_key()
        if not key:
            raise KybError("SOURCE_UNAVAILABLE", "no Companies House credential configured")
        return httpx.BasicAuth(key, "")

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        if jurisdiction != "GB":
            return []
        async with _client(self.base_url, self.transport) as c:
            c.auth = self._auth()
            _, _, doc = await _fetch(
                c, "GET", f"/search/companies?q={quote(query)}&items_per_page={MAX_CANDIDATES}"
            )
        items = _list(doc, "items")
        found = [
            Candidate(
                adapter_id=self.adapter_id,
                jurisdiction="GB",
                registry_authority="companies-house",
                registration_number=_text(i.get("company_number")),
                legal_name=_text(i.get("title")),
                registration_status=_text(i.get("company_status")),
                source_uri=f"{self.base_url}/company/{quote(_text(i.get('company_number')))}",
                confidence=name_similarity(query, _text(i.get("title"))),
            )
            for i in items
            if isinstance(i, dict) and _text(i.get("company_number"))
        ]
        return sorted(found, key=lambda x: (-x.confidence, x.registration_number))

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None:
        if jurisdiction != "GB":
            return None
        async with _client(self.base_url, self.transport) as c:
            c.auth = self._auth()
            status, raw, doc = await _fetch(c, "GET", f"/company/{quote(registration_number)}")
        if status == 404:
            return None
        source_status = _text(doc.get("company_status"))
        return CompanyFacts(
            adapter_id=self.adapter_id,
            source_kind=self.kind,
            jurisdiction="GB",
            registry_authority="companies-house",
            registration_number=_text(doc.get("company_number")) or registration_number,
            legal_name=_text(doc.get("company_name")),
            legal_form=_text(doc.get("type")) or None,
            registration_status=source_status,
            active=source_status == "active",
            registered_address=_address(doc.get("registered_office_address")),
            source_uri=f"{self.base_url}/company/{quote(registration_number)}",
            observed_at=now,
            response_hash=response_hash(raw),
        )

    async def register_people(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> RegisterRead:
        """GET /company/{n}/officers and /persons-with-significant-control (Companies House Public Data API). A list the register says
        is longer than what we read is UNAVAILABLE, never a partial answer that looks complete."""
        if jurisdiction != "GB":
            return RegisterRead("NOT_APPLICABLE", source=self.adapter_id)
        number = quote(registration_number)
        async with _client(self.base_url, self.transport) as c:
            c.auth = self._auth()
            _, _, officers = await _fetch(
                c, "GET", f"/company/{number}/officers?items_per_page=100"
            )
            _, _, pscs = await _fetch(
                c, "GET", f"/company/{number}/persons-with-significant-control?items_per_page=100"
            )
        people: list[RegisterPersonFact] = []
        for doc, kind in ((officers, "OFFICER"), (pscs, "PSC")):
            items = [i for i in _list(doc, "items") if isinstance(i, dict)]
            total = doc.get("total_results")
            if isinstance(total, int) and total > len(items):
                raise KybError(
                    "SOURCE_UNAVAILABLE", "the register list is longer than the page read"
                )
            for i in items:
                ceased = (
                    bool(i.get("ceased")) or bool(i.get("ceased_on")) or bool(i.get("resigned_on"))
                )
                role_or_kind = (
                    _text(i.get("officer_role")) if kind == "OFFICER" else _text(i.get("kind"))
                )
                entity = kind == "PSC" and not role_or_kind.startswith("individual")
                if kind == "OFFICER":
                    entity = _text(i.get("officer_role")).startswith("corporate")
                birth = i.get("date_of_birth")
                year = _text(birth.get("year")) if isinstance(birth, dict) else ""
                people.append(
                    RegisterPersonFact(
                        kind=kind,
                        name=_natural_name(_text(i.get("name"))),
                        entity=entity,
                        natures=tuple(_text(n) for n in i.get("natures_of_control", []) if _text(n))
                        if kind == "PSC"
                        else (role_or_kind,),
                        ceased=ceased,
                        birth_year=year or None,
                    )
                )
        canonical = json.dumps(
            sorted(
                [p.kind, p.name, p.entity, list(p.natures), p.ceased, p.birth_year or ""]
                for p in people
            ),
            separators=(",", ":"),
        )
        return RegisterRead(
            "OK",
            tuple(people),
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            self.adapter_id,
        )


def _natural_name(name: str) -> str:
    """Companies House writes an individual as "SURNAME, Forename Middle"; screening and declarations use natural order."""
    if "," in name:
        surname, _, rest = name.partition(",")
        return f"{rest.strip()} {surname.strip()}".strip()
    return name


_NUMBER_LIKE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .\-/]{2,19}")


@dataclass
class GleifKyb:
    """GLEIF LEI records: reference data that can corroborate an entity, never an applicant's authority."""

    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://api.gleif.org/api/v1"
    adapter_id: str = "gleif"
    kind: SourceKind = SourceKind.LEI_REFERENCE
    capabilities: Capabilities = field(  # a global reference, not universal coverage
        default_factory=lambda: Capabilities(countries=frozenset({"*"}))
    )

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        """Companies that hold an LEI, found by name or by their national registration number, in the applicant's country only (the country filter is
        GLEIF's own, so a common name is not crowded out by other countries' records). Reference data: it finds a company, it does not verify one."""
        text_query = query.strip()
        searches = [f"filter[fulltext]={quote(text_query)}"]
        if _NUMBER_LIKE.fullmatch(text_query) and any(ch.isdigit() for ch in text_query):
            searches.insert(
                0, f"filter[entity.registeredAs]={quote(text_query)}"
            )  # a national number finds its company exactly
        country_filter = (
            f"&filter[entity.legalAddress.country]={quote(jurisdiction)}" if jurisdiction else ""
        )
        data: list[Any] = []
        async with _client(self.base_url, self.transport) as c:
            for search in searches:
                _, _, doc = await _fetch(
                    c,
                    "GET",
                    f"/lei-records?{search}{country_filter}&page[size]={MAX_CANDIDATES}",
                    headers={"Accept": "application/vnd.api+json"},
                )
                data.extend(_list(doc, "data"))
        out: dict[str, Candidate] = {}
        for rec in data:
            attrs = (rec or {}).get("attributes") or {}
            entity = attrs.get("entity") or {}
            country = _text(entity.get("jurisdiction"))[:2].upper()
            if jurisdiction and country and country != jurisdiction:
                continue
            name = _text((entity.get("legalName") or {}).get("name"))
            lei = _text((rec or {}).get("id"))
            national = _text(entity.get("registeredAs"))
            if lei in out:
                continue
            out[lei] = Candidate(
                adapter_id=self.adapter_id,
                jurisdiction=country,
                registry_authority="gleif",
                registration_number=national
                or lei,  # the number the applicant knows; the LEI is carried beside it
                legal_name=name,
                registration_status=_text(entity.get("status")),
                source_uri=f"{self.base_url}/lei-records/{quote(lei)}",
                confidence=name_similarity(text_query, name)
                if not national or national != text_query
                else 1.0,
                lei=lei,
            )
        return sorted(out.values(), key=lambda x: (-x.confidence, x.registration_number))

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None:
        """`registration_number` is the LEI here."""
        async with _client(self.base_url, self.transport) as c:
            status, raw, doc = await _fetch(
                c,
                "GET",
                f"/lei-records/{quote(registration_number)}",
                headers={"Accept": "application/vnd.api+json"},
            )
        if status == 404:
            return None
        attrs = (doc.get("data") or {}).get("attributes") or {}
        entity = attrs.get("entity") or {}
        registration = attrs.get("registration") or {}
        entity_status, reg_status = _text(entity.get("status")), _text(registration.get("status"))
        return CompanyFacts(
            adapter_id=self.adapter_id,
            source_kind=self.kind,
            jurisdiction=_text(entity.get("jurisdiction"))[:2].upper(),
            registry_authority="gleif",
            registration_number=registration_number,
            legal_name=_text((entity.get("legalName") or {}).get("name")),
            legal_form=None,
            registration_status=f"{entity_status}/{reg_status}",
            active=entity_status == "ACTIVE" and reg_status == "ISSUED",
            registered_address=_address(entity.get("legalAddress")),
            source_uri=f"{self.base_url}/lei-records/{quote(registration_number)}",
            observed_at=now,
            response_hash=response_hash(raw),
            lei=registration_number,
            national_number=_text(entity.get("registeredAs")) or None,
        )


@dataclass
class CreditsafeKyb:
    """Creditsafe Connect API: the preferred paid multi-country supplier. UNVERIFIED against a live account."""

    credentials: Callable[
        [], tuple[str, str] | None
    ]  # (username, password) from the secrets broker
    countries: frozenset[str]
    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = (
        "https://connect.creditsafe.com/v1"  # sandbox: https://connect.sandbox.creditsafe.com/v1
    )
    adapter_id: str = "creditsafe"
    kind: SourceKind = SourceKind.SUPPLIER

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(countries=self.countries)

    async def _token(self, client: httpx.AsyncClient) -> dict[str, str]:
        creds = self.credentials()
        if not creds:
            raise KybError("SOURCE_UNAVAILABLE", "no Creditsafe credential configured")
        status, _, doc = await _fetch(
            client, "POST", "/authenticate", json_body={"username": creds[0], "password": creds[1]}
        )
        token = _text(doc.get("token")) if status == 200 else ""
        if not token:
            raise KybError("SOURCE_UNAVAILABLE", "Creditsafe refused the credential")
        return {"Authorization": f"Bearer {token}"}

    async def _companies(
        self, jurisdiction: str, params: str
    ) -> tuple[bytes, list[dict[str, Any]]]:
        async with _client(self.base_url, self.transport) as c:
            headers = await self._token(c)
            _, raw, doc = await _fetch(
                c, "GET", f"/companies?countries={jurisdiction}&{params}", headers=headers
            )
        items = _list(doc, "companies")
        return raw, [i for i in items if isinstance(i, dict)]

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        if jurisdiction not in self.countries:
            return []
        _, items = await self._companies(
            jurisdiction, f"name={quote(query)}&pageSize={MAX_CANDIDATES}"
        )
        out = [
            Candidate(
                adapter_id=self.adapter_id,
                jurisdiction=jurisdiction,
                registry_authority=f"creditsafe:{jurisdiction}",
                registration_number=_text(i.get("regNo")),
                legal_name=_text(i.get("name")),
                registration_status=_text(i.get("status")),
                source_uri=f"{self.base_url}/companies/{quote(_text(i.get('id')))}",
                confidence=name_similarity(query, _text(i.get("name"))),
            )
            for i in items
            if _text(i.get("regNo"))
        ]
        return sorted(out, key=lambda x: (-x.confidence, x.registration_number))

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None:
        if jurisdiction not in self.countries:
            return None
        raw, items = await self._companies(
            jurisdiction, f"regNo={quote(registration_number)}&pageSize=5"
        )
        exact = [i for i in items if _text(i.get("regNo")) == registration_number]
        if len(exact) != 1:  # none, or ambiguous: nothing is picked for the applicant
            return None
        i = exact[0]
        status = _text(i.get("status"))
        return CompanyFacts(
            adapter_id=self.adapter_id,
            source_kind=self.kind,
            jurisdiction=jurisdiction,
            registry_authority=f"creditsafe:{jurisdiction}",
            registration_number=registration_number,
            legal_name=_text(i.get("name")),
            legal_form=None,  # `type` is Ltd / NonLtd / NonLtdNonReg, not the legal form
            registration_status=status,
            active=status == "Active",  # the documented enum: Active, NonActive, Pending, Other
            registered_address=_address(i.get("address")),
            source_uri=f"{self.base_url}/companies/{quote(_text(i.get('id')))}",
            observed_at=now,
            response_hash=response_hash(raw),
            supplier_entity_id=_text(i.get("id")) or None,
        )


@dataclass
class SireneKyb:
    """Annuaire des Entreprises search API (INSEE Sirene and RNE): the French register of record, open and keyless.

    `q` with exactly nine digits is a direct SIREN lookup that ignores every other filter."""

    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://recherche-entreprises.api.gouv.fr"
    adapter_id: str = "sirene"
    kind: SourceKind = SourceKind.OFFICIAL_REGISTER
    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(countries=frozenset({"FR"}))
    )

    @staticmethod
    def _uri(siren: str) -> str:
        return f"https://annuaire-entreprises.data.gouv.fr/entreprise/{quote(siren)}"

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        if jurisdiction != "FR":
            return []
        async with _client(self.base_url, self.transport) as c:
            _, _, doc = await _fetch(
                c, "GET", f"/search?q={quote(query)}&per_page={MAX_CANDIDATES}"
            )
        out = []
        for r in _list(doc, "results"):
            if not isinstance(r, dict) or not _text(r.get("siren")):
                continue
            name = _text(r.get("nom_raison_sociale")) or _text(r.get("nom_complet"))
            out.append(
                Candidate(
                    adapter_id=self.adapter_id,
                    jurisdiction="FR",
                    registry_authority="sirene-rne",
                    registration_number=_text(r.get("siren")),
                    legal_name=name,
                    registration_status=_text(r.get("etat_administratif")),
                    source_uri=self._uri(_text(r.get("siren"))),
                    confidence=name_similarity(query, name),
                )
            )
        return sorted(out, key=lambda x: (-x.confidence, x.registration_number))

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None:
        if jurisdiction != "FR":
            return None
        async with _client(self.base_url, self.transport) as c:
            _, raw, doc = await _fetch(
                c, "GET", f"/search?q={quote(registration_number)}&per_page=1"
            )
        exact = [
            r
            for r in _list(doc, "results")
            if isinstance(r, dict) and _text(r.get("siren")) == registration_number
        ]
        if not exact:
            return None
        r = exact[0]
        state = _text(r.get("etat_administratif"))  # A = active, C = ceased
        siege_raw = r.get("siege")
        siege: dict[str, Any] = siege_raw if isinstance(siege_raw, dict) else {}
        return CompanyFacts(
            adapter_id=self.adapter_id,
            source_kind=self.kind,
            jurisdiction="FR",
            registry_authority="sirene-rne",
            registration_number=registration_number,
            legal_name=_text(r.get("nom_raison_sociale")) or _text(r.get("nom_complet")),
            legal_form=None,  # `nature_juridique` is a numeric INSEE category code, not a name to compare
            registration_status=state,
            active=state == "A",
            registered_address=_text(siege.get("adresse")) or None,
            source_uri=self._uri(registration_number),
            observed_at=now,
            response_hash=response_hash(raw),
        )


@dataclass
class InpiKyb:
    """INPI Registre National des Entreprises (RNE) API: the French register of record, behind an account. UNVERIFIED against a live account.

    It needs credentials (`inpi-rne-username` and `inpi-rne-password` in the secret broker): `POST /api/sso/login` with `username` and `password` returns `token`, and
    `GET /api/companies/{siren}` returns the company under `formality.content.personneMorale`. The field names below follow the RNE documentation as published and have not
    been run against a live account (ED-03 section 07), so the adapter stays DISABLED_UNTIL_CREDENTIALS in `config/kyb/kyb-routing.yaml`. With no credential it
    makes no request and says the source is unavailable (T03): nothing is looked up or made up, and the signed alternative (the open Sirene source, then a reviewer) answers."""

    credentials: Callable[[], tuple[str, str] | None]
    transport: httpx.AsyncBaseTransport | None = None
    base_url: str = "https://registre-national-entreprises.inpi.fr"
    adapter_id: str = "inpi"
    kind: SourceKind = SourceKind.OFFICIAL_REGISTER
    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(countries=frozenset({"FR"}))
    )

    @staticmethod
    def _uri(siren: str) -> str:
        return f"https://data.inpi.fr/entreprises/{quote(siren)}"

    async def _headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        creds = self.credentials()
        if not creds or not creds[0] or not creds[1]:
            raise KybError("SOURCE_UNAVAILABLE", "no INPI credential configured")
        status, _, doc = await _fetch(
            client,
            "POST",
            "/api/sso/login",
            json_body={"username": creds[0], "password": creds[1]},
        )
        token = _text(doc.get("token")) if status == 200 else ""
        if not token:
            raise KybError("SOURCE_UNAVAILABLE", "INPI refused the credential")
        return {"Authorization": f"Bearer {token}"}

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Candidate]:
        return []  # the RNE API looks a company up by SIREN; the open Sirene source does the name search

    async def get(
        self, registration_number: str, jurisdiction: str, now: datetime
    ) -> CompanyFacts | None:
        if jurisdiction != "FR" or not (
            len(registration_number) == 9 and registration_number.isdigit()
        ):
            return None
        async with _client(self.base_url, self.transport) as c:
            headers = await self._headers(c)
            status, raw, doc = await _fetch(
                c, "GET", f"/api/companies/{quote(registration_number)}", headers=headers
            )
        if status == 404:
            return None
        legal = ((doc.get("formality") or {}).get("content") or {}).get("personneMorale") or {}
        identity = (legal.get("identite") or {}).get("entreprise") or {}
        if _text(identity.get("siren")) != registration_number:
            return None  # an answer for another company is never taken for this one
        ended = _text(identity.get("dateRadiation") or doc.get("dateRadiation"))
        state = "radiee" if ended else "active"
        return CompanyFacts(
            adapter_id=self.adapter_id,
            source_kind=self.kind,
            jurisdiction="FR",
            registry_authority="sirene-rne",
            registration_number=registration_number,
            legal_name=_text(identity.get("denomination")),
            legal_form=None,  # `formeJuridique` is a numeric category code, not a name to compare
            registration_status=state,
            active=not ended,
            registered_address=None,
            source_uri=self._uri(registration_number),
            observed_at=now,
            response_hash=response_hash(raw),
        )


def default_adapters(broker: Any, transport: Any = None) -> dict[str, KybAdapter]:
    """The sources Zoiko reads without a supplier contract: the UK register (key from the secret broker), GLEIF and the French
    register. Creditsafe joins only when it is enabled in the routing and has credentials."""

    def key() -> str | None:
        try:
            return str(broker.get("companies-house-api-key")) if broker else None
        except Exception:  # noqa: BLE001 - the source then reports itself unavailable
            return None

    def inpi() -> tuple[str, str] | None:
        try:
            user, password = broker.get("inpi-rne-username"), broker.get("inpi-rne-password")
            return (str(user), str(password)) if user and password else None
        except Exception:  # noqa: BLE001 - no credential: the source then reports itself unavailable
            return None

    return {
        "companies-house": CompaniesHouseKyb(key, transport),
        "gleif": GleifKyb(transport),
        "sirene": SireneKyb(transport),
        "inpi": InpiKyb(inpi, transport),
    }
