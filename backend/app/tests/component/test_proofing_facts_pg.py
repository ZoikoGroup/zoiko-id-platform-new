"""WF01 result facts and events over HTTP against PostgreSQL (ZID-DTV-WF-01 sections 14 and 15; CR-107).

T05  a chip nobody can judge is announced as INDETERMINATE (never a pass), and nothing is completed
     a verified session announces exactly the facts that hold, and the read route says the same in WF01's words
     a chip alone never holds IDENTITY_VERIFIED_FOR_SCOPE for a profile that needs the holder bound
"""

from __future__ import annotations

import asyncio
import uuid

from conftest import ctx, engine
from epassport_fixture import make_chip
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_self_run_session_pg import ISS, chip_body, new_profile, start, world
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application.trust import TrustConfig


def events(rt_url: str, tenant_of: dict[str, str], vid: str) -> list[tuple[str, dict]]:
    async def go() -> list[tuple[str, dict]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant_of["tenant"])) as c:
            rows = await c.execute(
                text(
                    "SELECT event_type, payload FROM events.outbox_event "
                    "WHERE aggregate_type = 'verification_proofing' AND aggregate_id = :i ORDER BY aggregate_version"
                ),
                {"i": vid},
            )
            return [(r[0], dict(r[1]).get("data", dict(r[1]))) for r in rows]

    return asyncio.run(go())


def test_an_unjudgeable_chip_is_announced_as_indeterminate_and_a_verified_session_announces_its_facts(
    rt_url: str,
) -> None:
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    tenant = _tenant_of(adm, svc)
    with TestClient(app, base_url=ISS) as http:
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]

        # no country signing CA: the chip cannot be judged. The session waits, and the announcement says so.
        waiting = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        )
        assert waiting.status_code == 200 and waiting.json()["status"] == "IN_PROGRESS", (
            waiting.text
        )
        got = events(rt_url, tenant, vid)
        assert [e[0] for e in got] == ["com.zoiko.id.verification.proofing.indeterminate.v1"], got
        data = got[0][1]
        assert (
            data["failure_class"] == "TRUST_UNAVAILABLE" and data["missing_method"] == "chip_trust"
        )
        assert data["retriable"] is False and data["next_action"] == "USE_APPROVED_ALTERNATIVE"
        assert data["profile_id"] == pid and len(data["policy_hash"]) == 64
        early = http.get(f"/verifications/{vid}/proofing", headers=svc).json()
        assert early["assertions"] == [] and early["decision"] != "verified_for_scope"

        # with the country's CA configured the chip is judged: completed, with exactly the facts that hold
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        done = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        )
        assert done.status_code == 200 and done.json()["status"] == "VERIFIED", done.text
        names = [e[0] for e in events(rt_url, tenant, vid)]
        assert names == [
            "com.zoiko.id.verification.proofing.indeterminate.v1",
            "com.zoiko.id.verification.proofing.completed.v1",
        ]
        completed = events(rt_url, tenant, vid)[1][1]
        assert set(completed["assurance_facts"]) == {
            "DOCUMENT_COLLECTED", "DOCUMENT_VALIDATED", "DOCUMENT_CHIP_CRYPTO_VALID", "IDENTITY_VERIFIED_FOR_SCOPE",
        }  # fmt: skip
        assert (
            "PRESENCE_CONFIRMED" not in completed["assurance_facts"]
            and "FACE_MATCH_CONFIRMED" not in completed["assurance_facts"]
        )
        assert (
            completed["scope"] == "onboarding"
            and completed["region"] == "GB"
            and completed["provider_refs"] == ["self-run"]
        )
        assert len(completed["evidence_hashes"]) == 1 and completed["expires_at"]
        assert "ERIKSSON" not in str(completed)  # references and facts only: no claim value

        view = http.get(f"/verifications/{vid}/proofing", headers=svc)
        assert view.status_code == 200, view.text
        body = view.json()
        assert body["state"] == "VERIFIED_FOR_SCOPE" and body["decision"] == "verified_for_scope"
        assert body["policy"]["profile_id"] == pid and body["scope"] == "onboarding"
        results = {c["type"]: c["result"] for c in body["checks"]}
        assert results["chip_crypto"] == "pass" and results["document_validity"] == "pass"
        assert (
            results["face_match"] == "not_requested" and results["liveness_pad"] == "not_requested"
        )
        assert body["not_derived_here"] == ["COMPANY_DOCS_REVIEWED", "COMPANY_REGISTRY_MATCHED"]
        assert "ERIKSSON" not in view.text and "value_commitment" not in view.text

        # another tenant's caller cannot read it
        assert http.get(f"/verifications/{uuid.uuid4()}/proofing", headers=svc).status_code == 404


