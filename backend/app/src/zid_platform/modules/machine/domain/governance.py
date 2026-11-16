"""Agent governance: permissions, spending, usage, approvals and the emergency stop (ZID-AG-WF-002 v1.0, CR-105).

Everything here is a decision made by a rule, never by a model. An agent is denied unless a signed policy names the tool,
the resource, the purpose, the region and an expiry; a hard prohibition beats every allow and every approval; a paid or
metered step runs only after its cost and quota were reserved against EVERY ceiling above it; a change to an agent's
rights is effective only after the independent signatures its kind requires, bound to the exact policy hash; and a
freeze is read at every call, so a cached allow cannot outlive it.

Pure domain, no I/O, no clock (callers pass `now`): standard library only. The numbers in `QuotaLimits` and the
approval slots are the document's proposed pilot values, kept in one place so an owner can change them (AG2-D04).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------- decision codes (section 13)


class Code(StrEnum):
    AGENT_INACTIVE = "AGENT_INACTIVE"
    TENANT_SCOPE_DENIED = "TENANT_SCOPE_DENIED"
    SCOPE_PROHIBITED = "SCOPE_PROHIBITED"  # a platform prohibition: no approval can lift it
    SCOPE_NOT_APPROVED = "SCOPE_NOT_APPROVED"  # not prohibited, but nothing approved it
    REGION_DENIED = "REGION_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SPONSOR_INVALID = "SPONSOR_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    USAGE_LIMIT = "USAGE_LIMIT"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    EMERGENCY_FROZEN = "EMERGENCY_FROZEN"
    ENTITLEMENT_DENIED = "ENTITLEMENT_DENIED"


HTTP_STATUS: dict[str, int] = {
    Code.AGENT_INACTIVE: 403,
    Code.TENANT_SCOPE_DENIED: 403,
    Code.SCOPE_PROHIBITED: 403,
    Code.SCOPE_NOT_APPROVED: 403,
    Code.REGION_DENIED: 403,
    Code.APPROVAL_REQUIRED: 409,
    Code.SPONSOR_INVALID: 403,
    Code.BUDGET_EXCEEDED: 409,
    Code.USAGE_LIMIT: 429,
    Code.POLICY_UNAVAILABLE: 503,
    Code.EMERGENCY_FROZEN: 423,
    Code.ENTITLEMENT_DENIED: 403,
}

# ---------------------------------------------------------------- hard prohibitions (section 03)

# The actions DAARA v1 can never hold, however a tenant or an approver asks. Matched on the tool's catalogued action.
HARD_DENY_ACTIONS = frozenset(
    {
        "access_policy.write",
        "role.grant",
        "role.revoke",
        "credentials.read_secret",
        "keys.export",
        "kyc.final_decision",
        "qualification.attest",
        "external.purchase",
        "funds.transfer",
        "arbitrary_web",
        "shell",
        "tools.register",
        # the registry's own names for the same powers: an agent never governs itself or other agents
        "machine.tool.manage",
        "machine.agent.activate",
        "machine.agent.manage",
        "machine.blueprint.manage",
        "machine.build.manage",
        "machine.governance.manage",
    }
)
HARD_DENY_PREFIXES = (
    "role.",
    "access_policy.",
    "keys.",
    "kyc.",
    "qualification.",
    "external.",
    "funds.",
    "credentials.read_secret",
    "authority.grant",
    "authority.role",
)
# Side effects that move money or cannot be undone are never available to a governed agent in v1.
HARD_DENY_SIDE_EFFECTS = frozenset({"EXTERNAL_TRANSACTION"})


def hard_denied(action: str, side_effect: str = "NONE") -> bool:
    """True for anything on the platform prohibition list. This is checked before every allow and cannot be toggled."""
    a = action.strip().lower()
    return (
        a in HARD_DENY_ACTIONS
        or any(a.startswith(p) for p in HARD_DENY_PREFIXES)
        or side_effect in HARD_DENY_SIDE_EFFECTS
    )


# ---------------------------------------------------------------- canonical hashing


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- grant leases and the policy


def has_wildcard(value: str) -> bool:
    return (
        not value.strip()
        or any(c in value for c in "*?%")
        or value.strip().lower() in {"all", "any"}
    )


MAX_LEASE = timedelta(
    days=90
)  # a grant lease never outlives this; renewal is a new approved change


@dataclass(frozen=True, slots=True)
class GrantLease:
    """One exact permission: this tool, on this kind of resource and selector, for this purpose, in this region, until then."""

    grant_id: str
    tool_id: str
    action: str
    side_effect: str
    resource_kind: str
    resource_selector: str
    purpose: str
    region: str
    valid_until: datetime
    approval_ref: str | None = (
        None  # the approved change that created it; missing means not effective
    )
    data_class: str = "metadata"


def grant_problems(grant: GrantLease, *, now: datetime) -> list[str]:
    """Why a lease may not be requested at all (GOV02-02: exact operation and resource, expiry required, wildcards refused)."""
    problems: list[str] = []
    for label, value in (
        ("tool_id", grant.tool_id),
        ("resource_kind", grant.resource_kind),
        ("resource_selector", grant.resource_selector),
        ("purpose", grant.purpose),
        ("region", grant.region),
    ):
        if has_wildcard(value):
            problems.append(f"WILDCARD_OR_EMPTY:{label}")
    if hard_denied(grant.action, grant.side_effect):
        problems.append("HARD_DENIED")
    if grant.valid_until <= now:
        problems.append("EXPIRY_IN_PAST")
    elif grant.valid_until - now > MAX_LEASE:
        problems.append("EXPIRY_TOO_FAR")
    return problems


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """The conservative pilot defaults of section 06. Every value is configurable and none is a customer promise."""

    concurrent_runs_per_agent: int = 1
    running_agents_per_tenant: int = 2
    runs_per_agent_day: int = 5
    runs_per_tenant_day: int = 20
    records_per_run: int = 100
    records_per_tenant_day: int = 300
    run_seconds: int = 120
    tool_timeout_seconds: int = 15
    proposals_per_run: int = 25
    retries_per_read_step: int = 2
    ai_input_tokens: int = 8000
    ai_output_tokens: int = 2000
    schedules_enabled: bool = False

    def problems(self) -> list[str]:
        out = []
        for name, value in self.as_dict().items():
            if isinstance(value, bool):
                continue
            if not isinstance(value, int) or value < 0:
                out.append(f"NEGATIVE_OR_INVALID:{name}")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "concurrent_runs_per_agent": self.concurrent_runs_per_agent,
            "running_agents_per_tenant": self.running_agents_per_tenant,
            "runs_per_agent_day": self.runs_per_agent_day,
            "runs_per_tenant_day": self.runs_per_tenant_day,
            "records_per_run": self.records_per_run,
            "records_per_tenant_day": self.records_per_tenant_day,
            "run_seconds": self.run_seconds,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "proposals_per_run": self.proposals_per_run,
            "retries_per_read_step": self.retries_per_read_step,
            "ai_input_tokens": self.ai_input_tokens,
            "ai_output_tokens": self.ai_output_tokens,
            "schedules_enabled": self.schedules_enabled,
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> QuotaLimits:
        base = QuotaLimits()
        known = base.as_dict()
        values = {k: data[k] for k in known if k in data}
        return replace(base, **values)


def tightest(a: QuotaLimits, b: QuotaLimits) -> QuotaLimits:
    """The effective limits are the most restrictive of every ceiling (section 06: effective limit = MIN)."""
    merged: dict[str, Any] = {}
    bd = b.as_dict()
    for name, av in a.as_dict().items():
        merged[name] = (av and bd[name]) if isinstance(av, bool) else min(av, bd[name])
    return QuotaLimits(**merged)


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    policy_id: str
    tenant_id: str
    agent_id: str
    version: int
    risk_tier: str
    state: str  # DRAFT, PENDING_APPROVAL, ACTIVE, SUPERSEDED
    region: str
    grants: tuple[GrantLease, ...] = ()
    quotas: QuotaLimits = field(default_factory=QuotaLimits)
    ai_enabled: bool = False
    expires_at: datetime | None = None


def grant_entry(x: GrantLease) -> dict[str, Any]:
    """A lease as the hash sees it: every field that decides an outcome. The approval reference is not part of it, so
    approving a policy does not change the hash that was approved."""
    return {
        "tool_id": x.tool_id,
        "action": x.action,
        "side_effect": x.side_effect,
        "resource_kind": x.resource_kind,
        "resource_selector": x.resource_selector,
        "purpose": x.purpose,
        "region": x.region,
        "valid_until": x.valid_until.isoformat(),
        "data_class": x.data_class,
    }


def policy_content(policy: GovernancePolicy) -> dict[str, Any]:
    """What the hash covers: everything that decides an outcome, nothing that is only bookkeeping (state, ids of drafts)."""
    return {
        "tenant_id": policy.tenant_id,
        "agent_id": policy.agent_id,
        "region": policy.region,
        "risk_tier": policy.risk_tier,
        "ai_enabled": policy.ai_enabled,
        "quotas": policy.quotas.as_dict(),
        "expires_at": policy.expires_at.isoformat() if policy.expires_at else None,
        "grants": sorted(
            (grant_entry(x) for x in policy.grants),
            key=lambda e: (e["tool_id"], e["resource_kind"], e["resource_selector"], e["purpose"]),
        ),
    }


def policy_hash(policy: GovernancePolicy) -> str:
    return digest(policy_content(policy))


def policy_delta(old: GovernancePolicy | None, new: GovernancePolicy) -> dict[str, Any]:
    """The requested change, as a diff of exact leases and limits, so a signer sees what they are approving (GOV02-05)."""
    old_c = policy_content(old) if old else {"grants": [], "quotas": {}}
    new_c = policy_content(new)

    def key(g: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (g["tool_id"], g["resource_kind"], g["resource_selector"], g["purpose"])

    old_g = {key(g): g for g in old_c["grants"]}
    new_g = {key(g): g for g in new_c["grants"]}
    return {
        "added_grants": [new_g[k] for k in sorted(new_g) if k not in old_g],
        "removed_grants": [old_g[k] for k in sorted(old_g) if k not in new_g],
        "changed_grants": [new_g[k] for k in sorted(new_g) if k in old_g and old_g[k] != new_g[k]],
        "quotas": {
            k: {"from": old_c["quotas"].get(k), "to": v}
            for k, v in new_c["quotas"].items()
            if old_c["quotas"].get(k) != v
        },
        "ai_enabled": {"from": old_c.get("ai_enabled", False), "to": new_c["ai_enabled"]},
        "region": {"from": old_c.get("region"), "to": new_c["region"]},
    }


def delta_widens(delta: Mapping[str, Any]) -> bool:
    """True when the change gives the agent more than it had (a new or longer lease, higher limits, AI on, schedules on)."""
    if delta["added_grants"] or delta["changed_grants"]:
        return True
    if delta["ai_enabled"]["to"] and not delta["ai_enabled"]["from"]:
        return True
    for change in delta["quotas"].values():
        old, new = change["from"], change["to"]
        if isinstance(new, bool):
            if new and not old:
                return True
        elif old is None or (isinstance(new, int) and new > old):
            return True
    return False


# ---------------------------------------------------------------- company baseline (section 07)


@dataclass(frozen=True, slots=True)
class CompanyBaseline:
    """The tenant's own ceiling. A company may reduce what the platform allows, never raise it. Missing means no agent may act."""

    allowed_tools: frozenset[str] = frozenset()
    allowed_regions: frozenset[str] = frozenset()
    ai_providers: frozenset[str] = frozenset()  # empty: no AI provider is approved
    quotas: QuotaLimits = field(default_factory=QuotaLimits)
    schedules_allowed: bool = False
    frozen: bool = False  # the company-wide emergency freeze


