"""The market proofing rules file is read strictly and hashed (CR-110)."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain.idv_decision import Requirement
from zid_platform.modules.verification.domain.idv_policy import PolicyError, parse
from zid_platform.modules.verification.infrastructure import idv_policy_file


def entry(**kw: object) -> dict[str, object]:
    return {"policy_version": "v1", **kw}


def test_the_shipped_file_names_no_market() -> None:
    assert idv_policy_file.load() == {}


def test_a_market_is_read_with_safe_defaults_and_a_digest() -> None:
    m = parse({"markets": {"gb": entry(retries={"nfc": 3, "capture": 5})}})["GB"]
    assert (
        m.policy.chip is Requirement.REQUIRED
        and m.policy.expired_document == "REFUSE"
        and m.policy.capture_integrity is Requirement.REQUIRED
    )
    assert (
        m.basis.lawful_basis is None and m.self_hosted_engine is True
    )  # biometrics stay off until recorded
    assert m.policy_hash.startswith("sha256:")
    assert (
        parse({"markets": {"GB": entry(retries={"nfc": 3, "capture": 6})}})["GB"].policy_hash
        != m.policy_hash
    )


def test_a_malformed_entry_refuses_the_whole_file() -> None:
    for bad in (
        entry(chpi="REQUIRED"),
        entry(chip="SOMETIMES"),
        entry(expired_document="MAYBE"),
        entry(alternates=[{"route_id": "X", "reviewers_needed": 1}]),
        entry(alternates=[{"route_id": "CHIP"}]),
        entry(biometric={"colour": "red"}),
        {"policy_version": ""},
    ):
        with pytest.raises(PolicyError):
            parse({"markets": {"GB": entry(), "FR": bad}})
