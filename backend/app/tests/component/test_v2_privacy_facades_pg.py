"""/v2 verification sessions, rights, consent, retention and holds over HTTP against PostgreSQL (ZID-PRI-004 sections 07, 11, 13; AT-001 to AT-003, AT-012, AT-020 to AT-022, AT-024, AT-027)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from onboarding_fixture import onboard
from test_privacy_pg import governed_activity
from test_v2_privacy_pg import ACTIONS, RESOURCES, api_for, code, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as privacy

EXTRA = ("rights_request:*", "rights_request:new", "retention_policy:*", "retention_evaluation:new")


def seed(rt_url: str, o: Any) -> tuple[str, str]:
    """A consent-based activity, a published notice, a short retention policy, one due object and one hold covering profiles."""

    async def go() -> tuple[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            aid = await governed_activity(c, o.tenant, o.owner, o.second)
            await privacy.assign_role(
                c, o.tenant, aid, data_controller_principal_id=o.tenant, zoiko_role="PROCESSOR",
                jurisdiction_pack_id="uk-gdpr", approval_ref="COUNSEL-1", created_by=o.owner,
                approved_by=o.second, contract_or_dpa_ref="DPA-7",
            )  # fmt: skip
            await privacy.activate_activity(c, o.tenant, aid, actor=o.owner)
            await registry.publish_notice(
                c, o.tenant, code="NEWS_NOTICE", audience="SUBJECT", content="We send product news.",
                created_by=o.owner, approved_by=o.second,
            )  # fmt: skip
            await privacy.create_retention_policy(
                c, o.tenant, code="SHORT", object_type="profile", jurisdiction_pack_id="uk-gdpr",
                trigger="CREATED_AT", retention_days=0, action="DELETE", created_by=o.owner,
                approved_by=o.second,
            )  # fmt: skip
            past = datetime.now(UTC) - timedelta(days=1)
            for ref in ("due-1", "held-1"):
                await privacy.register_personal_data(
                    c, o.tenant, subject_principal_id=o.second, store_name="dsr.records",
                    object_type="profile" if ref == "due-1" else "registry_extract", object_ref=ref,
                    purpose_code="NEWSLETTER", data_category="D1", retention_policy_code="SHORT",
                    trigger_at=past,
                )  # fmt: skip
            hold = await vault.create_hold(
                c, o.tenant, basis="litigation", reference="CASE-9", requested_by=o.owner,
                approved_by=o.second, object_types=["registry_extract"],
                review_at=datetime.now(UTC) + timedelta(days=30),
            )  # fmt: skip
            return "NEWS_NOTICE@1", hold.legal_hold_id

    return asyncio.run(go())


def test_sessions_enable_capture_only_while_every_prerequisite_holds(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    notice, _hold = seed(rt_url, o)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    body = {
        "purpose_id": "NEWSLETTER", "activity_code": "MARKETING_EMAIL", "data_category": "D1",
        "region": "GB", "subject_ref": f"sub_{o.owner}", "assurance_goal": "IAL2",
        "notice_version": notice,
    }  # fmt: skip
    with api_for(rt_url, o) as api:
        assert api.post("/v2/verification-sessions", json=body).status_code == 401

        # AT-001 / AT-002: no consent and no acknowledged notice: capture is not enabled, and the reasons are named
        made = api.post("/v2/verification-sessions", headers=owner, json=body)
        assert made.status_code == 201, made.text
        s = made.json()
        ref = s["session_ref"]
        assert s["capture_enabled"] is False and "NOTICE_NOT_ACKNOWLEDGED" in s["blockers"]
        assert len(s["blockers"]) >= 2 and s["alternatives_available"] is True
        for banned in ("score", "template", "embedding"):
            assert banned not in made.text.lower()

        ack = api.post(
            f"/v2/verification-sessions/{ref}/notices/acknowledgments",
            headers=owner, json={"notice_version": notice, "language": "en"},
        )  # fmt: skip
        assert ack.status_code == 201
        s = api.get(f"/v2/verification-sessions/{ref}/subject-status", headers=owner).json()
        assert "NOTICE_NOT_ACKNOWLEDGED" not in s["blockers"] and s["capture_enabled"] is False

        # only the person consents for themselves
        other = api.post(
            f"/v2/verification-sessions/{ref}/consent-records", headers=second,
            json={"notice_version": notice, "capture_method": "WEB", "affirmative_action": "ticked box"},
        )  # fmt: skip
        assert other.status_code == 403 and code(other) == "consent_is_the_subjects_own"
        given = api.post(
            f"/v2/verification-sessions/{ref}/consent-records", headers=owner,
            json={"notice_version": notice, "capture_method": "WEB", "affirmative_action": "ticked box"},
        )  # fmt: skip
        assert given.status_code == 201, given.text
        assert given.json()["session"]["capture_enabled"] is True, given.json()["session"][
            "blockers"
        ]

        # AT-003: consent withdrawn mid-session: no new capture, the status moves at once
        gone = api.post(
            f"/v2/privacy/consent-records/{given.json()['consent_id']}:withdraw", headers=owner,
            json={"reason": "changed my mind"},
        )  # fmt: skip
        assert gone.status_code == 200 and gone.json()["status"] != "ACTIVE"
        s = api.get(f"/v2/verification-sessions/{ref}/subject-status", headers=owner).json()
        assert s["capture_enabled"] is False and s["blockers"], s

        # AT-012: choosing the non-biometric route is never a failure and never a loop
        alt = api.post(
            "/v2/verification-sessions", headers=owner,
            json={**body, "alternative_mode": "ASSISTED_DOCUMENT_REVIEW"},
        ).json()  # fmt: skip
        assert alt["route"] == "ALTERNATIVE" and alt["capture_enabled"] is False
        assert "NON_BIOMETRIC_ALTERNATIVE_CHOSEN" in alt["blockers"]
        assert "never counts against you" in alt["statement"] and "fraud" in alt["statement"]

        # cancelling stops everything, and a cancelled session takes no consent
        c = api.post(f"/v2/verification-sessions/{ref}/cancel", headers=owner).json()
        assert c["state"] == "CANCELED" and "SESSION_CANCELED" in c["blockers"]
        late = api.post(
            f"/v2/verification-sessions/{ref}/consent-records", headers=owner,
            json={"notice_version": notice, "capture_method": "WEB", "affirmative_action": "ticked box"},
        )  # fmt: skip
        assert late.status_code == 422 or late.status_code == 409
        assert (
            api.get("/v2/verification-sessions/vs_nope/subject-status", headers=owner).status_code
            == 404
        )


def test_rights_requests_need_proof_a_different_decider_and_never_leak_to_others(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    seed(rt_url, o)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with api_for(rt_url, o) as api:
        mk = {
            "right": "ACCESS",
            "subject_ref": f"sub_{o.second}",
            "jurisdiction": "uk-gdpr",
            "represented_by": "agent:acme",
        }
        assert api.post("/v2/privacy/requests", json=mk).status_code == 401
        opened = api.post("/v2/privacy/requests", headers=owner, json=mk)
        assert opened.status_code == 201, opened.text
        rid = opened.json()["request_id"]

        # AT-021: a representative's request waits for proof of authority
        assert opened.json()["identity_verification_state"] != "VERIFIED"
        st = api.get(f"/v2/privacy/requests/{rid}/status", headers=owner)
        assert st.status_code == 200 and st.json()["request_id"] == rid
        proof = api.post(
            f"/v2/privacy/requests/{rid}/proof-of-authority", headers=owner,
            json={"verified": True, "level": 2, "method": "signed_mandate"},
        )  # fmt: skip
        assert (
            proof.status_code == 200 and proof.json()["identity_verification_state"] == "VERIFIED"
        )

        # the requester, and the person the data is about, never decide the request
        d = {"rule": "ART_15", "evidence": [], "action": "FULFIL"}
        assert (
            api.post(
                f"/v2/privacy/requests/{rid}/review-decisions", headers=owner, json=d
            ).status_code
            == 403
        )
        assert (
            api.post(
                f"/v2/privacy/requests/{rid}/review-decisions", headers=second, json=d
            ).status_code
            == 403
        )
        # a refusal names its evidence
        deny = api.post(
            f"/v2/privacy/requests/{rid}/review-decisions", headers=owner,
            json={"rule": "ART_15_4", "evidence": [], "action": "DENY"},
        )  # fmt: skip
        assert deny.status_code == 422 and code(deny) == "evidence_required"

        # the export is the person's own and needs fresh strong authentication; another person is refused
        assert api.get(f"/v2/privacy/requests/{rid}/export", headers=owner).status_code in (
            403,
            404,
        )
        assert (
            api.get(f"/v2/privacy/requests/{uuid.uuid4()}/status", headers=owner).status_code == 404
        )
        # the processor stage cannot be acknowledged when there is none pending
        task = api.post(
            f"/v2/privacy/requests/{rid}/processor-tasks", headers=owner,
            json={"item_id": "nope", "ack_ref": "ACK-1"},
        )  # fmt: skip
        assert task.status_code in (409, 422)


def test_retention_policy_evaluation_and_holds_never_change_anything(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    _notice, hold_id = seed(rt_url, o)
    owner = o.bearer(o.owner)
    with api_for(rt_url, o) as api:
        pol = api.get("/v2/privacy/retention-policies/SHORT?version=1", headers=owner)
        assert pol.status_code == 200, pol.text
        assert pol.json()["retention_days"] == 0 and "not a legal assertion" in pol.json()["note"]
        assert api.get("/v2/privacy/retention-policies/NOPE", headers=owner).status_code == 404

        # AT-027: a due object is reported DUE, and the evaluation deletes nothing
        due = api.post(
            "/v2/privacy/retention-evaluations", headers=owner,
            json={"object_ref": "due-1", "purpose_id": "NEWSLETTER"},
        )  # fmt: skip
        assert due.status_code == 200 and due.json()["outcome"] == "DUE", due.text
        again = api.post(
            "/v2/privacy/retention-evaluations", headers=owner,
            json={"object_ref": "due-1", "purpose_id": "NEWSLETTER"},
        ).json()  # fmt: skip
        assert again["outcome"] == "DUE", "evaluating twice changes nothing"

        # AT-024: an object a hold covers is HELD, not due
        held = api.post(
            "/v2/privacy/retention-evaluations", headers=owner,
            json={"object_ref": "held-1", "purpose_id": "NEWSLETTER", "legal_hold_refs": [hold_id]},
        ).json()  # fmt: skip
        assert held["outcome"] == "HELD" and held["active_holds_named"] == [hold_id]
        unknown = api.post(
            "/v2/privacy/retention-evaluations", headers=owner,
            json={"object_ref": "gone", "purpose_id": "NEWSLETTER"},
        )  # fmt: skip
        assert unknown.status_code == 404

        # the /v2 hold route is the one hold model: without a second administrator's approval it creates nothing
        no = api.post(
            "/v2/privacy/holds", headers=owner,
            json={
                "authorization": str(uuid.uuid4()), "basis": "litigation", "reference": "CASE-10",
                "scope": {"object_types": ["profile"]},
                "review_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )  # fmt: skip
        assert no.status_code >= 400, no.text
