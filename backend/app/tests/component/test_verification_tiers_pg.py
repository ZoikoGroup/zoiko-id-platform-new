"""Progressive verification over the API (ZID-EDR-006; slice L-6): the caller's tier and claims, and what each tier asks for."""

from __future__ import annotations

from conftest import ctx, engine  # noqa: F401
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings


def test_the_tier_the_next_step_and_what_each_tier_asks_for(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app) as api:
        own = o.bearer(o.owner)
        assert api.get("/v1/verification/tier").status_code == 401

        me = api.get("/v1/verification/tier", headers=own)
        assert me.status_code == 200, me.text
        t = me.json()
        # no sign-up proof is on record for this account, so it says so instead of assuming one
        assert t["tier"] == "NONE" and t["next_tier"] == "R0" and t["reasons"] == ["EMAIL_NOT_PROVED"]
        assert t["missing"] == [{"check": "EMAIL", "why": "NEEDED_FOR_R0"}]
        assert t["facts"]["identity_proofed"] is False and t["claims"] == [] and t["alternatives"] == []
        assert "PHONE_OTP" in t["never_identity_proof"] and "Not identity verified" in t["note"]

        # a person who cannot use a camera is shown the approved alternatives; nobody else is
        alt = api.get("/v1/verification/tier", params={"cannot": ["NO_CAMERA"]}, headers=own).json()["alternatives"]
        assert {a["id"] for a in alt} == {"ASSISTED_PROOFING", "EQUIVALENT_CREDENTIAL"}
        assert all(a["status"] == "PROCESS_REQUIRED" for a in alt)

        # R0 asks for the email and nothing else
        r0 = api.get("/v1/verification/requirements", params={"target": "R0"}, headers=own).json()
        assert [c["check"] for c in r0["checks"] if c["required"]] == ["EMAIL"]
        # R1 adds the document and the selfie, and an address only for a stated reason
        r1 = api.get("/v1/verification/requirements", params={"target": "R1"}, headers=own).json()
        assert [c["check"] for c in r1["checks"] if c["required"]] == ["EMAIL", "GOVERNMENT_ID", "SELFIE_LIVENESS"]
        withaddr = api.get("/v1/verification/requirements", params={"target": "R1", "need": ["CONTRACT"]}, headers=own).json()
        assert {c["check"]: c["reason"] for c in withaddr["checks"]}["ADDRESS"] == "CONTRACT"
        alt1 = api.get("/v1/verification/requirements", params={"target": "R1", "alternative": "ASSISTED_PROOFING"}, headers=own).json()
        assert [c["check"] for c in alt1["checks"] if c["required"]] == ["EMAIL", "ASSISTED_PROOFING"]

        # an unrecognised target, reason or alternative is refused, never guessed
        for params in ({"target": "R9"}, {"target": "R1", "need": ["BECAUSE"]}, {"target": "R1", "alternative": "SKIP_IT"}, {"target": "NONE"}):
            assert api.get("/v1/verification/requirements", params=params, headers=own).status_code == 422, params