# ---------------------------------------------------------------- the emergency stop (section 15-16, GOV05)


@dataclass(frozen=True, slots=True)
class Freeze:
    case_id: str
    scope: str  # "AGENT" or "TENANT"
    agent_id: str | None
    generation: int
    initiator: str
    reason: str
    issued_at: datetime
    cleared_by: tuple[str, ...] = ()
    restored_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.restored_at is None


def freeze_for(freezes: Iterable[Freeze], agent_id: str) -> Freeze | None:
    """The newest active freeze that covers this agent (its own, or the whole tenant's)."""
    covering = [f for f in freezes if f.active and (f.scope == "TENANT" or f.agent_id == agent_id)]
    return max(covering, key=lambda f: f.generation) if covering else None


def restore_blockers(
    freeze: Freeze,
    *,
    approvers: Sequence[str],
    approver_slots: Mapping[str, frozenset[str]],
    health_checks_fresh: bool,
) -> list[str]:
    """Restore is never immediate (T25): two independent qualified people who did not freeze, and fresh credential,
    policy and evidence checks. Pausing needs none of this (T24)."""
    problems: list[str] = []
    distinct = list(dict.fromkeys(approvers))
    if freeze.initiator in distinct:
        problems.append("RESTORE_NOT_INDEPENDENT")
    held = [approver_slots.get(a, frozenset()) for a in distinct]
    if not any("SECURITY" in s for s in held):
        problems.append("RESTORE_NEEDS_SECURITY")
    if not any("AUTHORITY" in s for s in held):
        problems.append("RESTORE_NEEDS_AUTHORITY")
    if len(distinct) < 2:
        problems.append("RESTORE_NEEDS_TWO_PEOPLE")
    if not health_checks_fresh:
        problems.append("RESTORE_HEALTH_CHECK_STALE")
    return problems


