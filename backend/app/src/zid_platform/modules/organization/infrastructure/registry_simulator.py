"""Development register simulator: Companies House and GLEIF (ZID-ROOT-001 §4-6; local/test only).

Behind ZID_DEV_SIMULATORS (refused outside local/test). Answers the exact endpoints and shapes the
real adapters read, for a few fixed synthetic companies, so the register lookup, the organization
gate and the officer match run end to end without a Companies House API key. Every name here is
synthetic; nothing is a real company or person.

    01234567  ACME WIDGETS LIMITED      active     director "DOE, Jane Mary"   (verify as "Jane Mary Doe")
    02222222  NORTHWIND TRADING LTD     active     director "SMITH, Alex"      (verify as "Alex Smith")
    03333333  GONE LTD                  dissolved  director "DOE, Jane Mary"
    04444444  SECRETARIAL LTD           active     secretary only (no representative role)
    99xxxxxx  SYNTHETIC <number> LTD    active     director "DOE, Jane Mary"   (any 8-digit number starting 99;
              a fresh company per number, so repeatable test runs never collide on one fixed company)
"""

from __future__ import annotations

from typing import Any

import httpx

CH_HOST = "api.company-information.service.gov.uk"
GLEIF_HOST = "api.gleif.org"

COMPANIES: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {
    "01234567": (
        {
            "company_name": "ACME WIDGETS LIMITED",
            "company_status": "active",
            "date_of_creation": "2001-02-03",
        },
        [
            {"name": "DOE, Jane Mary", "officer_role": "director", "appointed_on": "2015-01-01"},
            {"name": "ROE, Richard", "officer_role": "secretary", "appointed_on": "2015-01-01"},
        ],
    ),
    "02222222": (
        {
            "company_name": "NORTHWIND TRADING LTD",
            "company_status": "active",
            "date_of_creation": "2010-06-30",
        },
        [{"name": "SMITH, Alex", "officer_role": "director", "appointed_on": "2012-03-01"}],
    ),
    "03333333": (
        {
            "company_name": "GONE LTD",
            "company_status": "dissolved",
            "date_of_creation": "1999-09-09",
        },
        [{"name": "DOE, Jane Mary", "officer_role": "director", "appointed_on": "2001-01-01"}],
    ),
    "04444444": (
        {
            "company_name": "SECRETARIAL LTD",
            "company_status": "active",
            "date_of_creation": "2005-05-05",
        },
        [{"name": "DOE, Jane Mary", "officer_role": "secretary", "appointed_on": "2006-01-01"}],
    ),
}


def _synthetic(number: str) -> bool:
    return len(number) == 8 and number.isdigit() and number.startswith("99")


def _synthetic_company(number: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "company_name": f"SYNTHETIC {number} LTD",
            "company_status": "active",
            "date_of_creation": "2020-01-01",
        },
        [{"name": "DOE, Jane Mary", "officer_role": "director", "appointed_on": "2020-01-01"}],
    )


class RegistrySimulator(httpx.AsyncBaseTransport):
    """Companies House `/company/{n}` and `/company/{n}/officers`; GLEIF lookups find nothing."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == GLEIF_HOST:
            return httpx.Response(404, request=request)
        if request.url.host != CH_HOST:
            return httpx.Response(502, request=request)
        parts = request.url.path.strip("/").split("/")
        if parts == ["search", "companies"]:
            return httpx.Response(200, json={"items": _search(request.url.params.get("q", ""))}, request=request)
        if len(parts) == 3 and parts[0] == "company" and parts[2] == "persons-with-significant-control":
            known = parts[1] in COMPANIES or _synthetic(parts[1])
            return httpx.Response(200 if known else 404, json={"items": [], "total_results": 0}, request=request)
        company = COMPANIES.get(parts[1]) if len(parts) >= 2 and parts[0] == "company" else None
        if company is None and len(parts) >= 2 and parts[0] == "company" and _synthetic(parts[1]):
            company = _synthetic_company(parts[1])
        if company is None:
            return httpx.Response(404, request=request)
        if len(parts) == 2:
            return httpx.Response(
                200, json=company[0], headers={"etag": f"sim-{parts[1]}"}, request=request
            )
        return httpx.Response(200, json={"items": company[1]}, request=request)


def _search(query: str) -> list[dict[str, Any]]:
    """`/search/companies?q=`: the fixed synthetic companies whose name or number contains the query, and a synthetic company for a 99xxxxxx number."""
    q = query.strip().lower()
    found = [
        {"company_number": number, "title": doc["company_name"], "company_status": doc["company_status"]}
        for number, (doc, _) in COMPANIES.items()
        if q and (q in doc["company_name"].lower() or q == number)
    ]
    if _synthetic(query.strip()) and query.strip() not in COMPANIES:
        synthetic = _synthetic_company(query.strip())[0]
        found.append({"company_number": query.strip(), "title": synthetic["company_name"], "company_status": synthetic["company_status"]})
    return found


SIMULATOR_KEY = "development-simulator"  # the adapter needs a credential; the simulator ignores it
