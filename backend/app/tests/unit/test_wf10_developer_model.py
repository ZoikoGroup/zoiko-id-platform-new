"""The developer platform's contracts (ZID-CP-WF10 v2.0; CR-173): pure rules, one section per item built."""

from __future__ import annotations

from zid_platform.modules.verify_api.api import sessions as sessions_api
from zid_platform.modules.verify_api.api import verify_api as verify_api_routes
from zid_platform.modules.verify_api.domain import errors as err

# ------------------------------------------------------------------------------------------------------------------- error taxonomy


def test_every_error_the_partner_api_can_raise_belongs_to_a_named_class_so_a_new_code_cannot_ship_unclassified() -> (
    None
):
    internal = set(sessions_api._STATUS) | set(verify_api_routes._STATUS)
    unclassified = sorted(c for c in internal if err.classify(c) is None)
    assert unclassified == [], f"classify these in verify_api/domain/errors.py: {unclassified}"


def test_the_documented_classes_are_all_present_and_each_says_what_the_caller_must_do() -> None:
    documented = {
        "AUTH_SCOPE_DENIED",
        "ENVIRONMENT_MISMATCH",
        "WORKFLOW_NOT_ELIGIBLE",
        "REGIONAL_POLICY_BLOCK",
        "IDEMPOTENCY_CONFLICT",
        "PROVIDER_OUTCOME_UNKNOWN",
        "RATE_LIMITED",
        "FINAL_DECISION_NOT_COMMITTED",
    }
    assert documented <= {c.code for c in err.CATALOG}
    for c in err.CATALOG:
        assert c.caller_behavior.strip() and c.meaning.strip() and 400 <= c.http_status < 600, (
            c.code
        )
    assert len({c.code for c in err.CATALOG}) == len(err.CATALOG)
    owner: dict[str, str] = {}
    for c in err.CATALOG:  # an internal code belongs to exactly one class
        for i in c.internal_codes:
            assert i not in owner, f"{i} is in both {owner[i]} and {c.code}"
            owner[i] = c.code


def test_only_a_rate_limit_is_retryable_and_a_scope_or_unknown_outcome_is_never_blindly_retried() -> (
    None
):
    assert [c.code for c in err.CATALOG if c.retryable] == ["RATE_LIMITED"]
    scope = err.classify("TENANT_SCOPE_DENIED")
    assert (
        scope is not None
        and scope.code == "AUTH_SCOPE_DENIED"
        and "Never retry with a broader scope" in scope.caller_behavior
    )
    unknown = next(c for c in err.CATALOG if c.code == "PROVIDER_OUTCOME_UNKNOWN")
    assert (
        "Never start another verification" in unknown.caller_behavior and unknown.retryable is False
    )
    pending = err.classify("DECISION_NOT_AVAILABLE")
    assert (
        pending is not None
        and pending.code == "FINAL_DECISION_NOT_COMMITTED"
        and "Never treat it as verified" in pending.caller_behavior
    )
    assert err.classify("INVALID_IDEMPOTENCY_REUSE").code == "IDEMPOTENCY_CONFLICT"  # type: ignore[union-attr]
    assert err.classify("SOMETHING_NEW") is None


def test_classes_the_platform_cannot_raise_yet_say_so_instead_of_pretending() -> None:
    view = err.catalog_view()
    not_yet = sorted(e["code"] for e in view["errors"] if not e["raised_today"])  # type: ignore[index, union-attr]
    assert not_yet == [
        "PROVIDER_OUTCOME_UNKNOWN",
        "REGIONAL_POLICY_BLOCK",
    ] and "does not raise them yet" in str(view["note"])


# ---------------------------------------------------------------------------------------------------------------- SDK release register

from datetime import date
from pathlib import Path

from zid_platform.modules.verify_api.domain import sdk_catalog as sdk
from zid_platform.modules.verify_api.infrastructure import sdk_register

TODAY = date(2026, 11, 25)
REPO = Path(__file__).resolve().parents[4]


def entry(**over: object) -> sdk.Sdk:
    base: dict[str, object] = {
        "id": "x", "name": "x", "language": "python", "audience": "PARTNER_SERVER", "state": "EXPERIMENTAL", "version": None, "published": False, "repo_path": "sdks/x", "tests_path": "sdks/x/tests",
        "last_test_run": None, "last_test_evidence": None, "provenance": {"signed": False, "sbom": False}, "compatibility": [],
    }  # fmt: skip
    return sdk.parse({**base, **over})


def test_the_real_register_lists_only_packages_that_exist_and_claims_none_is_generally_available() -> (
    None
):
    items = [sdk.parse(x) for x in sdk_register.register()]
    assert len(items) >= 7 and len({s.id for s in items}) == len(items)
    for s in items:
        assert (REPO / s.repo_path).is_dir(), f"{s.id}: {s.repo_path} is not in the repository"
        assert sdk.problems(s, TODAY) == [], f"{s.id}: {sdk.problems(s, TODAY)}"
    view = sdk.view(items, TODAY)
    assert view["ga_count"] == 0 and view["statement"] == "No package is generally available."
    assert {i["action"] for i in view["items"]} == {"VIEW_SOURCE"}  # type: ignore[union-attr]  # nothing earns a download button yet