# ---------------------------------------------------------------- usage quotas (section 06)


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Counters read inside the same transaction as the reservation, so the check and the use cannot race."""

    active_runs_agent: int = 0
    running_agents_tenant: int = 0
    runs_agent_today: int = 0
    runs_tenant_today: int = 0
    records_tenant_today: int = 0


@dataclass(frozen=True, slots=True)
class QuotaVerdict:
    quota: str
    window: str  # CONCURRENT, UTC_DAY, RUN
    limit: int
    used: int
    retry_after: datetime | None
    waiting_capacity: bool = False


def next_utc_midnight(now: datetime) -> datetime:
    day = now.astimezone(UTC).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def next_utc_month(now: datetime) -> datetime:
    n = now.astimezone(UTC)
    year, month = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def start_check(
    limits: QuotaLimits, usage: UsageSnapshot, *, now: datetime, new_agent_in_tenant: bool = True
) -> QuotaVerdict | None:
    """May a new run start? None means yes. Concurrency never retries in a loop: the caller gets WAITING_CAPACITY (T34)."""
    if usage.active_runs_agent >= limits.concurrent_runs_per_agent:
        return QuotaVerdict(
            "concurrent_runs_per_agent",
            "CONCURRENT",
            limits.concurrent_runs_per_agent,
            usage.active_runs_agent,
            None,
            waiting_capacity=True,
        )
    if new_agent_in_tenant and usage.running_agents_tenant >= limits.running_agents_per_tenant:
        return QuotaVerdict(
            "running_agents_per_tenant",
            "CONCURRENT",
            limits.running_agents_per_tenant,
            usage.running_agents_tenant,
            None,
            waiting_capacity=True,
        )
    if usage.runs_agent_today >= limits.runs_per_agent_day:
        return QuotaVerdict(
            "runs_per_agent_day",
            "UTC_DAY",
            limits.runs_per_agent_day,
            usage.runs_agent_today,
            next_utc_midnight(now),
        )
    if usage.runs_tenant_today >= limits.runs_per_tenant_day:
        return QuotaVerdict(
            "runs_per_tenant_day",
            "UTC_DAY",
            limits.runs_per_tenant_day,
            usage.runs_tenant_today,
            next_utc_midnight(now),
        )
    return None


def records_check(
    limits: QuotaLimits, usage: UsageSnapshot, *, requested: int, now: datetime
) -> QuotaVerdict | None:
    """Records per run and per tenant day. An overflow is a refusal, never a silent partial success (section 06)."""
    if requested > limits.records_per_run:
        return QuotaVerdict("records_per_run", "RUN", limits.records_per_run, requested, None)
    if usage.records_tenant_today + requested > limits.records_per_tenant_day:
        return QuotaVerdict(
            "records_per_tenant_day",
            "UTC_DAY",
            limits.records_per_tenant_day,
            usage.records_tenant_today,
            next_utc_midnight(now),
        )
    return None


def run_deadline(limits: QuotaLimits, started: datetime) -> datetime:
    return started + timedelta(seconds=limits.run_seconds)


def run_timed_out(limits: QuotaLimits, started: datetime, now: datetime) -> bool:
    return now >= run_deadline(limits, started)


# ---------------------------------------------------------------- money (section 05)

APPROVED_CURRENCIES: Mapping[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
}  # ISO 4217 minor units; the owner extends it


MAX_AMOUNT = Decimal(
    1000000000000
)  # one trillion in any approved currency: far above any cap the owners will set


class MoneyError(ValueError):
    pass


def money(
    value: str | int | Decimal, currency: str, *, approved: Mapping[str, int] = APPROVED_CURRENCIES
) -> Decimal:
    """Exact money. Floats are refused, unsupported currencies are refused, and more decimals than the currency has are
    refused rather than rounded (a silent round is a financial posting nobody approved)."""
    if isinstance(value, (float, bool)):
        raise MoneyError("MONEY_FLOAT_REFUSED")
    if currency not in approved:
        raise MoneyError("CURRENCY_NOT_APPROVED")
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise MoneyError("MONEY_INVALID") from e
    if not d.is_finite() or d < 0:
        raise MoneyError("MONEY_NEGATIVE_OR_INVALID")
    if d > MAX_AMOUNT:
        raise MoneyError(
            "MONEY_TOO_LARGE"
        )  # a cap or a cost beyond any real budget is a typo or an attack, and never fits a column
    if -d.as_tuple().exponent > approved[currency]:  # type: ignore[operator]
        raise MoneyError("MONEY_TOO_PRECISE")
    return d


class SpendClass(StrEnum):
    EXTERNAL_TRANSACTION = "EXTERNAL_TRANSACTION"  # permanently $0 and forbidden in v1
    AI_INFERENCE = "AI_INFERENCE"  # $0 until a model opt-in and finance approval
    PAID_VENDOR_CHECK = "PAID_VENDOR_CHECK"  # $0, no vendor connector in v1
    INTERNAL = "INTERNAL"  # metered by quota, not by money


@dataclass(frozen=True, slots=True)
class Ceiling:
    """One cap above a call: company, workspace, agent or the single call. `used` is committed plus reserved."""

    name: str
    scope: str
    currency: str
    window: str  # DAY, MONTH, CALL
    hard_cap: Decimal
    committed: Decimal = Decimal(0)
    reserved: Decimal = Decimal(0)
    warn_pct: tuple[int, int] = (70, 90)

    @property
    def available(self) -> Decimal:
        return max(Decimal(0), self.hard_cap - self.committed - self.reserved)


@dataclass(frozen=True, slots=True)
class ReserveVerdict:
    ok: bool
    ceiling: str | None = None
    scope: str | None = None
    available: Decimal | None = None
    reset_at: datetime | None = None
    reason: str = ""


def reset_at(window: str, now: datetime) -> datetime | None:
    if window == "DAY":
        return next_utc_midnight(now)
    if window == "MONTH":
        return next_utc_month(now)
    return None


def reserve_check(
    spend_class: SpendClass,
    amount: Decimal,
    currency: str,
    ceilings: Sequence[Ceiling],
    *,
    ai_enabled: bool,
    now: datetime,
) -> ReserveVerdict:
    """May this much be reserved? The answer is the MIN over every applicable ceiling (hierarchical, section 05). With no
    verified ceiling for a paid class the answer is no: a charge can never proceed against an unreserved budget."""
    if spend_class in (SpendClass.EXTERNAL_TRANSACTION, SpendClass.PAID_VENDOR_CHECK):
        return ReserveVerdict(
            False,
            reason="SPEND_CLASS_FORBIDDEN_V1",
            ceiling=spend_class.value,
            available=Decimal(0),
        )
    if spend_class is SpendClass.AI_INFERENCE and not ai_enabled:
        return ReserveVerdict(
            False, reason="AI_DISABLED", ceiling="ai_inference_disabled", available=Decimal(0)
        )
    if amount == 0:
        return ReserveVerdict(True)
    if not ceilings:
        return ReserveVerdict(
            False, reason="NO_VERIFIED_CEILING", ceiling="no_ceiling", available=Decimal(0)
        )
    tightest_ceiling: Ceiling | None = None
    for c in ceilings:
        if c.currency != currency:
            return ReserveVerdict(False, reason="CURRENCY_MISMATCH", ceiling=c.name, scope=c.scope)
        if tightest_ceiling is None or c.available < tightest_ceiling.available:
            tightest_ceiling = c
    assert tightest_ceiling is not None
    if amount > tightest_ceiling.available:
        return ReserveVerdict(
            False,
            ceiling=tightest_ceiling.name,
            scope=tightest_ceiling.scope,
            available=tightest_ceiling.available,
            reset_at=reset_at(tightest_ceiling.window, now),
            reason="OVER_CEILING",
        )
    return ReserveVerdict(True, available=tightest_ceiling.available)


def upper_bound(
    unit_price: Decimal,
    max_units: int,
    *,
    margin_pct: int = 10,
    currency: str = "USD",
    approved: Mapping[str, int] = APPROVED_CURRENCIES,
) -> Decimal:
    """The most a call can cost: the largest metered volume at the approved price plus a safety margin, rounded UP to the
    currency's smallest unit so a reservation never under-covers."""
    places = approved[currency]
    raw = unit_price * max_units * (Decimal(100 + margin_pct) / Decimal(100))
    quantum = Decimal(1).scaleb(-places)
    return raw.quantize(quantum, rounding="ROUND_CEILING")


