"""Registration entries: which running thing is allowed to be which workload (platform-owned, like SPIRE entries).

An entry says: the runtime object matching these selectors is the workload `spiffe_id`, in `environment_id`, and the
artifacts it may run are `approved_digests`. The attestor trusts nothing a workload sends; it trusts this file and what it
observes. The file is owned by the platform team and changed by review, never by a workload."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SPIFFE = re.compile(r"^spiffe://[a-z0-9._-]{1,255}/t/[0-9a-f-]{36}/.+$")
_ENVIRONMENT = re.compile(r"^[a-z0-9-]{1,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Entry:
    spiffe_id: str
    environment_id: str
    selectors: dict[str, str]
    approved_digests: frozenset[str]
    max_level: int = 2  # an entry can lower, never raise, what this attestor will vouch for


def parse(raw: dict[str, Any]) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    for i, w in enumerate(raw.get("workloads") or []):
        where = f"workloads[{i}]"
        sid = str(w.get("spiffe_id", ""))
        if not _SPIFFE.match(sid):
            raise RegistrationError(
                f"{where}: spiffe_id must be spiffe://<domain>/t/<tenant uuid>/..."
            )
        if sid in seen:
            raise RegistrationError(f"{where}: {sid} is registered twice")
        seen.add(sid)
        env = str(w.get("environment_id", ""))
        if not _ENVIRONMENT.match(env):
            raise RegistrationError(f"{where}: environment_id is not valid")
        selectors = w.get("selectors")
        if not isinstance(selectors, dict) or not selectors:
            raise RegistrationError(f"{where}: at least one selector is required")
        digests = frozenset(str(d) for d in w.get("approved_digests") or [])
        if any(not _DIGEST.match(d) for d in digests):
            raise RegistrationError(f"{where}: approved_digests must be sha256:<64 hex>")
        level = int(w.get("max_level", 2))
        if not 1 <= level <= 3:
            raise RegistrationError(f"{where}: max_level must be 1 to 3")
        entries.append(
            Entry(sid, env, {str(k): str(v) for k, v in selectors.items()}, digests, level)
        )
    if not entries:
        raise RegistrationError("no workloads are registered")
    return entries


def load(path: Path) -> list[Entry]:
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        if isinstance(e, RegistrationError):
            raise
        raise RegistrationError(f"cannot read {path}: {e}") from e