def test_ga_needs_a_published_signed_tested_package_and_a_supported_platform_with_evidence() -> (
    None
):
    bare = entry(state="GA")
    assert sdk.problems(bare, TODAY) == [
        "GA_NEEDS_AN_EXECUTED_TEST_RUN_WITH_EVIDENCE",
        "GA_NEEDS_A_SUPPORTED_PLATFORM_WITH_EVIDENCE",
        "GA_NEEDS_SIGNED_RELEASE_AND_SBOM",
        "NOT_PUBLISHED",
    ]
    assert (
        sdk.cta(bare, TODAY) == "VIEW_SOURCE"
    )  # an unreleased package never gets a download button
    good = entry(
        state="GA",
        version="1.0.0",
        published=True,
        provenance={"signed": True, "sbom": True},
        last_test_run="2026-11-01",
        last_test_evidence="ci-run-9",
        compatibility=[{"platform": "python3.12", "supported": True, "evidence_ref": "ci-run-9"}],
    )
    assert sdk.problems(good, TODAY) == [] and sdk.cta(good, TODAY) == "DOWNLOAD"
    stale = entry(
        state="GA",
        version="1.0.0",
        published=True,
        provenance={"signed": True, "sbom": True},
        last_test_run="2026-06-01",
        last_test_evidence="ci-run-1",
        compatibility=[{"platform": "python3.12", "supported": True, "evidence_ref": "ci-run-1"}],
    )
    assert (
        sdk.problems(stale, TODAY) == ["GA_TEST_RUN_IS_STALE"]
        and sdk.cta(stale, TODAY) == "VIEW_SOURCE"
    )  # a test run from last spring proves nothing about today
    unsigned = entry(
        state="GA",
        version="1.0.0",
        published=True,
        provenance={"signed": True, "sbom": False},
        last_test_run="2026-11-01",
        last_test_evidence="e",
        compatibility=[{"platform": "p", "supported": True, "evidence_ref": "e"}],
    )
    assert "GA_NEEDS_SIGNED_RELEASE_AND_SBOM" in sdk.problems(
        unsigned, TODAY
    )  # signed without a bill of materials is not enough


def test_a_supported_platform_without_executed_evidence_is_refused_and_a_beta_must_be_published() -> (
    None
):
    claim = entry(compatibility=[{"platform": "ios17", "supported": True, "evidence_ref": None}])
    assert sdk.problems(claim, TODAY) == ["SUPPORTED_PLATFORM_WITHOUT_EXECUTED_EVIDENCE"]
    assert sdk.view([claim], TODAY)["items"][0]["supported_platforms"] == []  # type: ignore[index]  # an unbacked claim is not shown as support
    assert (
        sdk.problems(entry(state="BETA"), TODAY) == ["NOT_PUBLISHED"]
        and sdk.cta(entry(state="BETA"), TODAY) == "VIEW_SOURCE"
    )
    beta = entry(state="BETA", version="0.9.0", published=True)
    assert sdk.problems(beta, TODAY) == [] and sdk.cta(beta, TODAY) == "TRY_BETA"
    assert sdk.problems(entry(state="MADE_UP", audience="NOBODY"), TODAY) == [
        "AUDIENCE_UNKNOWN",
        "STATE_UNKNOWN",
    ]
    assert sdk.problems(entry(published=True), TODAY) == ["PUBLISHED_WITHOUT_A_VERSION"]


# ----------------------------------------------------------------------------------------------------------- personas, plans, report

from zid_platform.modules.verify_api.domain import fixtures as fx


def good(pid: str, seed: str = "7", **over: object) -> fx.Observation:
    """What a correct partner server would report for this persona."""
    p = fx.persona(pid)
    assert p is not None
    plan = fx.delivery_plan(pid, seed)
    ex = fx.expectations(pid)
    ids = (
        tuple(
            dict.fromkeys(
                d["event_id"]
                for d in plan["deliveries"]
                if d["signature_age_s"] <= fx.SIGNATURE_TOLERANCE_S
                and d["body"] != "ALTERED_AFTER_SIGNING"
            )
        )
        if not ex["must_refuse_signature"] or p.fault == "TAMPERED_BODY"
        else ()
    )
    refused = sum(
        1
        for d in plan["deliveries"]
        if d["signature_age_s"] > fx.SIGNATURE_TOLERANCE_S or d["body"] == "ALTERED_AFTER_SIGNING"
    )
    base: dict[str, object] = {
        "persona": pid, "seed": seed, "plan_digest": plan["plan_digest"], "signatures_refused": refused, "acted_on_event_ids": ids, "final_outcome_seen": None if p.fault in ("STALE_SIGNATURE",) else p.final_outcome,
        "read_authoritative_state": True, "acted_on_pending": False, "refused_missing_field": ex["must_tolerate_extra_fields"], "accepted_extra_field": ex["must_tolerate_extra_fields"],
    }  # fmt: skip
    return fx.parse_observation({**base, **over})


def test_the_same_persona_and_seed_always_give_the_same_plan_and_a_new_seed_changes_the_timing() -> (
    None
):
    a, b = fx.delivery_plan("clear_pass", "7"), fx.delivery_plan("clear_pass", "7")
    assert (
        a == b
        and a["plan_digest"] == b["plan_digest"]
        and a["synthetic"] is True
        and a["marker"] == "TEST / SYNTHETIC"
    )
    assert fx.delivery_plan("clear_pass", "8")["plan_digest"] != a["plan_digest"]
    assert fx.delivery_plan("clear_decline", "7")["plan_digest"] != a["plan_digest"]
    assert [d["event_type"] for d in a["deliveries"]] == [
        "session.created",
        "session.submitted",
        "decision.finalized",
    ]
    assert fx.delivery_plan("clear_pass", "7")["deliveries"][0]["event_id"].startswith("evt_")


def test_each_adversarial_persona_plays_its_documented_pattern() -> None:
    dup = fx.delivery_plan("duplicate_webhook", "1")["deliveries"]
    assert (
        len(dup) == 6
        and len({d["event_id"] for d in dup}) == 3
        and [d["attempt"] for d in dup] == [1, 2, 1, 2, 1, 2]
    )  # every event twice, same id
    swapped = fx.delivery_plan("out_of_order", "1")["deliveries"]
    assert (
        next(d["event_type"] for d in swapped) == "decision.finalized"
        and swapped[0]["object_version"] == 3
    )
    stale = fx.delivery_plan("stale_signature", "1")["deliveries"]
    assert all(d["signature_age_s"] > fx.SIGNATURE_TOLERANCE_S for d in stale)
    assert [d["body"] for d in fx.delivery_plan("tampered_body", "1")["deliveries"]] == [
        "AS_SIGNED",
        "AS_SIGNED",
        "ALTERED_AFTER_SIGNING",
    ]
    assert [d["body"] for d in fx.delivery_plan("schema_drift", "1")["deliveries"]] == [
        "AS_SIGNED",
        "EXTRA_FIELD_ADDED",
        "REQUIRED_FIELD_REMOVED",
    ]
    assert [
        d["respond_after_ms"] for d in fx.delivery_plan("endpoint_timeout", "1")["deliveries"]
    ] == [30000, 0, 0]
    assert {p.fault for p in fx.PERSONAS} <= set(fx.FAULTS) and {p.id for p in fx.PERSONAS} >= {
        "clear_pass",
        "needs_review",
        "session_expired",
        "provider_unavailable",
    }
    try:
        fx.delivery_plan("nobody", "1")
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown persona must not produce a plan")