@dataclass(frozen=True, slots=True)
class Settlement:
    committed: Decimal
    released: Decimal
    overrun: Decimal  # actual cost above the reservation: never absorbed silently, recorded as a controlled adjustment


def settle(reserved: Decimal, actual: Decimal) -> Settlement:
    """Settle exactly once. Unused reservation is released; a cost above the reservation commits only what was reserved and
    reports the overrun so a person reconciles it (a late invoice never rewrites committed history, T31)."""
    if actual <= reserved:
        return Settlement(actual, reserved - actual, Decimal(0))
    return Settlement(reserved, Decimal(0), actual - reserved)


def usage_level(used: Decimal, cap: Decimal, warn: tuple[int, int] = (70, 90)) -> str:
    """A text level, never colour alone: OK, WARN_70, WARN_90 or EXHAUSTED. A zero cap with nothing used is simply OFF."""
    if cap == 0:
        return "OFF" if used == 0 else "EXHAUSTED"
    pct = used * 100 / cap
    if pct >= 100:
        return "EXHAUSTED"
    if pct >= warn[1]:
        return "WARN_90"
    if pct >= warn[0]:
        return "WARN_70"
    return "OK"


# ---------------------------------------------------------------- approvals and separation of duties (section 04)


class ChangeKind(StrEnum):
    PUBLISH = "PUBLISH"
    SCOPE_EXTENSION = "SCOPE_EXTENSION"
    AI_ENABLE = "AI_ENABLE"
    BUDGET_INCREASE = "BUDGET_INCREASE"
    QUOTA_INCREASE = "QUOTA_INCREASE"
    RESTORE = "RESTORE"
    PRIVILEGE_MUTATION = (
        "PRIVILEGE_MUTATION"  # forbidden in v1: it can be requested, it can never be approved
    )