def test_a_chip_alone_does_not_hold_the_scope_of_a_profile_that_needs_the_holder_bound(
    rt_url: str,
) -> None:
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    tenant = _tenant_of(adm, svc)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=True)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        out = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        ).json()
        assert out["status"] != "VERIFIED", out
        assert not [
            e for e in events(rt_url, tenant, vid) if e[0].endswith("proofing.completed.v1")
        ]
        body = http.get(f"/verifications/{vid}/proofing", headers=svc).json()
        assert (
            "IDENTITY_VERIFIED_FOR_SCOPE" not in body["assertions"]
            and body["decision"] != "verified_for_scope"
        )
        assert (
            "DOCUMENT_CHIP_CRYPTO_VALID" in body["assertions"]
            and "FACE_MATCH_CONFIRMED" not in body["assertions"]
        )
        results = {c["type"]: c["result"] for c in body["checks"]}
        assert (
            results["face_match"] == "indeterminate"
        )  # the holder was never bound, and that is said, not hidden


def _tenant_of(adm: dict[str, str], svc: dict[str, str]) -> dict[str, str]:
    """The tenant id is inside the bearer token the world helper issued."""
    import base64
    import json

    token = svc["Authorization"].split()[1]
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    return {"tenant": claims["tenant_id"]}


def test_a_profile_can_cap_attempts_and_the_usage_report_counts_without_naming_anyone(
    rt_url: str,
) -> None:
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        body = {
            "name": f"capped-{uuid.uuid4().hex[:6]}",
            "level": "IAL-Z1",
            "requirements": {
                "required_claims": ["legal_name", "birthdate"], "accepted_evidence": ["GOVERNMENT_ID"], "binding_required": False,
                "liveness_required": False, "biometric_jurisdictions": [], "jurisdictions": ["GB"], "freshness_days": 365,
                "manual_review": False, "review_approvals": 1, "reliance_purposes": ["onboarding"], "max_attempts": 2,
            },
        }  # fmt: skip
        made = http.post("/verification-profiles", headers=adm, json=body)
        assert made.status_code == 201, made.text
        pid = made.json()["profile_id"]
        assert http.post(f"/verification-profiles/{pid}:activate", headers=adm).status_code == 200

        assert start(http, svc, pid, alice).status_code == 201
        assert start(http, svc, pid, alice).status_code == 201
        third = start(http, svc, pid, alice)
        assert (
            third.status_code == 429 and third.json()["code"] == "verification_attempts_exceeded"
        ), third.text

        report = http.get("/verification-usage", headers=svc)
        assert report.status_code == 200, report.text
        out = report.json()
        assert out["total_sessions"] == 2 and out["repeat_attempts"] == 1
        assert (
            out["failover_sessions"] == 0 and out["failover_verified"] == 0
        )  # a self-run session never moves between providers
        assert (
            out["sessions"][0]["adapter_id"] == "self-run" and out["sessions"][0]["sessions"] == 2
        )
        assert alice not in report.text  # counts only: no person is named

        # a bad window is refused, and a cap that is not a small positive whole number is refused when the profile is written
        assert (
            http.get("/verification-usage?since=2026-01-01T00:00:00", headers=svc).status_code
            == 422
        )
        for bad in (0, 21, True, "3", 1.5):
            again = http.post(
                "/verification-profiles",
                headers=adm,
                json={
                    **body,
                    "name": f"bad-{uuid.uuid4().hex[:6]}",
                    "requirements": {**body["requirements"], "max_attempts": bad},
                },
            )
            assert again.status_code == 422, (bad, again.text)