def test_a_correct_server_passes_every_persona_and_the_report_is_reproducible() -> None:
    obs = [good(p.id) for p in fx.PERSONAS]
    rep = fx.report(obs)
    assert (
        rep["passed"] is True
        and rep["personas_passed"] == rep["personas_total"] == len(fx.PERSONAS)
        and rep["missing_personas"] == []
    )
    assert (
        rep["is_certification"] is False
        and "not a certification" in rep["statement"]
        and rep["synthetic"] is True
    )
    assert (
        fx.report(list(reversed(obs)))["report_digest"] == rep["report_digest"]
    )  # the order a partner sends them in never matters
    changed = [good(p.id) for p in fx.PERSONAS[:-1]] + [
        good("endpoint_timeout", final_outcome_seen="NOT_VERIFIED")
    ]
    assert (
        fx.report(changed)["report_digest"] != rep["report_digest"]
        and fx.report(changed)["passed"] is False
    )


def test_a_server_that_gets_it_wrong_fails_the_check_that_names_what_it_did() -> None:
    def failed(o: fx.Observation) -> set[str]:
        return {c["check"] for c in fx.score(o)["checks"] if not c["passed"]}

    assert failed(
        good("duplicate_webhook", acted_on_event_ids=("e1", "e1", "e2", "e2", "e3", "e3"))
    ) >= {"NEVER_ACTS_TWICE_ON_ONE_EVENT", "DEDUPLICATES"}
    assert "REFUSES_BAD_SIGNATURES" in failed(
        good("stale_signature", signatures_refused=0)
    ) and "DOES_NOT_ACT_ON_REFUSED" in failed(
        good("stale_signature", signatures_refused=0, acted_on_event_ids=("x",))
    )
    assert "REFUSES_BAD_SIGNATURES" in failed(
        good("tampered_body", signatures_refused=0)
    )  # it trusted a body that did not match its signature
    assert "READS_AUTHORITATIVE_STATE" in failed(
        good("out_of_order", read_authoritative_state=False)
    )
    assert "DOES_NOT_TREAT_PENDING_AS_VERIFIED" in failed(
        good("needs_review", acted_on_pending=True, final_outcome_seen="VERIFIED")
    )
    assert {"TOLERATES_NEW_FIELDS", "REFUSES_MISSING_REQUIRED_FIELD"} <= failed(
        good("schema_drift", accepted_extra_field=False, refused_missing_field=False)
    )
    assert "PLAN_MATCHES" in failed(
        good("clear_pass", plan_digest="sha-256:" + "0" * 64)
    )  # it ran some other scenario
    assert "ENDS_WITH_THE_RIGHT_OUTCOME" in failed(
        good("clear_decline", final_outcome_seen="VERIFIED")
    )
    assert (
        fx.score(fx.parse_observation({"persona": "ghost", "seed": "1", "plan_digest": "x"}))[
            "passed"
        ]
        is False
    )


def test_a_run_that_skips_personas_does_not_pass_and_says_which() -> None:
    rep = fx.report([good("clear_pass")])
    assert (
        rep["passed"] is False
        and "needs_review" in rep["missing_personas"]
        and rep["personas_passed"] == 1
    )


# ------------------------------------------------------------------------------------------------------- first-verification checklist

from datetime import UTC, datetime, timedelta

from zid_platform.modules.verify_api.domain import onboarding as ob

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def facts(**over: object) -> ob.Facts:
    base: dict[str, object] = {
        "credential_at": T0,
        "token_at": T0 + timedelta(minutes=2),
        "session_at": T0 + timedelta(minutes=5),
        "endpoint_active": True,
        "confirmed_at": T0 + timedelta(minutes=9),
        "confirmed_event_id": "ev-1",
    }
    return ob.Facts(**{**base, **over})  # type: ignore[arg-type]


def test_nothing_is_ready_until_the_partner_confirms_a_verified_signed_webhook() -> None:
    done = ob.checklist(facts())
    assert (
        done["complete"] is True
        and done["ready"] is True
        and done["next_step"] is None
        and [s["done"] for s in done["steps"]] == [True] * 5
    )
    not_yet = ob.checklist(facts(confirmed_at=None, confirmed_event_id=None))
    assert (
        not_yet["ready"] is False
        and not_yet["next_step"] == "VERIFIED_WEBHOOK"
        and "confirm its event id" in not_yet["next_action"]
    )
    assert [s["done"] for s in not_yet["steps"]] == [
        True,
        True,
        True,
        True,
        False,
    ]  # a key, a token, a session and an endpoint are not a working integration
    assert ob.checklist(facts(credential_at=None))["next_step"] == "CREDENTIAL"
    assert ob.checklist(facts(endpoint_active=False))["next_step"] == "ENDPOINT"


def test_the_time_to_first_verified_webhook_exists_only_for_a_finished_install() -> None:
    assert ob.seconds_to_first_verified_webhook(facts()) == 9 * 60
    for missing in ("credential_at", "token_at", "session_at", "confirmed_at"):
        assert ob.seconds_to_first_verified_webhook(facts(**{missing: None})) is None, missing
    assert ob.seconds_to_first_verified_webhook(facts(endpoint_active=False)) is None
    assert (
        ob.seconds_to_first_verified_webhook(facts(confirmed_at=T0 - timedelta(seconds=5))) == 0
    )  # a clock oddity never gives a negative time


