"""Provider parity and registry drift (ZID-UAT-005 WF-04, WF-07; OPS-03, OPS-06). Pure: no I/O.

Two seams that need an outside source to be useful and are built so the source is the only missing part:

- parity: compare the normalized claims a candidate provider returned with a reference provider's, naming every difference and flagging a lower
  assurance. A material difference is never hidden to make failover look safe;
- registry drift: compare two snapshots of an official register of provider services (a service added, withdrawn, suspended, past its date, or on a
  new scheme version) and say which routes must be disabled and which cases' PASS no longer counts. Nothing here fetches a register.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

REGISTERED = "REGISTERED"


# ------------------------------------------------------------------------------------------------------------------------------- parity


def parity(
    reference: Mapping[str, Any], candidate: Mapping[str, Any], *, keys: Sequence[str]
) -> dict[str, Any]:
    """Which normalized claims agree. A key missing on one side is a difference, never ignored. `assurance` is compared as a number: lower is flagged."""
    equal: list[Any] = []
    different: list[Any] = []
    for k in keys:
        (
            equal
            if k in reference and k in candidate and reference[k] == candidate[k]
            else different
        ).append(k)
    ref_a, cand_a = reference.get("assurance"), candidate.get("assurance")
    lower = isinstance(ref_a, (int, float)) and (
        not isinstance(cand_a, (int, float)) or cand_a < ref_a
    )
    return {
        "equal": equal,
        "different": different,
        "assurance_lower": bool(lower),
        "failover_safe": not different and not lower,
        "explain": [
            f"{k}: reference {reference.get(k)!r}, candidate {candidate.get(k)!r}"
            for k in different
        ],
    }


# ---------------------------------------------------------------------------------------------------------------------------------- drift


@dataclass(frozen=True, slots=True)
class Service:
    provider_id: str
    service_code: str
    status: str  # REGISTERED | SUSPENDED | WITHDRAWN
    valid_until: date | None = None
    scheme_version: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider_id, self.service_code)


@dataclass(frozen=True, slots=True)
class Change:
    kind: str  # ADDED | REMOVED | STATUS_CHANGED | EXPIRED | SCHEME_VERSION_CHANGED
    provider_id: str
    service_code: str
    detail: str


def diff(
    previous: Mapping[tuple[str, str], Service],
    current: Mapping[tuple[str, str], Service],
    *,
    today: date,
) -> list[Change]:
    out: list[Change] = []
    for key, now in sorted(current.items()):
        before = previous.get(key)
        if before is None:
            out.append(Change("ADDED", *key, "new in the register"))
            continue
        if before.status != now.status:
            out.append(Change("STATUS_CHANGED", *key, f"{before.status} to {now.status}"))
        if before.scheme_version != now.scheme_version:
            out.append(
                Change(
                    "SCHEME_VERSION_CHANGED",
                    *key,
                    f"{before.scheme_version} to {now.scheme_version}",
                )
            )
        if (
            now.valid_until is not None
            and now.valid_until < today
            and (before.valid_until is None or before.valid_until >= today)
        ):
            out.append(Change("EXPIRED", *key, f"valid until {now.valid_until.isoformat()}"))
    for key in sorted(set(previous) - set(current)):
        out.append(Change("REMOVED", *key, "no longer in the register"))
    return out


def disable_actions(
    changes: Sequence[Change], current: Mapping[tuple[str, str], Service]
) -> list[tuple[str, str, str]]:
    """The exact-service routes to close: removed, no longer REGISTERED, or past their date. A new scheme version closes nothing by itself but is
    reported, because every PASS pinned to the old one must be repeated."""
    out = []
    for c in changes:
        if (
            c.kind in ("REMOVED", "EXPIRED")
            or c.kind == "STATUS_CHANGED"
            and current.get((c.provider_id, c.service_code), Service("", "", REGISTERED)).status
            != REGISTERED
        ):
            out.append((c.provider_id, c.service_code, c.kind))
    return out


def pins_to_expire(changes: Sequence[Change]) -> list[str]:
    """Pinned-input values that no longer describe the world, in the form a run pins them (`provider_certification`)."""
    return sorted({f"{c.provider_id}:{c.service_code}" for c in changes if c.kind != "ADDED"})
