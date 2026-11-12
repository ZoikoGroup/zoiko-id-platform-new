"""Pinned inputs, QA-lead risk confirmation, listings, the registry watcher and a rollback that closes the provider route (ZID-UAT-005; CR-074)."""

from __future__ import annotations

from datetime import date, timedelta

from onboarding_fixture import onboard
from test_assurance_plane_pg import ACTIONS, BUILD, CASES, CONFIG, RESOURCES, UNIT, sha
from test_v2_events_pg import events
from test_v2_privacy_pg import api_for, runtime  # noqa: F401  (runtime is an autouse fixture)

PROVIDER_ACTIONS = (*ACTIONS, "credential.claim.verify")
PROVIDER_RESOURCES = (*RESOURCES, "uat_registry:*", "provider-entitlement:*")


def entitlement(service: str = "rtr") -> dict:
    today = date.today()
    return {
        "service_code": service, "countries": ["GB"], "purposes": ["rental"], "processing_regions": ["uk"],
        "valid_from": (today - timedelta(days=1)).isoformat(), "valid_until": (today + timedelta(days=200)).isoformat(), "assurance": 4,
    }  # fmt: skip


def test_pins_risk_confirmation_listings_registry_drift_and_a_rollback_that_closes_the_route(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=PROVIDER_ACTIONS, extra_resources=PROVIDER_RESOURCES)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with api_for(rt_url, o) as api:

        def post(path: str, who: dict, body: dict | None = None):
            return api.post(path, headers=who, json=body or {})

        a = "/v2/assurance"
        assert (
            post(
                f"{a}/role-assignments", owner, {"role": "TESTER", "principal": o.second}
            ).status_code
            == 201
        )
        assert (
            post(
                f"{a}/role-assignments", second, {"role": "QA_LEAD", "principal": o.owner}
            ).status_code
            == 201
        )
        assert post(f"{a}/baselines", owner, {"version": "1", "cases": CASES}).status_code == 201

        # ---- risk is confirmed by the QA lead alone, into a new baseline; the old one is untouched
        assert (
            post(
                f"{a}/baselines/risk-confirmations", second, {"risks": {"GOV-01": "P0"}}
            ).status_code
            == 403
        )
        assert (
            post(
                f"{a}/baselines/risk-confirmations", owner, {"risks": {"NOPE-99": "P0"}}
            ).status_code
            == 422
        )
        done = post(
            f"{a}/baselines/risk-confirmations", owner, {"risks": {"GOV-01": "P0", "INT-02": "P2"}}
        )
        assert (
            done.status_code == 201
            and done.json()["version"] == "1.r1"
            and done.json()["confirmed"] == 2
        ), done.text
        req = api.get(f"{a}/requirements", headers=owner).json()
        assert req["baseline"]["version"] == "1.r1"
        assert {c["case_id"]: c["risk"] for c in req["cases"]} == {
            "GOV-01": "P0",
            "DOC-02": "PROPOSED_P0",
            "INT-02": "P2",
        }

        # ---- a run pins what the PASS rests on: a changed policy or trust list expires it
        h = sha("fx")
        fx = post(f"{a}/fixtures", owner, {"name": "baseline-fixture", "content_sha256": h}).json()
        assert (
            post(
                f"{a}/fixtures/{fx['fixture_id']}/review",
                second,
                {"revision": 1, "approve": True, "rationale": "checked"},
            ).status_code
            == 200
        )
        run_body = {
            "release_unit": UNIT, "environment_id": "env-1", "build_sha256": BUILD, "config_digest": CONFIG, "case_ids": ["GOV-01"],
            "fixture_hashes": [h], "pins": {"policy_version": "v1", "trust_list_snapshot": "tl-1"},
        }  # fmt: skip
        assert (
            post(f"{a}/test-runs", second, {**run_body, "pins": {"made_up_input": "x"}}).status_code
            == 422
        ), "only watched inputs can be pinned"
        rid = post(f"{a}/test-runs", second, run_body).json()["run_id"]
        manifest = post(
            f"{a}/test-runs/{rid}/cases/GOV-01/manifest",
            second,
            {"result": "PASS", "requirement_ids": ["GID-036"], "trace_refs": ["t1"]},
        )
        assert manifest.json()["verdict"] == "PASS"

        cand = {"release_unit": UNIT, "build_sha256": BUILD, "policy_pack": "US-CA@1", "facts": {}}
        same = post(
            f"{a}/release-candidates",
            owner,
            {**cand, "current_inputs": {"policy_version": "v1", "trust_list_snapshot": "tl-1"}},
        ).json()
        assert same["expired_cases"] == []
        moved = post(
            f"{a}/release-candidates",
            owner,
            {**cand, "current_inputs": {"policy_version": "v1", "trust_list_snapshot": "tl-2"}},
        ).json()
        assert moved["expired_cases"] == ["GOV-01"]
        sent = events(rt_url, o.tenant, "assurance.case.expired")
        assert sent and "trust_list_snapshot" in str(sent[-1]), (
            "the event names the input that changed"
        )

        # ---- listings
        fixtures = api.get(f"{a}/fixtures", headers=owner).json()["items"]
        assert [(f["name"], f["state"]) for f in fixtures] == [("baseline-fixture", "APPROVED")]
        runs = api.get(f"{a}/test-runs", headers=owner).json()["items"]
        assert len(runs) == 1 and runs[0]["run_id"] == rid and runs[0]["manifests"] == 1

        # ---- registry drift closes the exact provider routes, and only those
        for provider in ("acme", "zeta"):
            assert (
                api.post(
                    f"/v2/providers/{provider}/entitlements", headers=owner, json=entitlement()
                ).status_code
                == 201
            )
        previous = [
            {
                "provider_id": "acme",
                "service_code": "rtr",
                "status": "REGISTERED",
                "scheme_version": "1.1",
            },
            {
                "provider_id": "zeta",
                "service_code": "rtr",
                "status": "REGISTERED",
                "scheme_version": "1.1",
            },
            {
                "provider_id": "ghost",
                "service_code": "rtr",
                "status": "REGISTERED",
                "scheme_version": "1.1",
            },
        ]
        current = [
            {
                "provider_id": "acme",
                "service_code": "rtr",
                "status": "SUSPENDED",
                "scheme_version": "1.1",
            }
        ]
        watched = post(f"{a}/registry-watch", owner, {"previous": previous, "current": current})
        assert watched.status_code == 200, watched.text
        result = {(x["provider_id"], x["change"]): x["result"] for x in watched.json()["applied"]}
        assert result == {
            ("acme", "STATUS_CHANGED"): "SUSPENDED",
            ("zeta", "REMOVED"): "REVOKED",
            ("ghost", "REMOVED"): "NO_LIVE_ENTITLEMENT",
        }
        assert {"acme:rtr", "zeta:rtr", "ghost:rtr"} <= set(watched.json()["pins_to_expire"])
        listed = {
            e["provider_id"]: e["status"]
            for e in api.get("/v2/providers/entitlements", headers=owner).json()["entitlements"]
        }
        assert listed == {"acme": "SUSPENDED", "zeta": "REVOKED"}
        assert len(events(rt_url, o.tenant, "provider.entitlement_revoked")) == 1
        nothing = post(
            f"{a}/registry-watch", owner, {"previous": current, "current": current}
        ).json()
        assert nothing["changes"] == [] and nothing["applied"] == [], "no drift, nothing closed"

        # ---- a rollback names the route it closes and closes it in the same step
        assert (
            api.post(
                "/v2/providers/beta/entitlements", headers=owner, json=entitlement("doc")
            ).status_code
            == 201
        )
        rel = post(f"{a}/release-candidates", owner, cand).json()["release_id"]
        rb = post(
            f"{a}/releases/{rel}/rollback",
            owner,
            {"scope": {"provider_id": "beta", "service_code": "doc"}, "reason": "canary alarm"},
        )
        assert rb.status_code == 200 and rb.json()["provider_route_closed"] is True, rb.text
        status = {
            e["provider_id"]: e["status"]
            for e in api.get("/v2/providers/entitlements?provider_id=beta", headers=owner).json()[
                "entitlements"
            ]
        }
        assert status == {"beta": "SUSPENDED"}
        rel2 = post(f"{a}/release-candidates", owner, cand).json()["release_id"]
        unnamed = post(
            f"{a}/releases/{rel2}/rollback",
            owner,
            {"scope": {"provider_id": "nobody", "service_code": "x"}, "reason": "no such route"},
        )
        assert unnamed.json()["provider_route_closed"] is False