def test_the_median_counts_only_installs_that_finished_and_says_how_many_did_not() -> None:
    m = ob.cohort_median([600, None, 300, None, 900, None])
    assert m == {
        "installs": 6,
        "complete": 3,
        "incomplete": 3,
        "median_seconds": 600,
    }  # the three that stopped are not averaged in as zero
    assert ob.cohort_median([None, None]) == {
        "installs": 2,
        "complete": 0,
        "incomplete": 2,
        "median_seconds": None,
    }
    assert ob.cohort_median([]) == {
        "installs": 0,
        "complete": 0,
        "incomplete": 0,
        "median_seconds": None,
    }
    assert (
        ob.confirmation_problems(False, False) == ["EVENT_NOT_FOUND_OR_NOT_DELIVERED"]
        and ob.confirmation_problems(True, True) == ["ALREADY_CONFIRMED"]
        and ob.confirmation_problems(True, False) == []
    )


# ------------------------------------------------------------------------------------------------------------------ versioned scopes

import pytest
from zid_platform.modules.verify_api.domain import scope_changes as sc
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW2 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
CUR = ("products:read", "workflows:read", "verifications:create", "verifications:read")


def test_a_removal_is_safe_and_can_be_staged_but_an_addition_to_production_needs_a_second_person() -> (
    None
):
    drop = sc.plan(CUR, CUR[:3], production=True, now=NOW2)
    assert drop.kind == "REMOVE" and drop.requires_approval is False and drop.effective_at == NOW2
    staged = sc.plan(CUR, CUR[:3], production=True, now=NOW2, effective_at=NOW2 + timedelta(days=7))
    assert staged.effective_at == NOW2 + timedelta(days=7)
    widen = sc.plan(CUR, (*CUR, "usage:read"), production=True, now=NOW2)
    assert widen.kind == "ADD" and widen.requires_approval is True
    assert (
        sc.plan(CUR, (*CUR, "usage:read"), production=False, now=NOW2).requires_approval is False
    )  # a sandbox widens at once
    swap = sc.plan(CUR, (*CUR[:3], "usage:read"), production=True, now=NOW2)
    assert (
        swap.kind == "REPLACE" and swap.requires_approval is True
    )  # any widening needs the second person, even when something is also removed


def test_a_bad_scope_change_is_refused_with_the_reason() -> None:
    def refused(**kw: object) -> str:
        with pytest.raises(PartnerError) as e:
            sc.plan(kw.pop("current", CUR), kw.pop("wanted"), now=NOW2, **kw)  # type: ignore[arg-type]
        return e.value.detail

    assert "at least one scope" in refused(wanted=(), production=False)
    assert "no wildcard scopes" in refused(
        wanted=("*",), production=False
    ) and "Unknown scope" in refused(wanted=("verifications:*",), production=False)
    assert "already has" in refused(wanted=CUR, production=False)
    assert "go-live process" in refused(wanted=(*CUR, "applications:submit"), production=False)
    assert "in the past" in refused(
        wanted=CUR[:2], production=False, effective_at=NOW2 - timedelta(hours=1)
    )
    assert "at most 30 days" in refused(
        wanted=CUR[:2], production=False, effective_at=NOW2 + timedelta(days=31)
    )
    assert "Only a removal can be staged" in refused(
        wanted=(*CUR, "usage:read"), production=False, effective_at=NOW2 + timedelta(days=2)
    )


def test_the_proposer_never_approves_their_own_change_and_a_token_is_never_wider_than_what_is_held_now() -> (
    None
):
    assert sc.approval_problems("a", "a", []) == ["PROPOSER_CANNOT_APPROVE_OWN_CHANGE"]
    assert (
        sc.approval_problems("a", "b", ["b"]) == ["ALREADY_APPROVED_BY_THIS_PERSON"]
        and sc.approval_problems("a", "b", []) == []
    )
    assert sc.effective_scopes(CUR, CUR, ["products:read"]) == (
        "products:read",
    )  # removed since the token was issued
    assert (
        sc.effective_scopes(CUR, CUR[:2], None) == CUR[:2]
    )  # no change ever made: the application own scopes apply
    assert sc.effective_scopes(CUR, ("usage:read",), ["usage:read", "products:read"]) == (
        "usage:read",
    )  # never wider than the token itself
    assert sc.effective_scopes(CUR, CUR, []) == ()


# ------------------------------------------------------------------------------------------------------------------ credential policy

from zid_platform.modules.verify_api.domain import credential_policy as cp
from zid_platform.modules.verify_api.domain import model as vm


def test_the_credential_statement_is_built_from_the_real_constants_and_the_tests_it_cites_exist() -> (
    None
):
    p = cp.policy()
    assert p["access_token_lifetime_seconds"] == int(vm.TOKEN_TTL.total_seconds()) == 900
    assert p["rotation"]["default_overlap_hours"] == 24 and p["rotation"]["max_overlap_days"] == 7
    c = p["containment"]
    assert (
        c["longest_an_issued_token_can_be_used_after_these_seconds"] == 0
        and c["upper_bound_if_a_token_is_stolen_and_the_credential_is_not_revoked_seconds"] == 900
    )
    assert (
        "design statement" in c["not_covered"]
        and p["revocation_objective"]["status"] == "NOT_RATIFIED"
        and "cannot be read back" in p["secrets"]
    )
    for name, ref in p["proved_by"].items():
        path, _, test = ref.partition("::")
        source = (REPO / path).read_text(encoding="utf-8")
        assert f"def {test}(" in source, f"{name}: {ref} does not exist"


# ------------------------------------------------------------------------------------------------------------------ API version catalog

import json

from zid_platform.modules.verify_api.application import partner_spec
from zid_platform.modules.verify_api.domain import api_versions as av
from zid_platform.modules.verify_api.infrastructure import api_version_register as reg


def rel(version: str, major: int, released: str, **over: object) -> av.Release:
    base: dict[str, object] = {
        "version": version,
        "major": major,
        "released": released,
        "state": "SUPPORTED",
        "breaking": False,
        "snapshot": f"contracts/openapi/partner/{version}.json",
        "digest": "sha-256:" + "a" * 64,
        "changelog": ["x"],
    }
    return av.parse({**base, **over})


