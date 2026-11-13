"""The sixteen commercial service families and what a configuration of them may contain (ZID-COM-WF-2026-11 v3.0, sections 02, 03.1 and WF-C02). Pure.

A family is a proposal for a SKU namespace: not a SKU, not a price, not a promise that anything is sold. This module only keeps the catalogue honest:

- the sixteen families cannot be dropped, renamed or added to by a stray edit of the file: the set is fixed here and a test fails if the file disagrees
  (the document's "16-family capability x unit catalog", critical risk G01: one generic "verification" SKU would hide very different rights and costs);
- every family names its exact chargeable unit;
- a dependency the document states (a signature needs a verified identity) blocks an impossible selection instead of quoting it (WF-C02 step 2);
- a meter named here must exist in the meter register, so no family claims a counter nobody ratified, and a family with no meter is simply not billed.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EXPECTED_CODES = frozenset(
    {
        "IDV", "DOC", "BIO", "KYB", "SCR", "QUAL", "PRO", "WORK",
        "AUTHN", "DELEG", "VC", "SIGN", "EVID", "API", "SOV", "REV",
    }
)  # fmt: skip


class FamilyError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Family:
    code: str
    name: str
    unit: str
    depends_on: tuple[str, ...]
    regulated: bool
    needs_provider: bool
    meter: str | None
    sales_qualifier: str


@dataclass(frozen=True, slots=True)
class FamilyRegistry:
    version: str
    status: str
    families: Mapping[str, Family]

    def get(self, code: str) -> Family | None:
        return self.families.get(code)


@dataclass(frozen=True, slots=True)
class SelectionProblem:
    code: str  # UNKNOWN_FAMILY | MISSING_DEPENDENCY | NOTHING_SELECTED
    family: str | None = None
    needs: str | None = None


def parse(doc: Mapping[str, Any], *, known_meters: Collection[str] | None = None) -> FamilyRegistry:
    """Read and validate the file. A malformed one never loads: there is no partial catalogue."""
    raw = doc.get("families")
    if not isinstance(raw, list):
        raise FamilyError("FAMILIES_MISSING")
    out: dict[str, Family] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise FamilyError("FAMILY_NOT_AN_OBJECT")
        code = str(item.get("code", "")).strip()
        if not code or code in out:
            raise FamilyError("FAMILY_CODE_INVALID_OR_DUPLICATE", code)
        unit = str(item.get("unit", "")).strip()
        if not unit:
            raise FamilyError("FAMILY_UNIT_MISSING", code)
        depends = item.get("depends_on") or []
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise FamilyError("FAMILY_DEPENDENCIES_INVALID", code)
        meter = item.get("meter")
        if meter is not None and (not isinstance(meter, str) or not meter):
            raise FamilyError("FAMILY_METER_INVALID", code)
        if meter is not None and known_meters is not None and meter not in known_meters:
            raise FamilyError("FAMILY_METER_NOT_IN_THE_REGISTER", f"{code}:{meter}")
        for flag in ("regulated", "needs_provider"):
            if not isinstance(item.get(flag), bool):
                raise FamilyError("FAMILY_FLAG_NOT_A_BOOLEAN", f"{code}:{flag}")
        out[code] = Family(
            code=code,
            name=str(item.get("name", "")).strip() or code,
            unit=unit,
            depends_on=tuple(depends),
            regulated=bool(item["regulated"]),
            needs_provider=bool(item["needs_provider"]),
            meter=meter,
            sales_qualifier=str(item.get("sales_qualifier", "")).strip(),
        )
    if set(out) != EXPECTED_CODES:
        missing, extra = sorted(EXPECTED_CODES - set(out)), sorted(set(out) - EXPECTED_CODES)
        raise FamilyError("FAMILY_SET_CHANGED", f"missing={missing} extra={extra}")
    for fam in out.values():
        for dep in fam.depends_on:
            if dep not in out:
                raise FamilyError("FAMILY_DEPENDENCY_UNKNOWN", f"{fam.code}->{dep}")
            if dep == fam.code:
                raise FamilyError("FAMILY_DEPENDS_ON_ITSELF", fam.code)
    _no_cycles(out)
    return FamilyRegistry(str(doc.get("version", "0")), str(doc.get("status", "PROPOSED")), out)


def _no_cycles(families: Mapping[str, Family]) -> None:
    state: dict[str, int] = {}

    def visit(code: str) -> None:
        if state.get(code) == 2:
            return
        if state.get(code) == 1:
            raise FamilyError("FAMILY_DEPENDENCY_CYCLE", code)
        state[code] = 1
        for dep in families[code].depends_on:
            visit(dep)
        state[code] = 2

    for code in families:
        visit(code)


def check_selection(
    registry: FamilyRegistry, selected: Sequence[str]
) -> tuple[SelectionProblem, ...]:
    """Why this combination cannot be configured: an unknown family, or one whose stated dependency is missing. Every problem is named, not just the first."""
    if not selected:
        return (SelectionProblem("NOTHING_SELECTED"),)
    chosen = set(selected)
    problems: list[SelectionProblem] = []
    for code in sorted(chosen):
        fam = registry.get(code)
        if fam is None:
            problems.append(SelectionProblem("UNKNOWN_FAMILY", code))
            continue
        problems.extend(
            SelectionProblem("MISSING_DEPENDENCY", code, dep)
            for dep in fam.depends_on
            if dep not in chosen
        )
    return tuple(problems)