SLOTS = (
    "SECURITY",
    "AUTHORITY",
    "PRIVACY",
    "FINANCE",
)  # a named governance capability, not a new console role (AG2-D02)
MAX_PENDING = timedelta(hours=24)


def required_slots(
    kind: ChangeKind,
    *,
    data_class_changed: bool = False,
    risk_increases: bool = False,
    above_baseline: bool = False,
) -> tuple[str, ...]:
    """Which independent signers a change needs (the approval matrix). An empty tuple means nobody may approve it."""
    if kind is ChangeKind.PRIVILEGE_MUTATION:
        return ()
    if kind in (ChangeKind.PUBLISH, ChangeKind.RESTORE):
        return ("SECURITY", "AUTHORITY")
    if kind is ChangeKind.SCOPE_EXTENSION:
        return ("SECURITY", "AUTHORITY") + (("PRIVACY",) if data_class_changed else ())
    if kind is ChangeKind.AI_ENABLE:
        return ("SECURITY", "PRIVACY", "FINANCE")
    # budget and quota increases: a finance delegate; above the baseline, or if risk rises, a second person from Security
    return ("FINANCE", "SECURITY") if (above_baseline or risk_increases) else ("FINANCE",)


@dataclass(frozen=True, slots=True)
class Signature:
    slot: str
    signer: str
    signed_at: datetime
    policy_hash: str