def test_the_real_register_is_consistent_and_every_snapshot_is_exactly_what_was_released() -> None:
    releases = [av.parse(x) for x in reg.register()]
    assert av.register_problems(releases) == []
    for r in releases:
        text = reg.snapshot_text(r.snapshot)
        assert av.snapshot_problems(r, text) == [], (
            r.version
        )  # a snapshot edited after release changes its digest and fails here
        assert av.secret_problems(text or "") == []
    assert [r.version for r in releases if r.state == "CURRENT"] == ["1.0"]


def test_the_current_snapshot_equals_what_the_code_publishes_so_an_unrecorded_change_fails_the_build() -> (
    None
):
    current = next(av.parse(x) for x in reg.register() if x["state"] == "CURRENT")
    live = partner_spec.build(
        json.loads((REPO / "contracts/openapi/public/verify-api.json").read_text(encoding="utf-8"))
    )
    snapshot = json.loads(reg.snapshot_text(current.snapshot) or "{}")
    assert snapshot == json.loads(json.dumps(live)), (
        "the partner API changed: add a release to config/developer/api-versions.yaml with a new snapshot and a changelog line"
    )


def test_a_breaking_release_needs_a_new_major_a_migration_note_and_a_support_window_for_the_one_before() -> (
    None
):
    cur = rel("2.0", 2, "2027-06-01", state="CURRENT", breaking=True, migration="Rename X to Y.")
    old = rel(
        "1.0",
        1,
        "2026-11-25",
        state="DEPRECATED",
        announced="2027-06-01",
        supported_until="2027-12-31",
    )
    assert av.register_problems([old, cur]) == []
    assert "BREAKING_CHANGE_WITHOUT_A_NEW_MAJOR:2.1" in av.register_problems(
        [old, rel("2.1", 1, "2027-07-01", state="CURRENT", breaking=True, migration="m")]
    )
    assert "BREAKING_RELEASE_WITHOUT_A_MIGRATION_NOTE:2.0" in av.register_problems(
        [old, rel("2.0", 2, "2027-06-01", state="CURRENT", breaking=True)]
    )
    short = rel(
        "1.0",
        1,
        "2026-11-25",
        state="DEPRECATED",
        announced="2027-06-01",
        supported_until="2027-09-01",
    )
    assert "SUPPORT_WINDOW_UNDER_180_DAYS:1.0" in av.register_problems([short, cur])
    assert "DEPRECATED_WITHOUT_DATES:1.0" in av.register_problems(
        [rel("1.0", 1, "2026-11-25", state="DEPRECATED"), cur]
    )
    assert "EXACTLY_ONE_RELEASE_MUST_BE_CURRENT" in av.register_problems(
        [old, rel("2.0", 2, "2027-06-01", state="SUPPORTED")]
    )
    assert "THE_CURRENT_RELEASE_MUST_BE_THE_NEWEST" in av.register_problems(
        [
            rel("1.0", 1, "2027-05-01", state="CURRENT"),
            rel("2.0", 2, "2027-06-01", state="SUPPORTED"),
        ]
    )
    assert "CHANGELOG_MISSING:1.0" in av.register_problems(
        [rel("1.0", 1, "2026-11-25", state="CURRENT", changelog=[])]
    )
    assert av.register_problems([]) == ["NO_RELEASES"]


def test_an_edited_or_missing_snapshot_and_a_secret_looking_example_are_caught() -> None:
    r = rel("1.0", 1, "2026-11-25", digest=av.digest_of("original"))
    assert (
        av.snapshot_problems(r, "original") == []
        and av.snapshot_problems(r, "edited") == ["SNAPSHOT_CHANGED_AFTER_RELEASE:1.0"]
        and av.snapshot_problems(r, None) == ["SNAPSHOT_MISSING:1.0"]
    )
    assert (
        av.secret_problems("-----BEGIN " + "PRIVATE KEY-----")
        and av.secret_problems("example sk_live_abc")
        and av.secret_problems("nothing here") == []
    )
    assert (
        reg.snapshot_text("../../etc/passwd") is None
        and reg.snapshot_text("backend/config/developer/sdk-releases.yaml") is None
    )  # only the contracts folder is readable


# ------------------------------------------------------------------------------------------------------------ diagnostics and trace

from zid_platform.modules.verify_api.domain import diagnostics as dg


def d(status: str, code: int | None = None, error: str | None = None) -> dict[str, object]:
    return {"status": status, "last_status_code": code, "last_error": error}


def test_each_delivery_falls_into_one_named_class_and_what_cannot_be_told_apart_is_said_so() -> (
    None
):
    assert dg.classify("DELIVERED", 200, None) == "DELIVERED"
    assert (
        dg.classify("RETRY_SCHEDULED", 403, None) == "REJECTED_BY_ENDPOINT"
        and dg.classify("FAILED", 410, None) == "REJECTED_BY_ENDPOINT"
    )
    assert dg.classify("RETRY_SCHEDULED", 503, None) == "ENDPOINT_ERROR"
    assert (
        dg.classify("RETRY_SCHEDULED", None, "ReadTimeout") == "TIMED_OUT"
        and dg.classify("RETRY_SCHEDULED", None, "ConnectTimeout") == "TIMED_OUT"
    )
    assert (
        dg.classify("RETRY_SCHEDULED", None, "ConnectError") == "COULD_NOT_CONNECT"
    )  # DNS, TLS and a refused connection look the same here
    assert dg.classify("FAILED", None, "WEBHOOK_DESTINATION_BLOCKED") == "DESTINATION_BLOCKED"
    assert dg.classify("QUEUED", None, None) == "WAITING"
    assert "does not tell these apart" in dg.ADVICE["COULD_NOT_CONNECT"] and set(dg.ADVICE) == set(
        dg.CLASSES
    )


