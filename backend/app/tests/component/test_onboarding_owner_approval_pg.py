"""Owner approval of onboarding steps (ZID_ONBOARDING_APPROVAL=owner; CR-036): the company's OWNER approves a plan
alone; an administrator still cannot; the default mode still needs an independent second administrator."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.modules.onboarding.domain.gates import (
    OWNER_APPROVAL_ACK,
    OWNER_APPROVER,
    require_owner_approval_ack,
)
from zid_platform.settings import Settings

BASE = "/v1/onboarding/plans"


def app_for(rt_url: str, o: Any, **extra: Any) -> TestClient:
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            **extra,
        ),
        keyring=o.ring,
    )
    return TestClient(app)


def new_plan(api: TestClient, o: Any) -> str:
    r = api.post(
        BASE,
        json={
            "target_mode": "O1",
            "scope": {"max_risk": 1},
            "exit_criteria": {"min_observations": 3},
        },
        headers=o.bearer(o.owner),
    )
    assert r.status_code == 201, r.text
    return str(r.json()["plan_id"])


def test_the_owner_approves_a_plan_alone_in_owner_mode(rt_url: str) -> None:
    o = onboard(rt_url)
    with app_for(rt_url, o, onboarding_approval="owner") as api:
        assert api.get("/v1/onboarding/mode", headers=o.bearer(o.owner)).json() == {
            "approval": "owner",
            "self_approve": True,
        }
        # someone who is not the owner may not approve alone
        plan = new_plan(api, o)
        refused = api.post(f"{BASE}/{plan}:approve", json={}, headers=o.bearer(o.second))
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "APPROVAL_REQUIRED"

        done = api.post(f"{BASE}/{plan}:approve", json={}, headers=o.bearer(o.owner))
        assert done.status_code == 200 and done.json()["status"] == "APPROVED", done.text
        assert (
            done.json()["approved_by"] == OWNER_APPROVER
        )  # recorded honestly: nobody else approved it


def test_the_default_mode_still_needs_an_independent_second_administrator(rt_url: str) -> None:
    o = onboard(rt_url)
    with app_for(rt_url, o) as api:
        assert api.get("/v1/onboarding/mode", headers=o.bearer(o.owner)).json() == {
            "approval": "second_admin",
            "self_approve": False,
        }
        plan = new_plan(api, o)
        r = api.post(f"{BASE}/{plan}:approve", json={}, headers=o.bearer(o.owner))
        assert r.status_code == 403 and r.json()["error"]["code"] == "APPROVAL_REQUIRED"


def test_a_deployed_cell_refuses_owner_approval_without_the_acknowledgement() -> None:
    for environment in ("production", "sandbox", "preprod"):
        with pytest.raises(RuntimeError, match="ZID_ONBOARDING_OWNER_ACK"):
            require_owner_approval_ack("owner", environment, None)
        with pytest.raises(RuntimeError, match="ZID_ONBOARDING_OWNER_ACK"):
            require_owner_approval_ack("owner", environment, "yes")
        require_owner_approval_ack("owner", environment, OWNER_APPROVAL_ACK)
        require_owner_approval_ack("second_admin", environment, None)
    for environment in ("local", "test", "dev"):
        require_owner_approval_ack("owner", environment, None)
