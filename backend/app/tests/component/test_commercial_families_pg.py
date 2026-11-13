"""GET /v1/catalog/families and POST /v1/commercial/configurations:check through the real app and Postgres (ZID-COM-WF-2026-11 v3.0, CW-02, CW-06, CW-08).

No market fact is recorded anywhere yet, so every family answers REQUEST_QUOTE, no price is ever shown, and a combination the document forbids (a signature
without a verified identity) is refused by name. A caller with no commercial.read authority cannot use the check.
"""

from __future__ import annotations

import asyncio
import uuid

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from test_commercial_pg import settings
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
)

URL = "/v1/commercial/configurations:check"


def test_the_public_family_list_names_sixteen_proposals_and_never_a_price_or_availability(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(app, base_url=ISS) as http:
        r = http.get("/v1/catalog/families")  # public: no token
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "PROPOSED" and len(out["families"]) == 16
        by = {f["family"]: f for f in out["families"]}
        assert by["SIGN"]["depends_on"] == ["IDV"] and by["SIGN"]["regulated"] is True
        assert all(
            f["sellable"] is False and f["next_action"] == "REQUEST_QUOTE" for f in out["families"]
        )
        assert all("price" not in f and "amount" not in f for f in out["families"])
        assert "$" not in r.text


def test_a_configuration_is_checked_for_dependencies_and_market_facts_without_a_price(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    nobody = str(uuid.uuid4())

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(nobody, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(seed())
    app = create_app(settings(rt_url), keyring=o.ring)
    adm = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        assert http.post(URL, json={"families": ["IDV"]}).status_code == 401
        assert (
            http.post(URL, headers=o.bearer(nobody), json={"families": ["IDV"]}).status_code == 403
        )

        # a signature without a verified identity cannot be configured
        bad = http.post(URL, headers=adm, json={"families": ["SIGN"]})
        assert bad.status_code == 200, bad.text
        out = bad.json()
        assert out["configurable"] is False
        assert out["problems"] == [{"code": "MISSING_DEPENDENCY", "family": "SIGN", "needs": "IDV"}]

        ok = http.post(
            URL,
            headers=adm,
            json={
                "families": ["SIGN", "IDV"],
                "subject_country": "DE",
                "payer_country": "US",
                "operating_region": "eu-central",
            },
        ).json()
        assert ok["configurable"] is True and ok["estimate"] is None
        assert "$" not in str(ok), "no price is invented"
        sign = next(f for f in ok["families"] if f["family"] == "SIGN")
        assert sign["sellable"] is False and sign["next_action"] == "REQUEST_QUOTE"
        dims = {r["dimension"] for r in sign["refusals"]}
        assert dims == {"subject", "payer", "operating"}, (
            "each market dimension is judged on its own"
        )
        assert {"CATALOGUE", "CONTRACT", "PROVIDER", "LEGAL", "TAX_PAYMENT", "PRODUCTION"} <= set(
            sign["causes"]
        )

        # a dimension nobody named is not silently passed
        partial = http.post(
            URL, headers=adm, json={"families": ["API"], "subject_country": "DE"}
        ).json()
        assert partial["families"][0]["sellable"] is False

        # malformed bodies and an unknown family are refused or named
        assert (
            http.post(
                URL, headers=adm, json={"families": ["IDV"], "subject_country": "germany"}
            ).status_code
            == 422
        )
        assert (
            http.post(URL, headers=adm, json={"families": ["IDV"], "price": 1}).status_code == 422
        )
        unknown = http.post(URL, headers=adm, json={"families": ["IDV", "GHOST"]}).json()
        assert (
            unknown["configurable"] is False and unknown["problems"][0]["code"] == "UNKNOWN_FAMILY"
        )