def test_an_endpoint_verdict_names_the_main_problem_and_never_calls_an_untried_endpoint_healthy() -> (
    None
):
    assert dg.endpoint_health([d("DELIVERED", 200)] * 5)["verdict"] == "HEALTHY"
    assert (
        dg.endpoint_health([d("QUEUED")] * 3)["verdict"] == "UNKNOWN"
        and dg.endpoint_health([])["verdict"] == "UNKNOWN"
    )
    mixed = dg.endpoint_health([d("DELIVERED", 200)] * 8 + [d("RETRY_SCHEDULED", 500)] * 2)
    assert (
        mixed["verdict"] == "DEGRADED"
        and mixed["main_problem"] == "ENDPOINT_ERROR"
        and mixed["counts"]["DELIVERED"] == 8
    )
    failing = dg.endpoint_health([d("RETRY_SCHEDULED", 401)] * 6 + [d("DELIVERED", 200)] * 2)
    assert (
        failing["verdict"] == "FAILING"
        and failing["main_problem"] == "REJECTED_BY_ENDPOINT"
        and "signature" in failing["advice"]
    )
    assert dg.endpoint_health([d("RETRY_SCHEDULED", 502)] * 4)["verdict"] == "FAILING"
    assert (
        dg.endpoint_health([d("DELIVERED", 200)] * 30)["sampled"] == dg.SAMPLE
    )  # only the newest are read
    assert (
        dg.endpoint_health([d("DELIVERED", 200)], confirmed_events=2)[
            "partner_confirmed_signature_events"
        ]
        == 2
    )


def test_the_trace_orders_the_links_by_time_names_the_missing_ones_and_carries_no_person_data() -> (
    None
):
    t = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    reqs = [
        {
            "occurred_at": t,
            "trace_id": "cor_1",
            "method": "POST",
            "route": "/v1/verify-api/verification-sessions",
            "status": 201,
            "error_code": None,
        }
    ]
    routes = [
        {
            "created_at": t + timedelta(seconds=1),
            "decision_id": "rd1",
            "status": "ROUTE_SELECTED",
            "selected": "prov",
        }
    ]
    decisions = [
        {
            "created_at": t + timedelta(seconds=30),
            "decision_id": "dec_1",
            "version": 1,
            "outcome": "verified",
        }
    ]
    hooks = [
        {
            "created_at": t + timedelta(seconds=31),
            "event_id": "ev1",
            "event_type": "verification.api.session_decided",
            "status": "DELIVERED",
            "last_status_code": 200,
            "last_error": None,
        }
    ]
    full = dg.build_trace("vfs_1", {"status": "completed"}, reqs, routes, decisions, hooks)
    assert (
        [s["kind"] for s in full["steps"]]
        == ["API_REQUEST", "ROUTE_DECISION", "DECISION", "WEBHOOK_DELIVERY"]
        and full["missing_links"] == []
        and full["found"] is True
    )
    assert (
        full["steps"][3]["summary"] == "verification.api.session_decided: delivered"
        and "applicant" not in str(full).lower()
    )
    partial = dg.build_trace("vfs_1", {"status": "ready"}, reqs, [], [], [])
    assert partial["missing_links"] == [
        "ROUTE_DECISION",
        "DECISION",
        "WEBHOOK_DELIVERY",
    ]  # listed, not invented
    assert dg.build_trace("vfs_x", None, [], [], [], [])["found"] is False


def test_the_explorer_only_takes_sane_filters_and_logs_a_route_template_never_a_path_with_ids() -> (
    None
):
    assert (
        dg.request_filters_problems(None, None, 50) == []
        and dg.request_filters_problems(429, "/v1/verify-api/whoami", 1) == []
    )
    assert dg.request_filters_problems(99, None, 50) == [
        "STATUS_OUT_OF_RANGE"
    ] and dg.request_filters_problems(None, "/x?y=1", 50) == ["ROUTE_FILTER_INVALID"]
    assert dg.request_filters_problems(None, None, 0) == [
        "LIMIT_OUT_OF_RANGE"
    ] and dg.request_filters_problems(None, None, 201) == ["LIMIT_OUT_OF_RANGE"]
    assert (
        dg.route_template("/v1/verify-api/verification-sessions/{session_id}", "/x")
        == "/v1/verify-api/verification-sessions/{session_id}"
    )
    assert (
        dg.route_template(None, "/v1/a?secret=1") == "/v1/a"
        and len(dg.route_template("/" + "a" * 300, "")) == 120
    )


# ------------------------------------------------------------------------------------------------------------------ developer MCP

from zid_platform.modules.verify_api.domain import mcp_dev as md

IMPLS = {n: (lambda a, n=n: {"tool": n, "args": dict(a)}) for n in md.READ_TOOLS}


def rpc(
    method: str, params: dict[str, object] | None = None, rid: int | None = 1
) -> dict[str, object]:
    msg: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        msg["id"] = rid
    if params is not None:
        msg["params"] = params
    return msg


def test_the_developer_mcp_offers_only_read_only_documentation_tools_and_says_what_is_off_and_why() -> (
    None
):
    listed = md.handle(rpc("tools/list"), IMPLS)["result"]["tools"]  # type: ignore[index]
    assert [t["name"] for t in listed] == list(md.READ_TOOLS) and all(
        t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in listed
    )
    reg = md.registry()
    assert {d["name"] for d in reg["disabled"]} >= {
        "create_verification_session",
        "rotate_credential",
        "anything_in_production",
    } and all(d["reason"] for d in reg["disabled"])
    assert not {d["name"] for d in reg["disabled"]} & {
        t["name"] for t in listed
    }  # a disabled tool is never discoverable
    assert (
        "None is needed" in reg["authentication"]
        and md.handle(rpc("initialize"), IMPLS)["result"]["protocolVersion"] == md.PROTOCOL
    )  # type: ignore[index]


