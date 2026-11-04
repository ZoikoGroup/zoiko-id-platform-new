"""The bulk session revoke asks for fresh strong authentication (ZID-ENG-DEC-009, S1), through the guard composed in at start-up."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings


def app_for(rt_url: str, o: Any, mode: str) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1",
                 webauthn_rp_id="id.zoiko.test", webauthn_origins=("https://id.zoiko.test",), step_up_enforcement=mode),
        keyring=o.ring,
    )  # fmt: skip


@pytest.mark.parametrize(("mode", "status"), [("enforce", 403), ("shadow", 200), ("off", 200)])
def test_global_logout_needs_a_fresh_proof_only_when_enforced(
    rt_url: str, mode: str, status: int
) -> None:
    o = onboard(rt_url)
    with TestClient(app_for(rt_url, o, mode), base_url=ISS) as api:
        r = api.post("/sessions:revoke-all", headers=o.bearer(o.owner))
        assert r.status_code == status, r.text
        if mode == "enforce":
            assert r.json()["error"]["code"] == "STEP_UP_REQUIRED"
            # a refused request revoked nothing: the session still works
            assert api.get("/sessions", headers=o.bearer(o.owner)).status_code == 200
