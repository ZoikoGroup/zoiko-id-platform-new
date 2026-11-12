"""Accessible alternatives to camera capture (ZID-GAP-001 v2.0 GID-062, DTV-12; GLB-30). Pure domain.

A person who cannot take a selfie or photograph a document (low vision, a tremor, an assistive technology that does not work with the capture
screen, no usable camera) must have another way to reach the same check. The rules, each of which is a test:

- asking for an alternative needs NO reason and NO proof of a disability. The reason, if given, is only used to choose a sensible route and is
  never required, never stored here, and never a ground for refusal;
- choosing an alternative, or failing to capture because of an accessibility reason, is NEVER a failed attempt and NEVER produces a
  rejection: the outcome is that an alternative route was selected, and the case carries on;
- every country that has the check offers at least one alternative. A configuration that leaves a country with none does not load, so
  "there is no other way" cannot be shipped by editing a file;
- an alternative may give a different level of assurance than camera capture. That is said in the offer, not hidden: a different route is not
  a lower-trust person.

Nothing here collects or stores anything: it answers "what can this person do instead".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# why someone might ask. Used only to order the offer; never required.
REASONS = frozenset(
    {
        "CANNOT_USE_CAMERA",
        "CANNOT_HOLD_STEADY",
        "ASSISTIVE_TECHNOLOGY_INCOMPATIBLE",
        "NO_USABLE_CAMERA",
        "PREFER_NOT_TO_SAY",
    }
)


class AlternativeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Alternative:
    code: str
    title: str
    how: str  # plain language, what the person does
    assurance_note: str  # what level of assurance this route gives, honestly
    suits: frozenset[str]  # reasons this route suits best (an ordering hint only)
    needs_human: bool = False


@dataclass(frozen=True, slots=True)
class Registry:
    version: str
    alternatives: Mapping[str, Alternative]
    by_country: Mapping[str, tuple[str, ...]]  # country (or "*") -> alternative codes
    default: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Offer:
    country: str
    routes: tuple[Alternative, ...]
    counts_as_failed_attempt: bool = (
        False  # always False: asking for an alternative is never a failure
    )
    rejection_possible: bool = False  # always False: choosing one never rejects
    reason_required: bool = False


def parse(doc: Mapping[str, Any]) -> Registry:
    alts: dict[str, Alternative] = {}
    for raw in doc.get("alternatives") or []:
        try:
            code = str(raw["code"])
            suits = frozenset(raw.get("suits") or ())
            if suits - REASONS:
                raise AlternativeError("REASON_UNKNOWN", ",".join(sorted(suits - REASONS)))
            alt = Alternative(
                code=code,
                title=str(raw["title"]),
                how=str(raw["how"]),
                assurance_note=str(raw["assurance_note"]),
                suits=suits,
                needs_human=bool(raw.get("needs_human", False)),
            )
        except KeyError as e:
            raise AlternativeError("ALTERNATIVE_INCOMPLETE", str(e)) from e
        if code in alts:
            raise AlternativeError("ALTERNATIVE_DUPLICATE", code)
        alts[code] = alt
    if not alts:
        raise AlternativeError("NO_ALTERNATIVES_DEFINED")
    default = tuple(str(c) for c in doc.get("default") or ())
    by_country: dict[str, tuple[str, ...]] = {}
    for country, codes in (doc.get("countries") or {}).items():
        by_country[str(country).upper()] = tuple(str(c) for c in codes or ())
    for where, codes in [("default", default), *by_country.items()]:
        if not codes:
            raise AlternativeError(
                "NO_ALTERNATIVE_FOR_COUNTRY", where
            )  # "no other way" cannot be configured
        unknown = [c for c in codes if c not in alts]
        if unknown:
            raise AlternativeError("ALTERNATIVE_UNKNOWN", f"{where}: {','.join(unknown)}")
    return Registry(str(doc.get("version", "")), alts, by_country, default)


def offer(registry: Registry, country: str, reason: str | None = None) -> Offer:
    """What this person can do instead. The reason, when given, only puts the routes that suit it first; a reason that is not recognised is
    ignored, never refused. Every route configured for the country is always offered."""
    code = country.upper()
    codes = registry.by_country.get(code, registry.default)
    routes = [registry.alternatives[c] for c in codes]
    if reason in REASONS:
        routes.sort(
            key=lambda a: 0 if reason in a.suits else 1
        )  # stable: the configured order otherwise
    return Offer(code, tuple(routes))
