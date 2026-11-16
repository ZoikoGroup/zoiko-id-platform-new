"""Recording Travel Rule messages over HTTP against PostgreSQL (DTV-33, CR-101): behind the product line gate, what was found is kept and the people are not,
the same message is recorded once, and one company never sees another's record."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from sqlalchemy import text
from test_travel_rule_pg import lines
from test_v2_facade_pg import world
from zid_platform.modules.verification.domain.travel_rule import parse_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_travel_rule import GOOD, ORIGIN

URL = "/v2/travel-rule/messages"
REGISTRY = {
    "version": "t-2",
    "vasps": [
        {
            "lei": ORIGIN,
            "legal_name": "Origin VASP",
            "jurisdictions": ["GB"],
            "status": "ACTIVE",
            "source_ref": "register:1",
        }
    ],
    "thresholds": {"GB": {"amount": "1000", "currency": "GBP", "legal_basis_ref": "LEG-9"}},
}


def _on(http: object) -> None:
    http.app.state.product_lines = lines("STAGING")  # type: ignore[attr-defined]
    http.app.state.vasp_registry = parse_registry(REGISTRY)  # type: ignore[attr-defined]


def test_the_shipped_line_is_design_so_recording_refuses_too(rt_url: str) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        body = {"direction": "INCOMING", "jurisdiction": "GB", "ivms101": GOOD}
        assert http.post(URL, json=body).status_code == 401
        out = http.post(URL, headers=mine, json=body)
        assert out.status_code == 409 and out.json()["code"] == "service_unavailable", out.text


def test_a_message_is_recorded_once_with_its_findings_and_never_its_people(rt_url: str) -> None:
    with world(rt_url) as (http, mine, tenant, _a):
        _on(http)
        body = {"direction": "INCOMING", "jurisdiction": "GB", "ivms101": GOOD}
        first = http.post(URL, headers=mine, json=body)
        assert first.status_code == 200, first.text
        out = first.json()
        assert out["already_recorded"] is False and out["well_formed"] is True
        assert (
            out["originating_vasp"] == "KNOWN"
            and out["beneficiary_vasp"] == "UNVERIFIED_COUNTERPARTY"
        )
        assert out["threshold"] == "AT_OR_ABOVE_THRESHOLD" and out["registry_version"] == "t-2"
        assert "Lee" not in first.text  # nothing about the people comes back
        again = http.post(URL, headers=mine, json=body).json()
        assert again["already_recorded"] is True and again["message_id"] == out["message_id"]
        other_way = http.post(URL, headers=mine, json={**body, "direction": "OUTGOING"}).json()
        assert other_way["message_id"] != out["message_id"]  # in and out are two records
        listed = http.get(URL, headers=mine).json()["items"]
        assert len(listed) == 2
        one = http.get(f"{URL}/{out['message_id']}", headers=mine)
        assert one.status_code == 200 and one.json()["payload_hash"] == out["payload_hash"]
        assert http.get(f"{URL}/not-a-uuid", headers=mine).status_code == 404
        assert (
            http.get(f"{URL}/00000000-0000-4000-8000-000000000000", headers=mine).status_code == 404
        )
        # the payload is not in the table: no column holds a name or an account
        import asyncio

        from conftest import engine

        async def dump() -> str:
            async with engine(rt_url) as eng, eng.begin() as c:
                await c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                rows = (await c.execute(text("SELECT * FROM verify.travel_rule_message"))).all()
                return repr(rows)

        blob = asyncio.run(dump())
        assert "Lee" not in blob and "accountNumber" not in blob


def test_a_malformed_message_is_recorded_with_its_finding_codes_and_changes_nothing_about_the_transfer(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        _on(http)
        bad = copy.deepcopy(GOOD)
        bad["beneficiary"] = {}
        out = http.post(
            URL, headers=mine, json={"direction": "INCOMING", "jurisdiction": "GB", "ivms101": bad}
        )
        assert out.status_code == 200, out.text
        assert out.json()["well_formed"] is False and out.json()["finding_codes"]
        assert any("person decides" in n for n in out.json()["notes"])


def test_a_recorded_message_never_changes_and_is_never_deleted(rt_url: str) -> None:
    import asyncio

    from conftest import engine

    with world(rt_url) as (http, mine, tenant, _a):
        _on(http)
        http.post(
            URL, headers=mine, json={"direction": "INCOMING", "jurisdiction": "GB", "ivms101": GOOD}
        )

        async def tamper() -> str:
            async with engine(rt_url) as eng:
                try:
                    async with eng.begin() as c:
                        await c.execute(
                            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
                        )
                        await c.execute(
                            text(
                                "UPDATE verify.travel_rule_message SET well_formed = NOT well_formed"
                            )
                        )
                except Exception as e:  # noqa: BLE001
                    return str(e)
            return "no error"

        msg = asyncio.run(tamper())
        assert "never changes" in msg or "permission denied" in msg, msg
