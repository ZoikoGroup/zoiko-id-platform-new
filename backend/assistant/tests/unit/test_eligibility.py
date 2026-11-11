"""Release truth (INV-02): an affirmative claim needs P01 + P02 + P04/P05, scoped by market, audience and time (CSR-008 section 11)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from zid_assistant.application.snapshot import Knowledge
from zid_assistant.domain.content import (
    Capability,
    CapabilityStatus,
    ClaimClass,
    ClaimKind,
    CommercialFact,
    ContentState,
)
from zid_assistant.domain.eligibility import (
    Context,
    Snapshot,
    Verdict,
    capability_released,
    claim_eligible,
    conflicting,
    link_usable,
    source_eligible,
)
from zid_assistant.domain.verdicts import Reason

TODAY = date(2026, 11, 11)


def released(**kw: object) -> Capability:
    base: dict[str, object] = {
        "capability_id": "CAP-TEST",
        "display_name": "Test",
        "status": CapabilityStatus.RELEASED,
        "public_eligibility": "PUBLIC",
        "market_codes": ("US",),
        "audience_codes": ("DEVELOPER",),
        "test_evidence_refs": ("T-1",),
        "approver": "Product Release Owner",
        "effective_from": date(2026, 1, 1),
    }
    base.update(kw)
    return Capability(**base)  # type: ignore[arg-type]


def snap(k: Knowledge, **kw: object) -> Snapshot:
    return replace(k.snapshot, **kw)


def ctx(**kw: object) -> Context:
    base: dict[str, object] = {"market": "US", "audience": "DEVELOPER", "today": TODAY}
    base.update(kw)
    return Context(**base)  # type: ignore[arg-type]


def test_every_launch_capability_is_unreleased(knowledge: Knowledge) -> None:
    assert knowledge.snapshot.capabilities, "P01 candidates must exist"
    for cid in knowledge.snapshot.capabilities:
        r = capability_released(knowledge.snapshot, cid, ctx())
        assert r.verdict is Verdict.ABSTAIN and r.reason is Reason.CAPABILITY_NOT_RELEASED


def test_a_fully_evidenced_capability_is_released_only_for_its_market_and_audience(
    knowledge: Knowledge,
) -> None:
    s = snap(knowledge, capabilities={"CAP-TEST": released()})
    assert capability_released(s, "CAP-TEST", ctx()).allowed
    assert not capability_released(s, "CAP-TEST", ctx(market="GB")).allowed  # other market
    assert not capability_released(
        s, "CAP-TEST", ctx(market=None)
    ).allowed  # unknown market: never guess (INV-06)
    assert not capability_released(s, "CAP-TEST", ctx(audience="ENTERPRISE")).allowed
    assert not capability_released(s, "CAP-TEST", ctx(audience=None)).allowed


def test_release_needs_evidence_approver_and_a_live_window(knowledge: Knowledge) -> None:
    for bad in (
        released(test_evidence_refs=()),
        released(approver=None),
        released(market_codes=()),
        released(effective_from=date(2027, 1, 1)),
        released(expires_at=date(2026, 6, 1)),
        released(status=CapabilityStatus.PILOT),
        released(public_eligibility="NOT_PUBLIC"),
    ):
        s = snap(knowledge, capabilities={"CAP-TEST": bad})
        assert not capability_released(s, "CAP-TEST", ctx()).allowed, bad


def test_withdrawn_capability_is_blocked_immediately(knowledge: Knowledge) -> None:
    s = snap(
        knowledge,
        capabilities={"CAP-TEST": released()},
        withdrawn_capabilities=frozenset({"CAP-TEST"}),
    )
    assert not capability_released(s, "CAP-TEST", ctx()).allowed


def test_claim_kinds(knowledge: Knowledge) -> None:
    claims = {
        "CL-90": ClaimClass(claim_id="CL-90", name="concept", kind=ClaimKind.CONCEPT),
        "CL-91": ClaimClass(claim_id="CL-91", name="product", kind=ClaimKind.PRODUCT),
        "CL-92": ClaimClass(claim_id="CL-92", name="sensitive", kind=ClaimKind.SENSITIVE),
        "CL-93": ClaimClass(
            claim_id="CL-93",
            name="approved sensitive",
            kind=ClaimKind.SENSITIVE,
            approved=True,
            approvers=("Legal",),
        ),
        "CL-94": ClaimClass(claim_id="CL-94", name="commercial", kind=ClaimKind.COMMERCIAL),
        "CL-95": ClaimClass(claim_id="CL-95", name="secure", kind=ClaimKind.SECURE_ROUTE),
    }
    fact = CommercialFact(
        fact_id="F-1",
        statement="x",
        market_codes=("US",),
        state=ContentState.PUBLISHED,
        approver="CFO",
    )
    s = snap(knowledge, claims=claims, capabilities={"CAP-TEST": released()}, facts={"F-1": fact})
    c = ctx()
    assert claim_eligible(s, "CL-90", c).allowed
    assert claim_eligible(s, "CL-91", c, capability_ids=("CAP-TEST",)).allowed
    assert not claim_eligible(s, "CL-91", c).allowed  # a product claim with no capability asserted
    assert claim_eligible(s, "CL-92", c).reason is Reason.CLAIM_NOT_APPROVED
    assert claim_eligible(s, "CL-93", c).allowed
    assert claim_eligible(s, "CL-94", c, fact_ids=("F-1",)).allowed
    assert not claim_eligible(s, "CL-94", c).allowed
    assert not claim_eligible(s, "CL-94", ctx(market="GB"), fact_ids=("F-1",)).allowed
    secure = claim_eligible(s, "CL-95", c)
    assert secure.verdict is Verdict.DENY_SECURE_ROUTE and secure.reason is Reason.SECURE_ROUTE_ONLY
    assert not claim_eligible(s, "CL-404", c).allowed  # unknown claim class


def test_sources_are_eligible_only_when_published_or_an_allowed_sample(
    knowledge: Knowledge,
) -> None:
    sid = next(iter(knowledge.snapshot.sources))
    assert not source_eligible(
        knowledge.snapshot, sid, Context()
    ).allowed  # draft is never eligible by default
    assert source_eligible(
        knowledge.snapshot, sid, Context(allow_sample=True)
    ).allowed  # a labelled engineering sample, local only
    s = snap(knowledge, withdrawn_sources=frozenset({sid}))
    r = source_eligible(s, sid, Context(allow_sample=True))
    assert not r.allowed and r.reason is Reason.SOURCE_WITHDRAWN
    assert not source_eligible(
        knowledge.snapshot, sid, Context(allow_sample=True, locale="de-DE")
    ).allowed  # wrong locale
    assert not source_eligible(knowledge.snapshot, "PUB-999", Context(allow_sample=True)).allowed


def test_sample_content_is_never_allowed_when_the_snapshot_is_not_a_sample(
    knowledge: Knowledge,
) -> None:
    sid = next(iter(knowledge.snapshot.sources))
    s = snap(knowledge, sample=False)
    assert not source_eligible(s, sid, Context(allow_sample=True)).allowed


def test_published_source_with_an_expired_review_is_not_eligible(knowledge: Knowledge) -> None:
    sid = next(iter(knowledge.snapshot.sources))
    src = knowledge.snapshot.sources[sid]
    stale = src.model_copy(
        update={"state": ContentState.PUBLISHED, "review_due_at": date(2026, 1, 1)}
    )
    s = snap(knowledge, sources={**knowledge.snapshot.sources, sid: stale})
    assert not source_eligible(s, sid, Context(today=TODAY)).allowed
    fresh = src.model_copy(
        update={"state": ContentState.PUBLISHED, "review_due_at": date(2027, 1, 1)}
    )
    s2 = snap(knowledge, sources={**knowledge.snapshot.sources, sid: fresh})
    assert source_eligible(s2, sid, Context(today=TODAY)).allowed


def test_links_must_be_active_and_not_withdrawn(knowledge: Knowledge) -> None:
    assert link_usable(knowledge.snapshot, "OVERVIEW").allowed
    assert not link_usable(
        knowledge.snapshot, "SIGN-IN"
    ).allowed  # suspended: no verified URL exists yet
    assert not link_usable(
        snap(knowledge, withdrawn_links=frozenset({"OVERVIEW"})), "OVERVIEW"
    ).allowed
    assert not link_usable(knowledge.snapshot, "NOT-A-LINK").allowed


def test_contradictory_sources_are_quarantined(knowledge: Knowledge) -> None:
    a, b = list(knowledge.snapshot.sources)[:2]
    sa = knowledge.snapshot.sources[a].model_copy(update={"claim_ids": ("CL-03",)})
    sb = knowledge.snapshot.sources[b].model_copy(update={"claim_ids": ("!CL-03",)})
    s = snap(knowledge, sources={**knowledge.snapshot.sources, a: sa, b: sb})
    assert conflicting(s, (a, b))
    assert not conflicting(s, (a,))
