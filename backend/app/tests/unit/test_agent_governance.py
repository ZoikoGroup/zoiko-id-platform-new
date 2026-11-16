"""Agent governance rules (ZID-AG-WF-002, CR-105): policy gates, hard denials, quotas, money, approvals, freeze.

Pure domain tests. Rows T01-T46 of the acceptance register that these exercise are named in each docstring; none of
this is an independent pass.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.machine.domain import governance as g

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
TENANT = "t-1"
AGENT = "a-1"
HASHLESS = "sha256:" + "0" * 64


def lease(**kw):
    base = {
        "grant_id": "g1",
        "tool_id": "read_mandate_metadata",
        "action": "mandate.metadata.read",
        "side_effect": "NONE",
        "resource_kind": "mandates",
        "resource_selector": "mandate:",
        "purpose": "access_posture_review",
        "region": "eu-west",
        "valid_until": NOW + timedelta(days=30),
        "approval_ref": "AGP-1",
    }
    base.update(kw)
    return g.GrantLease(**base)


def policy(**kw):
    base = {
        "policy_id": "p1",
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "version": 1,
        "risk_tier": "LOW",
        "state": "ACTIVE",
        "region": "eu-west",
        "grants": (lease(),),
    }
    base.update(kw)
    return g.GovernancePolicy(**base)


BASELINE = g.CompanyBaseline(
    allowed_tools=frozenset({"read_mandate_metadata", "create_access_review_proposal"}),
    allowed_regions=frozenset({"eu-west"}),
)


def approval_for(pol, **kw):
    h = g.policy_hash(pol)
    req = g.ChangeRequest(
        request_id="AGP-1",
        tenant_id=TENANT,
        agent_id=AGENT,
        kind=g.ChangeKind.PUBLISH,
        requested_by="dev",
        authors=("dev",),
        policy_hash=h,
        base_policy_hash=None,
        delta_hash=g.digest({}),
        required=("SECURITY", "AUTHORITY"),
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=23),
    )
    for slot, who in (("SECURITY", "sec"), ("AUTHORITY", "auth")):
        req = g.add_signature(req, g.Signature(slot, who, NOW - timedelta(minutes=30), h))
    return replace(req, **kw) if kw else req


def ctx(pol=None, **kw):
    pol = pol or policy()
    base = {
        "agent_tenant_id": TENANT,
        "agent_active": True,
        "sponsor_active": True,
        "policy": pol,
        "baseline": BASELINE,
        "approval": approval_for(pol),
        "freezes": (),
        "entitled": True,
    }
    base.update(kw)
    return g.GovernanceContext(**base)


def call(**kw):
    base = {
        "agent_id": AGENT,
        "session_tenant_id": TENANT,
        "tool_id": "read_mandate_metadata",
        "action": "mandate.metadata.read",
        "side_effect": "NONE",
        "resource_kind": "mandates",
        "resource_id": "mandate:42",
        "purpose": "access_posture_review",
        "region": "eu-west",
    }
    base.update(kw)
    return g.GovernedCall(**base)


def code(c, x=None, **kw):
    return g.evaluate(c, x or ctx(), now=NOW, **kw).code


# ---------------------------------------------------------------- authorization


def test_a_complete_lease_with_an_approval_is_allowed():
    d = g.evaluate(call(), ctx(), now=NOW)
    assert d.allowed and d.code is None and d.policy_hash == g.policy_hash(policy())


def test_t05_unknown_tool_is_denied_by_default():
    """T05: a tool no lease names is denied."""
    assert code(call(tool_id="export_everything")) is g.Code.SCOPE_NOT_APPROVED


@pytest.mark.parametrize(
    "action",
    [
        "role.grant",
        "role.revoke",
        "access_policy.write",
        "credentials.read_secret",
        "keys.export",
        "kyc.final_decision",
        "qualification.attest",
        "external.purchase",
        "funds.transfer",
        "arbitrary_web",
        "shell",
        "tools.register",
        "machine.tool.manage",
    ],
)
def test_t06_t11_t12_t27_hard_deny_beats_every_allow(action):
    """T06, T11, T12, T27: even with a lease and a valid approval for it, a prohibited action is SCOPE_PROHIBITED."""
    pol = policy(grants=(lease(tool_id="x", action=action),))
    assert code(call(tool_id="x", action=action), ctx(pol)) is g.Code.SCOPE_PROHIBITED


def test_an_external_transaction_side_effect_is_prohibited_whatever_its_name():
    pol = policy(
        grants=(lease(tool_id="pay", action="billing.settle", side_effect="EXTERNAL_TRANSACTION"),)
    )
    assert (
        code(
            call(tool_id="pay", action="billing.settle", side_effect="EXTERNAL_TRANSACTION"),
            ctx(pol),
        )
        is g.Code.SCOPE_PROHIBITED
    )


def test_t07_a_call_outside_the_leased_region_is_denied():
    """T07, T46: cross-region data call refused."""
    assert code(call(region="us-east")) is g.Code.REGION_DENIED


def test_region_must_also_be_in_the_company_baseline():
    base = replace(BASELINE, allowed_regions=frozenset({"us-east"}))
    assert code(call(), ctx(baseline=base)) is g.Code.REGION_DENIED


def test_t08_wildcards_and_empty_scopes_cannot_be_requested():
    """T08: a wildcard grant is refused at request time."""
    for field in ("tool_id", "resource_kind", "resource_selector", "purpose", "region"):
        bad = lease(**{field: "*"})
        assert any(p.startswith("WILDCARD_OR_EMPTY") for p in g.grant_problems(bad, now=NOW)), field
        assert any(
            p.startswith("WILDCARD_OR_EMPTY")
            for p in g.grant_problems(lease(**{field: " "}), now=NOW)
        )


def test_a_lease_needs_an_expiry_in_the_future_and_not_too_far():
    assert "EXPIRY_IN_PAST" in g.grant_problems(lease(valid_until=NOW), now=NOW)
    assert "EXPIRY_TOO_FAR" in g.grant_problems(
        lease(valid_until=NOW + timedelta(days=400)), now=NOW
    )
    assert g.grant_problems(lease(), now=NOW) == []


def test_t09_expired_lease_cannot_call():
    """T09."""
    pol = policy(grants=(lease(valid_until=NOW - timedelta(seconds=1)),))
    assert code(call(), ctx(pol)) is g.Code.SCOPE_NOT_APPROVED


def test_a_lease_without_its_approval_reference_is_not_effective():
    """Data model rule: deny on a missing source reference."""
    pol = policy(grants=(lease(approval_ref=None),))
    assert code(call(), ctx(pol)) is g.Code.SCOPE_NOT_APPROVED


def test_purpose_and_resource_must_match_the_lease():
    assert code(call(purpose="payments")) is g.Code.SCOPE_NOT_APPROVED
    assert code(call(purpose=None)) is g.Code.SCOPE_NOT_APPROVED
    assert code(call(resource_kind="credentials")) is g.Code.SCOPE_NOT_APPROVED
    assert code(call(resource_id="other:1")) is g.Code.SCOPE_NOT_APPROVED
    assert code(call(data_class="documents")) is g.Code.SCOPE_NOT_APPROVED


def test_t10_t22_a_changed_policy_invalidates_the_previous_approval():
    """T10, T22: the approval is for one hash; a different policy is not covered."""
    old = policy()
    stale = approval_for(old)
    newer = policy(
        grants=(
            lease(),
            lease(
                grant_id="g2",
                tool_id="create_access_review_proposal",
                action="access_review.proposal.create",
            ),
        )
    )
    assert code(call(), ctx(newer, approval=stale)) is g.Code.APPROVAL_REQUIRED


def test_an_unsigned_or_half_signed_approval_is_not_enough():
    pol = policy()
    h = g.policy_hash(pol)
    half = replace(
        approval_for(pol),
        signatures=(g.Signature("SECURITY", "sec", NOW, h),),
        state=g.RequestState.PENDING,
    )
    assert code(call(), ctx(pol, approval=half)) is g.Code.APPROVAL_REQUIRED
    assert code(call(), ctx(pol, approval=None)) is g.Code.APPROVAL_REQUIRED


def test_t15_missing_policy_sources_fail_closed():
    """T15: nothing missing may become an allow."""
    for field in ("policy", "baseline", "freezes", "entitled"):
        assert code(call(), ctx(**{field: None})) is g.Code.POLICY_UNAVAILABLE, field


def test_t03_inactive_agent_or_sponsor_blocks_a_run():
    """T03."""
    assert code(call(), ctx(sponsor_active=False)) is g.Code.SPONSOR_INVALID
    assert code(call(), ctx(agent_active=False)) is g.Code.AGENT_INACTIVE


def test_t01_t16_a_session_for_another_tenant_is_denied_without_detail():
    """T01, T16."""
    d = g.evaluate(call(session_tenant_id="t-other"), ctx(), now=NOW)
    assert (
        d.code is g.Code.TENANT_SCOPE_DENIED and "t-1" not in d.reason and "t-other" not in d.reason
    )


def test_the_company_baseline_can_remove_a_tool_the_lease_allows():
    base = replace(BASELINE, allowed_tools=frozenset())
    assert code(call(), ctx(baseline=base)) is g.Code.SCOPE_NOT_APPROVED


def test_no_entitlement_means_no_run():
    assert code(call(), ctx(entitled=False)) is g.Code.ENTITLEMENT_DENIED


def test_an_expired_policy_denies():
    pol = policy(expires_at=NOW - timedelta(minutes=1))
    assert code(call(), ctx(pol)) is g.Code.SCOPE_NOT_APPROVED


def test_http_status_follows_the_decision_table():
    d = g.evaluate(call(region="mars"), ctx(), now=NOW)
    assert d.http_status == 403
    assert g.evaluate(call(), ctx(policy=None), now=NOW).http_status == 503
    assert g.evaluate(call(), ctx(), now=NOW).http_status == 200


# ---------------------------------------------------------------- emergency stop


def freeze(**kw):
    base = {
        "case_id": "c1",
        "scope": "AGENT",
        "agent_id": AGENT,
        "generation": 3,
        "initiator": "sec",
        "reason": "incident",
        "issued_at": NOW,
    }
    base.update(kw)
    return g.Freeze(**base)


def test_t41_a_freeze_blocks_the_call_and_outranks_every_other_gate():
    """T41: even an otherwise perfect call is refused, and the freeze is the answer even if the agent is also inactive."""
    assert code(call(), ctx(freezes=(freeze(),))) is g.Code.EMERGENCY_FROZEN
    assert code(call(), ctx(freezes=(freeze(),), agent_active=False)) is g.Code.EMERGENCY_FROZEN


def test_a_tenant_wide_freeze_covers_every_agent_and_another_agents_freeze_does_not():
    assert (
        code(call(), ctx(freezes=(freeze(scope="TENANT", agent_id=None),)))
        is g.Code.EMERGENCY_FROZEN
    )
    assert code(call(), ctx(freezes=(freeze(agent_id="someone-else"),))) is None
    assert code(call(), ctx(baseline=replace(BASELINE, frozen=True))) is g.Code.EMERGENCY_FROZEN


def test_a_restored_freeze_no_longer_blocks_but_keeps_its_history():
    done = freeze(restored_at=NOW)
    assert not done.active
    assert code(call(), ctx(freezes=(done,))) is None


def test_t25_restore_needs_two_independent_qualified_people_and_fresh_checks():
    """T25."""
    f = freeze()
    slots = {
        "sec2": frozenset({"SECURITY"}),
        "auth": frozenset({"AUTHORITY"}),
        "sec": frozenset({"SECURITY"}),
    }
    ok = g.restore_blockers(
        f, approvers=["sec2", "auth"], approver_slots=slots, health_checks_fresh=True
    )
    assert ok == []
    assert "RESTORE_NOT_INDEPENDENT" in g.restore_blockers(
        f, approvers=["sec", "auth"], approver_slots=slots, health_checks_fresh=True
    )
    assert "RESTORE_NEEDS_AUTHORITY" in g.restore_blockers(
        f, approvers=["sec2"], approver_slots=slots, health_checks_fresh=True
    )
    assert "RESTORE_NEEDS_TWO_PEOPLE" in g.restore_blockers(
        f, approvers=["sec2"], approver_slots=slots, health_checks_fresh=True
    )
    assert "RESTORE_HEALTH_CHECK_STALE" in g.restore_blockers(
        f, approvers=["sec2", "auth"], approver_slots=slots, health_checks_fresh=False
    )


# ---------------------------------------------------------------- quotas


def test_t34_concurrency_waits_instead_of_retrying():
    """T34."""
    lim = g.QuotaLimits()
    v = g.start_check(lim, g.UsageSnapshot(active_runs_agent=1), now=NOW)
    assert v and v.waiting_capacity and v.retry_after is None
    v2 = g.start_check(lim, g.UsageSnapshot(running_agents_tenant=2), now=NOW)
    assert v2 and v2.quota == "running_agents_per_tenant"


def test_daily_run_caps_report_when_they_reset():
    lim = g.QuotaLimits()
    v = g.start_check(lim, g.UsageSnapshot(runs_agent_today=5), now=NOW)
    assert (
        v
        and v.quota == "runs_per_agent_day"
        and v.retry_after == datetime(2026, 11, 15, tzinfo=UTC)
    )
    v = g.start_check(lim, g.UsageSnapshot(runs_tenant_today=20), now=NOW)
    assert v and v.quota == "runs_per_tenant_day"
    assert (
        g.start_check(lim, g.UsageSnapshot(runs_agent_today=4, runs_tenant_today=19), now=NOW)
        is None
    )


def test_t35_records_per_run_and_per_day_are_hard_refusals():
    """T35: an overflow is a refusal, never a partial success."""
    lim = g.QuotaLimits()
    assert g.records_check(lim, g.UsageSnapshot(), requested=100, now=NOW) is None
    assert (
        g.records_check(lim, g.UsageSnapshot(), requested=101, now=NOW).quota == "records_per_run"
    )
    v = g.records_check(lim, g.UsageSnapshot(records_tenant_today=250), requested=60, now=NOW)
    assert v and v.quota == "records_per_tenant_day"


def test_quota_through_the_gate_returns_a_usage_limit():
    c = ctx(usage=g.UsageSnapshot(records_tenant_today=299))
    d = g.evaluate(call(records=5), c, now=NOW)
    assert (
        d.code is g.Code.USAGE_LIMIT
        and d.http_status == 429
        and d.quota.quota == "records_per_tenant_day"
    )


def test_t36_the_run_deadline_is_two_minutes():
    """T36."""
    lim = g.QuotaLimits()
    started = NOW
    assert not g.run_timed_out(lim, started, started + timedelta(seconds=119))
    assert g.run_timed_out(lim, started, started + timedelta(seconds=120))


def test_t37_schedules_stay_off_unless_both_the_policy_and_the_company_allow_them():
    """T37."""
    assert not g.QuotaLimits().schedules_enabled
    on = g.QuotaLimits(schedules_enabled=True)
    assert g.tightest(on, g.QuotaLimits()).schedules_enabled is False
    assert g.tightest(on, on).schedules_enabled is True


def test_the_effective_limit_is_the_most_restrictive_ceiling():
    low = g.QuotaLimits(runs_per_agent_day=2, records_per_run=50)
    eff = g.tightest(g.QuotaLimits(), low)
    assert eff.runs_per_agent_day == 2 and eff.records_per_run == 50 and eff.run_seconds == 120


def test_quota_values_must_not_be_negative():
    assert g.QuotaLimits(records_per_run=-1).problems() == ["NEGATIVE_OR_INVALID:records_per_run"]


# ---------------------------------------------------------------- money and budgets


def test_t32_money_is_exact_and_never_a_float():
    """T32."""
    assert g.money("0.10", "USD") == Decimal("0.10")
    assert g.money(5, "JPY") == Decimal(5)
    for bad, cur in (
        (0.1, "USD"),
        ("0.001", "USD"),
        ("1.5", "JPY"),
        ("-1", "USD"),
        ("NaN", "USD"),
        ("abc", "USD"),
        ("1", "XXX"),
        ("1e400", "USD"),
        ("1000000000001", "USD"),
    ):
        with pytest.raises(g.MoneyError):
            g.money(bad, cur)


def test_the_upper_bound_rounds_up_to_the_smallest_unit():
    assert g.upper_bound(Decimal("0.0003"), 1000, margin_pct=10) == Decimal("0.33")
    assert g.upper_bound(Decimal(1), 3, margin_pct=0, currency="JPY") == Decimal(3)


def ceilings(**used):
    return [
        g.Ceiling(
            "tenant_day",
            "company",
            "USD",
            "DAY",
            Decimal(20),
            committed=Decimal(used.get("tenant", 0)),
        ),
        g.Ceiling(
            "agent_day",
            "agent",
            "USD",
            "DAY",
            Decimal(5),
            committed=Decimal(used.get("agent", 0)),
        ),
        g.Ceiling("call", "call", "USD", "CALL", Decimal("0.10")),
    ]


def test_t26_ai_cost_stays_zero_while_the_provider_is_disabled():
    """T26."""
    v = g.reserve_check(
        g.SpendClass.AI_INFERENCE, Decimal("0.01"), "USD", ceilings(), ai_enabled=False, now=NOW
    )
    assert not v.ok and v.reason == "AI_DISABLED"


def test_t27_external_spend_and_paid_vendor_checks_are_never_reservable():
    """T27."""
    for cls in (g.SpendClass.EXTERNAL_TRANSACTION, g.SpendClass.PAID_VENDOR_CHECK):
        assert not g.reserve_check(cls, Decimal(0), "USD", ceilings(), ai_enabled=True, now=NOW).ok


def test_reservation_is_the_min_over_every_ceiling_and_names_the_binding_one():
    v = g.reserve_check(
        g.SpendClass.AI_INFERENCE,
        Decimal("0.10"),
        "USD",
        ceilings(agent="4.95"),
        ai_enabled=True,
        now=NOW,
    )
    assert (
        not v.ok
        and v.ceiling == "agent_day"
        and v.available == Decimal("0.05")
        and v.reset_at == datetime(2026, 11, 15, tzinfo=UTC)
    )
    ok = g.reserve_check(
        g.SpendClass.AI_INFERENCE,
        Decimal("0.05"),
        "USD",
        ceilings(agent="4.95"),
        ai_enabled=True,
        now=NOW,
    )
    assert ok.ok


def test_no_verified_ceiling_or_wrong_currency_denies_a_paid_step():
    assert (
        g.reserve_check(
            g.SpendClass.AI_INFERENCE, Decimal("0.01"), "USD", [], ai_enabled=True, now=NOW
        ).reason
        == "NO_VERIFIED_CEILING"
    )
    assert (
        g.reserve_check(
            g.SpendClass.AI_INFERENCE, Decimal("0.01"), "EUR", ceilings(), ai_enabled=True, now=NOW
        ).reason
        == "CURRENCY_MISMATCH"
    )


def test_a_free_internal_step_needs_no_reservation():
    assert g.reserve_check(
        g.SpendClass.INTERNAL, Decimal(0), "USD", [], ai_enabled=False, now=NOW
    ).ok


def test_the_gate_turns_a_failed_reservation_into_budget_exceeded_or_prohibited():
    pol = policy(ai_enabled=True)
    base = replace(BASELINE, ai_providers=frozenset({"approved-provider"}))
    over = call(spend_class=g.SpendClass.AI_INFERENCE, cost=Decimal("0.50"))
    d = g.evaluate(over, ctx(pol, baseline=base, ceilings=ceilings()), now=NOW)
    assert d.code is g.Code.BUDGET_EXCEEDED and d.budget.ceiling == "call" and d.http_status == 409
    d = g.evaluate(
        call(spend_class=g.SpendClass.EXTERNAL_TRANSACTION), ctx(pol, baseline=base), now=NOW
    )
    assert d.code is g.Code.SCOPE_PROHIBITED
    ok = g.evaluate(
        call(spend_class=g.SpendClass.AI_INFERENCE, cost=Decimal("0.05")),
        ctx(pol, baseline=base, ceilings=ceilings()),
        now=NOW,
    )
    assert ok.allowed


def test_ai_needs_both_the_policy_flag_and_an_approved_company_provider():
    over = call(spend_class=g.SpendClass.AI_INFERENCE, cost=Decimal("0.05"))
    assert (
        g.evaluate(over, ctx(policy(ai_enabled=True), ceilings=ceilings()), now=NOW).code
        is g.Code.BUDGET_EXCEEDED
    )
    base = replace(BASELINE, ai_providers=frozenset({"p"}))
    assert (
        g.evaluate(
            over, ctx(policy(ai_enabled=False), baseline=base, ceilings=ceilings()), now=NOW
        ).code
        is g.Code.BUDGET_EXCEEDED
    )


def test_t30_t31_settlement_releases_unused_and_reports_an_overrun_without_rewriting():
    """T30, T31."""
    s = g.settle(Decimal("0.10"), Decimal("0.04"))
    assert (s.committed, s.released, s.overrun) == (Decimal("0.04"), Decimal("0.06"), Decimal(0))
    late = g.settle(Decimal("0.10"), Decimal("0.13"))
    assert (late.committed, late.released, late.overrun) == (
        Decimal("0.10"),
        Decimal(0),
        Decimal("0.03"),
    )


def test_usage_levels_are_text_and_follow_the_70_90_100_thresholds():
    cap = Decimal(100)
    assert [g.usage_level(Decimal(x), cap) for x in (0, 69, 70, 89, 90, 99, 100)] == [
        "OK",
        "OK",
        "WARN_70",
        "WARN_70",
        "WARN_90",
        "WARN_90",
        "EXHAUSTED",
    ]
    assert g.usage_level(Decimal(0), Decimal(0)) == "OFF"


# ---------------------------------------------------------------- approvals


def fresh_request(**kw):
    pol = policy()
    h = g.policy_hash(pol)
    base = {
        "request_id": "AGP-9",
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "kind": g.ChangeKind.PUBLISH,
        "requested_by": "dev",
        "authors": ("dev", "builder"),
        "policy_hash": h,
        "base_policy_hash": None,
        "delta_hash": g.digest({}),
        "required": ("SECURITY", "AUTHORITY"),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=24),
    }
    base.update(kw)
    return g.ChangeRequest(**base), h


def blockers(req, h, signer="sec", slots=frozenset({"SECURITY"}), **kw):
    args = {
        "signer": signer,
        "signer_slots_now": slots,
        "signer_active": True,
        "presented_hash": h,
        "step_up_ok": True,
        "evidence_available": True,
        "now": NOW + timedelta(hours=1),
    }
    args.update(kw)
    return g.sign_blockers(req, **args)


def test_a_qualified_independent_signer_with_step_up_may_sign():
    req, h = fresh_request()
    assert blockers(req, h) == []


def test_t17_the_requester_and_the_authors_cannot_sign_their_own_change():
    """T17."""
    req, h = fresh_request()
    for who in ("dev", "builder"):
        assert "SEPARATION_OF_DUTIES" in blockers(
            req, h, signer=who, slots=frozenset({"SECURITY", "AUTHORITY"})
        )


def test_t18_publish_needs_both_security_and_authority_and_one_person_fills_one_slot():
    """T18."""
    req, h = fresh_request()
    both = frozenset({"SECURITY", "AUTHORITY"})
    one = g.add_signature(req, g.Signature(g.slot_for(req, both), "p1", NOW, h))
    assert one.state is g.RequestState.PENDING and g.open_slots(one) == ("AUTHORITY",)
    assert "ALREADY_SIGNED" in blockers(one, h, signer="p1", slots=both)
    two = g.add_signature(one, g.Signature(g.slot_for(one, frozenset({"AUTHORITY"})), "p2", NOW, h))
    assert two.state is g.RequestState.APPROVED and g.is_effective_approval(
        two, current_hash=h, now=NOW
    )


def test_t19_t20_slots_for_each_kind_of_change():
    """T19, T20."""
    k = g.ChangeKind
    assert g.required_slots(k.SCOPE_EXTENSION) == ("SECURITY", "AUTHORITY")
    assert g.required_slots(k.SCOPE_EXTENSION, data_class_changed=True) == (
        "SECURITY",
        "AUTHORITY",
        "PRIVACY",
    )
    assert g.required_slots(k.AI_ENABLE) == ("SECURITY", "PRIVACY", "FINANCE")
    assert g.required_slots(k.BUDGET_INCREASE) == ("FINANCE",)
    assert g.required_slots(k.BUDGET_INCREASE, above_baseline=True) == ("FINANCE", "SECURITY")
    assert g.required_slots(k.RESTORE) == ("SECURITY", "AUTHORITY")


def test_a_privilege_mutation_can_be_asked_for_but_never_approved():
    req, h = fresh_request(
        kind=g.ChangeKind.PRIVILEGE_MUTATION,
        required=g.required_slots(g.ChangeKind.PRIVILEGE_MUTATION),
    )
    assert blockers(req, h) == ["REQUEST_FORBIDDEN"]
    assert not g.is_effective_approval(req, current_hash=h, now=NOW)


def test_t21_an_expired_request_cannot_be_signed_or_applied():
    """T21."""
    req, h = fresh_request()
    assert "REQUEST_EXPIRED" in blockers(req, h, now=NOW + timedelta(hours=25))
    signed = fresh_request()[0]
    for slot, who in (("SECURITY", "s"), ("AUTHORITY", "a")):
        signed = g.add_signature(signed, g.Signature(slot, who, NOW, h))
    assert g.is_effective_approval(signed, current_hash=h, now=NOW + timedelta(hours=1))
    assert not g.is_effective_approval(signed, current_hash=h, now=NOW + timedelta(hours=25))


def test_t22_a_signature_on_another_hash_is_refused_and_does_not_count():
    """T22."""
    req, h = fresh_request()
    assert "SCOPE_HASH_MISMATCH" in blockers(req, "sha256:" + "f" * 64)
    forged = g.add_signature(req, g.Signature("SECURITY", "s", NOW, "sha256:" + "f" * 64))
    forged = g.add_signature(forged, g.Signature("AUTHORITY", "a", NOW, h))
    assert not g.is_effective_approval(forged, current_hash=h, now=NOW)


def test_t23_an_approver_whose_role_was_revoked_or_who_left_fails_at_decision_time():
    """T23: slots and standing are inputs read now, not a screen's memory."""
    req, h = fresh_request()
    assert "SIGNER_NOT_QUALIFIED" in blockers(req, h, slots=frozenset())
    assert "SIGNER_NOT_ACTIVE" in blockers(req, h, signer_active=False)


