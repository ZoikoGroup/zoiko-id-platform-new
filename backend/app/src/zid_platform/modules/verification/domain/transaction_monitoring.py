"""Transaction monitoring rules as data (ZID-GAP-001 v2.0 DTV-32; CR-101). Pure domain.

Transaction monitoring is a separately regulated product line, and the thresholds and typologies that matter are the compliance officer's. This module is the
integration shell: rules are versioned DATA, evaluation is deterministic, and the output is an alert for a person, never a block.

- a rule is one of a fixed few kinds (`amount_at_least`, `count_in_window`, `sum_in_window`, `just_below`, `new_counterparty_large`). A definition cannot hold
  code, an expression, or a kind this module does not know: it refuses the whole file;
- an alert names the rule, the person and the transactions that triggered it (ids, never amounts or counterparties), with the severity the rule's author chose.
  Its recommendation is always REVIEW: nothing here holds, delays or declines a transaction;
- money is a decimal string with a currency; a rule only compares like currencies (no conversion is invented). A transaction in another currency is not counted
  by a rule written for one, and the result says how many were skipped so a gap is visible;
- evaluation is a pure function of the rules and the transactions given, in any order, so the same input always gives the same alerts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

LINE = "transaction_monitoring"
KINDS = frozenset(
    {"amount_at_least", "count_in_window", "sum_in_window", "just_below", "new_counterparty_large"}
)
SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH"})
ID = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
CURRENCY = re.compile(r"^[A-Z]{3,10}$")
MAX_RULES = 100
MAX_EVIDENCE = 20


class MonitoringError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    kind: str
    severity: str
    currency: str
    amount: Decimal = Decimal(0)
    count: int = 0
    days: int = 0
    within_pct: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class RuleSet:
    version: str
    status: str  # SAMPLE | APPROVED
    rules: tuple[Rule, ...]


def _dec(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        raise MonitoringError("RULE_INVALID", f"{name} must be a decimal string")
    try:
        d = Decimal(value)
    except InvalidOperation as e:
        raise MonitoringError("RULE_INVALID", name) from e
    if not d.is_finite() or d < 0:
        raise MonitoringError("RULE_INVALID", name)
    return d


def _int(value: Any, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise MonitoringError("RULE_INVALID", name)
    return value


def parse_rules(doc: Mapping[str, Any]) -> RuleSet:
    """A rule set from configuration. Anything not recognised refuses the whole set: a half-read rule set would quietly watch less than intended."""
    status = str(doc.get("status", "SAMPLE"))
    if status not in {"SAMPLE", "APPROVED"}:
        raise MonitoringError("RULESET_INVALID", "status")
    if status == "APPROVED" and not (doc.get("approved_by") and doc.get("legal_basis_ref")):
        raise MonitoringError(
            "RULESET_APPROVAL_INCOMPLETE", "APPROVED needs approved_by and legal_basis_ref"
        )
    raw = doc.get("rules") or []
    if len(raw) > MAX_RULES:
        raise MonitoringError("RULESET_INVALID", "too many rules")
    rules: list[Rule] = []
    seen: set[str] = set()
    known = {"id", "kind", "severity", "currency", "amount", "count", "days", "within_pct"}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) - known:
            raise MonitoringError("RULE_INVALID", "unknown keys: only data, never code")
        rid, kind = str(item.get("id", "")), str(item.get("kind", ""))
        if (
            not ID.match(rid)
            or rid in seen
            or kind not in KINDS
            or item.get("severity") not in SEVERITIES
        ):
            raise MonitoringError("RULE_INVALID", rid or "id")
        seen.add(rid)
        currency = str(item.get("currency", ""))
        if not CURRENCY.match(currency):
            raise MonitoringError("RULE_INVALID", f"{rid}: currency")
        amount = _dec(item["amount"], f"{rid}: amount") if "amount" in item else Decimal(0)
        count = _int(item["count"], f"{rid}: count", 1, 100000) if "count" in item else 0
        days = _int(item["days"], f"{rid}: days", 1, 365) if "days" in item else 0
        pct = _dec(item["within_pct"], f"{rid}: within_pct") if "within_pct" in item else Decimal(0)
        need_amount = kind in {
            "amount_at_least",
            "sum_in_window",
            "just_below",
            "new_counterparty_large",
        }
        need_count = kind in {"count_in_window", "just_below"}
        need_days = kind in {
            "count_in_window",
            "sum_in_window",
            "just_below",
            "new_counterparty_large",
        }
        if (
            (need_amount and amount <= 0)
            or (need_count and not count)
            or (need_days and not days)
            or (kind == "just_below" and not 0 < pct < 100)
        ):
            raise MonitoringError("RULE_INVALID", f"{rid}: missing a value its kind needs")
        rules.append(Rule(rid, kind, str(item["severity"]), currency, amount, count, days, pct))
    return RuleSet(str(doc.get("version", "")), status, tuple(rules))


@dataclass(frozen=True, slots=True)
class Transaction:
    tx_id: str
    subject: str
    amount: Decimal
    currency: str
    at: datetime
    counterparty: str  # an opaque, already-hashed reference; this module never sees a name or an account number


@dataclass(frozen=True, slots=True)
class Alert:
    rule_id: str
    subject: str
    severity: str
    evidence: tuple[str, ...]  # transaction ids, never amounts or counterparties
    recommendation: str = "REVIEW"
    notes: tuple[str, ...] = (
        "An alert is a reason to look. Nothing was held, delayed or declined.",
    )


@dataclass(frozen=True, slots=True)
class Result:
    alerts: tuple[Alert, ...]
    skipped_other_currency: Mapping[str, int] = field(
        default_factory=dict
    )  # rule id -> transactions a rule could not compare


def evaluate(rules: RuleSet, transactions: Iterable[Transaction], *, now: datetime) -> Result:
    """Deterministic: the same rules and transactions give the same alerts in the same order, whatever order the transactions arrive in."""
    by_subject: dict[str, list[Transaction]] = defaultdict(list)
    for t in sorted(transactions, key=lambda t: (t.at, t.tx_id)):
        by_subject[t.subject].append(t)
    alerts: list[Alert] = []
    skipped: dict[str, int] = {}
    for rule in rules.rules:
        for subject in sorted(by_subject):
            txs = by_subject[subject]
            same = [t for t in txs if t.currency == rule.currency]
            other = len(txs) - len(same)
            if other:
                skipped[rule.rule_id] = skipped.get(rule.rule_id, 0) + other
            hit = _fire(rule, same, now)
            if hit:
                alerts.append(
                    Alert(rule.rule_id, subject, rule.severity, tuple(hit[:MAX_EVIDENCE]))
                )
    return Result(tuple(alerts), skipped)


def _window(rule: Rule, txs: Sequence[Transaction], now: datetime) -> list[Transaction]:
    start = now - timedelta(days=rule.days)
    return [t for t in txs if start <= t.at <= now]


def _fire(rule: Rule, txs: Sequence[Transaction], now: datetime) -> list[str]:
    """The transaction ids that make a rule fire for one person, or an empty list."""
    if rule.kind == "amount_at_least":
        return [t.tx_id for t in txs if t.amount >= rule.amount]
    inside = _window(rule, txs, now)
    if rule.kind == "count_in_window":
        return [t.tx_id for t in inside] if len(inside) >= rule.count else []
    if rule.kind == "sum_in_window":
        return (
            [t.tx_id for t in inside]
            if sum((t.amount for t in inside), Decimal(0)) >= rule.amount
            else []
        )
    if rule.kind == "just_below":
        floor = rule.amount * (Decimal(100) - rule.within_pct) / Decimal(100)
        near = [t for t in inside if floor <= t.amount < rule.amount]
        return [t.tx_id for t in near] if len(near) >= rule.count else []
    seen_before = {t.counterparty for t in txs if t.at < now - timedelta(days=rule.days)}
    return [
        t.tx_id for t in inside if t.amount >= rule.amount and t.counterparty not in seen_before
    ]
