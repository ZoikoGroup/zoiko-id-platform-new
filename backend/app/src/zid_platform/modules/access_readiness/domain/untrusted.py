"""Metadata is data, never instructions (ZID-AG-WF-001 section 12, "Malicious prompt in metadata"; CR-091).

A metadata string the adapter reads (an action, a tool, a status, a reference) is passed to the rules as an opaque value. Nothing here
changes that: this only notices a string that looks like an attempt to instruct an agent (to ignore its rules, take on a role, call a
tool or widen access), so the run can record a redacted marker: the field, the rule that matched and a digest of the value, never the
value itself. The rule result is computed from the same facts whether or not a marker is found.

No prompt-injection detector exists elsewhere in the platform image (the public assistant's patterns live in a separate service that the
platform never imports), so this is a small, conservative pattern set: phrases, not single words, so an ordinary action name such as
`invoice.approve` or `docs.read` never matches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from zid_platform.modules.access_readiness.domain.rules import MandateFacts, digest

# rule name -> pattern. Every pattern needs at least two words in a recognisable order.
INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "IGNORE_INSTRUCTIONS",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[\s\S]{0,40}?"
            r"\b(?:previous|prior|above|earlier|all|any|your|the|system|these)\b[\s\S]{0,20}?"
            r"\b(?:instructions?|prompts?|rules?|polic(?:y|ies)|guidelines?|guardrails?|checks?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ROLE_MARKER",
        re.compile(
            r"(?:^|[\n\r])\s*(?:system|assistant|developer|user)\s*:"
            r"|<\|?\s*(?:system|im_start|im_end|endoftext)\s*\|?>|\[/?INST\]|<</?SYS>>",
            re.IGNORECASE,
        ),
    ),
    (
        "PERSONA_SWITCH",
        re.compile(
            r"\b(?:you are now|act as|pretend (?:to be|you are)|from now on,? you|new instructions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "TOOL_REQUEST",
        re.compile(
            r"\b(?:call|invoke|run|execute|use)\b\s+(?:the\s+|a\s+|this\s+)?"
            r"(?:tool|function|command|shell|sql)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ACCESS_REQUEST",
        re.compile(
            r"\b(?:grant|approve|escalate|elevate|give)\b[\s\S]{0,20}?\b(?:all|admin|full|every|root|unrestricted)\b"
            r"[\s\S]{0,15}?\b(?:access|permissions?|privileges?|rights|roles?)\b"
            r"|\bmark\b[\s\S]{0,15}?\bas\b\s+(?:compliant|approved|clean|safe|healthy)\b",
            re.IGNORECASE,
        ),
    ),
)

# the MandateFacts fields that carry strings read from another module (ids are checked too: they are strings the adapter returns)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_STRING_FIELDS = ("mandate_id", "status", "evidence_ref", "grant_ref", "sponsor_ref", "adapter")
_SET_FIELDS = ("assigned_actions", "assigned_tools", "approved_actions", "approved_tools")


@dataclass(frozen=True, slots=True)
class InjectionMarker:
    """What is kept about a suspicious string: where it was, which rule matched and its digest. Never the string."""

    target_ref: str
    field: str
    rule: str
    value_digest: str

    def as_dict(self) -> dict[str, str]:
        return {
            "target": self.target_ref,
            "field": self.field,
            "rule": self.rule,
            "digest": self.value_digest,
        }


def injection_rule(value: str) -> str | None:
    """The first rule a string matches, or None. Pure; the string is only read."""
    for name, pattern in INJECTION_RULES:
        if pattern.search(value):
            return name
    return None


def _strings(fact: MandateFacts) -> Iterable[tuple[str, str]]:
    for name in _STRING_FIELDS:
        value = getattr(fact, name)
        if isinstance(value, str):
            yield name, value
    for name in _SET_FIELDS:
        values = getattr(fact, name)
        if values:
            for value in sorted(v for v in values if isinstance(v, str)):
                yield name, value


def injection_markers(facts: Iterable[MandateFacts]) -> list[InjectionMarker]:
    """One redacted marker per suspicious string, in a stable order. The facts are not changed."""
    out: list[InjectionMarker] = []
    for fact in facts:
        # the target is the mandate's id only when it is an id: a suspicious "id" is itself referred to by its digest
        mid = fact.mandate_id
        target = (
            f"mandate:{mid}"
            if isinstance(mid, str) and _UUID.fullmatch(mid)
            else "mandate:" + digest(["untrusted", str(mid)])
        )
        for field, value in _strings(fact):
            rule = injection_rule(value)
            if rule is not None:
                out.append(InjectionMarker(target, field, rule, digest(["untrusted", value])))
    return out
