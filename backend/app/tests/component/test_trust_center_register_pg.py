"""The Trust Center's claims register over HTTP, operator plane (ZID-CP-WF12 WF-12.09 to 12.11; CR-174), against PostgreSQL with real operator sessions.

The author drafts, two people in two roles approve the exact hash, a third publishes through the gate, one responder withdraws at once, and the database refuses what the code
should never attempt: the author's own approval, a publication with one approver, a withdrawn revision coming back, an edited revision."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
TODAY = date.today()
NAMES = (
    "author",
    "evidence_mgr",
    "evidence_checker",
    "sec",
    "legal",
    "product",
    "publisher",
    "responder",
    "stranger",
    "other_author",
)


def uid() -> str:
    return str(uuid.uuid4())


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    who = {n: uid() for n in NAMES} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in NAMES}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _app(rt_url: str, o: Operators) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL, operator_tenant_id=o.tenant, operator_client_ids=(o.client,)),
        keyring=o.ring,
    )  # fmt: skip


def _grant(
    api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str
) -> None:
    approvers = (
        (who["grant_1"], who["grant_2"])
        if CAPABILITIES[capability].two_person
        else (who["grant_1"],)
    )
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _sql(owner_url: str, stmt: str, **params: Any) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            r = await c.execute(text(stmt), params)
            return [dict(x._mapping) for x in r.all()] if r.returns_rows else []

    return asyncio.run(go())


def _evidence_body(digest: str, **kw: Any) -> dict[str, Any]:
    return {
        "digest": digest, "evidence_type": "report", "scheme": "SOC 2", "issuer": "Auditor LLP", "assessor": "Auditor LLP", "legal_entity": "entity_demo",
        "products": ["verify_api"], "regions": ["US"], "period_start": str(TODAY - timedelta(days=30)), "period_end": str(TODAY + timedelta(days=300)), "holder": "zoiko",
    } | kw  # fmt: skip


def _claim_body(
    digest: str, text_: str = "An independent SOC 2 report is available on request", **kw: Any
) -> dict[str, Any]:
    return {
        "exact_text": text_, "locale": "en-US", "claim_type": "third_party_assessment", "entity_id": "entity_demo", "service": "verify_api", "markets": ["US"],
        "channels": ["trust_center"], "evidence_digests": [digest], "valid_until": str(TODAY + timedelta(days=200)),
    } | kw  # fmt: skip


def test_a_claim_is_drafted_approved_by_two_roles_published_superseded_withdrawn_and_never_edited(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    digest = hashlib.sha256(b"soc2-report").hexdigest()
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        assert api.get("/v1/trust-center/claims").status_code == 401
        assert call("GET", "/v1/trust-center/claims", "stranger").status_code == 403
        denied = call("POST", "/v1/trust-center/claims", "author", json=_claim_body(digest))
        assert (
            denied.status_code == 403
            and denied.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"
        )

        for person, cap in (
            ("author", "trust.claim.author"), ("evidence_mgr", "trust.evidence.manage"), ("evidence_checker", "trust.evidence.manage"),
            ("sec", "trust.claim.review_security"), ("legal", "trust.claim.review_legal"), ("product", "trust.claim.review_product"),
            ("publisher", "trust.claim.publish"), ("responder", "trust.claim.withdraw"),
        ):  # fmt: skip
            _grant(api, o, who, person, cap)

        # evidence: a SOC 2 labelled a certificate is refused; unconfirmed evidence blocks publication; the person who recorded it cannot confirm it
        wrong = call(
            "POST",
            "/v1/trust-center/evidence",
            "evidence_mgr",
            json=_evidence_body(digest, evidence_type="certificate"),
        )
        assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "SCHEME_TYPE_MISMATCH"
        ev = call("POST", "/v1/trust-center/evidence", "evidence_mgr", json=_evidence_body(digest))
        assert ev.status_code == 201, ev.text
        ev_id = ev.json()["evidence_id"]
        assert ev.json()["issuer_confirmed"] is False

        claim = call("POST", "/v1/trust-center/claims", "author", json=_claim_body(digest))
        assert claim.status_code == 201, claim.text
        c1 = claim.json()
        cid, h1 = c1["claim_id"], c1["content_hash"]
        assert c1["status"] == "APPROVAL_PENDING" and c1["revision"] == 1
        assert {b["code"] for b in c1["blockers"]} >= {
            "EVIDENCE_UNVERIFIED",
            "APPROVAL_INCOMPLETE",
        } and c1["ready_to_publish"] is False

        self_confirm = call("POST", f"/v1/trust-center/evidence/{ev_id}:confirm", "evidence_mgr")
        assert (
            self_confirm.status_code == 409
            and self_confirm.json()["error"]["code"] == "TRUST_CENTER_REFUSED"
        )  # the database refuses the recorder confirming
        assert (
            call(
                "POST", f"/v1/trust-center/evidence/{ev_id}:confirm", "evidence_checker"
            ).status_code
            == 200
        )

        base = f"/v1/trust-center/claims/{cid}/revisions/1"
        # no approval yet: the gate refuses, and says why
        early = call("POST", f"{base}:publish", "publisher", json={"expected_hash": h1})
        assert (
            early.status_code == 409
            and early.json()["error"]["code"] == "PUBLISH_BLOCKED"
            and "APPROVAL_INCOMPLETE" in early.json()["error"]["message"]
        )

        # a reviewer needs the capability for the role they claim
        assert (
            call(
                "POST",
                f"{base}:approve",
                "sec",
                json={"role": "legal", "evidence_digests": [digest]},
            ).status_code
            == 403
        )
        # approving other evidence than the claim names is refused
        diff = call(
            "POST",
            f"{base}:approve",
            "sec",
            json={"role": "security", "evidence_digests": [hashlib.sha256(b"other").hexdigest()]},
        )
        assert diff.status_code == 409 and diff.json()["error"]["code"] == "EVIDENCE_DIFFERS"
        # a role the class does not need
        assert (
            call(
                "POST",
                f"{base}:approve",
                "product",
                json={"role": "product", "evidence_digests": [digest]},
            ).status_code
            == 422
        )

        one = call(
            "POST",
            f"{base}:approve",
            "sec",
            json={"role": "security", "evidence_digests": [digest]},
        )
        assert one.status_code == 200 and "legal" in one.json()["still_needed"][0]
        assert call(
            "POST",
            f"{base}:approve",
            "sec",
            json={"role": "security", "evidence_digests": [digest]},
        ).status_code in (409,)  # twice
        one_pub = call("POST", f"{base}:publish", "publisher", json={"expected_hash": h1})
        assert one_pub.status_code == 409  # one approval is not enough
        assert (
            call(
                "POST",
                f"{base}:approve",
                "legal",
                json={"role": "legal", "evidence_digests": [digest]},
            ).status_code
            == 200
        )

        # a tampered bundle: the hash does not match what was approved
        tampered = call("POST", f"{base}:publish", "publisher", json={"expected_hash": "0" * 64})
        assert (
            tampered.status_code == 409 and "BUNDLE_TAMPERED" in tampered.json()["error"]["message"]
        )
        live = call("POST", f"{base}:publish", "publisher", json={"expected_hash": h1})
        assert live.status_code == 200, live.text
        assert live.json()["status"] == "PUBLISHED" and len(live.json()["manifest_digest"]) == 64
        assert (
            call("POST", f"{base}:publish", "publisher", json={"expected_hash": h1}).status_code
            == 409
        )  # already live

        # a new revision is never an edit, and an earlier approval does not carry to it
        r2 = call(
            "POST",
            f"/v1/trust-center/claims/{cid}/revisions",
            "author",
            json=_claim_body(
                digest, "An independent SOC 2 report is available to customers on request"
            ),
        )
        assert (
            r2.status_code == 201 and r2.json()["revision"] == 2 and r2.json()["content_hash"] != h1
        )
        h2 = r2.json()["content_hash"]
        stale = call(
            "POST",
            f"/v1/trust-center/claims/{cid}/revisions/2:publish",
            "publisher",
            json={"expected_hash": h2},
        )
        assert (
            stale.status_code == 409 and "APPROVAL_INCOMPLETE" in stale.json()["error"]["message"]
        )
        for person, role in (("sec", "security"), ("legal", "legal")):
            assert (
                call(
                    "POST",
                    f"/v1/trust-center/claims/{cid}/revisions/2:approve",
                    person,
                    json={"role": role, "evidence_digests": [digest]},
                ).status_code
                == 200
            )
        assert (
            call(
                "POST",
                f"/v1/trust-center/claims/{cid}/revisions/2:publish",
                "publisher",
                json={"expected_hash": h2},
            ).status_code
            == 200
        )
        assert call("GET", f"{base}", "stranger").status_code == 403
        assert call("GET", f"{base}", "author").json()["status"] == "SUPERSEDED"

        # rollback goes to the last validly approved revision (1), re-checked
        rb = call("POST", f"/v1/trust-center/claims/{cid}:rollback", "publisher")
        assert (
            rb.status_code == 200
            and rb.json()["revision"] == 1
            and rb.json()["rolled_back_from"] == 2
            and rb.json()["status"] == "PUBLISHED"
        )

        # emergency withdrawal: one responder, at once, with a reason; no approver is needed
        assert (
            call(
                "POST", f"/v1/trust-center/claims/{cid}:withdraw", "responder", json={"reason": " "}
            ).status_code
            == 422
        )
        w = call(
            "POST",
            f"/v1/trust-center/claims/{cid}:withdraw",
            "responder",
            json={"reason": "The report scope was misdescribed"},
        )
        assert w.status_code == 200, w.text
        assert w.json()["revision"] == 1 and w.json()["purge_required"] is True
        assert call("GET", f"{base}", "author").json()["status"] == "EMERGENCY_WITHDRAWN"
        back = call("POST", f"{base}:publish", "publisher", json={"expected_hash": h1})
        assert (
            back.status_code == 409
            and back.json()["error"]["code"] == "TRUST_CENTER_REFUSED"
            or "WITHDRAWN" in back.text
        )
        open_w = call("GET", "/v1/trust-center/withdrawals?open=true", "author").json()["items"]
        assert len(open_w) == 1
        self_review = call(
            "POST",
            f"/v1/trust-center/withdrawals/{open_w[0]['withdrawal_id']}:review",
            "responder",
            json={"note": "Reviewed and confirmed"},
        )
        assert (
            self_review.status_code == 403
        )  # the responder holds no publish capability; and the database would refuse the same person anyway
        assert (
            call(
                "POST",
                f"/v1/trust-center/withdrawals/{open_w[0]['withdrawal_id']}:review",
                "publisher",
                json={"note": "Reviewed: the withdrawal was right"},
            ).status_code
            == 200
        )
        assert call("GET", "/v1/trust-center/withdrawals?open=true", "author").json()["items"] == []

        # evidence ending: the claims that rest on it are named at once
        gone = call(
            "POST",
            f"/v1/trust-center/evidence/{ev_id}:lifecycle",
            "evidence_mgr",
            json={"lifecycle": "EXPIRED"},
        )
        assert gone.status_code == 200 and gone.json()["lifecycle"] == "EXPIRED"

    audit = {
        (r["event_type"], r["outcome"])
        for r in _sql(
            owner_url,
            "SELECT event_type, outcome FROM evidence.audit_record WHERE tenant_id IS NULL AND event_type LIKE 'trust.%'",
        )
    }
    for expected in (
        ("trust.claim.drafted", "SUCCESS"),
        ("trust.claim.approved", "SUCCESS"),
        ("trust.claim.published", "SUCCESS"),
        ("trust.claim.withdrawn", "SUCCESS"),
        ("trust.claim.refused", "DENIED"),
    ):
        assert expected in audit, expected


def test_the_database_refuses_what_the_code_should_never_attempt(
    rt_url: str, owner_url: str
) -> None:
    cid, author, other, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h = hashlib.sha256(b"x").hexdigest()
    _sql(
        owner_url,
        "INSERT INTO platform.tc_claim_revision (claim_id, revision, exact_text, locale, claim_type, scope, channels, author, content_hash, status) "
        "VALUES (:c, 1, 'Some sentence', 'en-US', 'functionality', CAST('{\"entity_id\":\"e\",\"service\":\"s\",\"markets\":[\"US\"]}' AS jsonb), ARRAY['trust_center'], :a, :h, 'APPROVAL_PENDING')",
        c=cid, a=author, h=h,
    )  # fmt: skip

    def attempt(stmt: str, **p: Any) -> None:
        with pytest.raises(DBAPIError):
            _sql(owner_url, stmt, **p)

    ins = "INSERT INTO platform.tc_approval (approval_id, claim_id, revision, claim_hash, approver, role) VALUES (gen_random_uuid(), :c, 1, :h, :who, :role)"
    attempt(ins, c=cid, h=h, who=author, role="security")  # the author cannot review
    attempt(ins, c=cid, h="f" * 64, who=other, role="security")  # not this revision's hash
    _sql(owner_url, ins, c=cid, h=h, who=other, role="security")
    attempt(ins, c=cid, h=h, who=other, role="legal")  # the same person twice
    attempt(ins, c=cid, h=h, who=third, role="security")  # the same role twice
    attempt(
        "UPDATE platform.tc_approval SET refused = true WHERE claim_id = :c", c=cid
    )  # append-only
    attempt("DELETE FROM platform.tc_approval WHERE claim_id = :c", c=cid)

    pub = "INSERT INTO platform.tc_publication (publication_id, claim_id, revision, claim_hash, kind, channels, locale, manifest_digest, published_by) VALUES (gen_random_uuid(), :c, 1, :h, 'PUBLISH', ARRAY['trust_center'], 'en-US', :m, :who)"
    m = "a" * 64
    attempt(pub, c=cid, h=h, m=m, who=third)  # one approver is not enough
    _sql(owner_url, ins, c=cid, h=h, who=third, role="legal")
    attempt(pub, c=cid, h=h, m=m, who=author)  # the author cannot publish
    _sql(
        owner_url, pub, c=cid, h=h, m=m, who=uuid.uuid4()
    )  # two approvers, a third publisher: allowed
    attempt("DELETE FROM platform.tc_publication WHERE claim_id = :c", c=cid)

    attempt(
        "UPDATE platform.tc_claim_revision SET exact_text = 'Another sentence' WHERE claim_id = :c",
        c=cid,
    )  # content never changes
    _sql(
        owner_url,
        "UPDATE platform.tc_claim_revision SET status = 'EMERGENCY_WITHDRAWN', withdrawn_at = now() WHERE claim_id = :c",
        c=cid,
    )
    attempt(
        "UPDATE platform.tc_claim_revision SET status = 'PUBLISHED' WHERE claim_id = :c", c=cid
    )  # a withdrawn revision stays withdrawn
    attempt(pub, c=cid, h=h, m="b" * 64, who=uuid.uuid4())
    attempt("DELETE FROM platform.tc_claim_revision WHERE claim_id = :c", c=cid)