def test_a_disabled_or_unknown_tool_and_bad_arguments_are_refused_by_name_and_a_notification_gets_no_answer() -> (
    None
):
    def call(name: object, args: object = None) -> dict[str, object]:
        p: dict[str, object] = {"name": name}
        if args is not None:
            p["arguments"] = args
        out = md.handle(rpc("tools/call", p), IMPLS)
        assert out is not None
        return out

    assert call("create_verification_session")["error"]["message"] == "That tool is not available."  # type: ignore[index]
    assert call("no_such_tool")["error"]["message"] == "There is no such tool."  # type: ignore[index]
    assert (
        "unknown argument extra"
        in call("lookup_error", {"code": "X", "extra": 1})["error"]["message"]
    )  # type: ignore[index]
    assert (
        "code is required" in call("lookup_error", {})["error"]["message"]
        and "short string" in call("lookup_error", {"code": "x" * 500})["error"]["message"]
    )  # type: ignore[index]
    assert (
        "short string" in call("lookup_error", {"code": 7})["error"]["message"]
        and "arguments must be an object" in call("lookup_error", ["x"])["error"]["message"]
    )  # type: ignore[index]
    ok = call("lookup_error", {"code": "RATE_LIMITED"})
    assert ok["result"]["isError"] is False and "RATE_LIMITED" in ok["result"]["content"][0]["text"]  # type: ignore[index]
    assert md.handle(rpc("notifications/initialized", rid=None), IMPLS) is None
    assert (
        md.handle({"jsonrpc": "1.0", "method": "ping"}, IMPLS)["error"]["code"] == -32600
        and md.handle(rpc("resources/list"), IMPLS)["error"]["code"] == -32601
    )  # type: ignore[index]
    missing = md.handle(
        rpc("tools/call", {"name": "lookup_error", "arguments": {"code": "ghost"}}),
        {"lookup_error": lambda a: (_ for _ in ()).throw(KeyError("x"))},
    )
    assert missing["result"]["isError"] is True  # type: ignore[index]


# ------------------------------------------------------------------------------------------------------------------ certification runs


def stored(passed: bool = True, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "run_id": "r1",
        "passed": passed,
        "personas_passed": len(fx.PERSONAS) if passed else 3,
        "personas_total": len(fx.PERSONAS),
        "report_digest": "sha-256:" + "b" * 64,
        "created_at": datetime(2026, 11, 20, tzinfo=UTC),
        "report": {"catalog_version": fx.CATALOG_VERSION, "missing_personas": []},
    }
    return {**base, **over}


def test_a_stored_run_counts_only_when_it_passed_every_persona_of_the_current_catalog_recently() -> (
    None
):
    now = datetime(2026, 11, 25, tzinfo=UTC)
    assert fx.suite_status(None, now)["status"] == "NOT_RUN"
    ok = fx.suite_status(stored(), now)
    assert (
        ok["status"] == "PASSED"
        and ok["met"] is True
        and ok["approves_go_live"] is False
        and "does not certify" in ok["statement"]
    )
    assert fx.suite_status(stored(False), now)["reasons"] == ["THE_LATEST_RUN_DID_NOT_PASS"]
    assert (
        "RUN_IS_AGAINST_AN_OLDER_CATALOG"
        in fx.suite_status(stored(report={"catalog_version": "0", "missing_personas": []}), now)[
            "reasons"
        ]
    )
    assert (
        "NOT_EVERY_PERSONA_WAS_RUN"
        in fx.suite_status(
            stored(
                report={"catalog_version": fx.CATALOG_VERSION, "missing_personas": ["needs_review"]}
            ),
            now,
        )["reasons"]
    )
    assert fx.suite_status(stored(created_at=datetime(2026, 7, 1, tzinfo=UTC)), now)["reasons"] == [
        "THE_RUN_IS_OLDER_THAN_90_DAYS"
    ]
    assert (
        fx.suite_status(stored(created_at=datetime(2026, 7, 1, tzinfo=UTC)), now)["status"]
        == "NOT_CURRENT"
    )


def test_the_tampered_body_persona_is_scored_by_refusal() -> None:
    plan = fx.delivery_plan("tampered_body", "5")
    altered = [d for d in plan["deliveries"] if d["body"] == "ALTERED_AFTER_SIGNING"]
    assert len(altered) == 1 and altered[0]["event_type"] == "decision.finalized"
    refused = good("tampered_body", "5")
    assert fx.score(refused)["passed"] is True and refused.signatures_refused == 1
    trusted = good(
        "tampered_body",
        "5",
        signatures_refused=0,
        acted_on_event_ids=tuple(d["event_id"] for d in plan["deliveries"]),
    )
    assert (
        fx.score(trusted)["passed"] is False
    )  # acting on a body that no longer matches its signature fails the persona


# ------------------------------------------------------------------------------------------------------------------ SDK error parity


def test_every_sdk_ships_the_same_error_table_as_the_platform_catalog_and_nothing_is_left_out() -> (
    None
):
    expected = err.parity_json()
    copies = [
        "contracts/openapi/partner/error-classes.json",
        "sdks/verify-api-python/src/zoiko_verify/error_classes.json",
        "sdks/typescript/src/error-classes.json",
        "sdks/go/error_classes.json",
        "sdks/java/src/main/resources/zoiko/error_classes.json",
        "sdks/dotnet/src/error_classes.json",
    ]
    for rel_path in copies:
        assert (REPO / rel_path).read_text(encoding="utf-8") == expected, (
            f"{rel_path} differs from the catalog: regenerate it from verify_api/domain/errors.py parity_json()"
        )
    table = json.loads(expected)
    for code in set(sessions_api._STATUS) | set(verify_api_routes._STATUS):
        assert code in table["codes"], f"{code} is not in the SDK table"
    assert {v["class"] for v in table["codes"].values()} == set(table["classes"])
    assert (
        table["codes"]["RATE_LIMITED"] == {"class": "RATE_LIMITED", "retryable": True}
        and table["codes"]["TENANT_SCOPE_DENIED"]["class"] == "AUTH_SCOPE_DENIED"
    )


# ------------------------------------------------------------------------------------------------------------------ public claims

from zid_platform.modules.verify_api.domain import claims as cl


def feed_for(ga: int = 0, certs: list[dict[str, object]] | None = None) -> dict[str, object]:
    sdk_view = {
        "ga_count": ga,
        "as_of": "2026-11-25",
        "statement": "No package is generally available."
        if not ga
        else f"{ga} package(s) are generally available, each with a signed release, a bill of materials and a current executed test run.",
    }
    versions = {
        "releases": [{"state": "CURRENT", "version": "1.0", "digest": "sha-256:" + "e" * 64}]
    }
    return cl.build(
        sdk_view=sdk_view,
        versions_view=versions,
        persona_count=11,
        token_seconds=900,
        zoiko_certifications=certs or [],
        today=date(2026, 11, 25),
    )