def test_step_up_and_missing_evidence_each_block_a_signature():
    req, h = fresh_request()
    assert "STEP_UP_REQUIRED" in blockers(req, h, step_up_ok=False)
    assert "EVIDENCE_UNAVAILABLE" in blockers(req, h, evidence_available=False)


def test_sponsor_departure_credential_rotation_and_policy_edits_void_an_in_flight_approval():
    req, _ = fresh_request()
    for event in sorted(g.INVALIDATING_EVENTS):
        assert g.invalidated_by(req, event).state is g.RequestState.INVALIDATED
    assert g.invalidated_by(req, "PAGE_VIEWED").state is g.RequestState.PENDING


def test_a_delta_that_widens_access_is_detected():
    old = policy()
    narrower = policy(grants=())
    wider = policy(grants=(lease(), lease(grant_id="g2", tool_id="t2", action="a2")))
    longer = policy(grants=(lease(valid_until=NOW + timedelta(days=60)),))
    quota_up = policy(quotas=g.QuotaLimits(runs_per_agent_day=10))
    ai_on = policy(ai_enabled=True)
    assert not g.delta_widens(g.policy_delta(old, narrower))
    assert not g.delta_widens(g.policy_delta(old, old))
    for newer in (wider, longer, quota_up, ai_on):
        assert g.delta_widens(g.policy_delta(old, newer)), newer


