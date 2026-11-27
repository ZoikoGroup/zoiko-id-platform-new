"""The hosted applicant session over HTTP on PostgreSQL (ZID-CP-WF01 sections 05, 08, 10; Q01 to Q04, Q07, Q33, Q34, Q37 to Q39).

A business opens a session with a market; the person spends a one-use link for a session credential, reads the notice, chooses a method the server offered,
sends a photograph under a one-time value and submits once. The person sees a state, a reference and one safe message; the business reads the decision."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from conftest import ctx  # noqa: F401
from onboarding_fixture import onboard
from test_idv_auto_route_pg import GOOD, PRINTED, auto_entry
from test_idv_media_pg import READING, app_for, fake_imaging, photo, rows  # noqa: F401  (fake_imaging is autouse)
from test_v2_privacy_facades_pg import EXTRA, seed
from test_v2_privacy_pg import ACTIONS, RESOURCES, runtime  # noqa: F401  (runtime is autouse)

TOKEN = "X-Zoiko-Applicant-Token"
SCOPE = "verification:execute verification:read"


def hosted(api: Any, owner: dict[str, str], notice: str, **extra: Any) -> Any:
    body = {"purpose_id": "NEWSLETTER", "activity_code": "MARKETING_EMAIL", "data_category": "D1", "region": "GB", "market": "GB",
            "notice_version": notice, **extra}  # fmt: skip
    return api.post("/v2/verification-sessions", headers=owner, json=body)


def launched(api: Any, made: dict[str, Any]) -> tuple[str, dict[str, str]]:
    got = api.post("/v2/verification-sessions/launch", json={"launch_token": made["launch_token"]})
    assert got.status_code == 200, got.text
    return got.json()["session_ref"], {TOKEN: got.json()["access_token"]}


def test_a_person_completes_a_hosted_session_and_sees_only_a_state_a_reference_and_a_safe_message(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    notice, _ = seed(rt_url, o)
    owner = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = dict(PRINTED)
    with app_for(rt_url, o, auto_entry()) as api:
        made = hosted(api, owner, notice, return_url="http://localhost/back", return_state="abc")
        assert made.status_code == 201, made.text
        assert "#t=" in made.json()["launch_url"] and made.json()["mode"] == "sandbox"
        ref, me = launched(api, made.json())
        # Q02/Q37: the link works once
        again = api.post(
            "/v2/verification-sessions/launch", json={"launch_token": made.json()["launch_token"]}
        )
        assert again.status_code == 401 and again.json()["code"] == "token_already_used"

        ctx_ = api.get(f"/v2/verification-sessions/{ref}/public-context", headers=me).json()
        assert {m["method"] for m in ctx_["methods"]} == {"CHIP", "WEB_AUTO"}
        assert (
            next(m for m in ctx_["methods"] if m["method"] == "CHIP")["requires_handoff"] is True
        )  # no NFC here: offered only through a phone
        assert ctx_["status"]["state"] == "NOTICE_PRESENTED"

        # S02: no method before the notice, and never one the server did not offer
        early = api.post(
            f"/v2/verification-sessions/{ref}/method-select",
            headers=me,
            json={"method": "WEB_AUTO"},
        )
        assert early.status_code == 409 and early.json()["code"] == "notice_not_acknowledged"
        ack = api.post(
            f"/v2/verification-sessions/{ref}/notices",
            headers=me,
            json={"language": "en-GB", "consent": True},
        )
        assert ack.status_code == 200 and ack.json()["acknowledged"] is True, ack.text
        tampered = api.post(
            f"/v2/verification-sessions/{ref}/method-select",
            headers=me,
            json={"method": "SOMETHING_ELSE"},
        )
        assert tampered.status_code == 422 and tampered.json()["code"] == "unsupported_document"
        chosen = api.post(
            f"/v2/verification-sessions/{ref}/method-select",
            headers=me,
            json={"method": "WEB_AUTO"},
        )
        assert chosen.status_code == 200 and chosen.json()["state"] == "METHOD_SELECTED", (
            chosen.text
        )

        early_submit = api.post(f"/v2/verification-sessions/{ref}/submit", headers=me)
        assert early_submit.status_code == 409  # nothing to submit yet

        nonce = api.post(
            f"/v2/verification-sessions/{ref}/capture-intents",
            headers=me,
            json={"step": "DOCUMENT"},
        ).json()["nonce"]
        sent = api.post(
            f"/v2/verification-sessions/{ref}/evidence/document-image", headers=me,
            json={"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "captured_at": datetime.now(UTC).isoformat(), "declared": GOOD},
        )  # fmt: skip
        assert sent.status_code == 200, sent.text
        view = sent.json()
        assert (
            view["state"] == "CAPTURING" and view["final"] is False
        )  # captured is never shown as verified before submission
        for leaked in ("case_id", "reasons", "signals", "decision", "assertions"):
            assert (
                leaked not in view
            )  # Q34/Q35: no case, reason, signal or decision reaches the person

        done = api.post(f"/v2/verification-sessions/{ref}/submit", headers=me).json()
        assert done["state"] == "FINAL_APPROVED" and done["reference"].startswith("ZID-")
        assert done["return_url"] == "http://localhost/back" and done["return_state"] == "abc"
        assert (
            api.post(f"/v2/verification-sessions/{ref}/submit", headers=me).json() == done
        )  # Q33: a repeat changes nothing
        billed = rows(
            owner_url,
            "SELECT meter_id, transaction_type FROM commercial.billable_event WHERE deduplication_key = :k",
            k=f"idvsub:{ref}",
        )
        assert [tuple(r) for r in billed] == [
            ("IDV_SUBMISSION", "SUBMITTED")
        ]  # recorded once as usage, however often the person submits

        decided = api.get(f"/v2/verification-sessions/{ref}/decision", headers=owner).json()
        assert decided["state"] == "FINAL_APPROVED" and decided["decision"]["outcome"] == "APPROVED"
        assert len(decided["decision"]["decision_digest"]) == 64


def test_the_credential_is_bound_to_its_session_and_a_resume_revokes_every_earlier_one(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    notice, _ = seed(rt_url, o)
    owner = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, auto_entry()) as api:
        ref_a, me_a = launched(api, hosted(api, owner, notice).json())
        ref_b, _ = launched(api, hosted(api, owner, notice).json())
        assert (
            api.get(f"/v2/verification-sessions/{ref_b}/public-status", headers=me_a).status_code
            == 401
        )  # Q01: another session's credential
        assert api.get(f"/v2/verification-sessions/{ref_a}/public-status").status_code == 401
        forged = {TOKEN: me_a[TOKEN].split(".")[0] + "." + "x" * 43}
        assert (
            api.get(f"/v2/verification-sessions/{ref_a}/public-status", headers=forged).status_code
            == 401
        )

        resumed = api.post(f"/v2/verification-sessions/{ref_a}/resume-token", headers=owner)
        assert resumed.status_code == 201, resumed.text
        assert (
            api.get(f"/v2/verification-sessions/{ref_a}/public-status", headers=me_a).status_code
            == 401
        )  # S12: the earlier credential is gone
        ref, me = launched(api, resumed.json())
        assert (
            ref == ref_a
            and api.get(f"/v2/verification-sessions/{ref}/public-status", headers=me).status_code
            == 200
        )


def test_a_return_address_must_be_on_a_verified_domain_of_the_business(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    notice, _ = seed(rt_url, o)
    owner = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, auto_entry()) as api:
        for url in ("https://evil.example/back", "javascript:alert(1)", "https://user@localhost/x"):
            refused = hosted(api, owner, notice, return_url=url)
            assert (
                refused.status_code == 422 and refused.json()["code"] == "return_url_not_allowed"
            ), (url, refused.text)


def test_the_person_can_leave_and_nothing_more_is_taken(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    notice, _ = seed(rt_url, o)
    owner = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, auto_entry()) as api:
        ref, me = launched(api, hosted(api, owner, notice).json())
        api.post(
            f"/v2/verification-sessions/{ref}/notices",
            headers=me,
            json={"language": "en", "consent": True},
        )
        api.post(
            f"/v2/verification-sessions/{ref}/method-select",
            headers=me,
            json={"method": "WEB_AUTO"},
        )
        left = api.post(f"/v2/verification-sessions/{ref}/applicant-cancel", headers=me)
        assert left.status_code == 200 and left.json()["state"] == "CANCELED", left.text
        refused = api.post(
            f"/v2/verification-sessions/{ref}/capture-intents",
            headers=me,
            json={"step": "DOCUMENT"},
        )
        assert refused.status_code == 409