def test_a_policy_changed_during_a_session_never_downgrades_it(rt_url: str) -> None:
    """WF01 T24: the session keeps the profile it started under. A weaker profile activated afterwards does not rescue a chip that could not
    satisfy the stronger one, and the facts name the policy that was actually applied."""
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        strong = new_profile(http, adm, binding=True)
        vid = start(http, svc, strong, alice).json()["verification_id"]

        weak = new_profile(
            http, adm, binding=False
        )  # activated after the session began: it would verify a chip alone
        assert weak != strong
        out = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        ).json()
        assert out["status"] != "VERIFIED", out
        facts = http.get(f"/verifications/{vid}/proofing", headers=svc).json()
        assert (
            facts["policy"]["profile_id"] == strong
            and "IDENTITY_VERIFIED_FOR_SCOPE" not in facts["assertions"]
        )

        # a new session under the weak profile is its own session, with its own pinned policy
        other = start(http, svc, weak, alice).json()["verification_id"]
        done = http.post(
            f"/v2/self-run/verifications/{other}/chip", headers=svc, json=chip_body(chip)
        ).json()
        assert done["status"] == "VERIFIED"
        assert (
            http.get(f"/verifications/{other}/proofing", headers=svc).json()["policy"]["profile_id"]
            == weak
        )


def test_a_monthly_budget_stops_new_verifications_at_the_limit_and_clearing_it_lifts_it(
    rt_url: str,
) -> None:
    """WF01-014: no budget means no check; a limit of zero starts nothing; money is never a float; the report is totals only."""
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        pid = new_profile(http, adm, binding=False)
        none = http.get("/verification-budget", headers=svc)
        assert none.status_code == 200 and none.json()["budget"] is None
        assert start(http, svc, pid, alice).status_code == 201  # no budget row, no check

        zero = http.put(
            "/verification-budget", headers=adm, json={"monthly_limit": "0", "currency": "EUR"}
        )
        assert zero.status_code == 200, zero.text
        assert zero.json()["budget"] == {"monthly_limit": "0", "currency": "EUR", "version": 1}
        assert zero.json()["exceeded"] is True and zero.json()["remaining"] == "0"
        blocked = start(http, svc, pid, alice)
        assert (
            blocked.status_code == 429 and blocked.json()["code"] == "verification_budget_exceeded"
        ), blocked.text

        raised = http.put(
            "/verification-budget", headers=adm, json={"monthly_limit": "100.50", "currency": "EUR"}
        )
        body = raised.json()
        assert (
            raised.status_code == 200
            and body["budget"]["version"] == 2
            and body["budget"]["monthly_limit"] == "100.5"
        )
        assert (
            body["spent"] == "0"
            and body["remaining"] == "100.5"
            and body["exceeded"] is False
            and body["other_currencies"] == []
        )
        assert start(http, svc, pid, alice).status_code == 201
        assert alice not in raised.text  # totals only

        for bad in (
            {"monthly_limit": 1.5, "currency": "EUR"},
            {"monthly_limit": "abc", "currency": "EUR"},
            {"monthly_limit": "-1", "currency": "EUR"},
            {"monthly_limit": "10", "currency": "eur"},
            {"monthly_limit": "10"},
        ):
            assert http.put("/verification-budget", headers=adm, json=bad).status_code == 422, bad
        # the service token reads but cannot set a budget: that is a protected administrator action
        assert (
            http.put(
                "/verification-budget", headers=svc, json={"monthly_limit": "5", "currency": "EUR"}
            ).status_code
            == 403
        )

        cleared = http.delete("/verification-budget", headers=adm)
        assert cleared.status_code == 200 and cleared.json()["budget"] is None
        assert start(http, svc, pid, alice).status_code == 201
