"""Company document age rules (WF02 K03; acceptance T08, T09, T25 and the mandate window)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.organization.domain import company_documents as c

NOW = datetime(2026, 11, 17, tzinfo=UTC)
RULES = [
    c.DocumentRule("INCORPORATION_CERTIFICATE", c.Claim.LEGAL_EXISTENCE_HISTORICAL, None),
    c.DocumentRule("REGISTRY_EXTRACT", c.Claim.CURRENT_STATUS, timedelta(days=90)),
    c.DocumentRule("REGISTRY_EXTRACT", c.Claim.LEGAL_EXISTENCE_HISTORICAL, None),
    c.DocumentRule("SHAREHOLDER_REGISTER", c.Claim.OWNERSHIP_STRUCTURE, timedelta(days=365)),
    c.DocumentRule("POWER_OF_ATTORNEY", c.Claim.REPRESENTATIVE_AUTHORITY, None),
]
OLD_CERT = c.CompanyDocument("cert", "INCORPORATION_CERTIFICATE", NOW - timedelta(days=4000))


def run(required, docs):
    return {f.claim: f for f in c.assess(required, RULES, docs, now=NOW)}


def test_an_old_incorporation_certificate_is_not_rejected_for_age_where_it_is_historical_proof() -> (
    None
):  # T08
    r = run([c.Claim.LEGAL_EXISTENCE_HISTORICAL], [OLD_CERT])
    assert (
        r[c.Claim.LEGAL_EXISTENCE_HISTORICAL].satisfied
        and r[c.Claim.LEGAL_EXISTENCE_HISTORICAL].by == "cert"
    )


def test_an_old_certificate_never_stands_in_for_a_current_status_proof() -> None:  # T08
    r = run([c.Claim.LEGAL_EXISTENCE_HISTORICAL, c.Claim.CURRENT_STATUS], [OLD_CERT])
    assert r[c.Claim.CURRENT_STATUS].satisfied is False
    assert r[c.Claim.CURRENT_STATUS].reason == "NO_DOCUMENT_FOR_CLAIM"
    assert c.next_requests(r.values()) == ["UPLOAD_DOCUMENT:CURRENT_STATUS"]


def test_a_fresh_extract_proves_current_status_and_a_stale_one_asks_for_more() -> None:  # T09
    fresh = c.CompanyDocument(
        "x1", "REGISTRY_EXTRACT", NOW - timedelta(days=100), status_date=NOW - timedelta(days=30)
    )
    stale = c.CompanyDocument(
        "x2", "REGISTRY_EXTRACT", NOW - timedelta(days=100), status_date=NOW - timedelta(days=91)
    )
    assert run([c.Claim.CURRENT_STATUS], [fresh])[c.Claim.CURRENT_STATUS].satisfied
    r = run([c.Claim.CURRENT_STATUS], [stale])[c.Claim.CURRENT_STATUS]
    assert not r.satisfied and r.reason == "EVIDENCE_TOO_OLD"
    assert c.next_requests([r]) == ["UPLOAD_MORE_RECENT_EVIDENCE:CURRENT_STATUS"]


def test_the_age_is_counted_from_the_date_the_document_speaks_for_not_the_upload() -> None:
    issued_recently_but_as_of_long_ago = c.CompanyDocument(
        "x", "REGISTRY_EXTRACT", NOW - timedelta(days=1), status_date=NOW - timedelta(days=200)
    )
    assert (
        run([c.Claim.CURRENT_STATUS], [issued_recently_but_as_of_long_ago])[
            c.Claim.CURRENT_STATUS
        ].reason
        == "EVIDENCE_TOO_OLD"
    )


def test_one_good_document_among_stale_ones_is_enough_and_is_the_one_named() -> None:
    stale = c.CompanyDocument("old", "REGISTRY_EXTRACT", NOW, status_date=NOW - timedelta(days=500))
    fresh = c.CompanyDocument("new", "REGISTRY_EXTRACT", NOW, status_date=NOW - timedelta(days=5))
    r = run([c.Claim.CURRENT_STATUS], [stale, fresh])[c.Claim.CURRENT_STATUS]
    assert r.satisfied and r.by == "new"


def test_a_mandate_counts_only_inside_its_own_dates_and_until_it_is_revoked() -> None:
    def poa(**kw):
        return c.CompanyDocument("poa", "POWER_OF_ATTORNEY", NOW - timedelta(days=50), **kw)

    ok = poa(valid_from=NOW - timedelta(days=40), valid_to=NOW + timedelta(days=40))
    assert run([c.Claim.REPRESENTATIVE_AUTHORITY], [ok])[c.Claim.REPRESENTATIVE_AUTHORITY].satisfied
    for bad, why in (
        (poa(valid_from=NOW + timedelta(days=1)), "NOT_YET_VALID"),
        (
            poa(valid_from=NOW - timedelta(days=40), valid_to=NOW - timedelta(days=1)),
            "DOCUMENT_EXPIRED",
        ),
        (poa(valid_from=NOW - timedelta(days=40), revoked=True), "DOCUMENT_REVOKED"),
    ):
        r = run([c.Claim.REPRESENTATIVE_AUTHORITY], [bad])[c.Claim.REPRESENTATIVE_AUTHORITY]
        assert not r.satisfied and r.reason == why, why


def test_a_quarantined_file_proves_nothing_and_is_a_resubmit_not_a_pass() -> None:  # T25
    bad = c.CompanyDocument("evil", "INCORPORATION_CERTIFICATE", NOW, quarantined=True)
    r = run([c.Claim.LEGAL_EXISTENCE_HISTORICAL], [bad])[c.Claim.LEGAL_EXISTENCE_HISTORICAL]
    assert not r.satisfied and r.reason == "DOCUMENT_QUARANTINED"
    assert c.next_requests([r]) == ["RESUBMIT_FILE:LEGAL_EXISTENCE_HISTORICAL"]


def test_a_document_of_the_wrong_class_for_a_claim_does_not_count() -> None:
    wrong = c.CompanyDocument("s", "SHAREHOLDER_REGISTER", NOW)
    assert not run([c.Claim.REPRESENTATIVE_AUTHORITY], [wrong])[
        c.Claim.REPRESENTATIVE_AUTHORITY
    ].satisfied


def test_ownership_papers_age_by_their_own_rule() -> None:
    old = c.CompanyDocument("sh", "SHAREHOLDER_REGISTER", NOW - timedelta(days=400))
    assert (
        run([c.Claim.OWNERSHIP_STRUCTURE], [old])[c.Claim.OWNERSHIP_STRUCTURE].reason
        == "EVIDENCE_TOO_OLD"
    )


def test_nothing_required_nothing_asked() -> None:
    assert c.assess([], RULES, [], now=NOW) == []
    assert c.next_requests([]) == []
