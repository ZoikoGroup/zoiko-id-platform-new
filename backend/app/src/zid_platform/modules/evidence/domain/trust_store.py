"""The governed trust-store lifecycle, as pure rules (ZID-DTV-WF-01 section 13, screen O2; CR-107). Standard library only: no database, no certificates.

Importing a list of country signing CAs is not trusting it. A package moves

    STAGED --validate--> VALIDATED --approve--> APPROVED --activate--> ACTIVE --activate another--> SUPERSEDED
              \\--> REJECTED (the list did not verify)                     \\--rollback--> ROLLED_BACK (and a SUPERSEDED package is ACTIVE again)
                                                                          \\--retire--> RETIRED (the store then trusts nothing)

The rules, each with a test:
1. Only a package whose master list VERIFIED against the anchors the stager confirmed can be approved. A list that did not verify is REJECTED, never trusted.
2. The person who staged a package can neither approve nor activate it (two-person rule); a reject or a rollback needs a reason.
3. At most one package is ACTIVE. Activating another supersedes it. Rolling back re-activates the most recently active SUPERSEDED package (or a named one).
4. Once any package has ever been activated the store GOVERNS: the active set is the whole truth, and an empty active set (after a retire) trusts nothing.
   Before that, the deployment's csca folder is used as before, so nothing changes for a deployment that never opts in.
5. The manifest digest is over the sorted (country, fingerprint) pairs, so the same set always has the same digest, whatever order it arrived in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class TrustStoreError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class State(StrEnum):
    STAGED = "STAGED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"
    RETIRED = "RETIRED"


_GOVERNING = frozenset({State.ACTIVE, State.SUPERSEDED, State.ROLLED_BACK, State.RETIRED})
MAX_REASON = 500


@dataclass(frozen=True, slots=True)
class PackageRef:
    package_id: str
    seq: int
    state: State
    staged_by: str
    activated_at: str | None = None  # ISO timestamp, compared as text (same zone, same format)


def manifest_digest(certificates: Iterable[Mapping[str, str]]) -> str:
    """Over the sorted (country, fingerprint) pairs: the same set is the same digest in any order."""
    pairs = sorted({(str(c["country"]), str(c["fingerprint"])) for c in certificates})
    return hashlib.sha256("\n".join(f"{c}:{f}" for c, f in pairs).encode()).hexdigest()


def governs(states: Iterable[State]) -> bool:
    """True once any package has ever been activated for this tenant."""
    return any(s in _GOVERNING for s in states)


def _reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if len(text) < 3:
        raise TrustStoreError("REASON_REQUIRED", "a reason of at least three characters")
    if len(text) > MAX_REASON:
        raise TrustStoreError("REASON_TOO_LONG")
    return text


def require_validate(state: State) -> None:
    if state is not State.STAGED:
        raise TrustStoreError("INVALID_STATE", f"{state.value} cannot be validated")


def after_validation(
    list_status: str, certificate_count: int, *, authority_ok: bool = True
) -> State:
    """VERIFIED with at least one CA certificate, from an authority the policy accepts, is VALIDATED. Anything else is REJECTED: an unverified list is never a candidate."""
    return (
        State.VALIDATED
        if list_status == "VERIFIED" and certificate_count >= 1 and authority_ok
        else State.REJECTED
    )


def require_approve(state: State, *, staged_by: str, actor: str) -> None:
    if state is not State.VALIDATED:
        raise TrustStoreError("INVALID_STATE", f"{state.value} cannot be approved")
    if actor == staged_by:
        raise TrustStoreError("SELF_APPROVAL", "the person who staged a package cannot approve it")


def require_reject(state: State, reason: str | None) -> str:
    if state not in (State.STAGED, State.VALIDATED):
        raise TrustStoreError("INVALID_STATE", f"{state.value} cannot be rejected")
    return _reason(reason)


def require_activate(state: State, *, staged_by: str, actor: str) -> None:
    if state is not State.APPROVED:
        raise TrustStoreError(
            "INVALID_STATE", f"{state.value} cannot be activated; only an approved package can"
        )
    if actor == staged_by:
        raise TrustStoreError(
            "SELF_ACTIVATION", "the person who staged a package cannot activate it"
        )


def require_retire(state: State, reason: str | None) -> str:
    if state is not State.ACTIVE:
        raise TrustStoreError("NOTHING_TO_RETIRE", "there is no active package")
    return _reason(reason)


def plan_rollback(
    packages: Sequence[PackageRef], *, to_package_id: str | None, reason: str | None
) -> tuple[PackageRef, PackageRef, str]:
    """(current, target, reason). The current package becomes ROLLED_BACK and the target ACTIVE again.

    The target is a SUPERSEDED package (one that was approved and was once active): the most recently active by default, or the one named.
    A ROLLED_BACK, rejected or never-activated package is never a rollback target.
    """
    why = _reason(reason)
    current = next((p for p in packages if p.state is State.ACTIVE), None)
    if current is None:
        raise TrustStoreError("NOTHING_TO_ROLL_BACK", "there is no active package")
    candidates = [p for p in packages if p.state is State.SUPERSEDED and p.activated_at]
    if to_package_id is not None:
        target = next((p for p in candidates if p.package_id == to_package_id), None)
        if target is None:
            raise TrustStoreError(
                "ROLLBACK_TARGET_INVALID",
                "only a previously active, superseded package can be restored",
            )
    else:
        if not candidates:
            raise TrustStoreError("NO_PREVIOUS_PACKAGE", "no earlier package was ever active")
        target = max(candidates, key=lambda p: (p.activated_at or "", p.seq))
    return current, target, why


__all__ = [
    "MAX_REASON",
    "PackageRef",
    "State",
    "TrustStoreError",
    "after_validation",
    "governs",
    "manifest_digest",
    "plan_rollback",
    "require_activate",
    "require_approve",
    "require_reject",
    "require_retire",
    "require_validate",
]


# ---- which authority may publish trust material for which country (WF03 T02, T03, T38; CR-119)


@dataclass(frozen=True, slots=True)
class TrustSource:
    country: str
    authority: str  # the publishing host, as the security owner recorded it
    kind: str = "ROOT_LIST"


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """With `enforce` off the findings are advice on the package; with it on, a list carrying a country with no matching authority is REJECTED at validation. It ships off and empty: the owners record the authorities first."""

    enforce: bool = False
    sources: tuple[TrustSource, ...] = ()


def authority_of(uri: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(uri).hostname or "").lower()


def authority_problems(policy: SourcePolicy, countries: Iterable[str], uri: str) -> tuple[str, ...]:
    """For each country the list carries: accepted only when the publishing host is the authority recorded for that country. A bundle that merely claims to be from a country is not."""
    host = authority_of(uri)
    problems: list[str] = []
    for country in sorted(set(countries)):
        recorded = [s for s in policy.sources if s.country == country and s.kind == "ROOT_LIST"]
        if any(s.authority.lower() == host for s in recorded):
            continue
        problems.append(
            f"{'AUTHORITY_NOT_ALLOWLISTED' if recorded else 'NO_AUTHORITY_RECORDED_FOR_COUNTRY'}:{country}"
        )
    return tuple(problems)
