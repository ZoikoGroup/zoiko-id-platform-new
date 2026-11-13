"""The enforce-readiness report (CR-086): what switching every regulatory check to enforce would refuse today, and which owner configuration is
still empty. It reports, it changes nothing."""

from __future__ import annotations

from fastapi.testclient import TestClient
from operator_fixture import ISS
from test_regulatory_releases_api_pg import _app, _grant, _people


def test_readiness_lists_what_enforce_would_refuse_per_country_and_capability(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        # an operator without any regulatory capability is refused
        assert (
            api.get("/v1/regulatory/readiness", headers=o.bearer(who["recorder"])).status_code
            == 403
        )
        _grant(api, o, who, "recorder", "regulatory.attestation.record")
        r = api.get("/v1/regulatory/readiness", headers=o.bearer(who["recorder"]))
        assert r.status_code == 200, r.text
        body = r.json()

    assert body["modes"]["kill_switch"] == "always enforced"
    assert set(body["modes"]) >= {
        "regulatory_packs",
        "market_releases",
        "provider_entitlements",
        "privacy_overlay",
    }
    assert isinstance(body["empty_configuration"], list)
    assert {c["country"] for c in body["countries"]} >= {"US", "GB"}
    for country in body["countries"]:
        assert len(country["country"]) == 2
        for cap in country["capabilities"]:
            assert set(cap) == {"capability", "availability", "provider", "would_refuse", "missing"}
            # every refusal is named by the check that would make it, and `missing` is exactly those checks
            assert sorted({r.split(":", 1)[0] for r in cap["would_refuse"]}) == cap["missing"]
            if cap["capability"] == "SANCTIONS_ONGOING_MONITORING":
                # the shipped configuration approves no ongoing monitoring anywhere (other tests in a shared database may approve packs
                # and releases, but never monitoring), so enforce would refuse it in every country
                assert cap["missing"] == ["monitoring"]
