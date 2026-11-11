"""Policy packs (ZID-RCG-003 RCG-G01): no default pass, approvals bound to the pack hash, maker is not approver."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    LegalSource,
    Outcome,
    PackError,
    PackKey,
    PackStatus,
    PolicyPack,
    Rules,
    SourceStatus,
    approve,
    decision_stamp,
    pack_hash,
    publish,
    publish_problems,
    resolve,
    retire,
    revise,
    stamp_matches,
)

NOW = datetime(2026, 11, 10, tzinfo=UTC)
PEOPLE = {"legal": "lena", "grc": "gus", "security": "sam", "business_owner": "bea"}


def _source(status: SourceStatus = SourceStatus.LAW, **kw: object) -> LegalSource:
    base: dict[str, Any] = {
        "instrument": "Example Act 2024",
        "url": "https://example.test/act",
        "status": status,
        "retrieved_at": NOW - timedelta(days=1),
        "effective_from": NOW - timedelta(days=30),
        "effective_to": None,
        "owner": "legal-team",
    }
    base.update(kw)
    return LegalSource(**base)


def _draft(
    *,
    pack_id: str = "gb-eng-nurse",
    version: int = 1,
    sources: tuple[LegalSource, ...] | None = None,
    **key: object,
) -> PolicyPack:
    k: dict[str, Any] = {
        "country": "GB",
        "product": "PROFESSIONAL_ELIGIBILITY",
        "purpose": "ENGAGEMENT",
        "subdivision": "GB-ENG",
        "profession": "nurse",
    }
    k.update(key)
    return PolicyPack(
        pack_id=pack_id,
        key=PackKey(**k),
        version=version,
        status=PackStatus.DRAFT,
        sources=(_source(),) if sources is None else sources,
        rules=Rules(evidence_minimum=("LICENSE_STANDING",), lawful_basis="contract"),
        author="maker",
    )


def _signed(pack: PolicyPack) -> PolicyPack:
    for role in APPROVER_ROLES:
        pack = approve(pack, role=role, approver=PEOPLE[role], signed_hash=pack_hash(pack))
    return pack


def _approved(**kw: object) -> PolicyPack:
    return publish(_signed(_draft(**kw)), at=NOW)  # type: ignore[arg-type]


# ---- T01 / T02: no pack, no precision -> never APPLICABLE ------------------------------------------------------------


def test_t01_market_not_in_the_approved_registry_is_unavailable() -> None:
    r = resolve(
        [_approved()],
        country="FR",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
    )
    assert (r.outcome, r.reason, r.pack) == (Outcome.UNAVAILABLE, "market_not_approved", None)


def test_no_packs_at_all_is_unavailable() -> None:
    assert (
        resolve([], country="GB", product="P", purpose="Q", at=NOW).outcome is Outcome.UNAVAILABLE
    )


def test_a_draft_or_retired_pack_never_governs() -> None:
    for pack in (_draft(), retire(_approved())):
        r = resolve(
            [pack],
            country="GB",
            product="PROFESSIONAL_ELIGIBILITY",
            purpose="ENGAGEMENT",
            at=NOW,
            subdivision="GB-ENG",
            profession="nurse",
        )
        assert r.outcome is Outcome.UNAVAILABLE


def test_t02_unknown_subdivision_for_a_regulated_profession_needs_review() -> None:
    r = resolve(
        [_approved()],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        subdivision="GB-SCT",
        profession="nurse",
    )
    assert (r.outcome, r.reason, r.pack) == (Outcome.REVIEW, "policy_missing", None)


def test_missing_subdivision_asks_for_a_manual_jurisdiction() -> None:
    r = resolve(
        [_approved()],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        profession="nurse",
    )
    assert (r.outcome, r.reason) == (Outcome.REVIEW, "require_manual_jurisdiction")


def test_missing_profession_is_policy_missing_not_a_pass() -> None:
    r = resolve(
        [_approved()],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        subdivision="GB-ENG",
    )
    assert (r.outcome, r.reason) == (Outcome.REVIEW, "policy_missing")


def test_exact_subdivision_pack_governs_and_beats_the_country_wide_one() -> None:
    wide = _approved(pack_id="gb-nurse", subdivision=None)
    exact = _approved()
    r = resolve(
        [wide, exact],
        country="gb",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        subdivision="GB-ENG",
        profession="nurse",
    )
    assert (r.outcome, r.pack) == (Outcome.APPLICABLE, exact)


def test_a_country_wide_pack_covers_any_subdivision() -> None:
    wide = _approved(pack_id="gb-nurse", subdivision=None)
    r = resolve(
        [wide],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        subdivision="GB-SCT",
        profession="nurse",
    )
    assert (r.outcome, r.pack) == (Outcome.APPLICABLE, wide)


def test_an_approved_pack_whose_source_has_ended_is_unavailable() -> None:
    ended = _source(effective_to=NOW + timedelta(days=1))
    pack = publish(_signed(_draft(sources=(ended,))), at=NOW)
    later = NOW + timedelta(days=2)
    r = resolve(
        [pack],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=later,
        subdivision="GB-ENG",
        profession="nurse",
    )
    assert (r.outcome, r.reason) == (Outcome.UNAVAILABLE, "no_source_in_force")


def test_the_highest_approved_version_governs() -> None:
    v1 = _approved()
    v2 = publish(_signed(revise(v1, author="maker2")), at=NOW)
    r = resolve(
        [v1, v2],
        country="GB",
        product="PROFESSIONAL_ELIGIBILITY",
        purpose="ENGAGEMENT",
        at=NOW,
        subdivision="GB-ENG",
        profession="nurse",
    )
    assert r.pack is not None and r.pack.version == 2


# ---- T03: an edit after approval invalidates the approvals -----------------------------------------------------------


def test_t03_a_revision_is_a_new_draft_with_no_approvals() -> None:
    v1 = _approved()
    v2 = revise(
        v1, author="maker2", rules=Rules(evidence_minimum=("LICENSE_STANDING", "INSURANCE"))
    )
    assert (v2.version, v2.status, dict(v2.approvals)) == (2, PackStatus.DRAFT, {})
    assert pack_hash(v2) != pack_hash(v1)
    assert v1.status is PackStatus.APPROVED and v1.rules.evidence_minimum == (
        "LICENSE_STANDING",
    )  # untouched
    with pytest.raises(PackError) as e:
        publish(v2, at=NOW)
    assert e.value.code == "PACK_NOT_PUBLISHABLE" and "PACK_APPROVAL_MISSING:legal" in str(e.value)


def test_t03_an_approval_that_signed_an_earlier_hash_is_stale() -> None:
    signed = _signed(_draft())
    tampered = replace(signed, rules=Rules(evidence_minimum=("NOTHING",)))
    problems = publish_problems(tampered, at=NOW)
    assert all(f"PACK_APPROVAL_STALE:{r}" in problems for r in APPROVER_ROLES)
    with pytest.raises(PackError):
        publish(tampered, at=NOW)


def test_the_hash_ignores_approvals_and_status_but_not_the_rules() -> None:
    d = _draft()
    assert pack_hash(d) == pack_hash(_signed(d)) == pack_hash(replace(d, status=PackStatus.RETIRED))
    assert pack_hash(d) != pack_hash(replace(d, rules=Rules(evidence_minimum=("X",))))
    assert pack_hash(d) == pack_hash(_draft())  # deterministic


# ---- T04: a proposal is not law --------------------------------------------------------------------------------------


def test_t04_only_a_proposal_cannot_be_published() -> None:
    draft = _signed(_draft(sources=(_source(SourceStatus.PROPOSAL),)))
    assert "PACK_NO_SOURCE_IN_FORCE" in publish_problems(draft, at=NOW)
    with pytest.raises(PackError):
        publish(draft, at=NOW)


def test_no_source_or_a_source_not_yet_effective_cannot_be_published() -> None:
    assert "PACK_NO_SOURCE_IN_FORCE" in publish_problems(_signed(_draft(sources=())), at=NOW)
    future = _source(effective_from=NOW + timedelta(days=5))
    assert "PACK_NO_SOURCE_IN_FORCE" in publish_problems(_signed(_draft(sources=(future,))), at=NOW)


def test_guidance_in_force_is_enough_but_a_proposal_alongside_it_does_not_matter() -> None:
    pack = _signed(_draft(sources=(_source(SourceStatus.PROPOSAL), _source(SourceStatus.GUIDANCE))))
    assert publish(pack, at=NOW).status is PackStatus.APPROVED


# ---- T36: the maker cannot approve ------------------------------------------------------------------------------------


def test_t36_the_author_cannot_approve() -> None:
    d = _draft()
    with pytest.raises(PackError) as e:
        approve(d, role="legal", approver="maker", signed_hash=pack_hash(d))
    assert e.value.code == "PACK_APPROVER_IS_AUTHOR"


def test_one_person_cannot_hold_two_approvals() -> None:
    d = approve(_draft(), role="legal", approver="lena", signed_hash=pack_hash(_draft()))
    with pytest.raises(PackError) as e:
        approve(d, role="grc", approver="lena", signed_hash=pack_hash(d))
    assert e.value.code == "PACK_APPROVER_NOT_DISTINCT"


def test_an_approval_must_sign_the_current_hash_and_a_known_role() -> None:
    d = _draft()
    with pytest.raises(PackError) as e:
        approve(d, role="legal", approver="lena", signed_hash="0" * 64)
    assert e.value.code == "PACK_APPROVAL_HASH_MISMATCH"
    with pytest.raises(PackError) as e2:
        approve(d, role="intern", approver="lena", signed_hash=pack_hash(d))
    assert e2.value.code == "PACK_APPROVER_ROLE_UNKNOWN"


def test_only_a_draft_can_be_approved() -> None:
    with pytest.raises(PackError) as e:
        approve(_approved(), role="legal", approver="zed", signed_hash=pack_hash(_approved()))
    assert e.value.code == "PACK_NOT_DRAFT"


def test_all_four_approvals_are_needed_to_publish() -> None:
    d = _draft()
    for role in ("legal", "grc", "security"):
        d = approve(d, role=role, approver=PEOPLE[role], signed_hash=pack_hash(d))
    with pytest.raises(PackError) as e:
        publish(d, at=NOW)
    assert "PACK_APPROVAL_MISSING:business_owner" in str(e.value)
    assert (
        publish(
            approve(d, role="business_owner", approver="bea", signed_hash=pack_hash(d)), at=NOW
        ).status
        is PackStatus.APPROVED
    )


# ---- T37: history is not rewritten ------------------------------------------------------------------------------------


def test_t37_a_decision_stamp_keeps_pointing_at_the_version_it_used() -> None:
    v1 = _approved()
    stamp = decision_stamp(v1)
    v2 = publish(
        _signed(revise(v1, author="maker2", rules=Rules(evidence_minimum=("INSURANCE",)))), at=NOW
    )
    assert stamp_matches(v1, stamp)
    assert not stamp_matches(v2, stamp)
    assert stamp["version"] == 1 and stamp["pack_hash"] == pack_hash(v1)


def test_the_operator_api_uses_the_domains_approver_roles_and_real_capabilities() -> None:
    """The API lists the four roles itself (a load-time import of the module would close a dependency cycle), so keep them equal."""
    from zid_platform.modules.operator_access.api import regulatory as api
    from zid_platform.modules.operator_access.domain.model import CAPABILITIES, CONFLICT_SETS

    assert api.APPROVER_ROLES == APPROVER_ROLES
    assert set(api.APPROVE) == set(APPROVER_ROLES)
    wanted = {api.AUTHOR, api.PUBLISH, *api.APPROVE.values()}
    assert wanted <= set(CAPABILITIES)
    maker_vs_checker = next(
        r for r in CONFLICT_SETS if r.name == "regulatory-pack-maker-vs-checker"
    )
    assert maker_vs_checker.capabilities == {
        api.AUTHOR,
        *api.APPROVE.values(),
    }  # publishing is not in the set
