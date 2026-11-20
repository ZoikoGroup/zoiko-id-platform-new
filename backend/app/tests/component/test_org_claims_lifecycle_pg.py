"""A company claim over its life (ZID-UX-WF-013B states and events; slice L-12, CR-052): submitted once and never before it says whom to screen,
withdrawn and then closed to everything, a delegated mandate that counts only after a person confirms it, outcome changes announced once,
and a draft nobody submits abandoned. The status carries an ETag and is never cached."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from sqlalchemy import text
from test_org_claim_proofs_pg import PDF, FakeScanner
from test_org_claims_api_pg import REVIEW_ACTIONS, REVIEW_RESOURCES, Register, error, load_lists
from test_org_claims_trust_pg import CLAIM, app_for, key
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.application import claims
from zid_platform.modules.tenant.application import residency

SCREENING = {"representative": {"ref": "rep", "name": "Alice Smith"}, "owners": [{"ref": "owner1", "name": "Olivia Owner"}], "holdings": [{"owner": "owner1", "shares_pct": "60"}]}  # fmt: skip


class Setup:
    def __init__(self, rt_url: str, owner_url: str) -> None:
        asyncio.run(load_lists(rt_url))
        self.rt_url, self.owner_url = rt_url, owner_url
        self.o = onboard(rt_url, extra_actions=REVIEW_ACTIONS, extra_resources=REVIEW_RESOURCES)

        async def place() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(self.o.tenant)) as c:
                await residency.set_residency(
                    c, self.o.tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
                    key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=self.o.owner,
                )  # fmt: skip

        asyncio.run(place())
        app = app_for(self.o, rt_url, Register())
        app.state.proof_scanner = FakeScanner()
        self._client = TestClient(app)
        self.api = self._client.__enter__()  # the app starts, as it does in a real run
        self.owner, self.second = self.o.bearer(self.o.owner), self.o.bearer(self.o.second)

    def claim(self, number: str = "00123456") -> str:
        r = self.api.post(
            "/v1/org-claims",
            headers={**self.owner, **key()},
            json={**CLAIM, "registration_number": number},
        )
        assert r.status_code == 201, r.text
        return str(r.json()["claim_id"])

    def status(self, claim: str) -> dict[str, Any]:
        r = self.api.get(f"/v1/org-claims/{claim}/status", headers=self.owner)
        assert r.status_code == 200, r.text
        return dict(r.json())

    def declare(self, claim: str, **extra: Any) -> Any:
        return self.api.post(
            f"/v1/org-claims/{claim}/screening", headers=self.owner, json={**SCREENING, **extra}
        )

    def proof(self, claim: str, kind: str, body: bytes = PDF) -> Any:
        return self.api.post(
            f"/v1/org-claims/{claim}/proofs", content=body,
            headers={**self.owner, "Content-Type": "application/pdf", "X-Proof-Kind": kind, "Idempotency-Key": f"proof-{uuid.uuid4()}"},
        )  # fmt: skip

    def screening_case(self, claim: str) -> str:
        async def go() -> str:
            async with engine(self.owner_url) as eng, eng.begin() as c:
                return str(
                    (
                        await c.execute(
                            text(
                                "SELECT case_id FROM verify.screening_case WHERE kyb_case_id = :k"
                            ),
                            {"k": uuid.UUID(claim)},
                        )
                    ).scalar_one()
                )

        return asyncio.run(go())

    def sql(self, sql: str, **params: Any) -> list[Any]:
        async def go() -> list[Any]:
            async with engine(self.owner_url) as eng, eng.begin() as c:
                return list((await c.execute(text(sql), params)).all())

        return asyncio.run(go())

    def events(self, name: str, claim: str) -> int:
        return int(
            self.sql(
                "SELECT count(*) AS n FROM events.outbox_event WHERE event_type = :e AND payload::text LIKE :c",
                e=f"com.zoiko.id.{name}.v1",
                c=f"%{claim}%",
            )[0].n
        )


@pytest.fixture
def lab(rt_url: str, owner_url: str) -> Any:
    s = Setup(rt_url, owner_url)
    yield s
    s._client.__exit__(None, None, None)


def test_a_claim_is_submitted_once_not_before_it_says_whom_to_screen_and_its_status_carries_an_etag(
    lab: Setup,
) -> None:
    claim = lab.claim()
    first = lab.status(claim)
    assert (
        first["state"] == "DRAFT" and first["submitted_at"] is None and first["closed_at"] is None
    )

    # the status is never cached and says what it is: the same tag while nothing changes, a bare 304 for a poll that sends it back
    r = lab.api.get(f"/v1/org-claims/{claim}/status", headers=lab.owner)
    tag = r.headers["etag"]
    assert r.headers["cache-control"] == "no-store" and tag.startswith('"')
    same = lab.api.get(
        f"/v1/org-claims/{claim}/status", headers={**lab.owner, "If-None-Match": tag}
    )
    assert same.status_code == 304 and same.content == b"" and same.headers["etag"] == tag

    # CR-134: nothing about whom to screen is needed to send a claim; a declaration made later is still accepted
    early = lab.api.post(f"/v1/org-claims/{claim}/submit", headers=lab.owner)
    assert early.status_code == 202
    assert lab.declare(claim).status_code == 201

    done = lab.api.post(f"/v1/org-claims/{claim}/submit", headers=lab.owner)
    assert (
        done.status_code == 202
        and done.json()["state"] == "SUBMITTED"
        and done.json()["submitted_at"]
    )
    again = lab.api.post(f"/v1/org-claims/{claim}/submit", headers=lab.owner)
    assert (
        again.status_code == 202 and again.json()["submitted_at"] == done.json()["submitted_at"]
    )  # once: a repeat changes nothing
    assert lab.events("organization.claim.submitted", claim) == 1
    # accepted for review is not decided: the same four checks, nothing made verified by sending it
    assert done.json()["outcome"] != "VERIFIED" and "authority" not in done.json()["checks"]
    moved = lab.api.get(
        f"/v1/org-claims/{claim}/status", headers={**lab.owner, "If-None-Match": tag}
    )
    assert (
        moved.status_code == 200 and moved.headers["etag"] != tag
    )  # the old tag no longer matches what it says

    # only the applicant's own organization may ask
    assert lab.api.post(f"/v1/org-claims/{claim}/submit").status_code == 401


def test_a_claim_can_be_withdrawn_and_then_takes_nothing_more(
    lab: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim = lab.claim()
    assert lab.declare(claim).status_code == 201
    case = lab.screening_case(claim)
    assert (
        lab.sql(
            "SELECT closed_at FROM verify.screening_case WHERE case_id = :c", c=uuid.UUID(case)
        )[0].closed_at
        is None
    )

    gone = lab.api.post(f"/v1/org-claims/{claim}/withdraw", headers=lab.owner)
    assert (
        gone.status_code == 200
        and gone.json()["state"] == "WITHDRAWN"
        and gone.json()["outcome"] == "WITHDRAWN"
    )
    assert gone.json()["closed_at"]
    again = lab.api.post(f"/v1/org-claims/{claim}/withdraw", headers=lab.owner)
    assert (
        again.status_code == 200 and again.json()["closed_at"] == gone.json()["closed_at"]
    )  # a repeat is the same answer
    assert lab.events("organization.claim.closed", claim) == 1

    # nothing is deleted and nobody is watched for it any more
    assert (
        lab.sql(
            "SELECT closed_at FROM verify.screening_case WHERE case_id = :c", c=uuid.UUID(case)
        )[0].closed_at
        is not None
    )
    assert (
        lab.sql(
            "SELECT count(*) AS n FROM org.kyb_evidence WHERE case_id = :c", c=uuid.UUID(claim)
        )[0].n
        >= 1
    )
    assert claim not in [c for _, c, _, _ in asyncio.run(_live(lab))]

    # a closed claim takes no refresh, no declaration, no appeal, no submission and no document
    for method, path, body in (
        ("post", f"/v1/org-claims/{claim}/refresh", None),
        ("post", f"/v1/org-claims/{claim}/screening", SCREENING),
        ("put", f"/v1/org-claims/{claim}/screening", SCREENING),
        ("post", f"/v1/org-claims/{claim}/appeal", {"kind": "CORRECTION", "statement": "x" * 30}),
        ("post", f"/v1/org-claims/{claim}/submit", None),
    ):
        r = getattr(lab.api, method)(path, headers=lab.owner, **({"json": body} if body else {}))
        assert r.status_code == 409 and error(r) == "CLAIM_CLOSED", (path, r.text)
    late = lab.proof(claim, "OTHER")
    assert late.status_code == 409 and error(late) == "CLAIM_CLOSED"

    # the applicant may start again, and a claim that is verified is not withdrawn here
    other = lab.claim("00123457")
    monkeypatch.setattr(claims, "final_outcome", lambda checks: "VERIFIED")
    held = lab.api.post(f"/v1/org-claims/{other}/withdraw", headers=lab.owner)
    assert held.status_code == 409 and error(held) == "CLAIM_VERIFIED"


async def _live(lab: Setup) -> list[tuple[str, str, str, str]]:
    from zid_platform.modules.screening.ports import live_kyb_cases

    async with engine(lab.owner_url) as eng, eng.begin() as c:
        return await live_kyb_cases(c)


def test_a_delegated_mandate_is_evidence_for_a_reviewer_and_counts_only_after_one_confirms_it(
    lab: Setup,
) -> None:
    claim = lab.claim()
    good = lab.proof(claim, "BOARD_RESOLUTION")
    wrong = lab.proof(claim, "CERTIFICATE_OF_INCORPORATION", PDF.replace(b"Acme", b"Beta"))
    assert good.status_code == 201 and wrong.status_code == 201
    ends = (datetime.now(UTC) + timedelta(days=10)).isoformat()

    def delegated(**over: Any) -> Any:
        return lab.declare(
            claim,
            relationship="DELEGATED_EMPLOYEE",
            mandate_proof_id=good.json()["proof_id"],
            mandate_expires_at=ends,
            **over,
        )

    # what the server refuses: no document, the wrong kind of document, a document that is not on this claim, no end date, an officer with a mandate
    r = lab.declare(claim, relationship="DELEGATED_EMPLOYEE", mandate_expires_at=ends)
    assert r.status_code == 422 and error(r) == "AUTHORITY_PROOF_REQUIRED"
    r = lab.declare(
        claim,
        relationship="DELEGATED_EMPLOYEE",
        mandate_proof_id=wrong.json()["proof_id"],
        mandate_expires_at=ends,
    )
    assert r.status_code == 422 and error(r) == "AUTHORITY_PROOF_REQUIRED"
    r = lab.declare(
        claim,
        relationship="EXTERNAL_REPRESENTATIVE",
        mandate_proof_id=str(uuid.uuid4()),
        mandate_expires_at=ends,
    )
    assert r.status_code == 422 and error(r) == "AUTHORITY_PROOF_REQUIRED"
    r = lab.declare(
        claim, relationship="DELEGATED_EMPLOYEE", mandate_proof_id=good.json()["proof_id"]
    )
    assert r.status_code == 422 and error(r) == "MANDATE_EXPIRY_INVALID"
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = lab.declare(
        claim,
        relationship="DELEGATED_EMPLOYEE",
        mandate_proof_id=good.json()["proof_id"],
        mandate_expires_at=past,
    )
    assert r.status_code == 422 and error(r) == "MANDATE_EXPIRY_INVALID"
    r = lab.declare(
        claim,
        relationship="DIRECTOR_OR_OFFICER",
        mandate_proof_id=good.json()["proof_id"],
        mandate_expires_at=ends,
    )
    assert r.status_code == 422

    # a good declaration waits for a person: the applicant's own document proves nothing
    made = delegated()
    assert made.status_code == 201, made.text
    shown = made.json()
    assert (
        shown["relationship"] == "DELEGATED_EMPLOYEE"
        and "authority" not in shown["checks"]  # CR-134: the authority is no longer one of the checks
    )
    case = lab.screening_case(claim)

    # a ruling that is not about a mandate cannot confirm one, and neither can a ruling that does not verify
    bad = lab.api.post(
        f"/v1/reviews/screening-cases/{case}/rulings",
        headers=lab.second,
        json={"outcome": "REJECTED", "rationale": "x" * 30, "confirm_mandate": True},
    )
    assert bad.status_code == 422
    done = lab.api.post(
        f"/v1/reviews/screening-cases/{case}/rulings",
        headers=lab.second,
        json={
            "outcome": "VERIFIED",
            "rationale": "I read the board resolution and it names this person.",
            "confirm_mandate": True,
        },
    )
    assert done.status_code == 200, done.text
    after = lab.status(claim)
    assert "authority" not in after["checks"] and "risk" not in after["checks"]
    ends_at = datetime.fromisoformat(ends)
    assert (
        datetime.fromisoformat(after["screening"]["expires_at"]) <= ends_at
    )  # a clear outcome never outlives the mandate

    # the officer path has no mandate to confirm
    other = lab.claim("00123457")
    assert lab.declare(other).status_code == 201
    refused = lab.api.post(
        f"/v1/reviews/screening-cases/{lab.screening_case(other)}/rulings", headers=lab.second,
        json={"outcome": "VERIFIED", "rationale": "I read the register entry and it matches.", "confirm_mandate": True},
    )  # fmt: skip
    assert refused.status_code == 409 and error(refused) == "MANDATE_NOT_CONFIRMABLE"
    assert "authority" not in lab.status(other)["checks"]


def test_a_change_of_outcome_is_announced_once_whoever_caused_it(lab: Setup) -> None:
    claim = str(uuid.uuid4())  # the tracker keeps ids and states only: it needs no claim behind it
    pending = {
        "company": "PENDING",
        "person_assurance": "PENDING",
    }

    def seen(outcome: str, **over: str) -> dict[str, Any]:
        return {"checks": {**pending, **over}, "outcome": outcome}

    async def record(s: dict[str, Any]) -> list[str]:
        async with engine(lab.rt_url) as eng, tenant_transaction(eng, ctx(lab.o.tenant)) as c:
            return await claims.record_outcome(c, lab.o.tenant, claim, s, datetime.now(UTC))

    def run(s: dict[str, Any]) -> list[str]:
        return asyncio.run(record(s))

    assert run(seen("PROVISIONAL")) == []  # nothing has happened yet
    assert run(seen("PROVISIONAL", company="COMPLETED")) == ["organization.claim.source_verified"]
    assert (
        run(seen("PROVISIONAL", company="COMPLETED")) == []
    )  # again and again: nothing new, nothing said
    done = run(
        seen(
            "VERIFIED",
            company="COMPLETED",
            person_assurance="COMPLETED",
            authority="COMPLETED",
            risk="COMPLETED",
        )
    )
    assert sorted(done) == [
        "organization.claim.authority_evaluated",
        "organization.claim.decision_issued",
        "organization.claim.risk_assessed",
    ]
    assert (
        run(
            seen(
                "VERIFIED",
                company="COMPLETED",
                person_assurance="COMPLETED",
                authority="COMPLETED",
                risk="COMPLETED",
            )
        )
        == []
    )
    lapsed = run(
        seen(
            "STALE",
            company="COMPLETED",
            person_assurance="COMPLETED",
            authority="COMPLETED",
            risk="STALE",
        )
    )
    assert lapsed == [
        "organization.claim.risk_assessed"
    ]  # a lapse is announced as a change of the check, and is not a decision
    assert run(
        seen(
            "WITHDRAWN",
            company="COMPLETED",
            person_assurance="COMPLETED",
            authority="COMPLETED",
            risk="STALE",
        )
    ) == ["organization.claim.closed"]

    sent = {
        r.event_type: r.n
        for r in lab.sql(
            "SELECT event_type, count(*) AS n FROM events.outbox_event WHERE payload::text LIKE :c GROUP BY event_type",
            c=f"%{claim}%",
        )
    }
    assert (
        sent["com.zoiko.id.organization.claim.decision_issued.v1"] == 1
        and sent["com.zoiko.id.organization.claim.risk_assessed.v1"] == 2
    )
    assert "com.zoiko.id.organization.claim.closed.v1" in sent
    assert all(
        "Alice" not in str(r)
        for r in lab.sql(
            "SELECT payload::text FROM events.outbox_event WHERE payload::text LIKE :c",
            c=f"%{claim}%",
        )
    )


def test_a_draft_nobody_submits_is_abandoned_and_a_submitted_claim_is_not(
    lab: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the worker sees every tenant; this test shares its database with others, so it looks at its own organization only
    everyone = claims.kyb_drafts_before

    async def mine(conn: Any, cutoff: datetime) -> list[tuple[str, str]]:
        return [(t, c) for t, c in await everyone(conn, cutoff) if t == lab.o.tenant]

    monkeypatch.setattr(claims, "kyb_drafts_before", mine)
    draft, sent = lab.claim(), lab.claim("00123457")
    for c in (draft, sent):
        assert lab.declare(c).status_code == 201
    assert lab.api.post(f"/v1/org-claims/{sent}/submit", headers=lab.owner).status_code == 202

    async def sweep(now: datetime) -> dict[str, int]:
        async with (
            engine(lab.owner_url) as eng,
            eng.begin() as conn,
        ):  # the worker role sees every tenant
            return await claims.abandon_drafts(conn, now)

    assert (
        asyncio.run(sweep(datetime.now(UTC) + timedelta(days=1))) == {}
    )  # within the draft period: nothing
    closed = asyncio.run(sweep(datetime.now(UTC) + timedelta(days=31)))
    assert closed.get(lab.o.tenant) == 1

    gone = lab.status(draft)
    assert gone["state"] == "ABANDONED" and gone["outcome"] == "ABANDONED" and gone["closed_at"]
    assert (
        lab.status(sent)["state"] == "SUBMITTED"
    )  # someone who said it is complete is waiting for a person, not for the clock
    assert lab.events("organization.claim.closed", draft) == 1
    assert (
        lab.sql(
            "SELECT closed_at FROM verify.screening_case WHERE case_id = :c",
            c=uuid.UUID(lab.screening_case(draft)),
        )[0].closed_at
        is not None
    )
    assert (
        asyncio.run(sweep(datetime.now(UTC) + timedelta(days=60))) == {}
    )  # a second sweep finds nothing new
