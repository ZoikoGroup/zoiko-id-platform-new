"""Phone, email and device signals, and the link graph between people (ZID-GAP-001 v2.0 DTV-20, DTV-21, DTV-22; CR-098). Pure domain.

What is real and checkable without a data provider is here: the shape of a phone number and an email address, the hashing that lets two records be linked
without keeping the raw value, and a deterministic graph that says "these people share an identifier". What needs a provider is NOT here and not pretended:
whether a number is live, who owns it, whether an email address is deliverable, how risky a device is. `SignalProvider` is the interface a provider adapter
will fill; until one exists every provider answer is UNAVAILABLE.

The rules that hold whatever provider is attached:
- a signal is a reason for a person to look, never a verdict. Nothing here says FRAUD, and a graph link is never treated as proof two people are the same
  or are acting together (an office phone, a family device and a shared flat are all ordinary);
- raw identifiers do not leave this module's inputs: results carry a keyed hash, with the tenant and the identifier kind inside the key, so the same phone
  number in two companies never links across them;
- an identifier shared by very many people is a HUB (a switchboard, a library computer, a public wifi) and is ignored as evidence, not scored;
- shape only: a well-formed number is not a reachable one, and a well-formed address is not a deliverable one. Results say so.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

MAX_FANOUT = 20  # an identifier seen on more than this many people is a hub and says nothing about any of them
DEVICE_SHARED_MIN = (
    5  # this many different people on one device is worth a look (a family is 2 to 4)
)
ROLE_LOCALPARTS = frozenset(
    {"admin", "administrator", "info", "contact", "sales", "support", "help", "office", "hello", "billing", "accounts", "noreply", "no-reply", "postmaster", "webmaster"}
)  # fmt: skip
E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
LOCAL = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class SignalError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Kind(StrEnum):
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DEVICE = "DEVICE"
    ADDRESS = "ADDRESS"
    PAYMENT = "PAYMENT"


# ------------------------------------------------------------------------------------------------------------------- the interface


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    available: bool
    risk: str | None = (
        None  # LOW | MEDIUM | HIGH as the provider states it, passed through; this module never invents one
    )
    reasons: tuple[str, ...] = ()
    provider: str = ""


class SignalProvider(Protocol):
    """What a phone, email or device intelligence provider adapter implements. It receives the NORMALIZED value and answers; it is the only place a
    network call may happen, and it must fail by returning `ProviderAnswer(available=False)`, never by raising into a decision."""

    def check(self, kind: Kind, normalized: str, *, country: str | None) -> ProviderAnswer: ...


class NoProvider:
    """The provider that ships: there is none, and it says so."""

    def check(self, kind: Kind, normalized: str, *, country: str | None) -> ProviderAnswer:
        return ProviderAnswer(False, None, ("NO_PROVIDER_CONFIGURED",), "")


def answer_from(
    provider: SignalProvider | None, kind: Kind, normalized: str, *, country: str | None = None
) -> ProviderAnswer:
    """A provider that raises is an unavailable provider. The error is never allowed to become a decision."""
    try:
        return (provider or NoProvider()).check(kind, normalized, country=country)
    except Exception:  # noqa: BLE001 - an intelligence provider that fails is unavailable, never a pass and never a fail
        return ProviderAnswer(False, None, ("PROVIDER_FAILED",), "")


# ------------------------------------------------------------------------------------------------------------------- hashing


def identifier_hash(value: str, *, tenant_id: str, kind: Kind, pepper: bytes) -> str:
    """A keyed hash of a normalized identifier. The tenant and the kind are in the key, so the same value never links across companies or kinds."""
    if not pepper:
        raise SignalError(
            "PEPPER_REQUIRED", "a hash without a secret key can be reversed by guessing"
        )
    key = hashlib.sha256(pepper + b"|" + tenant_id.encode() + b"|" + kind.value.encode()).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------------------------------------------------------- phone and email


@dataclass(frozen=True, slots=True)
class Phone:
    e164: str
    notes: tuple[str, ...] = ("SHAPE_ONLY_NOT_REACHABILITY",)


def normalize_phone(raw: str) -> Phone:
    """E.164 shape: a plus, then 8 to 15 digits, the first not zero. Spaces, dots, dashes and brackets are formatting and are dropped. Anything else is refused."""
    text = (raw or "").strip()
    if not re.fullmatch(r"\+?[0-9 .\-()]{6,30}", text):
        raise SignalError("PHONE_INVALID", "characters")
    digits = re.sub(r"[^0-9+]", "", text)
    if not digits.startswith("+"):
        raise SignalError(
            "PHONE_NEEDS_COUNTRY_CODE", "write the number with its + and country code"
        )
    if not E164.match(digits):
        raise SignalError("PHONE_INVALID", "length or leading zero")
    return Phone(digits)


@dataclass(frozen=True, slots=True)
class Email:
    normalized: str
    domain: str
    role_account: bool
    plus_addressed: bool
    disposable: bool
    notes: tuple[str, ...] = ("SHAPE_ONLY_NOT_DELIVERABILITY",)


def normalize_email(raw: str, *, disposable_domains: frozenset[str] = frozenset()) -> Email:
    """A simplified RFC 5322 shape check. The domain is lower-cased; the local part is kept as written (it can be case-sensitive). `disposable_domains` is a
    list the operators supply: none ships, so nothing is flagged disposable until someone provides a list."""
    text = (raw or "").strip()
    if text.count("@") != 1 or len(text) > 254:
        raise SignalError("EMAIL_INVALID", "shape")
    local, domain = text.split("@")
    domain = domain.lower().rstrip(".")
    labels = domain.split(".")
    if (
        not local
        or len(local) > 64
        or not LOCAL.match(local)
        or len(labels) < 2
        or not all(LABEL.match(x) for x in labels)
        or labels[-1].isdigit()
    ):
        raise SignalError("EMAIL_INVALID", "shape")
    base = local.split("+", 1)[0].lower()
    return Email(
        f"{local}@{domain}",
        domain,
        base in ROLE_LOCALPARTS,
        "+" in local,
        domain in disposable_domains,
    )


# ------------------------------------------------------------------------------------------------------------------- devices and links


@dataclass(frozen=True, slots=True)
class Observation:
    """One person (a principal or case reference, opaque here) seen with one identifier, already hashed."""

    subject: str
    kind: Kind
    identifier: str


@dataclass(frozen=True, slots=True)
class LinkSignal:
    subjects: tuple[str, ...]
    shared: tuple[tuple[str, int], ...]  # (kind, how many different identifiers connect them)
    strength: str  # WEAK | MODERATE | STRONG: how many different kinds of identifier connect the group, nothing more
    recommendation: str = "REVIEW"  # always: a person looks. There is no other value.
    notes: tuple[str, ...] = ("A LINK IS NOT PROOF OF THE SAME PERSON OR OF ACTING TOGETHER",)


@dataclass(frozen=True, slots=True)
class DeviceSignal:
    device: str
    subjects: tuple[str, ...]
    code: str = "DEVICE_SHARED_BY_MANY"
    recommendation: str = "REVIEW"


def shared_devices(
    observations: Iterable[Observation], *, minimum: int = DEVICE_SHARED_MIN
) -> list[DeviceSignal]:
    """Devices seen with at least `minimum` different people, but fewer than the hub limit (a device seen with a hundred people is a kiosk, not a signal)."""
    by_device: dict[str, set[str]] = defaultdict(set)
    for o in observations:
        if o.kind is Kind.DEVICE:
            by_device[o.identifier].add(o.subject)
    return sorted(
        (
            DeviceSignal(d, tuple(sorted(s)))
            for d, s in by_device.items()
            if minimum <= len(s) <= MAX_FANOUT
        ),
        key=lambda x: (x.device, x.subjects),
    )


def link_graph(
    observations: Iterable[Observation], *, max_fanout: int = MAX_FANOUT
) -> list[LinkSignal]:
    """Groups of people connected by shared identifiers. Deterministic: the same observations always give the same groups, in the same order.

    A hub identifier (seen on more than `max_fanout` people) connects nobody. A group is only as strong as the number of DIFFERENT KINDS of identifier that
    connect it, because one shared phone is ordinary and a shared phone, device and address together is worth a look."""
    seen: dict[tuple[Kind, str], set[str]] = defaultdict(set)
    for o in observations:
        seen[(o.kind, o.identifier)].add(o.subject)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(
                ra, rb
            )  # the smaller id is the root, so the result does not depend on input order

    usable = {key: people for key, people in seen.items() if 2 <= len(people) <= max_fanout}
    for people in usable.values():
        ordered = sorted(people)
        for other in ordered[1:]:
            union(ordered[0], other)
    groups: dict[str, set[str]] = defaultdict(set)
    for people in usable.values():
        for p in people:
            groups[find(p)].add(p)
    out: list[LinkSignal] = []
    for members in groups.values():
        kinds: dict[str, set[str]] = defaultdict(set)
        for (kind, ident), people in usable.items():
            if people & members:
                kinds[kind.value].add(ident)
        shared = tuple(sorted((k, len(v)) for k, v in kinds.items()))
        strength = "STRONG" if len(shared) >= 3 else "MODERATE" if len(shared) == 2 else "WEAK"
        out.append(LinkSignal(tuple(sorted(members)), shared, strength))
    return sorted(out, key=lambda s: (s.subjects[0], len(s.subjects)))


def describe(signals: Sequence[LinkSignal | DeviceSignal]) -> Mapping[str, object]:
    """A short summary for a reviewer: counts and strengths, never an identifier."""
    links = [s for s in signals if isinstance(s, LinkSignal)]
    return {
        "link_groups": len(links),
        "people_in_groups": sum(len(s.subjects) for s in links),
        "strong": sum(1 for s in links if s.strength == "STRONG"),
        "device_flags": sum(1 for s in signals if isinstance(s, DeviceSignal)),
        "recommendation": "REVIEW" if signals else "NONE",
    }


@dataclass(frozen=True, slots=True)
class Assessment:
    """What one identifier gives, from shape and from the provider if there is one. Never a verdict."""

    kind: Kind
    shape_ok: bool
    flags: tuple[str, ...] = ()
    provider: ProviderAnswer = field(
        default_factory=lambda: ProviderAnswer(False, None, ("NO_PROVIDER_CONFIGURED",), "")
    )


def assess_phone(raw: str, *, provider: SignalProvider | None = None) -> Assessment:
    try:
        phone = normalize_phone(raw)
    except SignalError as e:
        return Assessment(Kind.PHONE, False, (e.code,))
    return Assessment(Kind.PHONE, True, phone.notes, answer_from(provider, Kind.PHONE, phone.e164))


def assess_email(
    raw: str,
    *,
    provider: SignalProvider | None = None,
    disposable_domains: frozenset[str] = frozenset(),
) -> Assessment:
    try:
        mail = normalize_email(raw, disposable_domains=disposable_domains)
    except SignalError as e:
        return Assessment(Kind.EMAIL, False, (e.code,))
    flags = list(mail.notes)
    if mail.role_account:
        flags.append("ROLE_ACCOUNT")
    if mail.disposable:
        flags.append("DISPOSABLE_DOMAIN")
    return Assessment(
        Kind.EMAIL, True, tuple(flags), answer_from(provider, Kind.EMAIL, mail.normalized)
    )
