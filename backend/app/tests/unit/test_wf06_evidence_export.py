"""WF06 evidence export (CR-113): purpose bound, redacted, capped, watermarked, tamper evident (QA-040, QA-043; ENG-16)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import evidence_export as x

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
PURPOSE = "Annual audit of screening decisions for the 2026 review"


def art(kind, ref="a1", **data):
    return x.Artifact(kind, ref, data)


ARTIFACTS = [
    art(
        "SOURCE_SNAPSHOT",
        "s1",
        source="OFAC_SDN",
        snapshot="sha256:aa",
        note="checked for Jane Smith",
    ),
    art(
        "DISPOSITION",
        "d1",
        status="CLEARED_FOR_PURPOSE",
        reason_codes=["NO_CANDIDATE_IN_CHECKED_SOURCES"],
    ),
    art(
        "MATCH_CANDIDATE",
        "m1",
        name="Jane Smith",
        birth="1970-01-01",
        identifiers=["AB1234567"],
        score=0.91,
    ),
    art("CASE_NOTE", "n1", text="spoke to the applicant", author="analyst-1"),
]


def build(**kw):
    args = {
        "purpose": PURPOSE,
        "requester": "auditor-1",
        "approver": None,
        "policy_version": "v1",
        "chain": ["h1", "h2"],
        "now": NOW,
    }
    args.update(kw)
    return x.build("scr_1", ARTIFACTS, **args)


def code(fn) -> str:
    with pytest.raises(x.ExportError) as e:
        fn()
    return e.value.code


def test_the_default_bundle_holds_only_the_open_kinds_and_says_what_it_left_out() -> None:
    b = build()
    assert [i["kind"] for i in b.included] == ["SOURCE_SNAPSHOT", "DISPOSITION"]
    assert b.excluded == (
        {"kind": "MATCH_CANDIDATE", "ref": "m1", "why": "SENSITIVE_NOT_APPROVED"},
        {"kind": "CASE_NOTE", "ref": "n1", "why": "SENSITIVE_NOT_APPROVED"},
    )
    assert b.approver is None and x.verify(b, now=NOW) == []


def test_a_stray_name_in_an_open_kind_never_leaves() -> None:
    b = build()
    note = b.included[0]["data"]["note"]
    assert (
        "Jane Smith" in note
    )  # free text is kept as written: the kind is open, so it must never be given a name
    assert x.leaks_personal_data(b) == []  # no personal FIELD is in clear


def test_sensitive_kinds_need_a_second_person_who_is_not_the_requester_and_leave_only_as_hashes() -> (
    None
):
    assert code(lambda: build(include_sensitive=True)) == "SECOND_APPROVER_REQUIRED"
    assert (
        code(lambda: build(include_sensitive=True, approver="auditor-1"))
        == "SECOND_APPROVER_REQUIRED"
    )
    b = build(include_sensitive=True, approver="privacy-1")
    cand = next(i for i in b.included if i["kind"] == "MATCH_CANDIDATE")["data"]
    assert cand["name"].startswith("redacted:") and cand["birth"].startswith("redacted:")
    assert cand["identifiers"].startswith("redacted:") and cand["score"] == 0.91
    assert (
        b.approver == "privacy-1" and b.excluded == () and x.leaks_personal_data(b) == []
    )  # QA-043
    assert "Jane" not in repr(cand) and "AB1234567" not in repr(cand)


def test_redaction_reaches_nested_fields_and_lists_and_is_stable() -> None:
    data = {
        "outer": {"name": "A B", "keep": 1, "people": [{"dob": "1980"}, {"x": 2}]},
        "aliases": ["a", "b"],
    }
    out = x.redact(data)
    assert out["outer"]["name"].startswith("redacted:") and out["outer"]["keep"] == 1
    assert out["outer"]["people"][0]["dob"].startswith("redacted:") and out["outer"]["people"][
        1
    ] == {"x": 2}
    assert out["aliases"].startswith("redacted:")
    assert (
        x.redact(data) == out
    )  # the same value gives the same token, so two bundles can be compared without the value


def test_a_purpose_a_requester_a_cap_a_known_kind_and_a_sane_expiry_are_required() -> None:
    assert code(lambda: build(purpose="audit")) == "PURPOSE_REQUIRED"
    assert code(lambda: build(requester="")) == "REQUESTER_REQUIRED"
    assert code(lambda: build(ttl=timedelta(0))) == "EXPIRY_INVALID"
    assert code(lambda: build(ttl=timedelta(days=31))) == "EXPIRY_INVALID"
    assert (
        code(
            lambda: x.build(
                "c",
                [art("FULL_DUMP")],
                purpose=PURPOSE,
                requester="r",
                approver=None,
                policy_version="v",
                chain=[],
                now=NOW,
            )
        )
        == "KIND_NOT_EXPORTABLE"
    )
    many = [art("COVERAGE", f"r{i}") for i in range(x.MAX_ARTIFACTS + 1)]
    assert (
        code(
            lambda: x.build(
                "c",
                many,
                purpose=PURPOSE,
                requester="r",
                approver=None,
                policy_version="v",
                chain=[],
                now=NOW,
            )
        )
        == "EXPORT_TOO_LARGE"
    )


def test_the_bundle_is_watermarked_and_expires() -> None:
    b = build(ttl=timedelta(days=2))
    assert "scr_1" in b.watermark and "auditor-1" in b.watermark and "Annual audit" in b.watermark
    assert b.expires_at == NOW + timedelta(days=2)
    assert x.verify(b, now=NOW + timedelta(days=1)) == []
    assert x.verify(b, now=NOW + timedelta(days=2)) == ["EXPIRED"]


def test_any_edit_addition_removal_or_reordering_is_detected() -> None:
    b = build()
    edited_item = replace(b, included=(replace_item(b.included[0], status="x"), *b.included[1:]))
    assert any(p.startswith("ITEM_ALTERED") for p in x.verify(edited_item, now=NOW))
    assert "MANIFEST_HASH_MISMATCH" in x.verify(
        replace(b, included=b.included[:1]), now=NOW
    )  # an item removed
    assert "MANIFEST_HASH_MISMATCH" in x.verify(
        replace(b, chain=("h1",)), now=NOW
    )  # the decision chain shortened
    assert "MANIFEST_HASH_MISMATCH" in x.verify(
        replace(b, purpose="a different stated purpose"), now=NOW
    )
    assert "MANIFEST_HASH_MISMATCH" in x.verify(replace(b, requester="someone-else"), now=NOW)
    assert "MANIFEST_HASH_MISMATCH" in x.verify(
        replace(b, included=tuple(reversed(b.included))), now=NOW
    )
    assert x.verify(b, now=NOW) == []


def test_two_builds_of_the_same_inputs_have_the_same_manifest_hash() -> None:
    assert build().manifest_hash == build().manifest_hash
    assert build().manifest_hash != build(chain=["h1"]).manifest_hash


def test_the_leak_check_finds_a_personal_field_left_in_clear() -> None:
    b = build()
    leaky = replace(b, included=(replace_item(b.included[0], name="Jane Smith"), *b.included[1:]))
    assert x.leaks_personal_data(leaky) == ["SOURCE_SNAPSHOT:s1.name"]


def replace_item(item: dict, **data) -> dict:
    return {**item, "data": {**item["data"], **data}}
