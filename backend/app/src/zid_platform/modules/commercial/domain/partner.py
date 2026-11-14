"""Partner channel rules (ZID-COM-WF-2026-11 WF-I06, P01; INV-09; CAT-34, CAT-35). Pure.

A partner call is an ordinary call with one more gate in front of it. The gate decides whether this partner may act for this child, in this country, now, and who
the single bill-to is. It never replaces the platform's own authority, consent, issuer, country or region checks: a partner API key or white-label flow is not a
way round them, and nothing here widens what the child consented to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

KINDS = ("RESELLER", "REFERRAL", "EMBEDDED_ISV")
BILL_TO = ("PARTNER", "CHILD")


class PartnerError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Partner:
    partner_id: str
    parent_tenant_id: str
    bill_to: str
    territory: tuple[str, ...]
    status: str
    effective_from: datetime | None
    effective_until: datetime | None


@dataclass(frozen=True, slots=True)
class Child:
    child_tenant_id: str
    revoked: bool
    downstream_consent_ref: str | None


def refusals(
    partner: Partner | None, child: Child | None, *, country: str | None, now: datetime
) -> list[str]:
    """Every reason a partner may not act for this child here and now. Empty means it may. Anything unknown is a refusal."""
    out: list[str] = []
    if partner is None:
        return ["PARTNER_UNKNOWN"]
    if partner.status != "ACTIVE":
        out.append("PARTNER_NOT_ACTIVE")
    if (
        partner.effective_from is None
        or now < partner.effective_from
        or (partner.effective_until is not None and now >= partner.effective_until)
    ):
        out.append("PARTNER_OUTSIDE_TERM")
    if child is None or child.revoked:
        out.append("CHILD_NOT_PERMITTED")
    elif not (child.downstream_consent_ref or "").strip():
        out.append("DOWNSTREAM_CONSENT_MISSING")
    if not country:
        out.append("COUNTRY_NOT_STATED")
    elif country not in partner.territory:
        out.append("CROSS_TERRITORY")
    return out


def check(
    partner: Partner | None, child: Child | None, *, country: str | None, now: datetime
) -> None:
    problems = refusals(partner, child, country=country, now=now)
    if problems:
        raise PartnerError(problems[0], ",".join(problems))


def legal_payer(partner: Partner, child_tenant_id: str) -> str:
    """The one bill-to for an operation the partner brought in: the partner's own tenant, or the child, by the signed agreement. Never both, never neither."""
    if partner.bill_to == "PARTNER":
        return partner.parent_tenant_id
    if partner.bill_to == "CHILD":
        return child_tenant_id
    raise PartnerError("BILL_TO_UNKNOWN", partner.bill_to)


def reconcile(events: list[dict[str, object]], partner: Partner) -> dict[str, object]:
    """Reconcile the events attributed to a partner for a period. Every event must carry the one payer the agreement names, and no logical operation may appear twice."""
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    wrong_payer: list[str] = []
    total = 0
    for e in events:
        key = str(e["deduplication_key"])
        ident = (str(e["tenant_id"]), key)
        if ident in seen:
            duplicates.append(key)
        seen.add(ident)
        total += 1
        expected = legal_payer(partner, str(e["tenant_id"]))
        if str(e.get("legal_payer_id") or "") != expected:
            wrong_payer.append(key)
    return {
        "events": total,
        "duplicates": duplicates,
        "wrong_or_missing_payer": wrong_payer,
        "reconciles": not duplicates and not wrong_payer,
    }
