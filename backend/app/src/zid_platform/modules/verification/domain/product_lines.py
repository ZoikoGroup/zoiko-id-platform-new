"""Regulated product lines and the gate that keeps them off (ZID-GAP-001 v2.0 DTV-26..28, DTV-32..34; CR-073 section D). Pure domain.

Open banking, payment ownership, source of funds and wealth, transaction monitoring, the crypto Travel Rule and on-chain screening are
regulated or need a licensed partner. This module is the one place that says whether any of them may run, and it fails closed:
- every line ships as DESIGN: it exists as a name, a state and a place to record approvals, and does nothing;
- a line may be STAGING or LIVE only with a recorded legal basis; a line that needs a partner needs a partner contract reference too;
  LIVE also needs the name of the owner who approved it. A configuration that says LIVE without them does not load (so a deployment
  cannot be switched on by editing one word);
- a line works only in the countries it lists; a country not listed is unavailable, never inferred from the neighbouring one;
- asking for a line that is not available is an error that names its state, never an empty success.

Nothing here collects or stores data. It is the gate a future route or partner adapter must pass through.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# The six regulated lines and whether each needs a licensed partner (source of funds is questionnaires plus review, which is ours).
KNOWN_LINES: Mapping[str, bool] = {
    "open_banking_ownership": True,  # DTV-26
    "payment_ownership": True,  # DTV-27
    "source_of_funds": False,  # DTV-28
    "transaction_monitoring": True,  # DTV-32
    "travel_rule": True,  # DTV-33
    "onchain_screening": True,  # DTV-34
}


class ProductLineError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class LineState(StrEnum):
    DESIGN = "DESIGN"
    BLOCKED = "BLOCKED"
    STAGING = "STAGING"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class ProductLine:
    code: str
    dtv: str
    state: LineState
    countries: tuple[str, ...]
    needs_partner: bool
    legal_basis_ref: str | None = None
    partner_contract_ref: str | None = None
    approved_by: str | None = None

    @property
    def available(self) -> bool:
        return self.state in {LineState.STAGING, LineState.LIVE}


@dataclass(frozen=True, slots=True)
class LineRegistry:
    version: str
    lines: Mapping[str, ProductLine]

    def get(self, code: str) -> ProductLine:
        line = self.lines.get(code)
        if line is None:
            raise ProductLineError("PRODUCT_LINE_UNKNOWN", code)
        return line


def _check(line: ProductLine) -> None:
    if line.state in {LineState.STAGING, LineState.LIVE}:
        if not line.legal_basis_ref:
            raise ProductLineError("LEGAL_BASIS_MISSING", line.code)
        if line.needs_partner and not line.partner_contract_ref:
            raise ProductLineError("PARTNER_CONTRACT_MISSING", line.code)
        if not line.countries:
            raise ProductLineError("NO_COUNTRIES_LISTED", line.code)
    if line.state is LineState.LIVE and not line.approved_by:
        raise ProductLineError("APPROVER_MISSING", line.code)


def parse(doc: Mapping[str, Any]) -> LineRegistry:
    """Build the registry from configuration. A line that is not one of the six, a state that is not known, or a line switched on without
    its approvals is refused, so a bad file stops the service rather than quietly enabling something. A known line left out is DESIGN."""
    raw = doc.get("lines") or {}
    unknown = set(raw) - set(KNOWN_LINES)
    if unknown:
        raise ProductLineError("PRODUCT_LINE_UNKNOWN", ",".join(sorted(unknown)))
    lines: dict[str, ProductLine] = {}
    for code, needs_partner in KNOWN_LINES.items():
        entry = raw.get(code) or {}
        try:
            state = LineState(str(entry.get("state", "DESIGN")))
        except ValueError as e:
            raise ProductLineError(
                "PRODUCT_LINE_STATE_UNKNOWN", f"{code}: {entry.get('state')}"
            ) from e
        line = ProductLine(
            code=code,
            dtv=str(entry.get("dtv", "")),
            state=state,
            countries=tuple(str(c).upper() for c in entry.get("countries") or ()),
            needs_partner=needs_partner,
            legal_basis_ref=entry.get("legal_basis_ref") or None,
            partner_contract_ref=entry.get("partner_contract_ref") or None,
            approved_by=entry.get("approved_by") or None,
        )
        _check(line)
        lines[code] = line
    return LineRegistry(str(doc.get("version", "")), lines)


def require_available(registry: LineRegistry, code: str, country: str) -> ProductLine:
    """The line, if it may run for this country. Otherwise an error that says why (the state, or the country), never a default."""
    line = registry.get(code)
    if not line.available:
        raise ProductLineError("SERVICE_UNAVAILABLE", f"{code} is {line.state.value}")
    if country.upper() not in line.countries:
        raise ProductLineError("SERVICE_UNAVAILABLE_IN_MARKET", f"{code} in {country.upper()}")
    return line
