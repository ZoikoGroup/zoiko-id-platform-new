"""Biometric asset classes and the retention rule a biometric method obeys (ZID-DTV-WF-04 B05, BIO-08; QA-021; CR-108). Pure: no I/O.

`config/privacy/biometric-asset-classes.yaml` lists the classes (live selfie, face template, match result, liveness capture, liveness result) and
which method produces each. A method is not available to a tenant while any class it produces has no ACTIVE retention policy for the purpose: "blank
retention => route OFF". The periods are the privacy officer's and the controller's, never set here.

Erasure (B05, CR-108): each class has a tier, and an erasure disposes in the order SOURCE (raw media), DERIVED (templates), RESULT (scores), then
other personal data; providers' copies follow, and backups are attested last, only once nothing else is outstanding. A class counts as purged only
when every task that covers it, and the backups, are acknowledged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

CLASS_ID = re.compile(r"^biometric\.[a-z][a-z0-9_]{1,62}$")
METHODS = frozenset({"FACE_MATCH", "LIVENESS"})
TIERS = ("SOURCE", "DERIVED", "RESULT")  # the deletion order


class AssetClassError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssetClass:
    class_id: str
    methods: frozenset[str]
    tier: str = "SOURCE"


def parse(doc: Mapping[str, Any]) -> tuple[AssetClass, ...]:
    """The classes in the file. A malformed entry refuses the whole file: a method must never look covered because its class was dropped."""
    out: dict[str, AssetClass] = {}
    for raw in doc.get("classes") or []:
        cid = str(raw.get("id", ""))
        methods = frozenset(str(m) for m in raw.get("methods") or ())
        tier = str(raw.get("tier", ""))
        if not CLASS_ID.match(cid):
            raise AssetClassError(f"class id {cid!r} is not biometric.<name>")
        if not methods or not methods <= METHODS:
            raise AssetClassError(f"{cid}: methods must be among {sorted(METHODS)}")
        if tier not in TIERS:
            raise AssetClassError(f"{cid}: tier must be one of {list(TIERS)}")
        if cid in out:
            raise AssetClassError(f"{cid}: listed twice")
        out[cid] = AssetClass(cid, methods, tier)
    for m in METHODS:
        if not any(m in c.methods for c in out.values()):
            raise AssetClassError(f"no class is produced by {m}")
    return tuple(out[k] for k in sorted(out))


def required(classes: Iterable[AssetClass], method: str) -> tuple[str, ...]:
    return tuple(sorted(c.class_id for c in classes if method in c.methods))


def missing(classes: Iterable[AssetClass], method: str, covered: Iterable[str]) -> tuple[str, ...]:
    """The classes this method produces that no ACTIVE retention policy covers."""
    have = set(covered)
    return tuple(c for c in required(classes, method) if c not in have)


# ----------------------------------------------------------------------------------------------------------------------------- erasure


def deletion_rank(classes: Iterable[AssetClass], object_type: str) -> int:
    """Where an object of this type falls in the deletion order: its class's tier, and every other personal data after the biometric classes."""
    for c in classes:
        if c.class_id == object_type:
            return TIERS.index(c.tier)
    return len(TIERS)


def class_of(classes: Iterable[AssetClass], object_type: str) -> str | None:
    return next((c.class_id for c in classes if c.class_id == object_type), None)


def backup_problem(other_task_states: Iterable[str]) -> str | None:
    """Backups are attested last: refused while any first-party or provider task is still PENDING or FAILED (a HELD one is settled: it is kept on
    purpose and stays held in the backups too)."""
    return (
        "BACKUP_BEFORE_SOURCES"
        if any(s in ("PENDING", "FAILED") for s in other_task_states)
        else None
    )


def class_summary(tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per biometric class in the job: the tasks covering it and its state. The backups cover every class. PURGED only when every one of those
    tasks is ACKNOWLEDGED; HELD when any is held; FAILED when any failed; otherwise PENDING."""
    rows = list(tasks)
    backups = [t for t in rows if t.get("target_kind") == "BACKUP"]
    seen: dict[str, list[Mapping[str, Any]]] = {}
    for t in rows:
        for c in t.get("asset_classes") or ():
            seen.setdefault(str(c), []).append(t)
    out = []
    for c in sorted(seen):
        covering = [*seen[c], *backups]
        states = [str(t["state"]) for t in covering]
        state = (
            "HELD"
            if "HELD" in states
            else "FAILED"
            if "FAILED" in states
            else "PURGED"
            if all(s == "ACKNOWLEDGED" for s in states)
            else "PENDING"
        )
        out.append(
            {
                "asset_class": c,
                "state": state,
                "tasks": len(covering),
                "outstanding": [
                    {
                        "target_kind": t["target_kind"],
                        "target_ref": t["target_ref"],
                        "state": t["state"],
                    }
                    for t in covering
                    if t["state"] != "ACKNOWLEDGED"
                ],
            }
        )
    return out
