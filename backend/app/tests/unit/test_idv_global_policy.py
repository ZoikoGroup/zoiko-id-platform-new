"""The built-in global identity policy (docs/specs/identity-and-company-verification-design.md, section 6).

It serves a country with no policy of its own, only with the owners' written acceptance configured, and a country's own policy always wins.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from zid_errors import ZidError
from zid_platform.modules.verification.api import idv_cases
from zid_platform.modules.verification.domain.idv_policy import PolicyError, parse
from zid_platform.modules.verification.infrastructure import idv_policy_file

SHIPPED = Path(__file__).resolve().parents[3] / "config" / "idv" / "global-policy.yaml"
ACK = idv_policy_file.GLOBAL_POLICY_ACK


def _rules(doc: dict[str, Any], market: str):
    return parse({"markets": {market: doc}})[market]


GB = {"policy_version": "gb-1", "purpose": "KYC", "expired_document": "ACCEPT", "alternates": [{"route_id": "WEB_REVIEW", "may_approve": True, "reviewers_needed": 2}], "media_retention_days": 7}
GLOBAL = _rules({"policy_version": "global-test", "purpose": "KYC", "expired_document": "REFUSE", "media_retention_days": 14, "alternates": [{"route_id": "WEB_REVIEW", "may_approve": True, "reviewers_needed": 2}]}, "GLOBAL")


def request(*, ack: str | None, table: dict[str, Any] | None = None, global_rules: Any = GLOBAL):
    state = SimpleNamespace(idv_policies=lambda: table or {}, idv_global_policy_ack=ack, idv_global_policy=(lambda: global_rules) if not callable(global_rules) else global_rules)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_the_shipped_file_parses_and_states_the_owner_decisions() -> None:
    rules = parse(yaml.safe_load(SHIPPED.read_text(encoding="utf-8")))["GLOBAL"]
    routes = {a.route_id: a for a in rules.policy.alternates}
    assert set(routes) == {"WEB_FACE", "WEB_REVIEW"}
    assert routes["WEB_FACE"].automatic and routes["WEB_FACE"].face_match and routes["WEB_FACE"].face_threshold == 0.363
    assert routes["WEB_REVIEW"].reviewers_needed == 2  # the exception route is never decided by one person
    assert rules.media_retention_days == 14  # unset would make the reviewed route refuse every photograph
    assert rules.result_valid_days == 365  # unset would leave no identity claim behind
    assert rules.review_sla_hours == 72


def test_the_shipped_file_says_it_is_not_accepted() -> None:
    text = SHIPPED.read_text(encoding="utf-8")
    assert "NOT ACCEPTED BY ANY OWNER" in text and "ZID_IDV_GLOBAL_POLICY_ACK" in text


def test_the_loader_returns_the_global_rules() -> None:
    assert idv_policy_file.load_global() is not None
    assert idv_policy_file.GLOBAL_MARKET == "GLOBAL"


def test_without_the_acceptance_an_unlisted_country_is_refused_as_before() -> None:
    with pytest.raises(ZidError) as e:
        idv_cases._rules(request(ack=None), "FR")
    assert e.value.code == "MARKET_POLICY_NOT_PUBLISHED"


@pytest.mark.parametrize("wrong", ["", "yes", "accept", "ACCEPT-GLOBAL-IDV-POLICY", "accept-global-idv-policy "])
def test_only_the_exact_sentence_counts_as_the_acceptance(wrong: str) -> None:
    with pytest.raises(ZidError) as e:
        idv_cases._rules(request(ack=wrong), "FR")
    assert e.value.code == "MARKET_POLICY_NOT_PUBLISHED"


def test_with_the_acceptance_any_unlisted_country_is_served_by_the_global_policy() -> None:
    for country in ("FR", "IN", "BR", "ZW", "GLOBAL"):
        assert idv_cases._rules(request(ack=ACK), country) is GLOBAL


def test_a_countrys_own_policy_always_wins_even_with_the_acceptance() -> None:
    gb = _rules(GB, "GB")
    got = idv_cases._rules(request(ack=ACK, table={"GB": gb}), "gb")
    assert got is gb and got.media_retention_days == 7  # not the global 14


def test_a_missing_global_file_with_the_acceptance_still_refuses() -> None:
    with pytest.raises(ZidError) as e:
        idv_cases._rules(request(ack=ACK, global_rules=lambda: None), "FR")
    assert e.value.code == "MARKET_POLICY_NOT_PUBLISHED"


def test_a_malformed_global_file_refuses_and_never_runs_on_a_guess() -> None:
    def broken() -> Any:
        raise PolicyError("GLOBAL: media_retention_days is a whole number of days from 1 to 90")

    with pytest.raises(ZidError) as e:
        idv_cases._rules(request(ack=ACK, global_rules=broken), "FR")
    assert e.value.code == "IDV_POLICY_FILE_INVALID"


def test_the_notice_follows_the_same_rule_and_never_raises() -> None:
    with_notice = _rules({**GB, "biometric": {"lawful_basis": "consent", "impact_assessment_ref": "DPIA-1", "notice_version": "n1"}}, "GLOBAL")
    assert idv_cases._notice(request(ack=ACK, global_rules=with_notice), "FR") == "n1"
    assert idv_cases._notice(request(ack=None, global_rules=with_notice), "FR") is None  # no acceptance, no notice

    def broken() -> Any:
        raise PolicyError("bad")

    assert idv_cases._notice(request(ack=ACK, global_rules=broken), "FR") is None
