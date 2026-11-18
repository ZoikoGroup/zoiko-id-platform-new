"""WF-05 check methods (ZID-DTV-WF-05 §01, §02, §04, §05, §23, A01; CR-112): what each method can show, the data it creates, and when a live call may
use it. Synthetic only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import PROVES, Assertion
from zid_platform.modules.verification.infrastructure import check_method_config as config

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
ADAPTER = cm.Adapter(cm.CheckMethod.EMAIL_CHALLENGE, "zid-email-challenge", "1", cm.Route.OPEN)


def approved_retention() -> dict[str, cm.RetentionRule]:
    return {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }


def live(scope_id: str = "email-gb") -> bm.Availability:
    rec = bm.Record(
        scope_id=scope_id, version=3, scope=bm.Scope(cm.CheckMethod.EMAIL_CHALLENGE, "zid-email-challenge", "1", "GB", "ONBOARDING"),
        state=bm.State.PRODUCTION_ELIGIBLE, dpia_ref="DPIA-1", evaluation_ref="QA-1", reason="approved for launch", evidence_ref="T-1",
        recorded_by="b", proposed_by="a", proposal_id="p", effective_at=NOW, recorded_at=NOW,
    )  # fmt: skip
    return bm.Availability(True, bm.State.PRODUCTION_ELIGIBLE, (), rec)


def elig(**kw: Any) -> cm.MethodEligibility:
    base: dict[str, Any] = {
        "adapter": ADAPTER,
        "availability": live(),
        "retention": approved_retention(),
        "licensed_entitled": False,
    }
    base.update(kw)
    return cm.eligibility(cm.CheckMethod.EMAIL_CHALLENGE, **base)


# ------------------------------------------------------------------------------------------------------------------- what a method can show


def test_six_assertions_each_say_what_they_prove_and_what_they_do_not():
    assert set(PROVES) == set(Assertion) and len(Assertion) == 6
    for proves, does_not in PROVES.values():
        assert proves and does_not and proves != does_not


def test_every_method_produces_only_its_own_kind_of_evidence():
    """WF-05 §02: a lookup is never a document review or an association; a challenge is only contact control; only device methods observe devices."""
    assert set(cm.PRODUCES) == set(cm.CheckMethod)
    lookup = cm.PRODUCES[cm.CheckMethod.ADDRESS_LOOKUP]
    assert Assertion.ADDRESS_DOCUMENT_REVIEWED not in lookup
    assert Assertion.ADDRESS_ASSOCIATION_CONFIRMED not in lookup
    for m in (
        cm.CheckMethod.EMAIL_CHALLENGE,
        cm.CheckMethod.PHONE_SMS_CHALLENGE,
        cm.CheckMethod.PHONE_VOICE_CHALLENGE,
    ):
        assert cm.PRODUCES[m] == {Assertion.CONTACT_CONTROL_CONFIRMED}
    observers = {m for m, a in cm.PRODUCES.items() if Assertion.DEVICE_RISK_OBSERVED in a}
    assert observers == {cm.CheckMethod.DEVICE_RISK, cm.CheckMethod.CARRIER_INTELLIGENCE}
    # only an email or phone challenge shows contact control (a postal code shows access to mail at an address, not an inbox or a number)
    controllers = {m for m, a in cm.PRODUCES.items() if Assertion.CONTACT_CONTROL_CONFIRMED in a}
    assert controllers == {
        cm.CheckMethod.EMAIL_CHALLENGE,
        cm.CheckMethod.PHONE_SMS_CHALLENGE,
        cm.CheckMethod.PHONE_VOICE_CHALLENGE,
    }


def test_an_assertion_a_method_cannot_show_is_refused():
    cm.check_produces(cm.CheckMethod.ADDRESS_LOOKUP, Assertion.ADDRESS_LOCATION_MATCH)
    for method, assertion in (
        (cm.CheckMethod.ADDRESS_LOOKUP, Assertion.ADDRESS_ASSOCIATION_CONFIRMED),
        (cm.CheckMethod.EMAIL_CHALLENGE, Assertion.ADDRESS_ASSOCIATION_CONFIRMED),
        (cm.CheckMethod.DEVICE_RISK, Assertion.CONTACT_CONTROL_CONFIRMED),
        (cm.CheckMethod.ADDRESS_DOCUMENT, Assertion.ADDRESS_ASSOCIATION_CONFIRMED),
    ):
        with pytest.raises(cm.CheckMethodError) as e:
            cm.check_produces(method, assertion)
        assert e.value.code == "ASSERTION_NOT_PRODUCED_BY_METHOD"


def test_every_method_names_the_data_it_creates_and_the_decision_record():
    assert set(cm.DATA_CLASSES) == set(cm.CheckMethod)
    assert all("audit.decision" in cs for cs in cm.DATA_CLASSES.values())
    assert "address.document" in cm.DATA_CLASSES[cm.CheckMethod.ADDRESS_DOCUMENT]
    assert "address.document" not in cm.DATA_CLASSES[cm.CheckMethod.ADDRESS_LOOKUP]


# ------------------------------------------------------------------------------------------------------------------- eligibility


def test_a_method_with_an_adapter_a_live_scope_and_approved_retention_is_available():
    e = elig()
    assert e.available and e.reasons == () and e.state is bm.State.PRODUCTION_ELIGIBLE
    assert e.produces == ("CONTACT_CONTROL_CONFIRMED",) and e.scope_id == "email-gb"


def test_no_adapter_is_never_available_whatever_else_holds():
    e = elig(adapter=None, availability=None)
    assert not e.available and e.reasons[0] == "NO_ADAPTER" and e.state is bm.State.OFF


def test_each_lifecycle_refusal_is_passed_through():
    for state, code in (
        (bm.State.OFF, "METHOD_OFF"),
        (bm.State.LAB_ONLY, "METHOD_LAB_ONLY"),
        (bm.State.PAUSED, "METHOD_PAUSED"),
    ):
        e = elig(availability=bm.Availability(False, state, (code,), None))
        assert not e.available and code in e.reasons and e.state is state
    assert "METHOD_OFF" in elig(availability=None).reasons


def test_each_data_class_without_an_approved_retention_rule_is_named():
    """WF-05 §23: no universal fixed retention is approved; a method whose data has no approved rule is never live."""
    retention = approved_retention()
    retention["challenge.secret_and_delivery"] = cm.RetentionRule(
        "challenge.secret_and_delivery", "SAMPLE", 7
    )
    del retention["contact.email"]
    e = elig(retention=retention)
    assert not e.available
    assert "RETENTION_NOT_APPROVED:challenge.secret_and_delivery" in e.reasons
    assert "RETENTION_NOT_APPROVED:contact.email" in e.reasons
    assert "RETENTION_NOT_APPROVED:audit.decision" not in e.reasons


def test_a_licensed_route_needs_an_entitlement_and_never_falls_back_quietly():
    paid = cm.Adapter(cm.CheckMethod.EMAIL_CHALLENGE, "vendor-email", "2", cm.Route.LICENSED)
    assert "LICENSED_ROUTE_NOT_ENTITLED" in elig(adapter=paid).reasons
    assert elig(adapter=paid, licensed_entitled=True).available
    assert (
        "LICENSED_ROUTE_NOT_ENTITLED" not in elig(adapter=ADAPTER).reasons
    )  # an open route is not paid


def test_a_pilot_or_partial_rollout_is_noted_on_an_available_method():
    rec = live().record
    pilot = elig(availability=bm.Availability(True, bm.State.PILOT_GATED, ("METHOD_PILOT",), rec))
    assert pilot.available and pilot.reasons == ("METHOD_PILOT",)


def test_the_same_inputs_give_the_same_answer_with_reasons_in_a_fixed_order():
    retention = {k: v for k, v in approved_retention().items() if k != "audit.decision"}
    a = elig(adapter=None, availability=None, retention=retention)
    assert a == elig(adapter=None, availability=None, retention=retention)
    assert a.reasons == ("NO_ADAPTER", "RETENTION_NOT_APPROVED:audit.decision")


# ------------------------------------------------------------------------------------------------------------------- configuration


def test_adapters_are_data_and_nonsense_is_refused():
    ok = cm.parse_adapters(
        {
            "adapters": {
                "EMAIL_CHALLENGE": {
                    "engine_id": "zid-email",
                    "engine_version": "1",
                    "route": "OPEN",
                }
            }
        }
    )
    assert ok[cm.CheckMethod.EMAIL_CHALLENGE].route is cm.Route.OPEN
    for bad in (
        {"adapters": {"FAX_CHALLENGE": {"engine_id": "x1", "engine_version": "1", "route": "OPEN"}}},
        {"adapters": {"EMAIL_CHALLENGE": {"engine_id": "X", "engine_version": "1", "route": "OPEN"}}},
        {"adapters": {"EMAIL_CHALLENGE": {"engine_id": "zid-email", "engine_version": "", "route": "OPEN"}}},
        {"adapters": {"EMAIL_CHALLENGE": {"engine_id": "zid-email", "engine_version": "1", "route": "FREE"}}},
        {"adapters": {"EMAIL_CHALLENGE": "zid-email"}},
    ):  # fmt: skip
        with pytest.raises(cm.CheckMethodError):
            cm.parse_adapters(bad)


def _all_unset() -> dict[str, Any]:
    return {"classes": {c: {"status": "UNSET"} for c in cm.ALL_DATA_CLASSES}}


def test_retention_lists_every_class_and_approval_needs_a_period_an_approver_and_a_basis():
    assert all(not r.approved for r in cm.parse_retention(_all_unset()).values())
    missing = _all_unset()
    del missing["classes"]["device.observation"]
    incomplete = _all_unset()
    incomplete["classes"]["contact.email"] = {"status": "APPROVED", "retention_days": 30}
    bad_days = _all_unset()
    bad_days["classes"]["contact.email"] = {"status": "SAMPLE", "retention_days": 0}
    for doc, code in (
        (missing, "RETENTION_CLASS_MISSING"),
        (incomplete, "RETENTION_APPROVAL_INCOMPLETE"),
        (bad_days, "RETENTION_INVALID"),
        ({"classes": {"Not A Class": {}}}, "RETENTION_INVALID"),
    ):
        with pytest.raises(cm.CheckMethodError) as e:
            cm.parse_retention(doc)
        assert e.value.code == code
    full = _all_unset()
    full["classes"]["contact.email"] = {
        "status": "APPROVED",
        "retention_days": 30,
        "approved_by": "controller",
        "legal_basis_ref": "L-1",
    }
    assert cm.parse_retention(full)["contact.email"].approved


def test_the_shipped_configuration_offers_nothing_and_approves_no_retention():
    """No adapter ships until it exists, and nobody has approved how long any WF-05 data is kept (DEC-11)."""
    assert config.adapters() == {}
    rules = config.retention()
    assert set(rules) == set(cm.ALL_DATA_CLASSES) and all(not r.approved for r in rules.values())
    raw = yaml.safe_load((config.CONFIG / config.RETENTION_FILE).read_text(encoding="utf-8"))
    assert all(body.get("status") == "UNSET" for body in raw["classes"].values())


def test_with_the_shipped_configuration_every_method_is_unavailable_and_says_why():
    for method in cm.CheckMethod:
        e = cm.eligibility(
            method,
            adapter=config.adapters().get(method),
            availability=None,
            retention=config.retention(),
            licensed_entitled=False,
        )
        assert not e.available and e.reasons[0] == "NO_ADAPTER"
        assert any(r.startswith("RETENTION_NOT_APPROVED:") for r in e.reasons)
