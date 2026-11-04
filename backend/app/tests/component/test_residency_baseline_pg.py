"""The first residency baseline is the owner's own; a change still needs a second administrator (ZID-ENG-DEC-010; 13C solo variant).

A one-person organization cannot have a second approver yet, and confirming where it already lives moves nothing. So the very first residency record, on the home cell the workspace was
created on and with no recovery cell added, needs only the permission and the owner's fresh proof. Anything that changes a boundary afterwards (another home cell, a recovery cell, a new profile)
needs an independent second administrator, exactly as before.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

BODY = {
    "residency_profile_id": "SRP-1",
    "home_cell_id": "uk-1",
    "permitted_recovery_cells": [],
    "prohibited_cells": [],
    "operator_jurisdictions": [],
    "key_profile": "PLATFORM_MANAGED",
    "jurisdiction_pack_id": "uk-gdpr",
    "approval_ref": "BASELINE-1",
}


def error(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_the_owner_confirms_the_first_baseline_alone_and_every_later_change_needs_a_second_administrator(
    rt_url: str,
) -> None:
    o = onboard(
        rt_url, extra_actions=["tenant.residency.manage"], extra_resources=["tenant_residency"]
    )
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    owner = o.bearer(o.owner)
    with TestClient(app) as api:
        none_yet = api.get("/v1/tenant/residency", headers=owner)
        assert none_yet.status_code == 200 and none_yet.json()["residency"] is None

        first = api.put(
            "/v1/tenant/residency", headers=owner, json=BODY
        )  # no approval request at all
        assert first.status_code == 200, first.text
        assert (
            first.json()["residency"]["residency_profile_id"] == "SRP-1"
            and first.json()["residency"]["home_cell_id"] == "uk-1"
        )

        # a second record is a change: without an independent approval it is refused, however the owner asks
        again = api.put(
            "/v1/tenant/residency", headers=owner, json={**BODY, "residency_profile_id": "SRP-2"}
        )
        assert again.status_code == 403 and error(again) == "APPROVAL_REQUIRED", again.text


def test_a_first_record_that_moves_anything_is_still_a_change(rt_url: str) -> None:
    o = onboard(
        rt_url, extra_actions=["tenant.residency.manage"], extra_resources=["tenant_residency"]
    )
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    owner = o.bearer(o.owner)
    with TestClient(app) as api:
        added_recovery = api.put(
            "/v1/tenant/residency",
            headers=owner,
            json={**BODY, "permitted_recovery_cells": ["uk-2"]},
        )
        assert (
            added_recovery.status_code in (403, 409, 422) and added_recovery.status_code != 200
        )  # a recovery cell widens where data may go: never the solo baseline
        if added_recovery.status_code == 403:
            assert error(added_recovery) == "APPROVAL_REQUIRED"
        elsewhere = api.put(
            "/v1/tenant/residency", headers=owner, json={**BODY, "home_cell_id": "us-1"}
        )
        assert (
            elsewhere.status_code != 200
        )  # another home cell is a move: refused or needs approval, never silently accepted
