"""Stopping agents by scope, and saying plainly where the stop is enforced (ZID-CP-WF07 v2.0 .10; A07-08; CR-169). Pure domain, no I/O.

A stop denies NEW calls at the places that check. It cannot undo a call that already ran, and it cannot reach a place that is not told. So a stop carries its own report of where it is enforced, and that report says "not told"
where that is true. Nothing is called "all revoked" while any point is not acknowledged.

Scopes: one AGENT (and the agents below it); every agent a SPONSOR vouches for; every agent of one BLUEPRINT (or one version of it); the whole company (TENANT).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from zid_platform.modules.machine.domain.agents import MachineError

SCOPES = ("AGENT", "SPONSOR", "BLUEPRINT", "ISSUER", "TENANT")
REASON_MIN, REASON_MAX = 3, 300


def clean_reason(text: str | None) -> str:
    out = " ".join((text or "").split())
    if not REASON_MIN <= len(out) <= REASON_MAX:
        raise MachineError("REVOCATION_INVALID", "say why, in a few words: the person who closes this stop reads it")
    return out


def check_shape(scope: str, subject: str | None) -> tuple[str, str | None, str | None]:
    """(scope, subject, blueprint version). A TENANT stop names no subject; every other scope names exactly one thing; a blueprint may be 'name' or 'name@version'."""
    if scope not in SCOPES:
        raise MachineError("REVOCATION_INVALID", "a stop is for one AGENT, one SPONSOR, one BLUEPRINT, one outside ISSUER (or one of its keys) or the whole company (TENANT)")
    s = (subject or "").strip()
    if scope == "TENANT":
        if s:
            raise MachineError("REVOCATION_INVALID", "a company-wide stop names no subject")
        return scope, None, None
    if not s or len(s) > 200:
        raise MachineError("REVOCATION_INVALID", f"a {scope.lower()} stop names its {'agent' if scope == 'AGENT' else 'sponsor' if scope == 'SPONSOR' else 'blueprint'}")
    if scope == "ISSUER":
        issuer, _, kid = s.partition("#")
        if not issuer.startswith("https://") or ("#" in s and not kid):
            raise MachineError("REVOCATION_INVALID", "an issuer stop names an https issuer, or issuer#key-id for one of its keys")
        return scope, issuer, kid or None
    if scope == "BLUEPRINT" and "@" in s:
        name, _, version = s.partition("@")
        if not name or not version:
            raise MachineError("REVOCATION_INVALID", "a blueprint is 'name' or 'name@version'")
        return scope, name, version
    return scope, s, None


def enforcement_report(servers: Iterable[str], epoch: int) -> dict[str, Any]:
    """Where the stop holds, kept apart as acknowledged, pending, failed and unsupported. This build has one enforcement point it can vouch for, the tool gateway, because it reads each agent's committed state on every
    call. A tool server is never told to stop work it began, so it is listed as unsupported, and the stop is not described as complete everywhere."""
    acknowledged = [
        {
            "point": "tool_gateway", "epoch": epoch,
            "how": "reads each agent's committed status, its parents, its sponsor and every freeze on every authorize and every redeem, so no new call passes after the stop commits",
        }
    ]  # fmt: skip
    unsupported = [
        {"point": f"tool_server:{sid}", "reason": "this server is not told to stop work it already began. A grant it holds is checked again when it is redeemed, and no new grant is signed."}
        for sid in sorted(set(servers))
    ]
    return {"acknowledged": acknowledged, "pending": [], "failed": [], "unsupported": unsupported, "enforced_everywhere": not unsupported, "epoch": epoch}


def closing_problems(*, actor: str, event: Mapping[str, Any]) -> list[str]:
    """A stop is closed once, by someone other than the person who made it."""
    out: list[str] = []
    if event.get("closed_at") is not None:
        out.append("REVOCATION_ALREADY_CLOSED")
    if str(event["actor"]) == actor:
        out.append("REVOCATION_CLOSE_NOT_INDEPENDENT")
    return out


def reinstate_problems(*, actor: str, open_events: Sequence[Mapping[str, Any]], initiators: Iterable[str]) -> list[str]:
    """An agent stopped by a recorded stop waits for that stop to be closed by someone else, and is never reinstated by the person who stopped it."""
    out: list[str] = []
    if open_events:
        out.append("REVOCATION_OPEN")
    if actor in set(initiators):
        out.append("REVOCATION_REINSTATE_NOT_INDEPENDENT")
    return out
