"""Metric computation, cohorts and small-group protection (ZID-CPS-WF-02 sections 05, 05.1, 07.02 to 07.09, 07.12). Pure domain, no I/O.

Everything is computed from one summary per session (a journey), built from the session's own events, so each figure reconciles to distinct
session ids (Q03). A result always carries its numerator, denominator, definition version and quality; a figure under the small-group
threshold is withheld, never shown (Q36)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from zid_platform.modules.verify_analytics.domain.definitions import CATALOGUE


@dataclass
class Journey:
    """One hosted session as its events describe it. A missing time is None, never guessed."""

    session_ref: str
    environment: str | None = None
    market: str | None = None
    method: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    presented_at: datetime | None = None
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    canceled_at: datetime | None = None
    review_required_at: datetime | None = None
    review_due_at: datetime | None = None
    attempts: list[tuple[int, str]] = field(default_factory=list)  # (attempt number, result code)
    decisions: list[dict[str, Any]] = field(default_factory=list)  # every finalized event, oldest first
    risk_signal: bool = False

    @property
    def final(self) -> dict[str, Any] | None:
        """The effective decision: the newest version. Earlier ones stay in `decisions` (superseded, never rewritten)."""
        return max(self.decisions, key=lambda d: (int(d.get("decision_version") or 0), d["at"])) if self.decisions else None

    def matured(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now


def journeys(facts: Iterable[dict[str, Any]]) -> list[Journey]:
    """Fold facts (event_type, session_ref, occurred_at, environment, market, attrs) into journeys. Duplicates cannot reach here (one fact per
    event id), so a replay cannot add a session, an attempt or a decision (Q04)."""
    by: dict[str, Journey] = {}
    for f in sorted(facts, key=lambda x: x["occurred_at"]):
        ref = f.get("session_ref")
        if not ref:
            continue
        j = by.setdefault(ref, Journey(ref))
        a = f.get("attrs") or {}
        t, at = f["event_type"], f["occurred_at"]
        j.environment = j.environment or f.get("environment")
        j.market = j.market or f.get("market")
        if t == "verification.session.created":
            j.created_at = at
            j.expires_at = _dt(a.get("expires_at"))
        elif t == "verification.session.notice_presented":
            j.presented_at = j.presented_at or at
        elif t == "verification.session.method_selected":
            j.method = a.get("method") or j.method
        elif t == "verification.session.capture_started":
            j.started_at = j.started_at or at
        elif t == "verification.session.capture_attempt_completed":
            j.attempts.append((int(a.get("attempt_no") or 0), str(a.get("result") or "UNKNOWN")))
        elif t == "verification.session.media_accepted":
            j.risk_signal = j.risk_signal or a.get("source_attestation_result") == "RISK"
        elif t == "verification.session.submitted":
            j.submitted_at = j.submitted_at or at
        elif t == "verification.session.review_required":
            j.review_required_at = j.review_required_at or at
            j.review_due_at = j.review_due_at or _dt(a.get("due_at"))
        elif t == "verification.session.finalized":
            j.decisions.append({**a, "at": at})
        elif t == "verification.session.canceled":
            j.canceled_at = j.canceled_at or at
    return list(by.values())


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------------------------------- small groups


def suppressed_ratio(numerator: int, denominator: int, k: int) -> bool:
    return denominator < k


def suppressed_count(count: int, k: int) -> bool:
    return 0 < count < k


def protect_rows(rows: list[dict[str, Any]], *, k: int, size_key: str = "denominator") -> list[dict[str, Any]]:
    """Primary suppression of every row under k, then complementary suppression: when only one row of a breakdown is withheld, the next
    smallest is withheld too, so the total minus the shown rows cannot reveal it (differencing, Q36)."""
    out = [dict(r) for r in rows]
    small = [r for r in out if r.get(size_key, 0) < k]
    for r in small:
        _withhold(r)
    if len(small) == 1:
        rest = sorted((r for r in out if not r.get("suppressed")), key=lambda r: r.get(size_key, 0))
        if rest:
            _withhold(rest[0])
    return out


def _withhold(r: dict[str, Any]) -> None:
    for key in ("value", "numerator", "denominator", "count", "p50", "p95"):
        if key in r:
            r[key] = None
    r["suppressed"] = True


def result(metric_id: str, numerator: float | None, denominator: int | None, *, k: int, quality: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """One governed figure: value with its numerator, denominator, definition version and quality, or withheld."""
    m = CATALOGUE[metric_id]
    out: dict[str, Any] = {"metric_id": metric_id, "name": m.name, "definition_version": m.version, "unit": m.unit, "quality": quality, "suppressed": False}
    if m.unit == "ratio":
        den = int(denominator or 0)
        num = int(numerator or 0)
        out.update(numerator=num, denominator=den, value=(round(num / den, 4) if den else None))
        if den and suppressed_ratio(num, den, k):
            _withhold(out)
        elif not den:
            out["quality"] = "NO_DATA"
    elif m.unit == "count":
        c = int(numerator or 0)
        out.update(value=c, numerator=c, denominator=None)
        if suppressed_count(c, k):
            _withhold(out)
    else:
        out.update(value=numerator, numerator=numerator, denominator=denominator)
        if denominator is not None and denominator < k:
            _withhold(out)
        elif not denominator:
            out["quality"] = "NO_DATA"
    out.update(extra or {})
    return out


def quantile(values: list[float], q: float) -> float | None:
    """Nearest-rank quantile; None for no values."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


