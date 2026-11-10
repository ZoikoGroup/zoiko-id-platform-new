"""Fill the attestor's approved digests from approved builds, so a digest is never typed twice (CR-064).

The registration file says which container may be which workload: its selectors (hand-written, operational) and the artifact digests it
may run. The digests used to be copied in by hand from wherever the build was approved. They now come from the platform's own record: a
workload's digest is listed only if an ACTIVE binding for it runs a build a second person approved. The file is still a reviewed change:
this plans the new content, a person reads the difference, and the file is committed like any other change to what an attestor may vouch for.

Nothing here imports the platform's modules (the attestor package must not), so it works on plain data: the registration file as parsed JSON
and a map of SPIFFE ID to the approved digests found for it.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_TENANT = re.compile(r"^spiffe://[^/]+/t/([0-9a-fA-F-]{36})(/|$)")


@dataclass(frozen=True, slots=True)
class Change:
    spiffe_id: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    registrations: dict[str, Any]
    changes: tuple[Change, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def tenant_of(spiffe_id: str) -> str | None:
    m = _TENANT.match(spiffe_id)
    return m.group(1).lower() if m else None


def plan(raw: Mapping[str, Any], approved: Mapping[str, frozenset[str]], *, tenant_id: str) -> Plan:
    """The registration file with each of one company's entries given exactly the approved digests of its active bindings.

    An entry that no active binding on an approved build backs is left as it is and reported: silently emptying it would be a surprise,
    and leaving it unreported would leave a stale approval no one is looking at. Entries of other companies are never touched."""
    out = copy.deepcopy(dict(raw))
    changes: list[Change] = []
    warnings: list[str] = []
    for w in out.get("workloads") or []:
        sid = str(w.get("spiffe_id", ""))
        if tenant_of(sid) != tenant_id.lower():
            continue
        want = approved.get(sid)
        have = {str(d) for d in w.get("approved_digests") or []}
        if not want:
            warnings.append(
                f"{sid}: no active binding runs an approved build, so its approved digests were left as they are "
                f"({len(have)} listed). Review it, or remove the entry if the workload is gone."
            )
            continue
        if set(want) == have:
            continue
        w["approved_digests"] = sorted(want)
        changes.append(Change(sid, tuple(sorted(set(want) - have)), tuple(sorted(have - set(want)))))
    return Plan(out, tuple(changes), tuple(warnings))
