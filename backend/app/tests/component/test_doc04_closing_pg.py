"""Document 04 closing checks over HTTP against PostgreSQL: AT-005 (no document identifiers in logs, events or stored results), AT-030 (an independent
review override leaves a notification event and a rationale on the record), a positive decision by an independent reviewer on a rights request,
and AT-028/AT-029 (no route anywhere returns raw biometric media or a template, so no role can obtain one through any API)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from test_v2_events_pg import events
from test_v2_facade_pg import world
from test_v2_privacy_facades_pg import EXTRA, seed
from test_v2_privacy_pg import ACTIONS, RESOURCES, api_for, runtime  # noqa: F401  (runtime is an autouse fixture)
from test_v2_uploads_pg import announce, put
from zid_db import tenant_transaction

MARKER = "PASSPORT-9XK3921"
PDF = (
    f"%PDF-1.4\n% holder document number {MARKER}\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    "trailer\n<< /Root 1 0 R >>\n%%EOF\n"
).encode()


def test_at005_no_document_identifier_reaches_a_log_an_event_or_a_stored_result(
    rt_url: str, caplog, capfd
) -> None:
    caplog.set_level(logging.DEBUG)
    with world(rt_url) as (http, mine, tenant, _admin):
        a = announce(http, mine, PDF, "application/pdf").json()
        assert put(http, mine, a, PDF).status_code == 201
        ref = a["evidence_ref"]
        v = http.post(
            "/v2/documents/validate",
            headers=mine,
            json={
                "evidence_ref": ref,
                "declared_type": "application/pdf",
                "checks": ["integrity", "analysis"],
            },
        )
        assert v.status_code == 200, v.text
        m = http.post(
            "/v2/mandates/verify",
            headers=mine,
            json={
                "organization_ref": "o",
                "subject_ref": "s",
                "authority_claim": "sign",
                "mandate_evidence_refs": [ref],
            },
        )
        assert m.status_code == 200
        assert http.delete(f"/v2/evidence/{ref}", headers=mine).status_code == 200

        out = capfd.readouterr()
        assert MARKER not in caplog.text and MARKER not in out.out and MARKER not in out.err, (
            "a log carried the identifier"
        )
        assert MARKER not in v.text and MARKER not in m.text, "an answer repeated the identifier"

        async def stored() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                parts = []
                for sql in (
                    "SELECT payload::text FROM events.outbox_event",
                    "SELECT results::text FROM evidence.document_validation",
                    "SELECT coalesce(content::text, '') FROM evidence.upload",
                ):
                    parts += list((await c.execute(text(sql))).scalars().all())
                return "\n".join(parts)

        assert MARKER not in asyncio.run(stored()), (
            "an event, a stored result or the stored bytes held the identifier in the clear"
        )


def test_at030_an_independent_review_override_is_notified_and_keeps_its_rationale(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with api_for(rt_url, o) as api:
        ap = api.post(
            "/v2/decisions/dec_fraud_1/appeals",
            headers=owner,
            json={"statement": "These two accounts are not linked."},
        )
        assert ap.status_code == 201, ap.text
        rid = ap.json()["review_id"]
        assert events(rt_url, o.tenant, "privacy.review.decided") == [], (
            "nothing is announced while the review is open"
        )
        # the requester cannot override their own review, and a decision with no rationale is refused
        assert (
            api.post(
                f"/v2/privacy/reviews/{rid}/decisions",
                headers=owner,
                json={"outcome": "OVERTURNED", "rationale": "mine"},
            ).status_code
            == 403
        )
        assert (
            api.post(
                f"/v2/privacy/reviews/{rid}/decisions",
                headers=second,
                json={"outcome": "OVERTURNED", "rationale": ""},
            ).status_code
            == 422
        )
        done = api.post(
            f"/v2/privacy/reviews/{rid}/decisions",
            headers=second,
            json={
                "outcome": "OVERTURNED",
                "rationale": "Checked provenance: different devices, different people.",
            },
        )
        assert done.status_code == 200 and done.json()["state"] == "OVERTURNED", done.text
        sent = events(rt_url, o.tenant, "privacy.review.decided")
        assert len(sent) == 1
        data = sent[0].get("data", sent[0])
        assert (
            data["review_id"] == rid
            and data["outcome"] == "OVERTURNED"
            and data["reviewer_is_independent"] is True
        )
        assert "provenance" not in json.dumps(sent), (
            "the rationale is on the record, never in the event"
        )
        assert (
            api.get(f"/v2/privacy/reviews/{rid}", headers=owner)
            .json()["rationale"]
            .startswith("Checked provenance")
        )


def test_an_independent_reviewer_can_decide_a_rights_request_the_requester_cannot(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, *EXTRA))
    seed(rt_url, o)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    subject = str(uuid.uuid4())  # a person who is neither the requester nor the reviewer
    with api_for(rt_url, o) as api:
        mk = {
            "right": "ACCESS",
            "subject_ref": f"sub_{subject}",
            "jurisdiction": "uk-gdpr",
            "represented_by": "agent:acme",
        }
        rid = api.post("/v2/privacy/requests", headers=owner, json=mk).json()["request_id"]
        deny = {"rule": "ART_15_4", "evidence": ["case-note-7"], "action": "DENY"}
        assert (
            api.post(
                f"/v2/privacy/requests/{rid}/review-decisions", headers=owner, json=deny
            ).status_code
            == 403
        )
        done = api.post(f"/v2/privacy/requests/{rid}/review-decisions", headers=second, json=deny)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "DENIED"
        again = api.post(f"/v2/privacy/requests/{rid}/review-decisions", headers=second, json=deny)
        assert again.status_code in (409, 422), "a decided request is not decided twice"


def test_at028_and_at029_no_route_returns_raw_biometric_media_or_a_template() -> None:
    """There is no raw-media or template route to ask a step-up or a role for: the check is that none is published, in any contract."""
    root = Path(__file__).resolve().parents[4] / "contracts" / "openapi"
    bad_words = (
        "raw",
        "template",
        "embedding",
        "selfie",
        "liveness-frame",
        "face-image",
        "biometric-media",
    )
    media = ("image/", "video/", "audio/", "application/octet-stream")
    found = []
    for f in root.rglob("*.json"):
        spec = json.loads(f.read_text(encoding="utf-8"))
        for path, item in spec.get("paths", {}).items():
            if any(
                w in re.split(r"[^a-z0-9]+", path.lower()) or w in path.lower()
                for w in bad_words
                if w != "raw"
            ) or "raw" in re.split(r"[^a-z0-9]+", path.lower()):
                found.append(f"{f.name}: {path}")
            for method in item.values():
                if not isinstance(method, dict):
                    continue
                for resp in method.get("responses", {}).values():
                    for ctype in resp.get("content") or {}:
                        if ctype.startswith(media):
                            found.append(f"{f.name}: {path} returns {ctype}")
    assert found == [], f"a published route can return raw media or a template: {found}"
