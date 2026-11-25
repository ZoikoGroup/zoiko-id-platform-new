"""The partner API version catalog and its stability rules (ZID-CP-WF10 v2.0, WF-10.05; CR-173). Pure domain, no I/O.

A partner builds against a version. These rules keep that version stable:

- a release names an immutable snapshot by its digest. The digest in the register must equal the digest of the file, so a snapshot cannot be edited in place;
- exactly one release is CURRENT, and it is the newest;
- a breaking release must raise the major version, and the major before it must be given a support window of at least 180 days from the day the deprecation was announced
  (an announcement date and a supported-until date are both required, and both are checked);
- every release has at least one changelog line, and a breaking one has a migration note;
- the snapshot of the CURRENT release must equal what the code publishes (checked in a test), so a code change that is not catalogued fails the build.

Nothing here publishes a schema; it judges the register that does.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

STATES = ("CURRENT", "SUPPORTED", "DEPRECATED", "RETIRED")
MIN_SUPPORT = timedelta(days=180)


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    major: int
    released: date
    state: str
    breaking: bool
    snapshot: str
    digest: str
    supported_until: date | None
    announced: date | None
    changelog: tuple[str, ...]
    migration: str | None


def _d(v: Any) -> date | None:
    return v if isinstance(v, date) else (date.fromisoformat(str(v)) if v else None)


def parse(raw: Mapping[str, Any]) -> Release:
    return Release(
        version=str(raw["version"]), major=int(raw["major"]), released=_d(raw["released"]) or date.min, state=str(raw["state"]), breaking=bool(raw.get("breaking", False)),
        snapshot=str(raw["snapshot"]), digest=str(raw["digest"]), supported_until=_d(raw.get("supported_until")), announced=_d(raw.get("announced")),
        changelog=tuple(str(x) for x in raw.get("changelog") or ()), migration=raw.get("migration"),
    )  # fmt: skip


def digest_of(text: str) -> str:
    return "sha-256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_problems(releases: Sequence[Release]) -> list[str]:
    """Everything wrong with the register itself. Empty means it is consistent."""
    out: list[str] = []
    if not releases:
        return ["NO_RELEASES"]
    if len({r.version for r in releases}) != len(releases):
        out.append("DUPLICATE_VERSION")
    ordered = sorted(releases, key=lambda r: (r.released, r.version))
    current = [r for r in releases if r.state == "CURRENT"]
    if len(current) != 1:
        out.append("EXACTLY_ONE_RELEASE_MUST_BE_CURRENT")
    elif current[0] is not ordered[-1]:
        out.append("THE_CURRENT_RELEASE_MUST_BE_THE_NEWEST")
    for r in releases:
        if r.state not in STATES:
            out.append(f"STATE_UNKNOWN:{r.version}")
        if not r.changelog:
            out.append(f"CHANGELOG_MISSING:{r.version}")
        if r.breaking and not r.migration:
            out.append(f"BREAKING_RELEASE_WITHOUT_A_MIGRATION_NOTE:{r.version}")
        if r.state == "DEPRECATED":
            if r.supported_until is None or r.announced is None:
                out.append(f"DEPRECATED_WITHOUT_DATES:{r.version}")
            elif r.supported_until - r.announced < MIN_SUPPORT:
                out.append(f"SUPPORT_WINDOW_UNDER_180_DAYS:{r.version}")
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if later.breaking and later.major <= earlier.major:
            out.append(f"BREAKING_CHANGE_WITHOUT_A_NEW_MAJOR:{later.version}")
        if later.breaking and earlier.state == "SUPPORTED" and earlier.supported_until is None:
            out.append(f"PREVIOUS_MAJOR_HAS_NO_SUPPORT_WINDOW:{earlier.version}")
    return sorted(set(out))


def snapshot_problems(release: Release, text: str | None) -> list[str]:
    """A snapshot that is missing, or whose digest no longer matches the register, has been edited."""
    if text is None:
        return [f"SNAPSHOT_MISSING:{release.version}"]
    if digest_of(text) != release.digest:
        return [f"SNAPSHOT_CHANGED_AFTER_RELEASE:{release.version}"]
    return []


def secret_problems(text: str) -> list[str]:
    """A schema example must not carry a live-looking secret, a private key or a real-looking credential."""
    markers = ("-----BEGIN", "client_secret\":\"zsk_", "AKIA", "sk_live_", "xoxb-")
    return [f"EXAMPLE_LOOKS_LIKE_A_SECRET:{m}" for m in markers if m in text]


def view(releases: Sequence[Release]) -> dict[str, Any]:
    return {
        "releases": [
            {"version": r.version, "major": r.major, "released": r.released.isoformat(), "state": r.state, "breaking": r.breaking, "digest": r.digest, "snapshot_url": f"/v1/verify-api/versions/{r.version}/openapi.json",
             "supported_until": r.supported_until.isoformat() if r.supported_until else None, "changelog": list(r.changelog), "migration": r.migration}
            for r in sorted(releases, key=lambda r: (r.released, r.version), reverse=True)
        ],
        "policy": "A published snapshot never changes. A change that breaks callers is a new major version, and the one before it keeps working for at least 180 days after it is deprecated.",
    }  # fmt: skip


def load_json(text: str) -> dict[str, Any]:
    return json.loads(text)  # type: ignore[no-any-return]