def test_the_claims_feed_says_what_is_true_today_and_lists_what_may_not_be_said_with_the_reason() -> (
    None
):
    f = feed_for()
    ids = {c["id"] for c in f["claims"]}  # type: ignore[union-attr]
    assert (
        ids == {"sdk-availability", "api-version", "sandbox-scenarios", "token-lifetime"}
        and cl.problems(f) == []
    )
    nots = {n["claim"]: n["reason"] for n in f["not_claimed"]}  # type: ignore[union-attr]
    assert (
        "Zoiko iD is a certified identity verification provider" in nots
        and "No active certification in Zoiko iD's own name"
        in nots["Zoiko iD is a certified identity verification provider"]
    )
    assert (
        "SDKs are available for production use" in nots
        and "The sandbox integration test certifies a partner" in nots
        and "Revocation is instant everywhere" in nots
    )
    assert (
        next(c for c in f["claims"] if c["id"] == "sdk-availability")["statement"]
        == "No package is generally available."
    )  # type: ignore[union-attr]


def test_a_certification_appears_only_with_an_active_record_in_zoikos_own_name_and_inside_its_dates() -> (
    None
):
    rec = {
        "record_id": "r1",
        "scheme": "UK_DVS",
        "scheme_version": "1.0",
        "country": "GB",
        "service_code": "idv",
        "certificate_ref": "CERT-123",
        "status": "ACTIVE",
        "valid_until": date(2027, 1, 1),
    }
    f = feed_for(certs=[rec])
    c = next(x for x in f["claims"] if x["id"] == "certification-r1")  # type: ignore[union-attr]
    assert c["evidence"] == "CERT-123" and "UK_DVS" in c["statement"] and cl.problems(f) == []
    assert not any(
        "certified identity verification provider" in n["claim"] for n in f["not_claimed"]
    )  # type: ignore[union-attr]
    for dead in ({**rec, "status": "SUSPENDED"}, {**rec, "valid_until": date(2026, 1, 1)}):
        assert all(
            not x["id"].startswith("certification-") for x in feed_for(certs=[dead])["claims"]
        )  # type: ignore[union-attr]
    assert (
        "(a component of the service only)"
        in next(
            x
            for x in feed_for(certs=[{**rec, "component_only": True}])["claims"]
            if x["id"].startswith("certification-")
        )["statement"]
    )  # type: ignore[union-attr]


def test_a_sentence_using_a_word_that_needs_a_record_is_flagged_when_nothing_backs_it() -> None:
    bad = {
        "claims": [
            {"id": "x", "statement": "Our SDK is certified and compliant.", "evidence": "none"},
            {"id": "y", "statement": "Fine.", "evidence": ""},
        ]
    }
    assert cl.problems(bad) == ["UNBACKED_WORD:x:certified", "NO_EVIDENCE:y"]
    assert cl.problems(feed_for(ga=2)) == [] and "2 package(s) are generally available" in str(
        feed_for(ga=2)["claims"]
    )


def test_a_containment_objective_is_shown_as_met_or_not_met_against_what_was_measured_and_never_as_a_promise_unless_ratified() -> (
    None
):
    assert (
        cp.objective(None, None, 0)["status"] == "NOT_RATIFIED"
        and cp.objective(None, 0.25, 40)["measured_p99_seconds"] == 0.25
    )
    assert cp.objective(5.0, None, 0)["status"] == "NOT_MEASURED_YET"
    ok = cp.objective(5.0, 1.0, 120)
    assert ok["status"] == "MET" and "120 refusals" in ok["statement"]
    assert (
        cp.objective(5.0, 5.0, 3)["status"] == "MET"
        and cp.objective(5.0, 10.0, 3)["status"] == "NOT_MET"
    )
    assert (
        "systems that cache" in cp.objective(5.0, 1.0, 1)["caveat"].lower()
        or "cache a token" in cp.objective(5.0, 1.0, 1)["caveat"]
    )


# ------------------------------------------------------------------------------------------------------------ sandbox synthetic data

from zid_platform.modules.verify_api.domain import sessions as vs


def probs(ref: str, **meta: str) -> list[str]:
    return vs.synthetic_problems({"customer_reference": ref, "metadata": meta})


def test_the_sandbox_refuses_what_looks_like_a_real_persons_details_and_accepts_invented_references() -> (
    None
):
    for fine in ("test-user-1", "customer-48201", "c-1", "try-it", "sandbox-test", "A1B2-C3"):
        assert probs(fine) == [], fine
    assert probs("anna.smith@example.com") == ["customer_reference:LOOKS_LIKE_AN_EMAIL_ADDRESS"]
    assert probs("ref-123456789") == ["customer_reference:LOOKS_LIKE_AN_ID_OR_PHONE_NUMBER"]
    assert probs("+44 7700 900 123") == ["customer_reference:LOOKS_LIKE_AN_ID_OR_PHONE_NUMBER"]
    assert probs("4111-1111-1111-1111") == ["customer_reference:LOOKS_LIKE_AN_ID_OR_PHONE_NUMBER"]
    assert probs("P<GBRSMITH<<ANNA<<<<<<<<<<<<<<<<<<<<<<<<") == [
        "customer_reference:LOOKS_LIKE_A_PASSPORT_MACHINE_READABLE_LINE"
    ]
    assert probs("fine", note="call me on 07700900123") == [
        "metadata.note:LOOKS_LIKE_AN_ID_OR_PHONE_NUMBER"
    ]  # metadata is checked too
    assert probs("fine", contact="a@b.io") == ["metadata.contact:LOOKS_LIKE_AN_EMAIL_ADDRESS"]
    with pytest.raises(PartnerError) as e:
        vs.check_synthetic({"customer_reference": "x@y.zz"})
    assert (
        e.value.code == "SANDBOX_REQUIRES_SYNTHETIC_DATA"
        and "made-up people only" in e.value.detail
    )
