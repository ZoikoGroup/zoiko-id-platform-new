"""The section 08.1 assertion contract: completion, canonical digest and the signature binding (pure, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.credential.application import assertion_contract as ac

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
SUBJECT = "0194f5a0-1111-7222-8333-444455556666"
CHECK = "0194f5a0-aaaa-7bbb-8ccc-ddddeeeeffff"


def base(**over):
    body = {
        "claim_type": "PROFESSIONAL_LICENSE",
        "decision": "VERIFIED",
        "credential_status": "ACTIVE",
        "assurance_tier": "PRIMARY_SOURCE_CONFIRMED",
        "subject_match": {"decision": "MATCHED"},
        "jurisdiction": {"country": "US", "subdivision": "CA"},
        "reason_codes": [],
    }
    body.update(over)
    return body


def complete(body=None, **over):
    args = {
        "check_id": CHECK,
        "subject": SUBJECT,
        "consumer": "zoikorum",
        "consent_ref": "cons_1",
        "receipts": ["r-1"],
        "pack_id": "us-ca-law",
        "pack_version": 3,
        "source_result": "RESPONDED",
        "source_id": "registrar",
    }
    args.update(over)
    return ac.complete(body or base(), **args)


def fake_signer(claims):
    return "header." + str(sorted(claims.items())) + ".sig"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ACTIVE", "GOOD"),
        ("REVOKED", "REVOKED"),
        ("SUSPENDED", "SUSPENDED"),
        ("EXPIRED", "EXPIRED"),
        ("UNKNOWN", "UNKNOWN"),
        ("", "UNKNOWN"),
        ("whatever", "UNKNOWN"),
    ],
)
def test_revocation_status_is_good_only_for_an_active_credential(status, expected):
    assert ac.revocation_status(status) == expected


@pytest.mark.parametrize(
    ("source_result", "tier", "source_id", "method"),
    [
        ("RESPONDED", "PRIMARY_SOURCE_CONFIRMED", "registrar", "PRIMARY_SOURCE"),
        ("RESPONDED", "PRIMARY_SOURCE_CONFIRMED", "outreach:ab12", "ISSUER_OUTREACH"),
        ("NOT_QUERIED", "SELF_DECLARED", "not-queried", "NOT_CHECKED"),
        ("RESPONDED", "DOCUMENT_INTEGRITY", "registrar", "NONE"),
        ("NO_RECORD", "PRIMARY_SOURCE_CONFIRMED", "registrar", "NONE"),
    ],
)
def test_the_match_method_says_how_the_subject_was_matched(source_result, tier, source_id, method):
    assert ac.match_method(source_result, tier, source_id) == method


def test_completion_adds_every_section_08_1_field_and_keeps_the_five_facts():
    c = complete()
    for key in (
        "assertion_id",
        "actor_ref",
        "consumer",
        "consent_ref",
        "policy_ref",
        "revocation_status",
        "evidence_receipts",
        "subject_match",
    ):
        assert key in c, key
    assert c["assertion_id"] == f"asrt_{CHECK}" and c["actor_ref"] == f"sub_{SUBJECT}"
    assert c["consumer"] == "zoikorum" and c["consent_ref"] == "cons_1"
    assert c["policy_ref"] == {"id": "us-ca-law", "version": 3}
    assert c["revocation_status"] == "GOOD" and c["evidence_receipts"] == ["rcpt_r-1"]
    assert c["subject_match"] == {"decision": "MATCHED", "method": "PRIMARY_SOURCE"}
    assert (
        c["credential_status"] == "ACTIVE" and c["decision"] == "VERIFIED"
    )  # the original facts are untouched


def test_completion_does_not_mutate_its_input_and_handles_no_receipt_and_no_consent():
    original = base()
    c = complete(original, receipts=[], consent_ref=None)
    assert original == base() and c["evidence_receipts"] == [] and c["consent_ref"] is None


def test_the_digest_ignores_key_order_signature_and_superseded_but_not_content():
    a = complete()
    shuffled = dict(reversed(list(a.items())))
    assert ac.body_digest(a) == ac.body_digest(shuffled)
    assert ac.body_digest(a) == ac.body_digest({**a, "signature": "x", "superseded": True})
    for changed in (
        {"decision": "NOT_VERIFIED"},
        {"jurisdiction": {"country": "US"}},
        {"consumer": "rooms"},
        {"extra": 1},
    ):
        assert ac.body_digest({**a, **changed}) != ac.body_digest(a), changed


def test_the_digest_covers_dates_as_strings_so_a_reader_sees_the_same_bytes():
    with_date = {**complete(), "checked": datetime(2026, 1, 1, tzinfo=UTC)}
    as_text = {**complete(), "checked": str(datetime(2026, 1, 1, tzinfo=UTC))}
    assert ac.body_digest(with_date) == ac.body_digest(as_text)


def test_seal_without_a_signer_leaves_a_null_signature_and_with_one_binds_the_body():
    c = complete()
    assert ac.seal(c, None, NOW)["signature"] is None
    sealed = ac.seal(c, fake_signer, NOW)
    assert sealed["signature"] and sealed["check_id"] == CHECK
    claims = {
        "typ": "zid-assertion",
        "check_id": CHECK,
        "assertion_id": f"asrt_{CHECK}",
        "body_sha256": ac.body_digest(c),
        "iat": int(NOW.timestamp()),
    }
    assert ac.binds(sealed, claims)


def test_the_signature_binds_the_exact_body_and_no_other():
    sealed = ac.seal(complete(), fake_signer, NOW)
    good = {
        "typ": "zid-assertion",
        "check_id": CHECK,
        "assertion_id": f"asrt_{CHECK}",
        "body_sha256": ac.body_digest(sealed),
    }
    assert ac.binds(sealed, good)
    assert ac.binds(
        {**sealed, "superseded": True}, good
    )  # a replaced check is the same signed body
    for tampered in (
        {**sealed, "decision": "NOT_VERIFIED"},
        {**sealed, "revocation_status": "REVOKED"},
        {**sealed, "evidence_receipts": []},
        {**sealed, "check_id": "other"},
        {**sealed, "actor_ref": "sub_someone-else"},
    ):
        assert not ac.binds(tampered, good), tampered
    for claims in (
        {**good, "typ": "something-else"},
        {**good, "check_id": "other"},
        {**good, "body_sha256": "0" * 64},
        {},
    ):
        assert not ac.binds(sealed, claims), claims


def test_seal_discards_an_old_signature_before_signing():
    first = ac.seal(complete(), fake_signer, NOW)
    again = ac.seal({**first, "decision": "REVIEW"}, fake_signer, NOW)
    assert again["signature"] != first["signature"]
    assert ac.body_digest(again) != ac.body_digest(first)


def test_the_digest_matches_the_pinned_cross_language_vector():
    """The same body and digest are pinned in the Python and TypeScript SDK tests: all three must agree byte for byte, including
    non-ASCII text, key order, nulls and the keys that are not part of the body."""
    body = {
        "check_id": "c1",
        "assertion_id": "asrt_c1",
        "decision": "VERIFIED",
        "jurisdiction": {"country": "US", "subdivision": "CA"},
        "reason_codes": [],
        "policy_ref": {"id": "p", "version": 1},
        "explanation": {"limitations": ["Does not prove x \u2014 \u00e9 \u4e2d"]},
        "primary_source_verified": True,
        "expires_at": None,
        "superseded": True,
        "signature": "zzz",
    }
    assert (
        ac.body_digest(body) == "afd153fe44c3678a55e6b8e6f2f9d6b4d0a33b5f854f82ea49d49a8f3a141789"
    )
