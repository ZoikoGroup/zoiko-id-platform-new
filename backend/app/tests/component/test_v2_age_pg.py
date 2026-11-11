"""POST /v2/age-checks (DTV-19): a threshold answer, never a date of birth, and no self-declared date ever gives AT_LEAST."""

from __future__ import annotations

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings


def test_age_checks_answer_a_threshold_and_never_echo_a_date_or_trust_a_typed_one(
    rt_url: str,
) -> None:
    o = onboard(
        rt_url, extra_actions=("credential.claim.verify",), extra_resources=("age-check:new",)
    )
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    h = o.bearer(o.owner)
    with TestClient(app) as api:
        assert (
            api.post(
                "/v2/age-checks",
                json={"threshold": 18, "estimate": {"estimated_age": 40, "margin": 3}},
            ).status_code
            == 401
        )

        # a typed birth date is self-declared: UNDETERMINED with a documented check offered, never AT_LEAST
        r = api.post(
            "/v2/age-checks", headers=h, json={"threshold": 18, "birth_date": "1980-01-01"}
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["result"] == "UNDETERMINED" and out["method"] == "SELF_DECLARED"
        assert (
            out["reason_codes"] == ["BIRTH_DATE_NOT_VERIFIED"] and out["fallback_offered"] is True
        )
        assert "1980" not in r.text, "the birth date is never echoed"

        # a clear estimate decides, and lapses within a day
        r = api.post(
            "/v2/age-checks",
            headers=h,
            json={"threshold": 18, "estimate": {"estimated_age": 40, "margin": 3}},
        )
        assert (
            r.json()["result"] == "AT_LEAST"
            and r.json()["method"] == "ESTIMATION"
            and r.json()["valid_until"]
        )
        r = api.post(
            "/v2/age-checks",
            headers=h,
            json={"threshold": 18, "estimate": {"estimated_age": 9, "margin": 2}},
        )
        assert r.json()["result"] == "UNDER"

        # near the line it is undetermined, not a refusal and not a pass
        r = api.post(
            "/v2/age-checks",
            headers=h,
            json={"threshold": 18, "estimate": {"estimated_age": 19, "margin": 2}},
        )
        assert r.json()["result"] == "UNDETERMINED" and r.json()["fallback_offered"] is True

        # a typed date cannot outvote a decisive estimate, and cannot rescue an unclear one
        r = api.post(
            "/v2/age-checks",
            headers=h,
            json={
                "threshold": 18,
                "birth_date": "1990-01-01",
                "estimate": {"estimated_age": 8, "margin": 1},
            },
        )
        assert r.json()["result"] == "UNDER"

        # only thresholds a policy defines; some evidence is required
        assert (
            api.post(
                "/v2/age-checks",
                headers=h,
                json={"threshold": 17, "estimate": {"estimated_age": 40, "margin": 3}},
            ).status_code
            == 422
        )
        assert api.post("/v2/age-checks", headers=h, json={"threshold": 18}).status_code == 422
        assert (
            api.post(
                "/v2/age-checks", headers=h, json={"threshold": 18, "birth_date": "2999-01-01"}
            ).status_code
            == 200
        )
