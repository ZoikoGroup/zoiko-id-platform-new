"""POST /v2/organizations/verify (ZID-INT-002 section 10): a stateless register lookup with one section 06.1 outcome, never a person's authority."""

from __future__ import annotations

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_org_claims_api_pg import Register
from zid_platform.main import create_app
from zid_platform.settings import Settings

BODY = {"country": "GB", "registration_number": "00123456", "legal_name": "Acme Widgets Limited"}


def client(rt_url: str, register: Register):
    o = onboard(rt_url)
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    app.state.kyb_adapters = {"companies-house": register}
    return o, app


def test_a_register_lookup_answers_with_an_outcome_and_names_no_person(rt_url: str) -> None:
    register = Register()
    o, app = client(rt_url, register)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with TestClient(app) as api:
        assert api.post("/v2/organizations/verify", json=BODY).status_code == 401
        r = api.post("/v2/organizations/verify", headers=second, json=BODY)
        assert r.status_code == 403, "a grant is not a tenant role"
        r = api.post("/v2/organizations/verify", headers=owner, json={**BODY, "country": "BR"})
        assert r.status_code == 422, r.text
        assert (
            api.post(
                "/v2/organizations/verify", headers=owner, json={**BODY, "extra": 1}
            ).status_code
            == 422
        )

        r = api.post("/v2/organizations/verify", headers=owner, json=BODY)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["outcome"] in {"VERIFIED", "INDETERMINATE"}, out
        assert out["company"]["registration_number"] == "00123456"
        assert out["company"]["legal_name"] == "Acme Widgets Limited"
        assert "limitations" in out and out["request_id"]
        assert not {"authentic", "pass", "verified", "valid", "people", "officers"} & set(out)

        # a different name is never VERIFIED
        r = api.post(
            "/v2/organizations/verify",
            headers=owner,
            json={**BODY, "legal_name": "Completely Different Trading Co"},
        )
        assert r.status_code == 200 and r.json()["outcome"] != "VERIFIED", r.text

        # a malformed identifier is refused before any source is asked
        r = api.post(
            "/v2/organizations/verify", headers=owner, json={**BODY, "registration_number": "x!"}
        )
        assert r.status_code == 422, r.text

        # an outage is UNAVAILABLE, never 'not found' and never success
        register.down = True
        r = api.post("/v2/organizations/verify", headers=owner, json=BODY)
        assert r.status_code == 200 and r.json()["outcome"] == "UNAVAILABLE", r.text
