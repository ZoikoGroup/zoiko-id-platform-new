"""The marked export (AT-047) and counsel's collection requirements inside a purpose decision (AT-034, AT-035, AT-037), over HTTP against PostgreSQL."""

from __future__ import annotations

from onboarding_fixture import onboard
from test_doc04_controls_pg import PROVIDER_ACTIONS, PROVIDER_RESOURCES
from test_v2_privacy_pg import api_for, runtime  # noqa: F401  (runtime is an autouse fixture)

RES = (*PROVIDER_RESOURCES, "export:new", "export:verify")
FIXTURE = {
    "uk-gdpr": {
        "approval_ref": "COUNSEL-FIXTURE-2",
        "effective_from": "2026-01-01",
        "release_required": True,
    }
}


def test_at047_an_export_carries_only_named_claims_is_signed_and_checks_its_recipient(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=PROVIDER_ACTIONS, extra_resources=RES)
    h = o.bearer(o.owner)
    body = {
        "claims": {"licence_status": "ACTIVE", "home_address": "1 High St", "selfie_image": "..."},
        "include": ["licence_status"],
        "recipient": "zoikorum",
        "purpose": "engagement_check",
        "ttl_seconds": 3600,
    }
    with api_for(rt_url, o) as api:
        assert api.post("/v2/privacy/exports", json=body).status_code == 401
        made = api.post("/v2/privacy/exports", headers=h, json=body)
        assert made.status_code == 201, made.text
        out = made.json()
        assert out["claims_included"] == ["licence_status"] and out["claims_not_found"] == []
        assert "High St" not in made.text and "selfie" not in made.text

        ok = api.post(
            "/v2/privacy/exports:verify",
            headers=h,
            json={"token": out["token"], "expected_recipient": "zoikorum"},
        )
        assert (
            ok.status_code == 200
            and ok.json()["valid"] is True
            and ok.json()["claims"] == {"licence_status": "ACTIVE"}
        )
        wrong = api.post(
            "/v2/privacy/exports:verify",
            headers=h,
            json={"token": out["token"], "expected_recipient": "someone-else"},
        )
        assert wrong.status_code == 422 and wrong.json()["code"] == "export_not_for_this_recipient"
        head, payload, sig = out["token"].split(".")
        bad = api.post(
            "/v2/privacy/exports:verify",
            headers=h,
            json={"token": f"{head}.{payload[:-2]}AA.{sig}"},
        )
        assert bad.status_code == 422 and bad.json()["code"] == "export_signature_invalid"

        # a raw biometric claim cannot be exported, and nothing is signed for it
        refused = api.post(
            "/v2/privacy/exports", headers=h, json={**body, "include": ["selfie_image"]}
        )
        assert refused.status_code == 422 and refused.json()["code"] == "claim_not_exportable"
        # a lifetime past the cap is refused before anything is signed
        assert (
            api.post(
                "/v2/privacy/exports", headers=h, json={**body, "ttl_seconds": 10**7}
            ).status_code
            == 422
        )


def test_at034_at035_at037_sensitive_collection_under_a_pack_follows_counsels_rules(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=PROVIDER_ACTIONS, extra_resources=RES)
    h = o.bearer(o.owner)
    base = {
        "activity_code": "BIOMETRIC_CHECK", "purpose_code": "IDENTITY_PROOFING", "data_category": "D3", "country": "GB",
        "notice_version": "NOPE@1", "jurisdiction_pack_id": "uk-gdpr",
    }  # fmt: skip
    with api_for(rt_url, o, collection_rules=FIXTURE) as api:
        d = api.post("/v2/privacy/purpose-decisions", headers=h, json=base)
        assert d.status_code == 200 and d.json()["allowed"] is False
        assert "RELEASE_REQUIRED" in d.json()["reason_codes"], (
            "counsel's release requirement is demanded by name"
        )
        with_release = api.post(
            "/v2/privacy/purpose-decisions", headers=h, json={**base, "release_ref": "REL-1"}
        ).json()
        assert "RELEASE_REQUIRED" not in with_release["reason_codes"]

    with api_for(
        rt_url, o, collection_rules={}
    ) as api:  # a pack counsel has no entry for is untouched by this check
        free = api.post("/v2/privacy/purpose-decisions", headers=h, json=base).json()
        assert "RELEASE_REQUIRED" not in free["reason_codes"]
        assert not any(c.startswith("BIOMETRIC_COLLECTION") for c in free["reason_codes"])
