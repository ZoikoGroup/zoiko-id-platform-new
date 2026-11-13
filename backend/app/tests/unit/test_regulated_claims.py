"""Regulated claims publisher (ZID-RCG-003 RCG-G07; T05, T33; CR-077): only an approved template, only while its exact certifications and markets hold,
never global, and every decision exportable as a proof bundle."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain import regulated_claims as rc
from zid_platform.modules.verification.domain.attestation import Decision, Outcome, Scope, Snapshot
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.domain.market_release import (
    MarketDecision,
    MarketKey,
    MarketRelease,
    ReleaseStatus,
)

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
RTR = Scope("PARTNER-1", "UK_DIATF", "GB", "RTR")
MARKET = MarketKey("GB", "INDIVIDUAL_VERIFICATION", "STANDARD")


def snapshot(scope: Scope) -> Snapshot:
    return Snapshot(
        snapshot_id="snap-1",
        scope=scope,
        scheme_version="1",
        certificate_ref="C-1",
        component_only=False,
        status=CertStatus.ACTIVE,
        valid_from=date(2025, 1, 1),
        valid_until=None,
        register_url="https://register.example.test/1",
        register_entry_id="E-1",
        cab=None,
        fetched_at=NOW - timedelta(days=1),
        proof_hash="a" * 64,
        evidence_locator="vault:x/y",
        recorded_by="ops",
        recorded_at=NOW - timedelta(days=1),
    )


CERTIFIED = {RTR: Decision(Outcome.CERTIFIED, "certified", snapshot(RTR))}
RELEASE = MarketRelease("gb-rel", 1, MARKET, ReleaseStatus.LAUNCHED, "maker", release_hash="b" * 64)
OPEN = {MARKET: MarketDecision(True, "release_launched", RELEASE)}


def template(**over: Any) -> rc.ClaimTemplate:
    base = rc.ClaimTemplate(
        template_id="gb-rtr",
        version=1,
        text="Right to Rent checks in England use a certified identity service provider.",
        audiences=frozenset({"PUBLIC_WEBSITE", "SALES"}),
        countries=("GB",),
        certifications=(RTR,),
        markets=(MARKET,),
    )
    t = replace(base, **over)
    if "approvals" not in over:
        digest = rc.template_hash(t)
        t = replace(
            t, approvals={r: rc.Approval(f"REF-{r}", digest) for r in rc.CLAIM_APPROVER_ROLES}
        )
    return t


def decide(t: rc.ClaimTemplate, **kw: Any) -> rc.ClaimDecision:
    args: dict[str, Any] = {"at": NOW, "certifications": CERTIFIED, "markets": OPEN} | kw
    return rc.decide(t, **args)


def test_an_approved_template_whose_basis_holds_is_publishable_with_its_evidence() -> None:
    d = decide(template(), audience="PUBLIC_WEBSITE")
    assert d.publishable and d.reasons == ()
    kinds = {e["kind"] for e in d.evidence}
    assert kinds == {"certification", "market"}
    cert = next(e for e in d.evidence if e["kind"] == "certification")
    assert (cert["snapshot_id"], cert["proof_hash"]) == ("snap-1", "a" * 64)


@pytest.mark.parametrize(
    "text",
    [
        "Globally certified identity verification.",
        "Certified worldwide.",
        "Certified world-wide identity checks.",
        "Accepted in all countries.",
        "Internationally certified provider.",
    ],
)
def test_t33_a_global_certification_claim_is_blocked(text: str) -> None:
    d = decide(template(text=text))
    assert not d.publishable and "TEMPLATE_GLOBAL_CLAIM" in d.reasons


def test_t33_a_template_with_no_country_or_a_wildcard_is_blocked() -> None:
    assert "TEMPLATE_GLOBAL_CLAIM" in decide(template(countries=())).reasons
    assert "TEMPLATE_GLOBAL_CLAIM" in decide(template(countries=("*",))).reasons


def test_t05_a_certified_company_with_an_unrelated_service_is_not_a_basis() -> None:
    idv = Scope("PARTNER-1", "UK_DIATF", "GB", "IDV")  # the same company, another service
    certified_idv = {idv: Decision(Outcome.CERTIFIED, "certified", snapshot(idv))}
    d = decide(template(), certifications=certified_idv)
    assert not d.publishable
    assert "certification_hold:PARTNER-1/UK_DIATF/GB/RTR:not_checked" in d.reasons


def test_a_certification_on_hold_or_a_closed_market_blocks_at_once() -> None:
    held = {RTR: Decision(Outcome.HOLD, "attestation_stale", None)}
    assert (
        "certification_hold:PARTNER-1/UK_DIATF/GB/RTR:attestation_stale"
        in decide(template(), certifications=held).reasons
    )
    closed = {MARKET: MarketDecision(False, "withdrawn", None)}
    assert (
        "market_closed:GB/INDIVIDUAL_VERIFICATION/STANDARD:withdrawn"
        in decide(template(), markets=closed).reasons
    )


def test_a_country_the_claim_names_must_be_backed_and_a_basis_must_be_inside_the_claim() -> None:
    assert "TEMPLATE_COUNTRY_UNSUPPORTED" in decide(template(countries=("GB", "IE"))).reasons
    fr = MarketKey("FR", "INDIVIDUAL_VERIFICATION", "STANDARD")
    assert "TEMPLATE_BASIS_OUTSIDE_COUNTRIES" in decide(template(markets=(MARKET, fr))).reasons
    assert "TEMPLATE_NO_BASIS" in decide(template(certifications=(), markets=())).reasons


def test_both_approvals_are_needed_and_editing_after_approval_breaks_the_hash() -> None:
    t = template()
    one = replace(t, approvals={"legal": t.approvals["legal"]})
    assert "approval_missing:claims_owner" in decide(one).reasons
    edited = replace(t, text=t.text + " Fast.")  # changed after both approvers signed
    reasons = decide(edited).reasons
    assert (
        "approval_hash_mismatch:legal" in reasons
        and "approval_hash_mismatch:claims_owner" in reasons
    )
    blank = replace(
        t, approvals={r: rc.Approval(" ", a.signed_hash) for r, a in t.approvals.items()}
    )
    assert "approval_missing:legal" in decide(blank).reasons


def test_dates_and_audience_limit_where_and_when_a_claim_appears() -> None:
    later = template(effective_from=NOW + timedelta(days=1))
    assert "not_yet_effective" in decide(later).reasons
    ended = template(effective_until=NOW)
    assert "expired" in decide(ended).reasons
    assert "TEMPLATE_NOT_FOR_AUDIENCE" in decide(template(), audience="PRODUCT_LABEL").reasons
    assert (
        "TEMPLATE_AUDIENCE_INVALID" in decide(template(audiences=frozenset({"BILLBOARD"}))).reasons
    )


def test_a_proof_bundle_carries_the_evidence_and_detects_tampering() -> None:
    t = template()
    bundle = rc.proof_bundle(t, decide(t), at=NOW)
    assert bundle["publishable"] is True and bundle["template"][
        "template_hash"
    ] == rc.template_hash(t)
    assert rc.verify_bundle(bundle)
    forged = dict(bundle) | {"publishable": False}
    assert not rc.verify_bundle(forged)
    # the same decision gives the same digest: it can be replayed
    assert rc.proof_bundle(t, decide(t), at=NOW)["digest"] == bundle["digest"]


def test_parse_reads_templates_and_keeps_an_unpublishable_one_visible() -> None:
    doc = {
        "templates": [
            {
                "template_id": "gb-rtr",
                "version": 2,
                "text": "Certified worldwide.",  # loads, then is blocked by the decision
                "audiences": ["public_website"],
                "countries": ["gb"],
                "certifications": [
                    {"entity": "P", "scheme": "S", "country": "gb", "service_code": "RTR"}
                ],
                "effective_from": "2027-01-01T00:00:00+00:00",
            },
            {"template_id": "gb-rtr", "version": 1, "text": "x", "countries": ["GB"]},
        ]
    }
    ts = rc.parse(doc)
    assert [t.version for t in rc.latest(ts)] == [2]
    t = rc.latest(ts)[0]
    assert t.audiences == frozenset({"PUBLIC_WEBSITE"}) and t.certifications[0].country == "GB"
    assert "TEMPLATE_GLOBAL_CLAIM" in decide(t).reasons


@pytest.mark.parametrize(
    "doc",
    [
        {"templates": [{"template_id": "Bad_ID", "version": 1, "text": "x"}]},
        {"templates": [{"template_id": "ok", "version": "one", "text": "x"}]},
        {
            "templates": [
                {
                    "template_id": "ok",
                    "version": 1,
                    "text": "x",
                    "effective_from": "2027-01-01T00:00:00",
                }
            ]
        },  # no zone
        {
            "templates": [
                {"template_id": "ok", "version": 1, "text": "x"},
                {"template_id": "ok", "version": 1, "text": "y"},
            ]
        },
    ],
)
def test_a_malformed_file_is_refused_whole(doc: dict[str, Any]) -> None:
    with pytest.raises(rc.ClaimTemplateError) as e:
        rc.parse(doc)
    assert e.value.code == "CLAIM_TEMPLATES_INVALID"


def test_the_shipped_file_lists_no_template_so_nothing_is_claimed() -> None:
    from zid_platform.modules.verification.infrastructure.claim_templates import claim_templates

    assert claim_templates() == ()