# ------------------------------------------------------------------------------------------------------------------------------ journeys

AUTOMATED = "AUTOMATED"
REVIEWER_FINDINGS = frozenset({"DOCUMENT_MANIPULATION_SUSPECTED"})


def session_metrics(js: list[Journey], *, now: datetime, k: int, quality: str) -> dict[str, dict[str, Any]]:
    created = [j for j in js if j.created_at]
    matured = [j for j in created if j.matured(now)]
    started_m = [j for j in matured if j.started_at]
    submitted = [j for j in created if j.submitted_at]
    finals = [j for j in submitted if j.final]
    auto = [j for j in finals if (j.final or {}).get("decided_by") == AUTOMATED]
    attempted = [j for j in created if j.attempts]
    first_ok = [j for j in attempted if _first(j) == "ACCEPTED"]
    proc = [(j.final["at"] - j.submitted_at).total_seconds() for j in auto if j.final and j.submitted_at]
    e2e = [(j.final["at"] - j.presented_at).total_seconds() for j in finals if j.final and j.presented_at]
    due = [j for j in created if j.review_due_at and j.review_due_at <= now]
    breached = [j for j in due if _breached(j)]
    findings = sum(1 for j in finals if (j.final or {}).get("finding_code") in REVIEWER_FINDINGS)
    overturned = sum(1 for j in created if len({d.get("outcome") for d in j.decisions}) > 1)
    return {
        "M01": result("M01", len(created), None, k=k, quality=quality),
        "M02": result("M02", len(started_m), len(matured), k=k, quality=quality, extra={"not_started": len(matured) - len(started_m)}),
        "M03": result("M03", sum(1 for j in started_m if j.submitted_at), len(started_m), k=k, quality=quality),
        "M04": result("M04", len(finals), len(submitted), k=k, quality=quality, extra={"pending": len(submitted) - len(finals)}),
        "M05": result("M05", len(auto), len(finals), k=k, quality=quality),
        "M06": result("M06", len(first_ok), len(attempted), k=k, quality=quality),
        "M07": result("M07", quantile(proc, 0.5), len(proc), k=k, quality=quality, extra={"p95": quantile(proc, 0.95)}),
        "M08": result("M08", quantile(e2e, 0.5), len(e2e), k=k, quality=quality, extra={"p95": quantile(e2e, 0.95), "missing_times": len(finals) - len(e2e)}),
        "M09": result("M09", len(breached), len(due), k=k, quality=quality, extra={"open_reviews": sum(1 for j in created if j.review_required_at and not _decided_after_review(j))}),
        "M10": result("M10", sum(1 for j in created if j.risk_signal), None, k=k, quality=quality,
                      extra={"series": {"risk_signals": _count(sum(1 for j in created if j.risk_signal), k), "confirmed_findings": _count(findings, k),
                                        "overturned": _count(overturned, k)}}),
    }  # fmt: skip


