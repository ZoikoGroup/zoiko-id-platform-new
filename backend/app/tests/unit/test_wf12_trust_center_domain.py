"""The Trust Center's contracts (ZID-CP-WF12 v2.0; CR-174): pure rules, one section per wireframe row built."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from zid_platform.modules.trust_center.domain import availability as av
from zid_platform.modules.trust_center.domain import claims as cl
from zid_platform.modules.trust_center.domain import evidence as ev
from zid_platform.modules.trust_center.domain import security_txt as stx

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
SCOPE = cl.Scope("entity_demo", "verify_api", ("US",))
DIG = "a" * 64


def claim(**kw: object) -> cl.Claim:
    base: dict[str, object] = {
        "claim_id": "clm_demo_024", "revision": 3, "exact_text": "Independent assessment available on request", "locale": "en-US",
        "claim_type": cl.ClaimType.THIRD_PARTY_ASSESSMENT, "scope": SCOPE, "channels": ("trust_center",), "evidence_digests": (DIG,),
        "valid_until": date(2027, 1, 31), "author": "alice",
    }  # fmt: skip
    base.update(kw)
    return cl.Claim(**base)  # type: ignore[arg-type]


def ok_evidence(**kw: object) -> dict[str, cl.EvidenceView]:
    v = {"digest": DIG, "current": True, "scope_covers": True, "verified": True}
    v.update(kw)
    return {DIG: cl.EvidenceView(**v)}  # type: ignore[arg-type]


def two_approvals(c: cl.Claim) -> list[cl.Approval]:
    a1 = cl.approve(
        c,
        approver="bob",
        role=cl.Role.SECURITY,
        existing=[],
        evidence_digests=c.evidence_digests,
        now=NOW,
    )
    a2 = cl.approve(
        c,
        approver="carol",
        role=cl.Role.LEGAL,
        existing=[a1],
        evidence_digests=c.evidence_digests,
        now=NOW,
    )
    return [a1, a2]


# ---------------------------------------------------------------------------------------------------- 12.11 hash-bound approvals and separation of duties


def test_nobody_approves_their_own_claim() -> None:
    with pytest.raises(cl.ApprovalError) as e:
        cl.approve(
            claim(),
            approver="alice",
            role=cl.Role.SECURITY,
            existing=[],
            evidence_digests=(DIG,),
            now=NOW,
        )
    assert e.value.code == "SELF_APPROVAL"


def test_one_character_of_copy_invalidates_every_earlier_approval() -> None:
    c = claim()
    approvals = two_approvals(c)
    assert (
        cl.publish_blockers(c, approvals, ok_evidence(), expected_hash=cl.content_hash(c), now=NOW)
        == []
    )
    edited = replace(c, exact_text=c.exact_text + ".")
    blockers = cl.publish_blockers(
        edited, approvals, ok_evidence(), expected_hash=cl.content_hash(edited), now=NOW
    )
    assert [b[0] for b in blockers] == ["APPROVAL_INCOMPLETE"]


def test_a_changed_scope_locale_channel_or_expiry_also_changes_the_hash() -> None:
    c = claim()
    for other in (
        replace(c, locale="fr-FR"),
        replace(c, channels=("trust_center", "sales_deck")),
        replace(c, valid_until=date(2027, 2, 1)),
        replace(c, scope=cl.Scope("entity_demo", "verify_api", ("US", "UK"))),
    ):
        assert cl.content_hash(other) != cl.content_hash(c)


def test_approvals_do_not_carry_to_a_new_revision() -> None:
    c = claim()
    approvals = two_approvals(c)
    v4 = replace(c, revision=4)
    assert cl.standing(v4, approvals, now=NOW)[1] != []


def test_the_same_person_or_the_same_role_cannot_approve_twice() -> None:
    c = claim()
    a1 = cl.approve(
        c, approver="bob", role=cl.Role.SECURITY, existing=[], evidence_digests=(DIG,), now=NOW
    )
    for who, role, code in (
        ("bob", cl.Role.LEGAL, "ALREADY_APPROVED"),
        ("dave", cl.Role.SECURITY, "ROLE_ALREADY_COVERED"),
    ):
        with pytest.raises(cl.ApprovalError) as e:
            cl.approve(c, approver=who, role=role, existing=[a1], evidence_digests=(DIG,), now=NOW)
        assert e.value.code == code


def test_a_role_the_claim_class_does_not_need_is_refused_and_a_certification_sounding_sentence_is_classified_up() -> (
    None
):
    c = claim()
    with pytest.raises(cl.ApprovalError) as e:
        cl.approve(
            c, approver="bob", role=cl.Role.MARKETING, existing=[], evidence_digests=(DIG,), now=NOW
        )
    assert e.value.code == "ROLE_NOT_REQUIRED"
    sneaky = claim(exact_text="We are SOC 2 compliant", claim_type=cl.ClaimType.FUNCTIONALITY)
    assert cl.classify(sneaky.exact_text, sneaky.claim_type) == cl.ClaimType.CERTIFICATION
    assert cl.REQUIRED_ROLES[cl.ClaimType.CERTIFICATION] == {cl.Role.SECURITY, cl.Role.LEGAL}


def test_every_claim_class_needs_at_least_two_distinct_roles() -> None:
    assert all(len(r) >= 2 for r in cl.REQUIRED_ROLES.values())
    assert set(cl.REQUIRED_ROLES) == set(cl.ClaimType)


def test_approving_evidence_other_than_the_evidence_named_is_refused() -> None:
    with pytest.raises(cl.ApprovalError) as e:
        cl.approve(
            claim(),
            approver="bob",
            role=cl.Role.SECURITY,
            existing=[],
            evidence_digests=("b" * 64,),
            now=NOW,
        )
    assert e.value.code == "EVIDENCE_DIFFERS"


def test_a_refusal_needs_a_reason_and_blocks_that_version() -> None:
    c = claim()
    with pytest.raises(cl.ApprovalError):
        cl.refuse(c, approver="bob", role=cl.Role.SECURITY, reason=" ", now=NOW)
    r = cl.refuse(
        c, approver="bob", role=cl.Role.SECURITY, reason="Scope is wider than the report", now=NOW
    )
    assert cl.standing(c, [r], now=NOW)[0] == cl.Status.DRAFT


def test_an_approval_past_its_date_no_longer_counts() -> None:
    c = claim()
    a1 = cl.approve(
        c,
        approver="bob",
        role=cl.Role.SECURITY,
        existing=[],
        evidence_digests=(DIG,),
        now=NOW,
        effective_until=NOW - timedelta(days=1),
    )
    a2 = cl.approve(
        c, approver="carol", role=cl.Role.LEGAL, existing=[a1], evidence_digests=(DIG,), now=NOW
    )
    assert cl.standing(c, [a1, a2], now=NOW)[1]


# ---------------------------------------------------------------------------------------------------- 12.11 publish-time checks


def test_a_tampered_bundle_fails_the_gate() -> None:
    c = claim()
    got = cl.publish_blockers(c, two_approvals(c), ok_evidence(), expected_hash="0" * 64, now=NOW)
    assert "BUNDLE_TAMPERED" in [b[0] for b in got]


@pytest.mark.parametrize(
    ("view", "code"),
    [
        ({"current": False}, "EVIDENCE_EXPIRED"),
        ({"scope_covers": False}, "SCOPE_MISMATCH"),
        ({"verified": False}, "EVIDENCE_UNVERIFIED"),
        ({"self_referential": True}, "EVIDENCE_SELF_REFERENTIAL"),
    ],
)
def test_stale_unscoped_unverified_or_self_referential_evidence_blocks_publication_even_with_both_approvals(
    view: dict[str, bool], code: str
) -> None:
    c = claim()
    got = cl.publish_blockers(
        c, two_approvals(c), ok_evidence(**view), expected_hash=cl.content_hash(c), now=NOW
    )
    assert code in [b[0] for b in got]


def test_a_claim_with_no_evidence_or_a_passed_expiry_cannot_publish() -> None:
    bare = claim(evidence_digests=())
    assert "EVIDENCE_MISSING" in [
        b[0]
        for b in cl.publish_blockers(bare, [], {}, expected_hash=cl.content_hash(bare), now=NOW)
    ]
    old = claim(valid_until=date(2026, 1, 1))
    assert "CLAIM_EXPIRED" in [
        b[0]
        for b in cl.publish_blockers(
            old, two_approvals(old), ok_evidence(), expected_hash=cl.content_hash(old), now=NOW
        )
    ]


def test_a_suppliers_certificate_cannot_be_shown_as_ours() -> None:
    c = claim(
        exact_text="Zoiko iD is certified to ISO 27001", claim_type=cl.ClaimType.CERTIFICATION
    )
    got = cl.publish_blockers(
        c,
        two_approvals(c),
        ok_evidence(is_supplier=True),
        expected_hash=cl.content_hash(c),
        now=NOW,
    )
    assert "SUPPLIER_EVIDENCE_AS_OWN" in [b[0] for b in got]
    honest = claim(
        exact_text="Our cloud provider is certified to ISO 27001",
        claim_type=cl.ClaimType.CERTIFICATION,
    )
    got = cl.publish_blockers(
        honest,
        two_approvals(honest),
        ok_evidence(is_supplier=True),
        expected_hash=cl.content_hash(honest),
        now=NOW,
    )
    assert "SUPPLIER_EVIDENCE_AS_OWN" not in [b[0] for b in got]


# ---------------------------------------------------------------------------------------------------- 12.11 emergency withdrawal and rollback


def test_one_responder_withdraws_at_once_without_any_approver_and_a_review_is_demanded() -> None:
    w = cl.emergency_withdraw(
        claim(),
        actor="eve",
        actor_roles=[cl.Role.RESPONDER],
        reason="FedRAMP status is not verified",
        now=NOW,
    )
    assert w.purge_required and w.review_due > NOW and w.channels == ("trust_center",)


def test_only_a_responder_with_a_reason_can_withdraw() -> None:
    for roles, reason, code in (
        ([cl.Role.PRODUCT], "x", "NOT_A_RESPONDER"),
        ([cl.Role.RESPONDER], "  ", "REASON_REQUIRED"),
    ):
        with pytest.raises(cl.WithdrawError) as e:
            cl.emergency_withdraw(claim(), actor="eve", actor_roles=roles, reason=reason, now=NOW)
        assert e.value.code == code


def test_a_withdrawn_claim_cannot_return_unchanged_only_as_a_new_version() -> None:
    c = claim()
    assert cl.after_withdrawal(c, c)
    assert (
        cl.after_withdrawal(
            c,
            replace(
                c, revision=4, exact_text="Independent assessment summary available on request"
            ),
        )
        == []
    )


def test_rollback_goes_only_to_the_latest_validly_approved_version_never_a_draft_or_a_withdrawn_one() -> (
    None
):
    hist = [
        cl.Revision(1, "h1", approved_validly=True),
        cl.Revision(2, "h2", approved_validly=True, withdrawn=True),
        cl.Revision(3, "h3", approved_validly=False),
        cl.Revision(4, "h4", approved_validly=True),
    ]
    assert cl.rollback_target(hist, current=5).revision == 4  # type: ignore[union-attr]
    assert cl.rollback_target(hist, current=4).revision == 1  # type: ignore[union-attr]
    assert cl.rollback_target(hist[1:3], current=4) is None


# ---------------------------------------------------------------------------------------------------- 12.01 public projection


def test_only_a_published_unexpired_claim_is_projected_and_no_reviewer_detail_leaks() -> None:
    c = replace(claim(), status=cl.Status.PUBLISHED)
    p = cl.project(c, published=True, as_of=NOW, today=TODAY)
    assert p is not None and p.effective_as_of == NOW
    assert "author" not in p.__dict__ and "approvals" not in p.__dict__
    for status in (
        cl.Status.DRAFT,
        cl.Status.APPROVAL_PENDING,
        cl.Status.EMERGENCY_WITHDRAWN,
        cl.Status.EVIDENCE_MISSING,
    ):
        assert cl.project(replace(c, status=status), published=True, as_of=NOW, today=TODAY) is None
    assert cl.project(c, published=False, as_of=NOW, today=TODAY) is None
    assert (
        cl.project(
            replace(c, valid_until=date(2026, 11, 24)), published=True, as_of=NOW, today=TODAY
        )
        is None
    )


# ---------------------------------------------------------------------------------------------------- 12.02 and 12.10 evidence semantics


def artifact(**kw: object) -> ev.Artifact:
    base: dict[str, object] = {
        "evidence_id": "ev_1", "evidence_type": ev.EvidenceType.REPORT, "scheme": "SOC 2", "issuer": "Auditor LLP", "assessor": "Auditor LLP",
        "assessed": ev.Assessed("entity_demo", frozenset({"verify_api"}), frozenset({"US"}), date(2026, 1, 1), date(2026, 12, 31)),
        "digest": DIG, "issuer_confirmed": True, "holder": "zoiko",
    }  # fmt: skip
    base.update(kw)
    return ev.Artifact(**base)  # type: ignore[arg-type]


def test_soc_2_is_a_report_never_a_certificate_and_iso_27001_is_a_certificate_never_a_self_assessment() -> (
    None
):
    assert ev.scheme_type_error("SOC 2", ev.EvidenceType.CERTIFICATE)
    assert ev.scheme_type_error("SOC 2", ev.EvidenceType.REPORT) is None
    assert ev.scheme_type_error("ISO 27001", ev.EvidenceType.SELF_ASSESSMENT)
    assert ev.scheme_type_error("ISO 27001", ev.EvidenceType.CERTIFICATE) is None


def test_an_expired_artifact_is_not_active_whatever_its_stored_state_says() -> None:
    a = artifact(
        assessed=ev.Assessed(
            "entity_demo",
            frozenset({"verify_api"}),
            frozenset({"US"}),
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
    )
    assert ev.lifecycle_on(a, TODAY) == ev.Lifecycle.EXPIRED and not ev.is_current(a, TODAY)
    assert ev.supports_own_certification(a, TODAY)


def test_a_subset_cannot_support_a_universal_claim() -> None:
    a = artifact()
    assert ev.covers(a, ev.Subject("entity_demo", "verify_api", "US", TODAY)) == []
    assert ev.covers(
        a, ev.Subject("entity_demo", "verify_api", "UK", TODAY)
    )  # a UK claim from US-only evidence
    assert ev.covers(a, ev.Subject("entity_demo", "hosted_flow", "US", TODAY))
    assert ev.covers(a, ev.Subject("other_entity", "verify_api", "US", TODAY))
    assert ev.covers(
        replace(a, assessed=replace(a.assessed, regions=frozenset())),
        ev.Subject("entity_demo", "verify_api", "US", TODAY),
    )  # unstated region covers nothing


def test_a_supplier_artifact_never_supports_our_own_certification_and_an_unconfirmed_one_proves_nothing() -> (
    None
):
    assert ev.supports_own_certification(artifact(holder="CloudCo"), TODAY)
    assert ev.supports_own_certification(artifact(issuer_confirmed=False), TODAY)
    assert ev.supports_own_certification(artifact(), TODAY) == []


def test_a_fedramp_claim_needs_a_current_official_listing_for_that_exact_offering() -> None:
    listing = artifact(scheme="FedRAMP", evidence_type=ev.EvidenceType.PROGRAM_LISTING)
    assert ev.fedramp_blockers([], offering="verify_api", today=TODAY)
    assert ev.fedramp_blockers(
        [artifact(scheme="FedRAMP", evidence_type=ev.EvidenceType.REPORT)],
        offering="verify_api",
        today=TODAY,
    )
    assert ev.fedramp_blockers([listing], offering="hosted_flow", today=TODAY)
    assert ev.fedramp_blockers([listing], offering="verify_api", today=TODAY) == []


def test_a_figure_like_6b_or_150_customers_fails_without_a_definition_a_query_a_source_and_a_window() -> (
    None
):
    q = ev.Quantity("6B+ decisions", "", "", "", None, None, False)
    assert len(ev.quantity_blockers(q)) == 5
    good = ev.Quantity(
        "150+ customers",
        "active paying tenants",
        "sql://finance/q1",
        "crm",
        date(2026, 1, 1),
        date(2026, 10, 31),
        True,
    )
    assert ev.quantity_blockers(good) == []


def test_a_named_customer_story_needs_signed_current_permission_for_exactly_what_it_shows() -> None:
    e = ev.Endorsement("TechCorp", None, None, None, False, False, None)
    got = ev.endorsement_blockers(
        e, today=TODAY, shows_name=True, shows_logo=True, shows_results=True
    )
    assert len(got) == 4
    signed = ev.Endorsement(
        "TechCorp", "perm_1", date(2026, 3, 1), date(2027, 3, 1), True, False, "ev_results"
    )
    assert (
        ev.endorsement_blockers(
            signed, today=TODAY, shows_name=True, shows_logo=False, shows_results=True
        )
        == []
    )
    assert ev.endorsement_blockers(
        signed, today=TODAY, shows_name=True, shows_logo=True, shows_results=False
    )  # logo not allowed
    assert ev.endorsement_blockers(
        replace(signed, permission_expires=date(2026, 6, 1)),
        today=TODAY,
        shows_name=True,
        shows_logo=False,
        shows_results=False,
    )


# ---------------------------------------------------------------------------------------------------- 12.06 availability


def test_an_empty_history_is_never_one_hundred_percent() -> None:
    for s in (None, av.Snapshot("verify_api", "us", "2026-10", 0, 0, "", True)):
        r = av.read(s)
        assert r.state == av.Shown.METRIC_UNAVAILABLE and r.percent is None


def test_an_unvalidated_or_irreconcilable_figure_is_not_shown() -> None:
    assert av.read(av.Snapshot("verify_api", "us", "2026-10", 99, 100, "", False)).percent is None
    assert av.read(av.Snapshot("verify_api", "us", "2026-10", 101, 100, "", True)).percent is None
    r = av.read(
        av.Snapshot(
            "verify_api", "us", "2026-10", 9990, 10000, "Excludes planned maintenance.", True
        )
    )
    assert r.percent == 99.9 and "maintenance" in r.note


def test_a_published_figure_must_reproduce_from_raw_telemetry() -> None:
    s = av.Snapshot("verify_api", "us", "2026-10", 9990, 10000, "", True)
    assert (
        av.reconciles(s, 9990, 10000)
        and not av.reconciles(s, 9900, 10000)
        and not av.reconciles(s, 0, 0)
    )


def test_a_service_target_is_worded_as_a_target_and_only_for_a_covered_tier() -> None:
    t = av.SlaTerm("enterprise", 99.9, ("enterprise",))
    assert "target, not a measured result" in (
        av.sla_statement(t, customer_tier="enterprise") or ""
    )
    assert (
        av.sla_statement(t, customer_tier="free") is None
        and av.sla_statement(t, customer_tier="sandbox") is None
    )
    assert av.sla_statement(av.SlaTerm("x", 99.9, ("free",)), customer_tier="free") is None


# ---------------------------------------------------------------------------------------------------- 12.07 security.txt


def good_txt(**kw: object) -> stx.SecurityTxt:
    base: dict[str, object] = {
        "contacts": ("mailto:security@example.test",),
        "expires": NOW + timedelta(days=180),
        "canonical": ("https://example.test/.well-known/security.txt",),
        "policy": "https://example.test/security/policy",
    }
    base.update(kw)
    return stx.SecurityTxt(**base)  # type: ignore[arg-type]


def test_security_txt_renders_the_required_fields_and_the_checker_accepts_it() -> None:
    body = stx.render(good_txt(), now=NOW)
    assert body.startswith("Contact: mailto:security@example.test\nExpires: 2027-")
    assert (
        stx.check(
            body,
            content_type="text/plain; charset=utf-8",
            served_url="https://example.test/.well-known/security.txt",
            now=NOW,
        )
        == []
    )


def test_an_expired_or_contactless_or_insecure_file_is_never_rendered() -> None:
    for bad in (
        good_txt(expires=NOW - timedelta(days=1)),
        good_txt(contacts=()),
        good_txt(contacts=("security@example.test",)),
        good_txt(canonical=("http://example.test/x",)),
        good_txt(expires=NOW + timedelta(days=900)),
    ):
        with pytest.raises(ValueError):
            stx.render(bad, now=NOW)


def test_the_checker_names_what_is_wrong_with_a_served_file() -> None:
    body = "Contact: mailto:a@example.test\nExpires: 2020-01-01T00:00:00.000Z\nCanonical: https://elsewhere.test/x\n"
    got = stx.check(
        body,
        content_type="text/html",
        served_url="https://example.test/.well-known/security.txt",
        now=NOW,
    )
    assert len(got) == 3


def test_placeholder_and_broken_links_are_reported() -> None:
    got = stx.dead_links(
        [
            ("#", 200),
            ("javascript:void(0)", 200),
            ("https://x.test/a", 404),
            ("https://x.test/ok", 200),
            ("https://x.test/gone", 0),
        ]
    )
    assert len(got) == 4


def test_changed_evidence_bytes_change_the_hash_so_the_approval_no_longer_applies() -> None:
    c = claim()
    other = replace(c, evidence_digests=("b" * 64,))
    assert cl.content_hash(other) != cl.content_hash(c)
    assert cl.standing(other, two_approvals(c), now=NOW)[1]


# ---------------------------------------------------------------------------------------------------- 12.04 disclosure minimization and 12.07, 12.08 reports and advisories

from zid_platform.modules.trust_center.domain import disclosure as dz
from zid_platform.modules.trust_center.domain import reports as rp


@pytest.mark.parametrize(
    ("sample", "name"),
    [
        (
            "key -----BEGIN PRIVATE KEY----- abc",  # zid-secret-scan: allow: redaction test input
            "PRIVATE_KEY",
        ),
        ("token zvl_abcdefghijkl", "PLATFORM_TOKEN"),
        ("AKIAABCDEFGHIJKLMNOP", "CLOUD_KEY"),  # zid-secret-scan: allow: redaction test input
        ("host 10.0.3.17 is reachable", "IPV4"),
        ("subnet 10.0.0.0/16", "CIDR_OR_SUBNET"),
        ("db.prod.internal", "INTERNAL_HOST"),
        ("postgresql://u:p@db/app", "DB_URL"),
        ("a card 4111111111111111", "LONG_NUMBER"),
        ("see jane@example.com", "EMAIL_ADDRESS"),
        ("the exploit chain works", "EXPLOIT_DETAIL"),
    ],
)
def test_public_text_carrying_a_secret_an_address_or_attack_detail_is_found(
    sample: str, name: str
) -> None:
    assert name in dz.findings(sample)


def test_ordinary_trust_language_passes_and_an_allowed_contact_is_not_a_finding() -> None:
    assert dz.findings("Customer data is stored in the region the customer chooses.") == []
    assert dz.findings("Write to security@example.com", allow=("EMAIL_ADDRESS",)) == []
    assert dz.vague_security_language("Our military-grade encryption is unbreakable") == [
        "military-grade",
        "unbreakable",
    ]


def test_a_report_state_only_moves_forward_and_a_severity_sets_a_response_target() -> None:
    assert rp.can_move(rp.ReportState.RECEIVED, rp.ReportState.ACKNOWLEDGED)
    assert not rp.can_move(rp.ReportState.RECEIVED, rp.ReportState.FIXED)
    assert not rp.can_move(rp.ReportState.CLOSED, rp.ReportState.RECEIVED)
    assert rp.ack_due(NOW, "critical") < rp.ack_due(NOW, "low")
    r = rp.receipt("VR-ABCDEFGHJK", NOW, "high")
    assert "target" in r["note"].lower() and "reward" in r["note"].lower() and "summary" not in r


def test_a_report_too_short_or_too_long_is_refused_and_a_real_one_is_taken_as_written() -> None:
    assert rp.intake_problems("short", "", contact=None)
    assert rp.intake_problems("x" * 9000, "", contact=None)
    assert (
        rp.intake_problems(
            "I found that the sign-up page reveals whether an address is registered.",
            "",
            contact=None,
        )
        == []
    )


def _v(seq: int, kind: rp.AdvisoryKind, **kw: object) -> rp.AdvisoryVersion:
    base: dict[str, object] = {
        "seq": seq,
        "kind": kind,
        "title": "Delayed sign-in",
        "body": "Some sign-ins were delayed.",
        "reason": "",
        "author": "alice",
        "approved_by": "bob",
    }
    base.update(kw)
    return rp.AdvisoryVersion(**base)  # type: ignore[arg-type]


def test_an_advisory_needs_a_second_approver_a_reason_for_a_correction_and_no_attack_detail() -> (
    None
):
    first = _v(1, rp.AdvisoryKind.PUBLISH)
    assert rp.advisory_problems(first, None) == []
    assert rp.advisory_problems(_v(1, rp.AdvisoryKind.PUBLISH, approved_by=None), None)
    assert rp.advisory_problems(_v(1, rp.AdvisoryKind.PUBLISH, approved_by="alice"), None)
    assert rp.advisory_problems(_v(2, rp.AdvisoryKind.CORRECTION), first)  # no reason
    assert (
        rp.advisory_problems(_v(2, rp.AdvisoryKind.CORRECTION, reason="Wrong start time"), first)
        == []
    )
    assert rp.advisory_problems(
        _v(2, rp.AdvisoryKind.UPDATE, body="The exploit chain used 10.1.2.3"), first
    )
    assert rp.advisory_problems(_v(1, rp.AdvisoryKind.PUBLISH), first)  # already public
    assert rp.advisory_problems(_v(2, rp.AdvisoryKind.UPDATE), None)  # nothing to update


def test_the_public_history_keeps_every_approved_version_and_marks_a_correction() -> None:
    vs = [
        _v(1, rp.AdvisoryKind.PUBLISH),
        _v(2, rp.AdvisoryKind.CORRECTION, reason="Wrong start time"),
        _v(3, rp.AdvisoryKind.UPDATE, approved_by=None),
    ]
    h = rp.public_history(vs)
    assert (
        [x["seq"] for x in h] == [1, 2]
        and h[1]["correction_reason"] == "Wrong start time"
        and h[0]["correction_reason"] is None
    )


# ---------------------------------------------------------------------------------------------------- 12.05 privacy and subprocessor disclosures

from zid_platform.modules.trust_center.domain import privacy as pv


def test_each_disclosure_kind_names_what_it_must_carry() -> None:
    assert pv.problems("subprocessor", "CloudCo", {})
    ok = {
        "purpose": "hosting",
        "country": "GB",
        "access_mode": "hosting",
        "data_categories": ["DC2"],
        "contract_ref": "DPA-7",
    }
    assert pv.problems("subprocessor", "CloudCo", ok) == []
    assert pv.problems(
        "subprocessor", "CloudCo", ok | {"notice_days": 30}
    )  # a notice period must name its source
    assert (
        pv.problems(
            "subprocessor", "CloudCo", ok | {"notice_days": 30, "notice_source": "DPA-7 clause 9"}
        )
        == []
    )
    assert pv.problems("subprocessor", "CloudCo", ok | {"country": "UK1"})
    assert pv.problems(
        "retention",
        "images",
        {"data_category": "DC3", "rule": "Deleted 30 days after the decision"},
    )  # no basis, no instruction
    assert pv.problems(
        "biometric",
        "face",
        {
            "necessity": "x",
            "safeguards": "y",
            "alternative_route": "manual",
            "capability_ref": "DTV-12",
            "dpia_applicable": True,
        },
    )
    assert pv.problems(
        "transfer", "GB-US", {"from_country": "GB", "to_country": "US", "mechanism": "IDTA"}
    )  # remote support and backups unsaid
    assert (
        pv.problems(
            "transfer",
            "GB-US",
            {
                "from_country": "GB",
                "to_country": "US",
                "mechanism": "IDTA",
                "remote_support": False,
                "backups": False,
            },
        )
        == []
    )


def test_a_universal_thirty_day_notice_promise_is_refused_unless_it_cites_a_contract() -> None:
    assert pv.text_problems("We give 30 days notice of any change.")
    assert pv.text_problems("Under the customer contract, notice is 30 days.") == []


def test_a_regional_residency_claim_is_blocked_by_any_subprocessor_or_transfer_that_leaves_the_region() -> (
    None
):
    subs = [
        {"name": "CloudCo", "country": "GB", "data_categories": ["DC2"], "access_mode": "hosting"},
        {
            "name": "SupportCo",
            "country": "IN",
            "data_categories": ["DC3"],
            "access_mode": "support_remote",
        },
    ]
    assert pv.region_claim_blockers("GB", subs, [])
    assert pv.region_claim_blockers("GB", subs[:1], []) == []
    assert pv.region_claim_blockers(
        "GB",
        subs[:1],
        [
            {
                "from_country": "GB",
                "to_country": "US",
                "mechanism": "IDTA",
                "remote_support": True,
                "backups": False,
            }
        ],
    )
    assert (
        pv.region_claim_blockers(
            "GB",
            [
                {
                    "name": "Stats",
                    "country": "US",
                    "data_categories": ["DC1"],
                    "access_mode": "analytics",
                }
            ],
            [],
        )
        == []
    )


# ---------------------------------------------------------------------------------------------------- 12.12 drift scanning and the release gate

from zid_platform.modules.trust_center.domain import monitoring as mon

PAGE = """<html><body><h1>Trust</h1>
<p>Zoiko iD is SOC 2 Type II and ISO 27001 certified. We guarantee 99.99% uptime with under 50ms latency. Over 150+ enterprise customers trust us.</p>
<p>An independent SOC 2 report is available on request.</p><p>Our military-grade encryption is unbreakable.</p>
<script type="application/ld+json">{"@type":"Organization","aggregateRating":{"ratingValue":5},"award":"Best IDV"}</script>
</body></html>"""


def test_the_scanner_finds_number_certification_superlative_and_structured_data_claims() -> None:
    found = mon.observe("https://x.test/", PAGE)
    kinds = {o.why for o in found}
    assert {"CERTIFICATION", "NUMBER", "SUPERLATIVE", "STRUCTURED_DATA"} <= kinds


def test_only_a_sentence_that_matches_a_live_approved_one_passes_and_a_withdrawn_one_is_a_defect() -> (
    None
):
    found = mon.observe("https://x.test/", PAGE)
    live = ["An independent SOC 2 report is available on request."]
    codes = [f.code for f in mon.reconcile(found, live, withdrawn=[])]
    assert "UNREGISTERED_CLAIM" in codes and "VAGUE_SECURITY_LANGUAGE" in codes
    assert not any(
        "independent soc 2 report" in f.sentence.lower()
        for f in mon.reconcile(found, live, withdrawn=[])
    )  # the approved sentence is not reported
    withdrawn = mon.reconcile(
        found, [], withdrawn=["An independent SOC 2 report is available on request."]
    )
    assert "WITHDRAWN_CLAIM_STILL_LIVE" in [f.code for f in withdrawn]
    clean = mon.observe("https://x.test/about", "<p>We build identity verification.</p>")
    assert mon.reconcile(clean, [], []) == []


def test_the_expiry_watch_lists_soonest_first_and_a_lapsed_date_is_zero_days() -> None:
    soon = claim(claim_id="clm_a", valid_until=TODAY + timedelta(days=10))
    far = claim(claim_id="clm_b", valid_until=TODAY + timedelta(days=200))
    got = mon.expiry_watch([(far, TODAY + timedelta(days=5)), (soon, None)], today=TODAY)
    assert [(w.claim_id, w.days_left) for w in got] == [("clm_b", 5), ("clm_a", 10)]
    assert mon.expiry_watch([(far, TODAY - timedelta(days=3))], today=TODAY)[0].days_left == 0


def test_the_release_gate_needs_zero_unsupported_claims_and_no_overdue_obligations() -> None:
    assert mon.release_gate(
        unsupported_live=0, overdue_withdrawal_reviews=0, unacknowledged_reports=0
    ) == (True, [])
    ok, why = mon.release_gate(
        unsupported_live=2, overdue_withdrawal_reviews=1, unacknowledged_reports=1
    )
    assert not ok and len(why) == 3
