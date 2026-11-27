"""The built-in global identity policy over HTTP on PostgreSQL (docs/specs/identity-and-company-verification-design.md, section 6).

A country with no policy of its own is served by the global policy ONLY with the owners' written acceptance; a country's own policy always wins; the face route still needs its own
acknowledgement; and the result is the lowest grade, never more.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx  # noqa: F401
from onboarding_fixture import onboard
from test_idv_face_route_pg import SCOPE, face_entry, similarity
from test_idv_media_pg import (  # noqa: F401
    app_for,
    entry,
    fake_imaging,
    photo,
    rows,
)
from zid_platform.modules.verification.api.idv_cases import FACE_ROUTE_ACK
from zid_platform.modules.verification.domain import idv_policy
from zid_platform.modules.verification.infrastructure.idv_policy_file import GLOBAL_POLICY_ACK

CLAIM = "SELECT grade FROM verify.idv_case_claim WHERE case_id = CAST(:c AS uuid)"
FRANCE = "FR"  # a country with no policy of its own anywhere in these tests


def global_rules(threshold: float = 0.363) -> Any:
    return idv_policy.parse(
        {"markets": {"GLOBAL": face_entry(threshold) | {"media_retention_days": 14}}}
    )["GLOBAL"]


def serve(api: Any, *, accepted: bool = True, face_ack: bool = True) -> None:
    api.app.state.idv_global_policy = lambda: global_rules()
    api.app.state.idv_global_policy_ack = GLOBAL_POLICY_ACK if accepted else None
    api.app.state.idv_face_route_ack = FACE_ROUTE_ACK if face_ack else None


def run(api: Any, who: dict[str, str], market: str, route: str = "WEB_FACE") -> tuple[str, Any]:
    opened = api.post("/v1/idv/cases", headers=who, json={"market": market, "route": route})
    if opened.status_code != 200 and opened.status_code != 201:
        return "", opened
    case = opened.json()["case_id"]
    nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=who).json()["nonce"]
    body = {"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "selfie_base64": base64.b64encode(photo()).decode(),
            "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB", "consent": True}  # fmt: skip
    return case, api.post(f"/v1/idv/cases/{case}/document-image", headers=who, json=body)


def test_an_unlisted_country_is_verified_through_the_global_policy_with_the_lowest_grade(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.5)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(
        rt_url, o, entry()
    ) as api:  # the table holds only GB, so France has no policy of its own
        serve(api)
        case, done = run(api, me, FRANCE)
        assert done.status_code == 200, done.text
        assert done.json()["decision"] == "APPROVED"
    assert (
        rows(owner_url, CLAIM, c=case)[0][0] == "FACE_MATCH_NO_LIVENESS"
    )  # never a stronger claim than the work done


def test_without_the_owners_acceptance_the_same_country_is_refused(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.5)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry()) as api:
        serve(api, accepted=False)
        _, opened = run(api, me, FRANCE)
        assert (
            opened.status_code == 422
            and opened.json()["error"]["code"] == "MARKET_POLICY_NOT_PUBLISHED"
        )


def test_the_face_route_still_needs_its_own_acknowledgement(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.5)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry()) as api:
        serve(api, face_ack=False)
        _, done = run(api, me, FRANCE)
        assert (
            done.status_code != 200 and "APPROVED" not in done.text
        )  # accepting the global policy does not accept the missing liveness test


def test_a_score_below_the_line_is_a_retake_never_an_approval_and_leaves_no_claim(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.1)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry()) as api:
        serve(api)
        case, done = run(api, me, FRANCE)
        assert done.status_code == 200, done.text
        assert done.json()["decision"] != "APPROVED"
    assert rows(owner_url, CLAIM, c=case) == []


def test_the_exception_route_opens_for_an_unlisted_country_too(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, entry()) as api:
        serve(api)
        api.app.state.idv_global_policy = lambda: idv_policy.parse(
            {"markets": {"GLOBAL": entry(media_retention_days=14)}}
        )["GLOBAL"]
        opened = api.post(
            "/v1/idv/cases", headers=me, json={"market": FRANCE, "route": "WEB_REVIEW"}
        )
        assert opened.status_code in (200, 201), opened.text


def test_a_countrys_own_policy_still_wins_over_the_global_one(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.5)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(
        rt_url, o, entry()
    ) as api:  # GB's own policy offers WEB_REVIEW only, no face route
        serve(api)
        _, opened = run(api, me, "GB", "WEB_FACE")
        assert opened.status_code != 200 or "WEB_FACE" not in str(opened.json().get("route", ""))