def _count(n: int, k: int) -> int | None:
    return None if suppressed_count(n, k) else n


def _first(j: Journey) -> str | None:
    tries = sorted(j.attempts)
    return tries[0][1] if tries else None


def _decided_after_review(j: Journey) -> bool:
    return any(j.review_required_at and d["at"] >= j.review_required_at for d in j.decisions)


def _breached(j: Journey) -> bool:
    decided = [d["at"] for d in j.decisions if j.review_required_at and d["at"] >= j.review_required_at]
    return not decided or (j.review_due_at is not None and min(decided) > j.review_due_at)


def state_of(j: Journey, now: datetime) -> str:
    """The normalized state (section 05.1): CREATED, STARTED, SUBMITTED, REVIEW, the final outcome, ABANDONED or EXPIRED."""
    f = j.final
    if f:
        return str(f.get("state") or "FINAL")
    if j.canceled_at:
        return "ABANDONED"
    if j.submitted_at:
        return "REVIEW" if j.review_required_at else "SUBMITTED"
    if j.matured(now):
        return "EXPIRED"
    return "STARTED" if j.started_at else "CREATED"


def funnel(js: list[Journey], *, now: datetime, k: int, cohort: str) -> dict[str, Any]:
    """The funnel for matured journeys (Q09). `cohort` = created (every session) or started (only those that began a capture). Drop-off is
    classified only from observed events: a session that stopped with no terminal event is EXPIRED, one whose events contradict each other is
    UNKNOWN, never abandonment (Q08)."""
    pool = [j for j in js if j.created_at and j.matured(now) and (cohort == "created" or j.started_at)]
    steps = [
        ("created", len(pool)),
        ("started", sum(1 for j in pool if j.started_at)),
        ("submitted", sum(1 for j in pool if j.submitted_at)),
        ("decided", sum(1 for j in pool if j.final)),
    ]
    reasons: Counter[str] = Counter()
    for j in pool:
        if j.final:
            continue
        if j.submitted_at and not j.started_at:
            reasons["UNKNOWN"] += 1  # instrumentation gap: a submission with no capture start
        elif j.canceled_at:
            reasons["USER_EXITED"] += 1
        elif j.submitted_at:
            reasons["AWAITING_DECISION"] += 1
        elif any(r == "TECHNICAL_FAILED" for _, r in j.attempts):
            reasons["PROVIDER_FAILED"] += 1
        else:
            reasons["EXPIRED"] += 1
    base = steps[0][1]
    rows = [{"step": s, "count": n, "denominator": base, "value": (round(n / base, 4) if base else None)} for s, n in steps]
    return {
        "cohort": cohort,
        "matured_only": True,
        "steps": protect_rows(rows, k=k),
        "drop_off": protect_rows([{"reason": r, "count": n, "denominator": n} for r, n in sorted(reasons.items())], k=k),
    }


def breakdown(js: list[Journey], *, dimension: str, metric: str, now: datetime, k: int, tz: str = "UTC") -> list[dict[str, Any]]:
    """A metric by one allow-listed dimension (market, method, environment, day). Each row is a ratio with its own denominator, protected."""
    key: Callable[[Journey], str]
    if dimension == "day":
        zone = ZoneInfo(tz)
        key = lambda j: j.created_at.astimezone(zone).date().isoformat() if j.created_at else "UNKNOWN"
    else:
        key = lambda j: getattr(j, dimension) or "UNKNOWN"
    groups: dict[str, list[Journey]] = {}
    for j in js:
        if j.created_at:
            groups.setdefault(key(j), []).append(j)
    rows = []
    for g, members in sorted(groups.items()):
        r = session_metrics(members, now=now, k=1, quality="OBSERVED")[metric]  # k applied below, across the breakdown
        rows.append({dimension: g, "numerator": r.get("numerator"), "denominator": r.get("denominator") or r.get("numerator") or 0, "value": r.get("value")})
    return protect_rows(rows, k=k)


