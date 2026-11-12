"""Document 04 controls over HTTP against PostgreSQL: the ADM and outcome-routing routes, AT-046 (a new purpose needs its own permission), AT-050 (a kill
switch stops a provider and a model without deleting any evidence of what happened)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from test_doc04_closing_pg import events
from test_model_governance_pg import metrics
from test_v2_privacy_facades_pg import EXTRA, seed
from test_v2_privacy_pg import ACTIONS, RESOURCES, api_for, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction

PROVIDER_ACTIONS = (*ACTIONS, "credential.claim.verify")
PROVIDER_RESOURCES = (*RESOURCES, *EXTRA, "model:*", "model:new", "provider-entitlement:*")
FIXTURE = {
    "required_safeguards": ["SAFEGUARD_A"],
    "approval_ref": "COUNSEL-FIXTURE-1",
    "effective_from": "2026-01-01",
}


def test_the_adm_route_blocks_without_counsels_rules_and_checks_them_when_supplied(
    rt_url: str,
) -> None:
    o = onboard(
        rt_url,
        extra_actions=PROVIDER_ACTIONS,
        extra_resources=(*PROVIDER_RESOURCES, "adm_evaluation:new", "outcome_routing:new"),
    )
    h = o.bearer(o.owner)
    body = {
        "jurisdiction_pack_id": "uk-gdpr",
        "significant": True,
        "solely_automated": True,
        "safeguards_offered": ["SAFEGUARD_A"],
    }
    with api_for(rt_url, o) as api:
        assert api.post("/v2/privacy/adm-evaluations", json=body).status_code == 401
        # the shipped rules are all COUNSEL: a significant, solely automated decision is blocked and goes to a person
        r = api.post("/v2/privacy/adm-evaluations", headers=h, json=body)
        assert r.status_code == 200, r.text
        assert r.json()["allowed"] is False and r.json()["route_to_person"] is True
        assert r.json()["reason_codes"] == ["ADM_RULES_NOT_SET_BY_COUNSEL"]
        assert "states no rule of its own" in r.json()["statement"]
        assert (
            api.post(
                "/v2/privacy/adm-evaluations", headers=h, json={**body, "significant": False}
            ).json()["allowed"]
            is True
        )

    with api_for(rt_url, o, adm_rules={"uk-gdpr": FIXTURE}) as api:  # counsel's rules, supplied
        ok = api.post("/v2/privacy/adm-evaluations", headers=h, json=body).json()
        assert ok["allowed"] is True and ok["rules_ref"] == "uk-gdpr@COUNSEL-FIXTURE-1"
        short = api.post(
            "/v2/privacy/adm-evaluations", headers=h, json={**body, "safeguards_offered": []}
        ).json()
        assert short["allowed"] is False and short["missing_safeguards"] == ["SAFEGUARD_A"]


def test_outcome_routing_never_approves_and_never_refuses_on_a_biometric_band(rt_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=PROVIDER_ACTIONS,
        extra_resources=(*PROVIDER_RESOURCES, "adm_evaluation:new", "outcome_routing:new"),
    )
    h = o.bearer(o.owner)
    base = {
        "match_band": "MATCH",
        "pad_band": "LOW_RISK",
        "injection_suspected": False,
        "capture_quality_ok": True,
        "high_impact": False,
    }
    with api_for(rt_url, o) as api:

        def route(**over):
            r = api.post("/v2/privacy/outcome-routing", headers=h, json={**base, **over})
            assert r.status_code == 200, r.text
            return r.json()

        assert route()["route"] == "CONTINUE_TO_NEXT_CHECK"
        assert route(injection_suspected=True)["route"] == "HUMAN_REVIEW"  # AT-010
        assert route(capture_quality_ok=False)["route"] == "RETRY_OR_ALTERNATIVE"  # AT-012
        assert route(high_impact=True, match_band="NO_MATCH")["route"] == "HUMAN_REVIEW"  # AT-013
        nm = route(match_band="NO_MATCH")  # AT-014
        assert (
            nm["route"] == "RETRY_OR_ALTERNATIVE"
            and nm["penalty"] is False
            and nm["offer_correction_route"] is True
        )
        assert "fraud" not in nm["statement"].replace("never called fraud", "")


def test_at046_a_purpose_the_activity_never_declared_is_denied_not_silently_shared(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=PROVIDER_ACTIONS, extra_resources=PROVIDER_RESOURCES)
    seed(rt_url, o)
    h = o.bearer(o.owner)
    base = {
        "activity_code": "MARKETING_EMAIL",
        "purpose_code": "NEWSLETTER",
        "data_category": "D1",
        "subject_ref": o.owner,
        "country": "GB",
    }
    with api_for(rt_url, o) as api:
        reuse = api.post(
            "/v2/privacy/purpose-decisions",
            headers=h,
            json={**base, "purpose_code": "SOURCE_OF_FUNDS_REVIEW"},
        )
        assert reuse.status_code == 200 and reuse.json()["allowed"] is False, reuse.text
        assert reuse.json()["reason_codes"] != ["PROCESSING_PERMITTED_BY_RECORDED_FACTS"]


def test_at050_a_kill_switch_stops_a_provider_and_a_model_and_keeps_every_record(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=PROVIDER_ACTIONS, extra_resources=PROVIDER_RESOURCES)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    today = date.today()
    ent = {"service_code": "id.document", "countries": ["GB"], "purposes": ["onboarding"], "processing_regions": ["uk"],
           "valid_from": (today - timedelta(days=1)).isoformat(), "valid_until": (today + timedelta(days=90)).isoformat(), "assurance": 4}  # fmt: skip
    ask = {
        "country": "GB",
        "service_code": "id.document",
        "purpose": "onboarding",
        "processing_region": "uk",
        "required_assurance": 3,
    }
    reg = {
        "model_ref": "doc-check",
        "version": "1.0",
        "kind": "face_match",
        "live_features": ["capture_quality"],
    }
    ev = {"model_ref": "doc-check", "version": "1.0", "evaluator_ref": "lab-1", "independent": True, "cohorts": 4,
          "metrics": metrics(), "accessible_alternative_confirmed": True}  # fmt: skip
    with api_for(rt_url, o) as api:
        assert (
            api.post("/v2/providers/acme/entitlements", headers=owner, json=ent).status_code == 201
        )
        assert api.post("/v2/privacy/models", headers=owner, json=reg).status_code == 201
        assert api.post("/v2/privacy/model-evaluations", headers=owner, json=ev).status_code == 201
        assert (
            api.post("/v2/privacy/models/doc-check/versions/1.0:promote", headers=second).json()[
                "state"
            ]
            == "ACTIVE"
        )
        assert (
            api.post("/v2/providers/route", headers=owner, json=ask).json()["provider_id"] == "acme"
        )

        # the canary misbehaves: stop the provider's path and hold the model, in two calls, with nothing deleted
        assert (
            api.post(
                "/v2/providers/acme/entitlements/id.document/revoke",
                headers=owner,
                json={"reason": "canary alarm"},
            ).status_code
            == 200
        )
        held = api.post(
            "/v2/privacy/models/doc-check:observe", headers=owner, json={"seen_version": "1.1"}
        ).json()
        assert held["held"] is True
        assert (
            api.post("/v2/providers/route", headers=owner, json=ask).json()["provider_id"] is None
        ), "the path is closed"

        # every record of what happened is still there
        listed = api.get("/v2/providers/entitlements?provider_id=acme", headers=owner).json()[
            "entitlements"
        ]
        assert [e["status"] for e in listed] == ["REVOKED"]
        assert (
            api.get("/v2/privacy/models/doc-check/versions/1.0", headers=owner).json()["state"]
            == "HELD"
        )
        assert len(events(rt_url, o.tenant, "provider.entitlement_revoked")) == 1

        async def counts() -> tuple[int, int]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                e = (
                    await c.execute(text("SELECT count(*) FROM privacy.model_evaluation"))
                ).scalar_one()
                ob = (
                    await c.execute(text("SELECT count(*) FROM privacy.model_observation"))
                ).scalar_one()
                return int(e), int(ob)

        assert asyncio.run(counts()) == (1, 1), (
            "evaluations and drift observations are append-only and survive the kill switch"
        )
