"""The email template catalogue (ZID-EM-001..160; Communication Estate copybook v2.0). Pure domain, no I/O.

The copybook is the source of the words and `templates/catalogue.json` is that copy, structured by
`tools/scripts/build_email_catalogue.py`. This module loads it, classifies each template, names its merge
fields and validates the invariants the copybook states (one action at most, one-time codes only in the
dedicated template, every placeholder of a known kind). A template's `version` changes whenever its copy
does, so a message log row always says exactly which words were sent (§05: no silent changes).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CATALOGUE_VERSION = "2.0"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
OTP_TEMPLATE = "ZID-EM-009"  # §03: the only template in which a one-time code may appear


class MessageClass(StrEnum):
    """§05 classification. Only MARKETING can be unsubscribed from; every other class is a service message."""

    SECURITY = "SECURITY"
    SERVICE = "SERVICE"
    PRIVACY = "PRIVACY"
    INCIDENT = "INCIDENT"
    MARKETING = "MARKETING"

    @property
    def mandatory(self) -> bool:
        return self is not MessageClass.MARKETING


# The copybook labels three "Marketing" templates, but its own engineering controls say two of them are
# confirmations of an action the person just took (a registration, a preference change, an unsubscribe) and
# must still arrive after they opt out of marketing, so they are service messages.
_SERVICE_DESPITE_LABEL = frozenset({"ZID-EM-143", "ZID-EM-144", "ZID-EM-145"})


def classify(template_id: str, class_label: str) -> MessageClass:
    label = class_label.lower()
    if template_id in _SERVICE_DESPITE_LABEL:
        return MessageClass.SERVICE
    if label.startswith("marketing"):
        return MessageClass.MARKETING
    if "incident" in label or "critical" in label or "legal" in label:
        return MessageClass.INCIDENT
    if "privacy" in label:
        return MessageClass.PRIVACY
    if label.startswith("security"):
        return MessageClass.SECURITY
    return MessageClass.SERVICE


class FieldKind(StrEnum):
    TEXT = "text"
    REFERENCE = "reference"  # opaque ids and case numbers: no free text
    DATETIME = "datetime"  # *_local: rendered in the recipient's time zone
    MONEY = "money"  # *_formatted amounts
    CODE = "code"  # a one-time code
    URL_ROUTE = "url_route"  # trusted_*_url, secure_console_url: resolved from the allowlist
    URL_TOKEN = "url_token"  # single_use_*_url: allowlisted route plus a caller-supplied token


def field_kind(name: str) -> FieldKind:
    if name.startswith("single_use_") and name.endswith("_url"):
        return FieldKind.URL_TOKEN
    if name.endswith("_url"):
        return FieldKind.URL_ROUTE
    if name == "one_time_code":
        return FieldKind.CODE
    if name.endswith("_local"):
        return FieldKind.DATETIME
    if name.endswith("_formatted"):
        return FieldKind.MONEY
    if name.endswith("_reference"):
        return FieldKind.REFERENCE
    return FieldKind.TEXT


def max_length(name: str) -> int:
    """Longest value a merge field may take (§05: max-length limits)."""
    if name.startswith("approved_") or name.endswith("_summary"):
        return 1000
    return 200


@dataclass(frozen=True, slots=True)
class Action:
    label: str
    url_field: str


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    title: str
    event: str
    to: str
    class_label: str
    subject: str
    preheader: str
    paragraphs: tuple[str, ...]
    action: Action | None
    control: str | None
    message_class: MessageClass
    version: str

    @property
    def fields(self) -> frozenset[str]:
        """Every merge field the copy uses, including the action's destination."""
        found: set[str] = set()
        for text in (self.subject, self.preheader, *self.paragraphs):
            found.update(PLACEHOLDER.findall(text))
        if self.action:
            found.add(self.action.url_field)
        return frozenset(found)

    @property
    def url_fields(self) -> frozenset[str]:
        return frozenset(
            f for f in self.fields if field_kind(f) in (FieldKind.URL_ROUTE, FieldKind.URL_TOKEN)
        )


def _digest(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _build(raw: dict[str, Any]) -> Template:
    action = raw.get("action")
    return Template(
        id=raw["id"],
        title=raw["title"],
        event=raw["event"],
        to=raw["to"],
        class_label=raw["class_label"],
        subject=raw["subject"],
        preheader=raw["preheader"],
        paragraphs=tuple(raw["paragraphs"]),
        action=Action(action["label"], action["url_field"]) if action else None,
        control=raw.get("control"),
        message_class=classify(raw["id"], raw["class_label"]),
        version=f"{CATALOGUE_VERSION}.{_digest(raw)}",
    )


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Catalogue:
    templates: tuple[Template, ...]

    def get(self, template_id: str) -> Template:
        for t in self.templates:
            if t.id == template_id:
                return t
        raise CatalogueError(f"unknown template {template_id}")

    def by_event(self, event: str) -> tuple[Template, ...]:
        return tuple(t for t in self.templates if t.event == event)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.templates)

    def __len__(self) -> int:
        return len(self.templates)


def validate(templates: Iterable[Template]) -> None:
    """The copybook's own invariants. Raises `CatalogueError` naming the first template that breaks one."""
    seen: set[str] = set()
    for t in templates:
        if t.id in seen:
            raise CatalogueError(f"{t.id}: duplicate id")
        seen.add(t.id)
        if not re.fullmatch(r"ZID-EM-\d{3}", t.id):
            raise CatalogueError(f"{t.id}: id is not ZID-EM-###")
        if not t.subject or not t.preheader or not t.paragraphs:
            raise CatalogueError(f"{t.id}: subject, preheader and body are all required")
        if "one_time_code" in t.fields and t.id != OTP_TEMPLATE:
            raise CatalogueError(f"{t.id}: a one-time code is allowed only in {OTP_TEMPLATE}")
        if t.action and field_kind(t.action.url_field) not in (
            FieldKind.URL_ROUTE,
            FieldKind.URL_TOKEN,
        ):
            raise CatalogueError(f"{t.id}: the action does not point at a URL field")
        if "recipient_display_name" in t.fields:
            raise CatalogueError(f"{t.id}: the greeting is the wrapper's, not a body field")
        for name in t.fields:
            if field_kind(name) is FieldKind.URL_TOKEN and not t.action:
                raise CatalogueError(f"{t.id}: {name} appears without an action")


def parse(raw: Mapping[str, Any]) -> Catalogue:
    """Build and validate the catalogue from its decoded JSON (the file is read in infrastructure)."""
    templates = tuple(_build(t) for t in raw["templates"])
    validate(templates)
    return Catalogue(templates)