class RequestState(StrEnum):
    PENDING = "PENDING"
    CLARIFICATION = "CLARIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    APPLIED = "APPLIED"


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    request_id: str
    tenant_id: str
    agent_id: str
    kind: ChangeKind
    requested_by: str
    authors: tuple[
        str, ...
    ]  # everyone who wrote, built or deployed the change: none of them may be the only signer
    policy_hash: str  # the exact policy the request would create
    base_policy_hash: str | None  # the policy it changes
    delta_hash: str
    required: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    state: RequestState = RequestState.PENDING
    signatures: tuple[Signature, ...] = ()


def open_slots(req: ChangeRequest) -> tuple[str, ...]:
    filled = {s.slot for s in req.signatures}
    return tuple(s for s in req.required if s not in filled)


def sign_blockers(
    req: ChangeRequest,
    *,
    signer: str,
    signer_slots_now: frozenset[str],
    signer_active: bool,
    presented_hash: str,
    step_up_ok: bool,
    evidence_available: bool,
    now: datetime,
) -> list[str]:
    """Why this person may not sign now. Every input is read at decision time on the server, never from the page (GOV04-03):
    the signer's slots and standing now, not when the screen rendered."""
    problems: list[str] = []
    if not req.required:
        return ["REQUEST_FORBIDDEN"]
    if req.state is not RequestState.PENDING:
        problems.append("REQUEST_NOT_OPEN")
    if now >= req.expires_at:
        problems.append("REQUEST_EXPIRED")
    if presented_hash != req.policy_hash:
        problems.append("SCOPE_HASH_MISMATCH")
    if signer == req.requested_by or signer in req.authors:
        problems.append("SEPARATION_OF_DUTIES")
    if any(s.signer == signer for s in req.signatures):
        problems.append("ALREADY_SIGNED")
    if not signer_active:
        problems.append("SIGNER_NOT_ACTIVE")
    if not (set(open_slots(req)) & signer_slots_now):
        problems.append("SIGNER_NOT_QUALIFIED")
    if not step_up_ok:
        problems.append("STEP_UP_REQUIRED")
    if not evidence_available:
        problems.append("EVIDENCE_UNAVAILABLE")
    return list(dict.fromkeys(problems))


def slot_for(req: ChangeRequest, signer_slots_now: frozenset[str]) -> str | None:
    """The slot a signature fills: the first still-open slot the signer holds. One person fills one slot (four eyes)."""
    for slot in open_slots(req):
        if slot in signer_slots_now:
            return slot
    return None


def add_signature(req: ChangeRequest, sig: Signature) -> ChangeRequest:
    signed = replace(req, signatures=(*req.signatures, sig))
    return replace(signed, state=RequestState.APPROVED) if not open_slots(signed) else signed


def is_effective_approval(req: ChangeRequest, *, current_hash: str, now: datetime) -> bool:
    """An approval authorizes only the exact policy it was signed for, and only while it is fresh (T10, T21, T22)."""
    return (
        req.state in (RequestState.APPROVED, RequestState.APPLIED)
        and bool(req.required)
        and not open_slots(req)
        and all(s.policy_hash == req.policy_hash for s in req.signatures)
        and len({s.signer for s in req.signatures}) == len(req.required)
        and req.policy_hash == current_hash
        and (req.state is RequestState.APPLIED or now < req.expires_at)
    )


INVALIDATING_EVENTS = frozenset(
    {
        "SPONSOR_DEPARTED",
        "CREDENTIAL_ROTATED",
        "CONNECTOR_REPLACED",
        "POLICY_MODIFIED",
        "RISK_ELEVATED",
        "APPROVER_ROLE_REVOKED",
    }
)


def invalidated_by(req: ChangeRequest, event: str) -> ChangeRequest:
    """Sponsor departure, credential rotation, a replaced connector or any policy edit voids an in-flight approval."""
    if event in INVALIDATING_EVENTS and req.state in (
        RequestState.PENDING,
        RequestState.APPROVED,
        RequestState.CLARIFICATION,
    ):
        return replace(req, state=RequestState.INVALIDATED)
    return req


# ---------------------------------------------------------------- the governed call (section 13)


@dataclass(frozen=True, slots=True)
class GovernedCall:
    agent_id: str
    session_tenant_id: str
    tool_id: str
    action: str
    side_effect: str
    resource_kind: str
    resource_id: str
    purpose: str | None
    region: str
    data_class: str = "metadata"
    spend_class: SpendClass = SpendClass.INTERNAL
    cost: Decimal = Decimal(0)
    currency: str = "USD"
    records: int = 0


