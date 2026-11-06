"""Directory sync: what a company's directory says about its people, and what that means for the provisioning source. Pure domain, no I/O.

A provider (Auth0 today) turns its own user list into `DirectoryUser`s. Everything after that is provider-neutral: the same user shape, the same
comparison with what the source already holds, and the same safety limits, so a second provider adds only its own reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
INTERVAL_DEFAULT = 3600
FAILURE_BACKOFF_MAX = 6 * 3600
# Safety limits: a directory that suddenly lists nobody, or loses most of its people, is far more likely to be a mistake (wrong tenant, a revoked
# permission, a filter) than a real mass departure. Such a run stops before it deactivates anyone and waits for a person to look.
MASS_DEACTIVATION_FLOOR = 10
MASS_DEACTIVATION_SHARE = 0.3


class DirectoryError(Exception):
    """A sync that could not complete; `code` is what is recorded and shown (never provider text, which may carry names)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    external_id: str  # the provider's own id for the person (Auth0: `auth0|...`, the same value the provider puts in the sign-in `sub`)
    user_name: str
    email: str | None = None
    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    active: bool = True


def to_scim(u: DirectoryUser) -> dict[str, Any]:
    out: dict[str, Any] = {"schemas": [USER_SCHEMA], "userName": u.user_name, "externalId": u.external_id, "active": u.active}
    if u.display_name:
        out["displayName"] = u.display_name
    name = {k: v for k, v in (("givenName", u.given_name), ("familyName", u.family_name)) if v}
    if name:
        out["name"] = name
    if u.email:
        out["emails"] = [{"value": u.email, "primary": True}]
    return out


def _primary_email(user: Mapping[str, Any]) -> str | None:
    emails = user.get("emails") or []
    return next((e.get("value") for e in emails if e.get("primary")), None)


def differs(want: Mapping[str, Any], have: Mapping[str, Any]) -> bool:
    """Does the person the source holds need an update to match the directory?"""
    return (
        want["userName"] != have.get("userName")
        or want["active"] != have.get("active", True)
        or want.get("displayName") != have.get("displayName")
        or _primary_email(want) != _primary_email(have)
        or (want.get("name") or {}).get("givenName") != (have.get("name") or {}).get("givenName")
        or (want.get("name") or {}).get("familyName") != (have.get("name") or {}).get("familyName")
        or want["externalId"] != have.get("externalId")
    )


@dataclass(slots=True)
class Plan:
    create: list[dict[str, Any]] = field(default_factory=list)
    update: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (resource id, new state)
    same: int = 0
    deactivate: list[str] = field(default_factory=list)  # resource ids

    def counts(self) -> dict[str, int]:
        return {"created": len(self.create), "updated": len(self.update), "unchanged": self.same, "deactivated": len(self.deactivate)}


def plan(users: Sequence[DirectoryUser], existing: Sequence[Mapping[str, Any]]) -> Plan:
    """What to do so the source matches the directory. `existing` are the source's users as SCIM resources (with their `id`).

    A person is matched by the provider's id first, then by user name. People the directory no longer lists are deactivated, never deleted."""
    by_external = {r["externalId"]: r for r in existing if r.get("externalId")}
    by_name = {str(r["userName"]).lower(): r for r in existing}
    out = Plan()
    seen: set[str] = set()
    for u in users:
        want = to_scim(u)
        have = by_external.get(u.external_id) or by_name.get(u.user_name.lower())
        if have is None:
            out.create.append(want)
            continue
        seen.add(str(have["id"]))
        if differs(want, have):
            out.update.append((str(have["id"]), want))
        else:
            out.same += 1
    out.deactivate = [str(r["id"]) for r in existing if str(r["id"]) not in seen and r.get("active", True)]
    return out


def check_safe(directory_size: int, active_in_source: int, deactivations: int) -> None:
    """Refuse a run that would empty or gut the source (see the limits above); a person must look at it first."""
    if directory_size == 0 and active_in_source > 0:
        raise DirectoryError("EMPTY_DIRECTORY")
    if deactivations > max(MASS_DEACTIVATION_FLOOR, int(active_in_source * MASS_DEACTIVATION_SHARE)):
        raise DirectoryError("MASS_DEACTIVATION")


def next_delay(interval_seconds: int, consecutive_failures: int) -> int:
    """Seconds until the next run: the interval, stretched after failures (doubling, capped), so a broken credential is not hammered."""
    if consecutive_failures <= 0:
        return interval_seconds
    return min(interval_seconds * (2 ** min(consecutive_failures, 6)), FAILURE_BACKOFF_MAX)