def first_attempt_causes(js: list[Journey], *, k: int) -> list[dict[str, Any]]:
    """Why first attempts were not accepted, from the attempt's own result code; no code is UNKNOWN (07.03 item 05)."""
    c: Counter[str] = Counter(_first(j) or "UNKNOWN" for j in js if j.attempts and _first(j) != "ACCEPTED")
    return protect_rows([{"cause": cause, "count": n, "denominator": n} for cause, n in sorted(c.items())], k=k)


def decision_split(js: list[Journey], *, now: datetime, k: int) -> list[dict[str, Any]]:
    """Current state of each session (07.04 item 01): final outcomes, review, unresolved and system-error kept apart."""
    c: Counter[str] = Counter(state_of(j, now) for j in js if j.created_at)
    return protect_rows([{"state": s, "count": n, "denominator": n} for s, n in sorted(c.items())], k=k)


# ------------------------------------------------------------------------------------------------------------------- providers, webhooks


def provider_quality(checks: list[dict[str, Any]], *, k: int) -> list[dict[str, Any]]:
    """Per provider: technical error rate (policy denials excluded, Q23) and latency percentiles."""
    by: dict[str, list[dict[str, Any]]] = {}
    for c in checks:
        by.setdefault(str(c.get("provider") or "UNKNOWN"), []).append(c)
    rows = []
    for p, cs in sorted(by.items()):
        eligible = [c for c in cs if c.get("outcome") != "POLICY_DENIED"]
        errors = sum(1 for c in eligible if c.get("outcome") == "TECHNICAL_ERROR")
        lat = [float(c["latency_ms"]) for c in eligible if c.get("latency_ms") is not None]
        rows.append({"provider": p, "numerator": errors, "denominator": len(eligible), "value": round(errors / len(eligible), 4) if eligible else None,
                     "p50": quantile(lat, 0.5), "p95": quantile(lat, 0.95), "policy_denials": len(cs) - len(eligible)})  # fmt: skip
    return protect_rows(rows, k=k)


def webhook_delivery(rows: list[dict[str, Any]], *, k: int, quality: str) -> dict[str, Any]:
    """Final delivery per delivery id (a retried event counts once, Q31): delivered only on a recipient 2xx (Q30)."""
    last: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda x: x["occurred_at"]):
        last[str(r.get("delivery_id"))] = r
    final = [r for r in last.values() if r["event_type"] != "webhook.delivery.failed" or r.get("final")]
    ok = [r for r in final if r["event_type"] == "webhook.delivery.succeeded"]
    delays = [float(r["delay_ms"]) for r in ok if r.get("delay_ms") is not None]
    return result("M12", len(ok), len(final), k=k, quality=quality, extra={
        "dead_lettered": _count(sum(1 for r in final if r["event_type"] == "webhook.delivery.exhausted"), k),
        "delay_p50_ms": quantile(delays, 0.5), "delay_p95_ms": quantile(delays, 0.95),
        "retried": _count(sum(1 for r in ok if int(r.get("attempt") or 1) > 1), k),
    })  # fmt: skip


# --------------------------------------------------------------------------------------------------------------------------- freshness


def freshness(watermarks: list[dict[str, Any]], *, now: datetime, stale_after: timedelta) -> tuple[str, list[dict[str, Any]]]:
    """Overall quality and per-source lag (M16). No source is NO_DATA; any source past the policy makes dependent figures PROVISIONAL (Q12)."""
    rows = []
    for w in watermarks:
        lag = (now - w["last_ingested_at"]).total_seconds()
        rows.append({"source": w["source"], "last_event_at": w["last_occurred_at"].isoformat(), "last_ingested_at": w["last_ingested_at"].isoformat(),
                     "lag_seconds": round(lag), "stale": lag > stale_after.total_seconds(), "accepted": w["accepted"], "late": w["late"], "rejected": w["rejected"]})  # fmt: skip
    if not rows:
        return "NO_DATA", rows
    return ("PROVISIONAL" if any(r["stale"] for r in rows) else "OBSERVED"), rows