@dataclass(frozen=True, slots=True)
class GovernanceContext:
    """Everything the gates read, loaded by the caller in one transaction. A field that could not be loaded is None, and
    a None that matters is POLICY_UNAVAILABLE: there is no fall back to allow."""

    agent_tenant_id: str
    agent_active: bool
    sponsor_active: bool
    policy: GovernancePolicy | None
    baseline: CompanyBaseline | None
    approval: ChangeRequest | None
    freezes: Sequence[Freeze] | None
    entitled: bool | None
    usage: UsageSnapshot | None = None
    ceilings: Sequence[Ceiling] = ()
    require_approval: bool = True


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    allowed: bool
    code: Code | None = None
    reason: str = ""
    policy_hash: str | None = None
    quota: QuotaVerdict | None = None
    budget: ReserveVerdict | None = None
    trace: tuple[tuple[str, str], ...] = ()

    @property
    def http_status(self) -> int:
        return 200 if self.allowed else HTTP_STATUS[self.code]  # type: ignore[index]


def _selector_covers(selector: str, resource_id: str) -> bool:
    """An exact selector, or a prefix ending in ':' that names a class (never a bare wildcard, which a lease cannot hold)."""
    return selector == resource_id or (selector.endswith(":") and resource_id.startswith(selector))


def evaluate(call: GovernedCall, ctx: GovernanceContext, *, now: datetime) -> GovernanceDecision:
    """Run every gate in order; the first that fails is the answer. Nothing a model wrote is read here. The trace records
    each gate passed so the simulator and the audit can explain a decision (GOV02-03)."""
    trace: list[tuple[str, str]] = []

    def deny(code: Code, reason: str, **extra: Any) -> GovernanceDecision:
        trace.append((str(code), "DENY"))
        return GovernanceDecision(
            False, code, reason, extra.pop("policy_hash", None), trace=tuple(trace), **extra
        )

    if ctx.policy is None or ctx.baseline is None or ctx.freezes is None or ctx.entitled is None:
        return deny(
            Code.POLICY_UNAVAILABLE, "a required policy source is missing or could not be read"
        )
    pol = ctx.policy
    phash = policy_hash(pol)

    live = freeze_for(ctx.freezes, call.agent_id)
    if live is not None or ctx.baseline.frozen:
        return deny(
            Code.EMERGENCY_FROZEN, "execution is frozen by an emergency control", policy_hash=phash
        )
    trace.append(("freeze", "PASS"))

    if call.session_tenant_id != ctx.agent_tenant_id or pol.tenant_id != ctx.agent_tenant_id:
        return deny(Code.TENANT_SCOPE_DENIED, "tenant mismatch", policy_hash=phash)
    trace.append(("tenant", "PASS"))

    if not ctx.agent_active:
        return deny(Code.AGENT_INACTIVE, "agent is not active", policy_hash=phash)
    if not ctx.sponsor_active:
        return deny(Code.SPONSOR_INVALID, "no active accountable sponsor", policy_hash=phash)
    if pol.expires_at is not None and pol.expires_at <= now:
        return deny(Code.SCOPE_NOT_APPROVED, "the policy has expired", policy_hash=phash)
    if pol.state != "ACTIVE":
        return deny(Code.APPROVAL_REQUIRED, "the policy is not active", policy_hash=phash)
    trace.append(("agent", "PASS"))

    if hard_denied(call.action, call.side_effect):
        return deny(Code.SCOPE_PROHIBITED, "platform prohibition", policy_hash=phash)
    trace.append(("hard_deny", "PASS"))

    leases = [g for g in pol.grants if g.tool_id == call.tool_id]
    if not leases:
        return deny(Code.SCOPE_NOT_APPROVED, "no lease names this tool", policy_hash=phash)
    live_leases = [g for g in leases if g.valid_until > now and g.approval_ref]
    if not live_leases:
        return deny(
            Code.SCOPE_NOT_APPROVED,
            "the lease is expired or has no approval reference",
            policy_hash=phash,
        )
    in_region = [g for g in live_leases if g.region == call.region]
    if call.region not in ctx.baseline.allowed_regions or not in_region:
        return deny(Code.REGION_DENIED, "region is not allowed", policy_hash=phash)
    match = [
        g
        for g in in_region
        if g.resource_kind == call.resource_kind
        and _selector_covers(g.resource_selector, call.resource_id)
        and call.purpose is not None
        and g.purpose == call.purpose
        and g.data_class == call.data_class
    ]
    if not match:
        return deny(
            Code.SCOPE_NOT_APPROVED, "the request is outside every lease", policy_hash=phash
        )
    trace.append(("lease", "PASS"))

    if call.tool_id not in ctx.baseline.allowed_tools:
        return deny(
            Code.SCOPE_NOT_APPROVED,
            "the company baseline does not allow this tool",
            policy_hash=phash,
        )
    trace.append(("baseline", "PASS"))

    if not ctx.entitled:
        return deny(
            Code.ENTITLEMENT_DENIED, "the plan does not include agent governance", policy_hash=phash
        )

    if ctx.require_approval and (
        ctx.approval is None or not is_effective_approval(ctx.approval, current_hash=phash, now=now)
    ):
        return deny(
            Code.APPROVAL_REQUIRED,
            "no approval is bound to the current policy hash",
            policy_hash=phash,
        )
    trace.append(("approval", "PASS"))

    limits = tightest(pol.quotas, ctx.baseline.quotas)
    if ctx.usage is not None and call.records:
        verdict = records_check(limits, ctx.usage, requested=call.records, now=now)
        if verdict is not None:
            return deny(
                Code.USAGE_LIMIT, f"quota {verdict.quota}", policy_hash=phash, quota=verdict
            )
    trace.append(("quota", "PASS"))

    spend = reserve_check(
        call.spend_class,
        call.cost,
        call.currency,
        ctx.ceilings,
        ai_enabled=pol.ai_enabled and bool(ctx.baseline.ai_providers),
        now=now,
    )
    if not spend.ok:
        code = (
            Code.SCOPE_PROHIBITED
            if spend.reason == "SPEND_CLASS_FORBIDDEN_V1"
            else Code.BUDGET_EXCEEDED
        )
        return deny(code, spend.reason, policy_hash=phash, budget=spend)
    trace.append(("budget", "PASS"))
    trace.append(("allow", "ALLOW"))
    return GovernanceDecision(True, None, "allowed", phash, trace=tuple(trace))


