"""ZID-INT-002 section 07.1 (pack dry run and rollback) and section 08 (provider guards): T04, T16, T17, T18."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from test_service_packs import FUTURE, NOW, _pack, _reg
from zid_platform.modules.verification.domain import pack_diff as pd
from zid_platform.modules.verification.domain import provider_adapter as pa
from zid_platform.modules.verification.domain.service_packs import FeatureState, sealed

ENV = "production"


def diffs(old, new, **kw):
    return pd.compare(old, new, now=NOW, environment=ENV, **kw)


# ---------------------------------------------------------------------------------------------- pack dry run


def test_t04_a_dry_run_changes_nothing_and_a_restored_registry_reproduces_every_answer() -> None:
    old = _reg(_pack())
    new = _reg(
        _pack(), _pack(pack_id="us-ca-law", version=2, source_registry_ids=("src-calbar", "src-x"))
    )
    before = [pd.answer(old, s, now=NOW, environment=ENV) for s in pd.scenarios_of(old, new)]
    assert [d.change for d in diffs(old, new)] == [pd.Change.NEW_VERSION]
    # the dry run mutated nothing: the old registry answers exactly as before
    assert [
        pd.answer(old, s, now=NOW, environment=ENV) for s in pd.scenarios_of(old, new)
    ] == before
    # rolling back = publishing the old registry again; it must answer every scenario as the original did
    restored = _reg(_pack())
    assert pd.identical(old, restored, now=NOW, environment=ENV)


def test_a_pack_that_opens_a_market_is_listed_as_widening_for_the_approver() -> None:
    old = _reg()
    new = _reg(_pack())
    d = diffs(old, new)
    assert [x.change for x in d] == [pd.Change.NEWLY_AVAILABLE]
    assert pd.widening(d) == d


def test_a_blocked_or_removed_pack_is_newly_unavailable_and_not_widening() -> None:
    old = _reg(_pack())
    blocked = _reg(sealed(replace(_pack(version=2), feature_state=FeatureState.BLOCKED)))
    for new in (blocked, _reg()):
        d = diffs(old, new)
        assert [x.change for x in d] == [pd.Change.NEWLY_UNAVAILABLE] and not pd.widening(d)


def test_the_same_version_with_different_content_is_reported() -> None:
    old = _reg(_pack())
    new = _reg(_pack(retention_policy_id="ret-2"))
    assert [d.change for d in diffs(old, new)] == [pd.Change.CONTENT_CHANGED]


def test_a_pack_still_in_design_does_not_widen_a_deployed_environment() -> None:
    new = _reg(sealed(replace(_pack(), feature_state=FeatureState.DESIGN)))
    assert diffs(_reg(), new) == []  # design packs never serve production, so nothing opens


def test_a_pack_that_expires_between_two_instants_changes_the_answer() -> None:
    reg = _reg(_pack(valid_until=NOW + timedelta(days=1)))
    s = pd.scenarios_of(reg)[0]
    assert pd.answer(reg, s, now=NOW, environment=ENV).available
    assert not pd.answer(reg, s, now=FUTURE, environment=ENV).available


# ---------------------------------------------------------------------------------------------- provider routing

TODAY = datetime(2026, 11, 11, 12, tzinfo=UTC)


def ent(pid="p1", **over) -> pa.Entitlement:
    base = {
        "provider_id": pid, "service_code": "identity.verify", "countries": frozenset({"US", "DE"}),
        "purposes": frozenset({"ONBOARDING"}), "processing_regions": frozenset({"us", "eu"}),
        "valid_from": date(2026, 1, 1), "valid_until": date(2027, 1, 1), "assurance": 3,
    }  # fmt: skip
    base.update(over)
    return pa.Entitlement(**base)


REQ = pa.RouteRequest("US", "identity.verify", "ONBOARDING", "us", 2)


def test_t17_an_expired_or_revoked_entitlement_disables_the_path_by_itself() -> None:
    assert pa.route([ent()], REQ, TODAY).provider_id == "p1"
    expired = pa.route([ent(valid_until=date(2026, 11, 10))], REQ, TODAY)
    assert expired.provider_id is None and pa.Refusal.ENTITLEMENT_NOT_IN_PERIOD in expired.refusals
    revoked = pa.route([ent(status=pa.EntitlementStatus.REVOKED)], REQ, TODAY)
    assert revoked.provider_id is None and pa.Refusal.ENTITLEMENT_NOT_ACTIVE in revoked.refusals


def test_legality_outranks_cost_and_speed() -> None:
    cheap_but_unlawful = ent("cheap", countries=frozenset({"DE"}), cost_rank=0)
    dear_but_lawful = ent("dear", cost_rank=9)
    assert pa.route([cheap_but_unlawful, dear_but_lawful], REQ, TODAY).provider_id == "dear"


def test_every_reason_is_reported_and_no_unlawful_provider_is_ever_chosen() -> None:
    bad = ent(
        "bad",
        countries=frozenset({"FR"}),
        purposes=frozenset({"MARKETING"}),
        processing_regions=frozenset({"ap"}),
        assurance=1,
    )
    d = pa.route([bad], REQ, TODAY)
    assert d.provider_id is None
    assert {
        pa.Refusal.COUNTRY_NOT_COVERED,
        pa.Refusal.PURPOSE_NOT_PERMITTED,
        pa.Refusal.REGION_NOT_PERMITTED,
        pa.Refusal.ASSURANCE_TOO_LOW,
    } <= set(d.refusals)


def test_a_provider_entitled_to_a_different_service_is_never_used() -> None:
    assert pa.route([ent(service_code="kyb.verify")], REQ, TODAY).provider_id is None


def test_no_providers_at_all_is_a_refusal_not_a_default() -> None:
    assert pa.route([], REQ, TODAY).refusals == (pa.Refusal.NO_PROVIDER,)


def test_among_lawful_providers_the_strongest_then_the_cheapest_wins() -> None:
    a, b, c = (
        ent("a", assurance=3, cost_rank=5),
        ent("b", assurance=4, cost_rank=9),
        ent("c", assurance=4, cost_rank=1),
    )
    assert pa.route([a, b, c], REQ, TODAY).provider_id == "c"


def test_t18_a_failover_that_would_lower_assurance_is_refused_without_an_approval() -> None:
    strong, weak = ent("strong", assurance=4), ent("weak", assurance=2)
    refused = pa.route([strong, weak], REQ, TODAY, failing_over_from=strong)
    assert refused.provider_id is None and refused.refusals == (
        pa.Refusal.FAILOVER_WOULD_LOWER_ASSURANCE,
    )
    approved = pa.route(
        [strong, weak], REQ, TODAY, failing_over_from=strong, downgrade_approval_ref="CHG-42"
    )
    assert approved.provider_id == "weak" and approved.approved_downgrade == "CHG-42"
    blank = pa.route(
        [strong, weak], REQ, TODAY, failing_over_from=strong, downgrade_approval_ref="  "
    )
    assert blank.provider_id is None


def test_a_failover_to_an_equal_or_stronger_provider_needs_no_approval() -> None:
    first, second = ent("first", assurance=3), ent("second", assurance=3)
    assert pa.route([first, second], REQ, TODAY, failing_over_from=first).provider_id == "second"


def test_a_failover_never_returns_to_the_provider_that_just_failed() -> None:
    only = ent("only")
    assert pa.route([only], REQ, TODAY, failing_over_from=only).provider_id is None


# ---------------------------------------------------------------------------------------------- webhooks

SECRET, OLD = b"current-secret", b"previous-secret"
RAW = b'{"event":"case.completed","case":"c1"}'


def signed(secret=SECRET, ts=None, nonce="n-1", raw=RAW):
    ts = ts or int(TODAY.timestamp())
    return {
        "x-zid-timestamp": str(ts),
        "x-zid-nonce": nonce,
        "x-zid-signature": pa.sign_webhook(secret, ts, nonce, raw),
    }


def test_t16_a_forged_or_altered_webhook_is_refused() -> None:
    ok = pa.verify_webhook(RAW, signed(), [SECRET, OLD], now=TODAY, seen_nonces=set())
    assert ok.ok and ok.key_index == 0
    forged = pa.verify_webhook(
        RAW, signed(secret=b"attacker"), [SECRET, OLD], now=TODAY, seen_nonces=set()
    )
    assert not forged.ok and forged.refusal is pa.WebhookRefusal.BAD_SIGNATURE
    altered = pa.verify_webhook(RAW + b" ", signed(), [SECRET, OLD], now=TODAY, seen_nonces=set())
    assert altered.refusal is pa.WebhookRefusal.BAD_SIGNATURE  # exact bytes, not a re-serialization


def test_a_replayed_or_stale_webhook_is_refused() -> None:
    h = signed()
    assert (
        pa.verify_webhook(RAW, h, [SECRET], now=TODAY, seen_nonces={"n-1"}).refusal
        is pa.WebhookRefusal.REPLAYED
    )
    old = signed(ts=int((TODAY - timedelta(minutes=10)).timestamp()))
    assert (
        pa.verify_webhook(RAW, old, [SECRET], now=TODAY, seen_nonces=set()).refusal
        is pa.WebhookRefusal.STALE
    )


def test_the_previous_key_is_accepted_during_rotation_and_a_third_is_not() -> None:
    assert (
        pa.verify_webhook(
            RAW, signed(secret=OLD), [SECRET, OLD], now=TODAY, seen_nonces=set()
        ).key_index
        == 1
    )
    assert not pa.verify_webhook(RAW, signed(secret=OLD), [SECRET], now=TODAY, seen_nonces=set()).ok
    assert not pa.verify_webhook(
        RAW, signed(secret=b"older"), [SECRET, OLD, b"older"], now=TODAY, seen_nonces=set()
    ).ok


def test_a_missing_or_garbled_header_is_malformed() -> None:
    for bad in (
        {},
        {"x-zid-timestamp": "x", "x-zid-nonce": "n", "x-zid-signature": "s"},
        {**signed(), "x-zid-nonce": ""},
    ):
        assert (
            pa.verify_webhook(RAW, bad, [SECRET], now=TODAY, seen_nonces=set()).refusal
            is pa.WebhookRefusal.MALFORMED
        )


def test_the_platform_never_claims_a_deletion_the_provider_did_not_confirm() -> None:
    unconfirmed = pa.deletion_statement(pa.CancelResult(True, False))
    assert "has not yet confirmed" in unconfirmed and "confirmed the case" not in unconfirmed
    assert "deleted" in pa.deletion_statement(pa.CancelResult(True, True))
    assert "has not confirmed" in pa.deletion_statement(pa.CancelResult(False, False))


# ------------------------------------------------------------------------------------- withdrawal impact (RCG-G08)


def _ent(pid: str, assurance: int, countries=("GB",)):
    return pa.Entitlement(
        provider_id=pid,
        service_code="idv",
        countries=frozenset(countries),
        purposes=frozenset({"onboarding"}),
        processing_regions=frozenset({"eu"}),
        valid_from=date(2020, 1, 1),
        valid_until=None,
        assurance=assurance,
    )


def test_withdrawal_with_no_other_provider_loses_the_scope_and_never_falls_back() -> None:
    a = _ent("a", 5)
    impact = pa.withdrawal_impact([a], a, NOW)
    assert [i.outcome for i in impact] == ["LOST"]
    assert impact[0].after is None


def test_a_weaker_replacement_is_degraded_and_an_equal_one_is_rerouted() -> None:
    a, weak, peer = _ent("a", 5), _ent("w", 3), _ent("z", 5)
    assert [i.outcome for i in pa.withdrawal_impact([a, weak], a, NOW)] == ["DEGRADED"]
    assert [i.outcome for i in pa.withdrawal_impact([a, weak, peer], a, NOW)] == ["REROUTED"]


def test_a_scope_another_provider_already_wins_is_unaffected() -> None:
    a, strong = _ent("a", 5), _ent("s", 7)
    assert [i.outcome for i in pa.withdrawal_impact([a, strong], a, NOW)] == ["UNAFFECTED"]


def test_a_replacement_for_another_country_does_not_cover_this_one() -> None:
    a, other = _ent("a", 5, ("GB", "FR")), _ent("o", 5, ("FR",))
    got = {i.country: i.outcome for i in pa.withdrawal_impact([a, other], a, NOW)}
    assert got == {"FR": "REROUTED", "GB": "LOST"}


def test_an_entitlement_too_broad_to_enumerate_is_refused_not_truncated() -> None:
    import pytest

    a = _ent("a", 5, ("GB", "FR", "DE"))
    with pytest.raises(pa.ImpactTooLarge):
        pa.withdrawal_impact([a], a, NOW, max_scopes=2)