def test_the_policy_hash_ignores_bookkeeping_and_changes_with_any_decision_input():
    a = policy()
    assert g.policy_hash(a) == g.policy_hash(
        replace(a, state="PENDING_APPROVAL", policy_id="other", version=9)
    )
    assert g.policy_hash(a) != g.policy_hash(replace(a, region="us-east"))
    assert g.policy_hash(a) != g.policy_hash(replace(a, ai_enabled=True))
    assert g.policy_hash(a) != g.policy_hash(replace(a, grants=(lease(purpose="other"),)))
    reordered = policy(grants=(lease(), lease(grant_id="g2", tool_id="b", action="x")))
    swapped = policy(grants=(lease(grant_id="g2", tool_id="b", action="x"), lease()))
    assert g.policy_hash(reordered) == g.policy_hash(swapped)


def test_trace_lists_each_gate_passed_for_the_simulator():
    d = g.evaluate(call(), ctx(), now=NOW)
    assert [step for step, _ in d.trace] == [
        "freeze",
        "tenant",
        "agent",
        "hard_deny",
        "lease",
        "baseline",
        "approval",
        "quota",
        "budget",
        "allow",
    ]


# ---------------------------------------------------------------- the export and the entitlement answer


def test_an_export_cell_a_spreadsheet_would_run_as_a_formula_is_made_inert():
    """T40: a leading = + - @ tab or carriage return is prefixed, so an exported value can never execute when the file is opened."""
    from zid_platform.modules.machine.application.governance import _cell

    for evil in ('=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"):
        assert _cell(evil).startswith("'"), evil
    assert (
        _cell("tool_call.governed") == "tool_call.governed"
        and _cell(None) == ""
        and _cell(7) == "7"
    )
    assert _cell({"rows": 3}) == '{"rows": 3}'


def test_the_first_publish_with_ai_on_is_an_ai_change_that_needs_every_signer():
    """A first version with AI explanations on is not a plain publish."""
    from zid_platform.modules.machine.application.governance import derive_kind

    ai = policy(ai_enabled=True)
    kind, _flags, _delta = derive_kind(None, ai)
    assert kind is g.ChangeKind.AI_ENABLE
    assert derive_kind(None, policy())[0] is g.ChangeKind.PUBLISH
    assert (
        derive_kind(policy(ai_enabled=True), ai)[0] is not g.ChangeKind.AI_ENABLE
    )  # already on: not a new AI approval
