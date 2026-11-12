"""Risk dashboards and assurance reporting over real decision records: counts only, tenant-scoped, and honest about a control that is not met."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.settings import Settings

ROWS = [
    # outcome, risk, reasons, release?, chain?, evidence
    ("ALLOW", "R3", [], True, True, "E3"),
    (
        "ALLOW",
        "R3",
        [],
        True,
        False,
        "E3",
    ),  # a critical allow with no authority chain: the control must show it
    ("ALLOW", "R1", [], True, True, "E1"),
    ("DENY", "R2", ["NO_AUTHORITY"], False, False, "E2"),
    ("DENY", "R2", ["NO_AUTHORITY"], False, False, "E2"),
    ("STEP_UP", "R2", ["ASSURANCE_LOW"], False, False, "E2"),
]


def test_figures_count_the_records_and_show_an_unmet_control(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["decision_receipt:*"])

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            rel = uuid.uuid4()
            for outcome, risk, reasons, release, chain, ev in ROWS:
                await c.execute(
                    text(
                        "INSERT INTO authz.policy_decision (decision_id, tenant_id, subject_principal_id, action, resource_ref, risk_class, evidence_class, outcome, "
                        "reason_codes, policy_release_id, relation_revision, security_revision, authority_chain_commitment, input_commitment, decided_at) "
                        "VALUES (:id, :t, :s, 'payment.release', 'payment:1', :r, :e, CAST(:o AS authz.decision_outcome), :rc, :pr, :rr, :sr, :ac, 'x', :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "t": uuid.UUID(o.tenant),
                        "s": uuid.UUID(o.owner),
                        "r": risk,
                        "e": ev,
                        "o": outcome,
                        "rc": reasons,
                        "pr": rel if release else None,
                        "rr": 1 if release else None,
                        "sr": 1 if release else None,
                        "ac": "c" if chain else None,
                        "at": datetime.now(UTC) - timedelta(hours=1),
                    },
                )

    asyncio.run(seed())
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
    h = o.bearer(o.owner)
    with TestClient(app) as api:
        r = api.get("/v1/insights/risk", params={"days": 7}, headers=h)
        assert r.status_code == 200, r.text
        risk = r.json()
        # the six seeded records, plus the (R1, allowed) record of this very request being authorised
        assert risk["total"] == 7 and risk["by_outcome"] == {"ALLOW": 4, "DENY": 2, "STEP_UP": 1}
        assert risk["refusal_rate"] == round(3 / 7, 4)
        assert risk["top_refusal_reasons"][0] == {"code": "NO_AUTHORITY", "count": 2}
        assert risk["by_risk_class"]["R2"] == {"DENY": 2, "STEP_UP": 1}
        assert "subject" not in r.text and "payment:1" not in r.text  # counts and codes only

        a = api.get("/v1/insights/assurance", params={"days": 7}, headers=h).json()
        controls = {c["control"]: c for c in a["controls"]}
        assert (
            controls["HIGH_RISK_ALLOW_HAS_AUTHORITY"]["passed"] == 1
            and controls["HIGH_RISK_ALLOW_HAS_AUTHORITY"]["of"] == 2
        )
        assert controls["HIGH_RISK_ALLOW_HAS_AUTHORITY"]["met"] is False
        assert controls["CRITICAL_ALLOW_IS_EVIDENCED"]["met"] is True
        # by organization unit: a unit and the units beneath it, counting only the people in them now
        group = api.post(
            "/v1/tenant/org-units", json={"name": "Acme Group", "kind": "GROUP"}, headers=h
        ).json()["unit_id"]
        uk = api.post(
            "/v1/tenant/org-units",
            json={"name": "Acme UK", "kind": "SUBSIDIARY", "parent_unit_id": group},
            headers=h,
        ).json()["unit_id"]
        empty = api.get("/v1/insights/risk", params={"days": 7, "unit_id": group}, headers=h).json()
        assert empty["total"] == 0 and empty["unit_id"] == group
        assert (
            api.post(
                f"/v1/tenant/org-units/{uk}/members", json={"principal_id": o.owner}, headers=h
            ).status_code
            == 201
        )
        in_group = api.get(
            "/v1/insights/risk", params={"days": 7, "unit_id": group}, headers=h
        ).json()  # the owner sits in a subsidiary
        in_uk = api.get("/v1/insights/risk", params={"days": 7, "unit_id": uk}, headers=h).json()
        assert (
            in_group["total"] >= 6
            and in_group["by_outcome"]["DENY"] == 2
            and in_uk["total"] >= in_group["total"]
            and in_uk["by_outcome"]["DENY"] == 2
        )
        other = api.get(
            "/v1/insights/assurance", params={"days": 7, "unit_id": group}, headers=h
        ).json()
        assert other["unit_id"] == group and other["decisions"] >= 6
        assert (
            api.get(
                "/v1/insights/risk",
                params={"unit_id": "00000000-0000-4000-8000-000000000000"},
                headers=h,
            ).status_code
            == 404
        )
        assert api.get("/v1/insights/risk", params={"days": 0}, headers=h).status_code == 422
        # a window with no decisions is empty, not an error
        assert api.get("/v1/insights/risk", params={"days": 1}, headers=h).json()["total"] >= 6
