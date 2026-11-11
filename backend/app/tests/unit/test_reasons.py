"""Plain-language reasons (GID-059, GID-062): every code the credential rules emit has words, and the words stay honest.

The source scan is the drift guard: a new reason code added to a rule without text fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from zid_platform.modules.credential.domain import reasons

CRED = Path(reasons.__file__).resolve().parents[1]
FILES = [
    *sorted((CRED / "domain").glob("*.py")),
    CRED / "application" / "claim_checks.py",
    CRED / "application" / "identity_match.py",
    CRED / "application" / "trust_service.py",
]
# the places a rule puts a reason code: reasons.append("X"), a one-item reason tuple ("X",), reason_codes=("X",)
PATTERNS = [
    re.compile(r"reasons?\.append\(\s*\"([A-Z][A-Z0-9_]+)\""),
    re.compile(r"\(\s*\"([A-Z][A-Z0-9_]{5,})\",\s*\)"),
    re.compile(r"reason_codes=\(\s*\"([A-Z][A-Z0-9_]+)\""),
]
# codes that are not outcome reasons: they are error codes or enum values that happen to look alike
NOT_REASONS = {
    "PROFESSIONAL_LICENSE",  # a claim type used as a default, not a reason
    "ELIGIBILITY_POLICY_INVALID",
    "TRUST_GRAPH_INVALID",
    "SOURCE_REGISTRY_INVALID",
    "PACK_REGISTRY_INVALID",
}


def emitted() -> dict[str, str]:
    found: dict[str, str] = {}
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        for pat in PATTERNS:
            for code in pat.findall(text):
                if code not in NOT_REASONS:
                    found.setdefault(code, f.name)
    return found


def test_the_scan_actually_finds_the_codes_the_rules_emit():
    found = emitted()
    assert len(found) > 40, "the scan found too few codes: it is not looking at the rules"
    for must in (
        "PRIMARY_SOURCE_NOT_CONFIRMED",
        "WORK_EXCLUDED",
        "HOLDER_BINDING_FAILED",
        "ISSUER_DID_NOT_ANSWER",
    ):
        assert must in found


def test_every_emitted_reason_code_has_its_own_words():
    missing = {c: f for c, f in emitted().items() if not reasons.describe(c)["known"]}
    assert not missing, f"reason codes without text: {missing}"


def test_dynamic_codes_are_described_by_pattern():
    for code in (
        "CREDENTIAL_SUSPENDED",
        "CREDENTIAL_REVOKED",
        "CREDENTIAL_EXPIRED",
        "PROFESSIONAL_LICENSE_MISSING",
        "PROFESSIONAL_LICENSE_WRONG_JURISDICTION",
        "PROFESSIONAL_LICENSE_SERVICE_NOT_PERMITTED",
        "INSURANCE_ASSERTION_EXPIRED",
        "ACADEMIC_AWARD_RECOGNITION_REVIEW",
        "AWARD_STATUS_NOT_CONFERRED",
    ):
        d = reasons.describe(code)
        assert d["known"] is True, code
        assert d["title"] and d["explanation"] and d["next_step"]
    assert "license" in str(
        reasons.describe("PROFESSIONAL_LICENSE_WRONG_JURISDICTION")["explanation"]
    )


def test_an_unknown_code_never_raises_and_never_invents_a_meaning():
    d = reasons.describe("SOMETHING_NEW_AND_STRANGE")
    assert d["known"] is False and d["code"] == "SOMETHING_NEW_AND_STRANGE"
    assert "reviewer" in str(d["next_step"]).lower()
    assert reasons.describe("")["known"] is False


def test_a_locale_without_text_falls_back_to_english_and_says_so():
    assert reasons.describe("PROOF_INVALID", "fr-CA")["locale_used"] == "en"
    assert reasons.describe("PROOF_INVALID", "en-GB")["locale_used"] == "en"
    reasons.LOCALES["xx"] = {"PROOF_INVALID": ("T", "E", "N")}
    try:
        assert reasons.describe("PROOF_INVALID", "xx")["title"] == "T"
        assert reasons.describe("PROOF_INVALID", "xx")["locale_used"] == "xx"
        assert (
            reasons.describe("SEAL_INVALID", "xx")["locale_used"] == "en"
        )  # not translated yet: honest about it
    finally:
        del reasons.LOCALES["xx"]


@pytest.mark.parametrize("code", sorted(reasons.EN))
def test_every_text_is_complete_plain_and_does_not_accuse(code):
    title, explanation, step = reasons.EN[code]
    for part in (title, explanation, step):
        assert part and part == part.strip()
        assert not re.search(r"\b[A-Z]{3,}_[A-Z_]+\b", part), (
            f"a raw code leaked into the text of {code}"
        )
        assert not re.search(
            r"\b(fraud\w*|fake|forged|liar|lying|criminal|guilty)\b", part, re.IGNORECASE
        )
    assert explanation.endswith(".") and step.endswith(".")


def test_describe_all_keeps_order():
    out = reasons.describe_all(["WORK_EXCLUDED", "PROOF_INVALID"])
    assert [d["code"] for d in out] == ["WORK_EXCLUDED", "PROOF_INVALID"]