# ---------------------------------------------------------------- starting a run (section 06, 13)


def evaluate_run_start(
    ctx: GovernanceContext,
    usage: UsageSnapshot,
    *,
    agent_id: str,
    now: datetime,
    agent_already_running: bool,
) -> GovernanceDecision:
    """A run is governed before its first step: not frozen, agent and sponsor live, an active policy whose exact hash carries
    an effective approval, and room under the run caps. No lease is needed yet: leases are checked at every tool call."""
    if ctx.policy is None or ctx.baseline is None or ctx.freezes is None:
        return GovernanceDecision(
            False, Code.POLICY_UNAVAILABLE, "a required policy source is missing"
        )
    phash = policy_hash(ctx.policy)

    def deny(code: Code, reason: str, **extra: Any) -> GovernanceDecision:
        return GovernanceDecision(False, code, reason, phash, **extra)

    if freeze_for(ctx.freezes, agent_id) is not None or ctx.baseline.frozen:
        return deny(Code.EMERGENCY_FROZEN, "execution is frozen by an emergency control")
    if not ctx.agent_active:
        return deny(Code.AGENT_INACTIVE, "agent is not active")
    if not ctx.sponsor_active:
        return deny(Code.SPONSOR_INVALID, "no active accountable sponsor")
    if ctx.policy.state != "ACTIVE" or (
        ctx.policy.expires_at is not None and ctx.policy.expires_at <= now
    ):
        return deny(Code.APPROVAL_REQUIRED, "no active policy")
    if ctx.approval is None or not is_effective_approval(ctx.approval, current_hash=phash, now=now):
        return deny(Code.APPROVAL_REQUIRED, "no approval is bound to the current policy hash")
    limits = tightest(ctx.policy.quotas, ctx.baseline.quotas)
    verdict = start_check(limits, usage, now=now, new_agent_in_tenant=not agent_already_running)
    if verdict is not None:
        return deny(Code.USAGE_LIMIT, f"quota {verdict.quota}", quota=verdict)
    return GovernanceDecision(True, None, "allowed", phash)


# ---------------------------------------------------------------- monitoring (section 15)


def telemetry_state(last_ingest: datetime | None, now: datetime, *, max_age: timedelta) -> str:
    """CURRENT, STALE or UNKNOWN. A silent feed is never "healthy": with no ingest at all the answer is UNKNOWN (T39)."""
    if last_ingest is None:
        return "UNKNOWN"
    return "CURRENT" if now - last_ingest <= max_age else "STALE"


ALERT_SEVERITY = {
    "INCIDENT_OPEN": "P0",
    "SPONSOR_INVALID": "P1",
    "POLICY_DRIFT": "P1",
    "PROHIBITED_ATTEMPT": "P1",
    "AUDIT_GAP": "P1",
    "CAP_NEAR_LIMIT": "P2",
    "GRANT_EXPIRING": "P2",
    "APPROVAL_BACKLOG": "P2",
    "TELEMETRY_UNKNOWN": "P2",
}


def expiring_leases(
    policy: GovernancePolicy | None, now: datetime, *, within: timedelta = timedelta(days=7)
) -> list[str]:
    if policy is None:
        return []
    return sorted(x.grant_id for x in policy.grants if now < x.valid_until <= now + within)


def redact_event(event: Mapping[str, Any], *, detail: bool) -> dict[str, Any]:
    """The export of a role that may not see detail loses resource, actor, tool, cost and correlation (T40). The digest and
    ordering stay, so the stream is still checkable."""
    keep = {
        "event_id",
        "seq",
        "agent_principal_id",
        "action",
        "outcome",
        "code",
        "occurred_at",
        "digest",
        "prev_digest",
    }
    if detail:
        keep |= {
            "run_id",
            "actor",
            "actor_type",
            "tool_id",
            "resource",
            "region",
            "policy_hash",
            "correlation_id",
            "cost_receipt",
        }
    return {k: v for k, v in event.items() if k in keep}
