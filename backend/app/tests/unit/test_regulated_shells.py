"""DTV-26, DTV-27, DTV-32, DTV-34: the integration shells for open banking, payment ownership, transaction monitoring and on-chain screening (CR-101).

Each shell is checked for what it must never do (hold account or card data, invent a risk, block anything) as much as for what it does. Synthetic data only.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.verification.domain import onchain_screening as oc
from zid_platform.modules.verification.domain import open_banking as ob
from zid_platform.modules.verification.domain import payment_ownership as po
from zid_platform.modules.verification.domain import transaction_monitoring as tm

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)

# ====================================================================================================================== open banking

H = "a" * 64
REG = ob.parse_registry(
    {
        "version": "r1",
        "providers": [
            {
                "provider_id": "agg_one",
                "status": "ACTIVE",
                "jurisdictions": ["GB"],
                "licence_ref": "FCA-1",
            }
        ],
    }
)


def assertion(**over):
    p = {
        "provider_id": "agg_one", "consent_ref": "consent-1", "purpose": "verify_account_ownership", "scope": "ACCOUNT_OWNERSHIP",
        "holder_name_match": "MATCH", "account_ref_hash": H, "consent_expires_at": (NOW + timedelta(days=30)).isoformat(),
        "obtained_at": NOW.isoformat(),
    }  # fmt: skip
    p.update(over)
    return p


def claim(**over):
    return ob.to_claim(ob.parse_assertion(assertion(**over)), REG, jurisdiction="GB", now=NOW)


def test_a_licensed_active_provider_with_live_consent_and_a_matching_name_is_asserted_and_nothing_more():
    c = claim()
    assert (
        c.outcome is ob.Outcome.ASSERTED_BY_LICENSED_PROVIDER
        and c.assurance == "PROVIDER_ASSERTED"
        and c.recommendation == "REVIEW"
    )
    assert any("holds no account data" in n for n in c.notes)


@pytest.mark.parametrize(
    "key",
    [
        "balance",
        "transactions",
        "iban",
        "account_number",
        "sort_code",
        "card_number",
        "statement",
        "routing_number",
        "Balance",
    ],
)
def test_a_payload_that_carries_account_data_is_refused_whole_and_not_echoed(key):
    with pytest.raises(ob.OpenBankingError, match="ACCOUNT_DATA_REFUSED") as e:
        ob.parse_assertion({**assertion(), key: "GB29NWBK60161331926819"})
    assert "GB29" not in str(e.value)


@pytest.mark.parametrize(
    "bad",
    [
        {"scope": "ACCOUNT_BALANCE"},
        {"holder_name_match": "YES"},
        {"account_ref_hash": "xyz"},
        {"purpose": "Verify Account"},
        {"provider_id": ""},
        {"consent_expires_at": "tomorrow"},
        {"extra": 1},
    ],
)
def test_a_malformed_assertion_is_refused(bad):
    with pytest.raises(ob.OpenBankingError):
        ob.parse_assertion(assertion(**bad))


def test_an_unlisted_suspended_or_wrong_jurisdiction_provider_is_not_licensed_however_plausible():
    assert claim(provider_id="agg_two").outcome is ob.Outcome.PROVIDER_NOT_LICENSED
    assert (
        ob.to_claim(ob.parse_assertion(assertion()), REG, jurisdiction="FR", now=NOW).outcome
        is ob.Outcome.PROVIDER_NOT_LICENSED
    )
    suspended = ob.parse_registry(
        {
            "providers": [
                {
                    "provider_id": "agg_one",
                    "status": "SUSPENDED",
                    "jurisdictions": ["GB"],
                    "licence_ref": "x",
                }
            ]
        }
    )
    assert (
        ob.to_claim(ob.parse_assertion(assertion()), suspended, jurisdiction="GB", now=NOW).outcome
        is ob.Outcome.PROVIDER_NOT_LICENSED
    )
    empty = ob.parse_registry({})
    assert (
        ob.to_claim(ob.parse_assertion(assertion()), empty, jurisdiction="GB", now=NOW).outcome
        is ob.Outcome.PROVIDER_NOT_LICENSED
    )


def test_a_lapsed_or_open_ended_consent_is_refused():
    assert (
        claim(consent_expires_at=(NOW - timedelta(seconds=1)).isoformat()).outcome
        is ob.Outcome.CONSENT_EXPIRED
    )
    assert (
        claim(
            consent_expires_at=(NOW + timedelta(days=ob.MAX_CONSENT_DAYS + 1)).isoformat()
        ).outcome
        is ob.Outcome.CONSENT_EXPIRED
    )
    assert (
        claim(consent_expires_at=(NOW + timedelta(days=ob.MAX_CONSENT_DAYS)).isoformat()).outcome
        is ob.Outcome.ASSERTED_BY_LICENSED_PROVIDER
    )


def test_a_partial_name_goes_to_a_person_and_a_different_name_is_reported_not_declined():
    assert claim(holder_name_match="PARTIAL").outcome is ob.Outcome.NAME_NEEDS_REVIEW
    assert claim(holder_name_match="NO_MATCH").outcome is ob.Outcome.NAME_DOES_NOT_MATCH
    assert claim(holder_name_match="UNAVAILABLE").outcome is ob.Outcome.NOT_AVAILABLE
    assert all(claim(holder_name_match=m).recommendation == "REVIEW" for m in ob.NAME_MATCH)


def test_a_malformed_registry_is_refused_whole():
    for bad in ({"providers": [{"provider_id": "X", "status": "ACTIVE", "jurisdictions": ["GB"], "licence_ref": "l"}]},
                {"providers": [{"provider_id": "agg_one", "status": "ACTIVE", "jurisdictions": [], "licence_ref": "l"}]},
                {"providers": [{"provider_id": "agg_one", "status": "PAUSED", "jurisdictions": ["GB"], "licence_ref": "l"}]},
                {"providers": [{"provider_id": "agg_one", "status": "ACTIVE", "jurisdictions": ["GB"]}]}):  # fmt: skip
        with pytest.raises(ob.OpenBankingError):
            ob.parse_registry(bad)


# ================================================================================================================ payment ownership

GOOD_PM = {
    "processor": "acme_pay",
    "payment_method_ref": "pm_1A2b3C4d5E6f",
    "belongs_to_subject": "YES",
    "cvc_check": "PASS",
    "postcode_check": "PASS",
    "three_ds": "PASS",
    "funding": "CREDIT",
    "country": "GB",
}


def pm(**over):
    return po.to_claim(po.parse_assertion({**GOOD_PM, **over}))


def test_a_processor_statement_of_ownership_with_passing_checks_is_asserted_and_never_called_verified():
    c = pm()
    assert c.outcome is po.Outcome.OWNERSHIP_ASSERTED and c.assurance == "PROVIDER_ASSERTED"
    assert "verified" not in c.outcome.value.lower() and any(
        "holds no card data" in n for n in c.notes
    )


@pytest.mark.parametrize(
    "card",
    [
        "4242424242424242",
        "4242 4242 4242 4242",
        "4242-4242-4242-4242",
        "5555555555554444",
        "378282246310005",
    ],
)
def test_a_card_number_anywhere_in_the_payload_is_refused_whole_and_never_echoed(card):
    for payload in (
        {**GOOD_PM, "processor": card},
        {**GOOD_PM, "note": card},
        {**GOOD_PM, "nested": {"a": [f"x {card} y"]}},
    ):
        with pytest.raises(po.PaymentError, match="CARD_DATA_REFUSED") as e:
            po.parse_assertion(payload)
        assert "4242" not in str(e.value) and "5555" not in str(e.value)


@pytest.mark.parametrize(
    "key",
    [
        "card_number",
        "pan",
        "cvv",
        "cvc_value",
        "security_code",
        "expiry",
        "exp_month",
        "iban",
        "account_number",
    ],
)
def test_card_shaped_keys_are_refused_even_with_innocent_values(key):
    with pytest.raises(po.PaymentError, match="CARD_DATA_REFUSED"):
        po.parse_assertion({**GOOD_PM, key: "x"})


def test_a_long_number_that_is_not_a_card_is_not_mistaken_for_one():
    assert po.parse_assertion(
        {**GOOD_PM, "processor": "acme 1234567890123"}
    ).processor  # fails Luhn, so not a card number


@pytest.mark.parametrize(
    "bad",
    [
        {"payment_method_ref": "4242"},
        {"payment_method_ref": "xx_1A2b3C4d5E6f"},
        {"belongs_to_subject": "MAYBE"},
        {"funding": "GIFT"},
        {"cvc_check": "OK"},
        {"country": "gb"},
        {"extra": 1},
        {"processor": " "},
    ],
)
def test_a_malformed_statement_is_refused(bad):
    with pytest.raises(po.PaymentError):
        po.parse_assertion({**GOOD_PM, **bad})


def test_a_denial_a_failed_check_or_a_weak_funding_type_is_never_a_pass():
    assert pm(belongs_to_subject="NO").outcome is po.Outcome.OWNERSHIP_DENIED_BY_PROCESSOR
    assert pm(belongs_to_subject="UNKNOWN").outcome is po.Outcome.NOT_AVAILABLE
    assert pm(cvc_check="FAIL").outcome is po.Outcome.OWNERSHIP_NEEDS_REVIEW
    prepaid = pm(funding="PREPAID")
    assert (
        prepaid.outcome is po.Outcome.OWNERSHIP_NEEDS_REVIEW
        and "FUNDING_TYPE_IS_A_WEAK_BASIS" in prepaid.reasons
    )
    assert "SOME_PROCESSOR_CHECKS_NOT_RUN" in pm(three_ds="NOT_CHECKED").reasons


def test_luhn_is_the_standard_check():
    assert po.luhn_ok("4242424242424242") and not po.luhn_ok("4242424242424241")


# ============================================================================================================ transaction monitoring


def tx(i, amount, days_ago=1, subject="p1", currency="GBP", counterparty="cp1"):
    return tm.Transaction(
        f"t{i}", subject, Decimal(amount), currency, NOW - timedelta(days=days_ago), counterparty
    )


RULES = tm.parse_rules(
    {
        "version": "t", "status": "SAMPLE",
        "rules": [
            {"id": "big", "kind": "amount_at_least", "severity": "MEDIUM", "currency": "GBP", "amount": "10000"},
            {"id": "busy", "kind": "count_in_window", "severity": "LOW", "currency": "GBP", "count": 5, "days": 7},
            {"id": "total", "kind": "sum_in_window", "severity": "HIGH", "currency": "GBP", "amount": "20000", "days": 30},
            {"id": "split", "kind": "just_below", "severity": "HIGH", "currency": "GBP", "amount": "10000", "within_pct": "10", "count": 3, "days": 14},
            {"id": "newbig", "kind": "new_counterparty_large", "severity": "MEDIUM", "currency": "GBP", "amount": "5000", "days": 90},
        ],
    }
)  # fmt: skip


def alerts(txs, ruleset=RULES):
    return {a.rule_id: a for a in tm.evaluate(ruleset, txs, now=NOW).alerts}


def test_each_rule_kind_fires_on_its_own_pattern_and_names_the_transactions():
    assert set(alerts([tx(1, "10000"), tx(2, "50")])) >= {"big"} and alerts([tx(1, "10000")])[
        "big"
    ].evidence == ("t1",)
    assert "busy" in alerts([tx(i, "10", days_ago=1) for i in range(5)]) and "busy" not in alerts(
        [tx(i, "10") for i in range(4)]
    )
    assert "total" in alerts([tx(1, "12000", 3), tx(2, "9000", 20)])
    split = alerts([tx(1, "9500", 2), tx(2, "9200", 3), tx(3, "9900", 4)])
    assert "split" in split and set(split["split"].evidence) == {"t1", "t2", "t3"}
    assert "split" not in alerts(
        [tx(1, "9500", 2), tx(2, "9200", 3), tx(3, "8000", 4)]
    )  # one is not close enough to the line


def test_a_new_large_counterparty_fires_but_a_known_one_does_not():
    known = [
        tx(1, "100", days_ago=200, counterparty="cpx"),
        tx(2, "6000", days_ago=1, counterparty="cpx"),
    ]
    new = [
        tx(1, "100", days_ago=200, counterparty="cpy"),
        tx(2, "6000", days_ago=1, counterparty="cpx"),
    ]
    assert "newbig" not in alerts(known) and "newbig" in alerts(new)


def test_an_alert_is_a_reason_to_look_and_carries_no_amount_or_counterparty():
    a = alerts([tx(1, "10000", counterparty="secret-cp")])["big"]
    assert a.recommendation == "REVIEW" and any("Nothing was held" in n for n in a.notes)
    assert "10000" not in repr(a) and "secret-cp" not in repr(a)
    assert not any(hasattr(a, n) for n in ("blocked", "decline", "hold", "score"))


def test_rules_compare_like_currencies_only_and_the_gap_is_reported():
    result = tm.evaluate(RULES, [tx(1, "99999", currency="USD"), tx(2, "10000")], now=NOW)
    assert "big" in {a.rule_id for a in result.alerts}
    assert all(
        "t1" not in a.evidence for a in result.alerts
    )  # the dollar transaction was never counted by a rule in pounds
    assert (
        result.skipped_other_currency["big"] == 1
    )  # a rule written in pounds did not count the dollars, and says so


def test_people_are_evaluated_separately():
    r = alerts([tx(1, "6000", subject="a"), tx(2, "6000", subject="b")], RULES)
    assert "total" not in r  # neither person alone reaches 20000
    assert "total" in alerts([tx(1, "11000", subject="a"), tx(2, "11000", subject="a")])


def test_the_result_does_not_depend_on_the_order_transactions_arrive_in():
    base = [tx(i, "9600", days_ago=i + 1) for i in range(1, 5)] + [tx(9, "12000", 2)]
    expected = tm.evaluate(RULES, base, now=NOW)
    for seed in range(15):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        assert tm.evaluate(RULES, shuffled, now=NOW) == expected


def test_evidence_is_capped_so_an_alert_stays_readable():
    many = [tx(i, "10", days_ago=1) for i in range(60)]
    assert len(alerts(many)["busy"].evidence) == tm.MAX_EVIDENCE


@pytest.mark.parametrize(
    "rule",
    [
        {"id": "x", "kind": "run_script", "severity": "LOW", "currency": "GBP"},
        {
            "id": "x",
            "kind": "amount_at_least",
            "severity": "SEVERE",
            "currency": "GBP",
            "amount": "1",
        },
        {
            "id": "x",
            "kind": "amount_at_least",
            "severity": "LOW",
            "currency": "GBP",
            "amount": 100,
        },  # a number, not a decimal string
        {
            "id": "x",
            "kind": "amount_at_least",
            "severity": "LOW",
            "currency": "GBP",
            "amount": "-1",
        },
        {"id": "x", "kind": "amount_at_least", "severity": "LOW", "currency": "GBP"},
        {"id": "x", "kind": "count_in_window", "severity": "LOW", "currency": "GBP", "count": 3},
        {
            "id": "x",
            "kind": "just_below",
            "severity": "LOW",
            "currency": "GBP",
            "amount": "10",
            "count": 2,
            "days": 5,
            "within_pct": "150",
        },
        {
            "id": "x",
            "kind": "amount_at_least",
            "severity": "LOW",
            "currency": "GBP",
            "amount": "1",
            "when": "1==1",
        },
        {
            "id": "X Y",
            "kind": "amount_at_least",
            "severity": "LOW",
            "currency": "GBP",
            "amount": "1",
        },
        {"id": "x", "kind": "amount_at_least", "severity": "LOW", "currency": "gbp", "amount": "1"},
    ],
)
def test_a_malformed_rule_refuses_the_whole_set(rule):
    with pytest.raises(tm.MonitoringError):
        tm.parse_rules({"rules": [rule]})


def test_a_repeated_rule_and_an_unapproved_approval_are_refused():
    one = {
        "id": "x",
        "kind": "amount_at_least",
        "severity": "LOW",
        "currency": "GBP",
        "amount": "1",
    }
    with pytest.raises(tm.MonitoringError):
        tm.parse_rules({"rules": [one, one]})
    with pytest.raises(tm.MonitoringError, match="APPROVAL_INCOMPLETE"):
        tm.parse_rules({"status": "APPROVED", "rules": [one]})


# ============================================================================================================== on-chain screening

BTC = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
ETH_MIXED = "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"
TRON = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
SOL = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def test_well_formed_addresses_are_accepted_and_the_limits_of_the_check_are_stated():
    assert oc.check_address("BITCOIN", BTC) == (True, ())
    assert oc.check_address("BITCOIN", BTC_P2SH) == (True, ())
    assert oc.check_address("ETHEREUM", ETH_MIXED.lower()) == (True, ())
    assert oc.check_address("ETHEREUM", ETH_MIXED) == (
        True,
        ("MIXED_CASE_CHECKSUM_NOT_VERIFIED",),
    )  # not verified here, and it says so
    assert oc.check_address("TRON", TRON) == (True, ())
    assert oc.check_address("SOLANA", SOL) == (True, ("SHAPE_ONLY",))
    assert oc.check_address("BITCOIN", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")[1] == (
        "BECH32_CHECKSUM_NOT_VERIFIED",
    )


@pytest.mark.parametrize(
    ("chain", "address"),
    [
        ("BITCOIN", BTC[:-1] + "x"),
        ("BITCOIN", "not an address"),
        ("ETHEREUM", "0x123"),
        ("ETHEREUM", "de0b295669a9fd93d5f28d9ec85e40f4cb697bae"),
        ("TRON", TRON[:-1] + "9"),
        ("TRON", "T" + "1" * 33),
        ("SOLANA", "short"),
        ("SOLANA", "0" * 40),
    ],
)
def test_a_malformed_address_is_reported_as_such(chain, address):
    assert oc.check_address(chain, address)[0] is False


def test_an_unsupported_chain_is_an_error_not_a_guess():
    with pytest.raises(oc.ChainError, match="CHAIN_NOT_SUPPORTED"):
        oc.check_address("DOGE", "x")


def test_the_address_hash_is_keyed_per_company_and_chain_and_ignores_ethereum_case():
    a = oc.address_hash("ETHEREUM", ETH_MIXED, tenant_id="t1", pepper=b"p")
    assert a == oc.address_hash("ETHEREUM", ETH_MIXED.lower(), tenant_id="t1", pepper=b"p")
    assert a != oc.address_hash("ETHEREUM", ETH_MIXED, tenant_id="t2", pepper=b"p")
    assert oc.address_hash("BITCOIN", BTC, tenant_id="t1", pepper=b"p") != oc.address_hash(
        "BITCOIN", BTC, tenant_id="t1", pepper=b"q"
    )
    assert BTC not in oc.address_hash("BITCOIN", BTC, tenant_id="t1", pepper=b"p")
    with pytest.raises(oc.ChainError, match="PEPPER_REQUIRED"):
        oc.address_hash("BITCOIN", BTC, tenant_id="t1", pepper=b"")


def test_with_no_provider_the_answer_is_unavailable_and_a_person_looks_never_clean():
    s = oc.screen("BITCOIN", BTC)
    assert s.well_formed and not s.provider.available and s.provider.band is None
    assert s.recommendation == "REVIEW" and s.provider.reasons == ("NO_PROVIDER_CONFIGURED",)
    assert any("not an address anyone owns" in c for c in s.caveats)


class Provider:
    def __init__(self, answer=None, raises=False):
        self.answer, self.raises, self.calls = answer, raises, []

    def check(self, chain, address):
        self.calls.append((chain, address))
        if self.raises:
            raise ConnectionError("down")
        return self.answer


def test_the_providers_band_is_passed_through_and_only_a_high_or_severe_band_needs_a_person():
    for band, rec in (
        ("LOW", "NONE"),
        ("MEDIUM", "NONE"),
        ("HIGH", "REVIEW"),
        ("SEVERE", "REVIEW"),
    ):
        s = oc.screen(
            "BITCOIN", BTC, Provider(oc.ProviderAnswer(True, band, ("MIXER",), (), "acme"))
        )
        assert (
            s.provider.band == band
            and s.recommendation == rec
            and s.assurance == "PROVIDER_ASSERTED"
        )
    assert not any(hasattr(s, n) for n in ("blocked", "verdict", "score"))


def test_a_provider_that_raises_or_returns_an_unknown_band_is_unavailable_never_clean():
    assert oc.screen("BITCOIN", BTC, Provider(raises=True)).provider.reasons == ("PROVIDER_FAILED",)
    odd = oc.screen("BITCOIN", BTC, Provider(oc.ProviderAnswer(True, "CRITICAL", (), (), "acme")))
    assert not odd.provider.available and odd.recommendation == "REVIEW"


def test_a_malformed_address_is_never_sent_to_a_provider():
    p = Provider(oc.ProviderAnswer(True, "LOW", (), (), "acme"))
    s = oc.screen("BITCOIN", "garbage", p)
    assert not s.well_formed and p.calls == [] and s.recommendation == "REVIEW"
