"""When does one business operation become one billable event? (ZID-COM-WF-2026-11 v3.0, section 04.2 and the causal-integrity rule, INV-03, INV-12.) Pure.

The document: "Exactly one authorized user or business operation leads to one chargeability decision, which leads to zero or one canonical meter bill, with
permitted corrections linked separately. Duplicate webhooks, callback retries or provider fanout must never create extra customer charges."

This is the decision table, as code. It is the DEFAULT treatment the document gives, and each line says where a signed contract may differ. It records nothing
and bills nothing: nothing here turns on recording, which is the owners' ruling (`bill_on_commit` stays off). It only answers "would this be chargeable, and why".

- an operation has one id; every retry carries the same id, so any number of attempts yields at most one chargeable event (CAT-12);
- the outcome of an identity check is not what is charged: the unit in the signed SKU is, "regardless of verified or rejected", with the policy disclosed;
- an abandoned session before any provider request is not an attempt, and a failed provider call is costed but not charged;
- a manual reviewer's action and a pass-through lookup are charged only where the SKU and the customer's instruction say so, and a pass-through also needs
  its contract terms (a Zoiko margin is never a supplier fee);
- a re-screen or a renewal is a NEW operation with its own schedule and entitlement, never an implied perpetual one (CAT-16);
- sandbox and test events are never billable and never become live evidence (INV-12);
- a cancelled, reversed or disputed operation is corrected by a linked adjustment through the credit pipeline, never by rewriting the original event.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Scenario(StrEnum):
    IDENTITY_COMPLETED = "IDENTITY_COMPLETED"
    ABANDONED_BEFORE_PROVIDER_REQUEST = "ABANDONED_BEFORE_PROVIDER_REQUEST"
    PROVIDER_FAILURE_OR_INTERNAL_RETRY = "PROVIDER_FAILURE_OR_INTERNAL_RETRY"
    MANUAL_ADJUDICATION = "MANUAL_ADJUDICATION"
    PASS_THROUGH_LOOKUP = "PASS_THROUGH_LOOKUP"
    ONGOING_MONITORING = "ONGOING_MONITORING"
    RESCREEN_OR_RENEWAL = "RESCREEN_OR_RENEWAL"
    CREDENTIAL_VERIFICATION = "CREDENTIAL_VERIFICATION"
    CREDENTIAL_ISSUANCE = "CREDENTIAL_ISSUANCE"
    CANCELLED_REVERSED_OR_DISPUTED = "CANCELLED_REVERSED_OR_DISPUTED"


@dataclass(frozen=True, slots=True)
class SkuPolicy:
    """What the signed SKU and the customer's instruction allow. Everything defaults to the document's cautious treatment."""

    unit_disclosed: bool = False  # the SKU's chargeability policy (verified and rejected both count) was disclosed to the customer
    manual_review_priced: bool = (
        False  # the SKU prices manual review AND the customer instructed it
    )
    pass_through_contracted: bool = (
        False  # contract terms allow charging the lookup, with its evidence
    )
    monitoring_schedule: str | None = (
        None  # the explicit re-screen schedule or period the customer bought
    )
    verification_meter: bool = (
        False  # a verification meter exists and is ratified (separate from issuance)
    )
    issuance_meter: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    billable: bool
    reason: str
    treatment: str = "NONE"  # NONE | CHARGE | ADJUSTMENT | NEW_OPERATION_REQUIRED

    @classmethod
    def no(cls, reason: str, treatment: str = "NONE") -> Decision:
        return cls(False, reason, treatment)

    @classmethod
    def yes(cls, reason: str) -> Decision:
        return cls(True, reason, "CHARGE")


def decide(
    scenario: Scenario,
    *,
    environment: str,
    policy: SkuPolicy,
    new_operation: bool = True,
    entitlement_in_force: bool = False,
) -> Decision:
    """The default chargeability of one operation. `environment` is TEST, SANDBOX or LIVE; only LIVE is ever billable."""
    if environment != "LIVE":
        return Decision.no("NOT_LIVE_NEVER_BILLABLE")
    if scenario is Scenario.ABANDONED_BEFORE_PROVIDER_REQUEST:
        return Decision.no("ABANDONED_BEFORE_ANY_PROVIDER_REQUEST")
    if scenario is Scenario.PROVIDER_FAILURE_OR_INTERNAL_RETRY:
        return Decision.no("A_RETRY_IS_THE_SAME_OPERATION_AND_A_FAILURE_IS_COSTED_NOT_CHARGED")
    if scenario is Scenario.CANCELLED_REVERSED_OR_DISPUTED:
        return Decision.no("CORRECT_BY_LINKED_ADJUSTMENT_NEVER_REWRITE", "ADJUSTMENT")
    if scenario is Scenario.IDENTITY_COMPLETED:
        if not policy.unit_disclosed:
            return Decision.no("CHARGEABILITY_POLICY_NOT_DISCLOSED")
        return Decision.yes("THE_SKU_UNIT_IS_CHARGED_WHATEVER_THE_DECISION")
    if scenario is Scenario.MANUAL_ADJUDICATION:
        if not policy.manual_review_priced:
            return Decision.no("MANUAL_REVIEW_NOT_PRICED_OR_NOT_INSTRUCTED")
        return Decision.yes("MANUAL_REVIEW_PRICED_AND_INSTRUCTED")
    if scenario is Scenario.PASS_THROUGH_LOOKUP:
        if not policy.pass_through_contracted:
            return Decision.no("NO_CONTRACT_TERMS_FOR_A_PASS_THROUGH_CHARGE")
        return Decision.yes("PASS_THROUGH_UNDER_CONTRACT_WITH_EVIDENCE")
    if scenario in (Scenario.ONGOING_MONITORING, Scenario.RESCREEN_OR_RENEWAL):
        if not new_operation:
            return Decision.no("A_RESCREEN_NEEDS_ITS_OWN_OPERATION", "NEW_OPERATION_REQUIRED")
        if not entitlement_in_force or not policy.monitoring_schedule:
            return Decision.no("NO_EXPLICIT_SCHEDULE_AND_ENTITLEMENT")
        return Decision.yes("A_NEW_SCHEDULED_OPERATION_UNDER_AN_ENTITLEMENT")
    if scenario is Scenario.CREDENTIAL_VERIFICATION:
        if not policy.verification_meter:
            return Decision.no("NO_RATIFIED_VERIFICATION_METER")
        return Decision.yes("A_VERIFICATION_IS_METERED_SEPARATELY_FROM_ISSUANCE")
    if scenario is Scenario.CREDENTIAL_ISSUANCE:
        if not policy.issuance_meter:
            return Decision.no("NO_RATIFIED_ISSUANCE_METER")
        return Decision.yes("AN_ISSUANCE_IS_METERED_SEPARATELY_FROM_VERIFICATION")
    return Decision.no(
        "SCENARIO_NOT_CLASSIFIED"
    )  # unreachable for a known scenario; an unknown one is never billed


def chargeable_events(attempts: Iterable[tuple[str, bool]]) -> int:
    """How many billable events a set of attempts produces. `attempts` is (operation_id, chargeable) per attempt: retries, duplicate callbacks and provider
    fanout carry the same operation id, so the answer is the number of DISTINCT operations that were chargeable, never the number of attempts (INV-03)."""
    return len({operation for operation, chargeable in attempts if chargeable})
