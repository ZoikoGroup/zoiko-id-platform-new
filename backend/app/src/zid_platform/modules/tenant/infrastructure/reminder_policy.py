"""When an approval request about to expire is reminded about (`backend/config/approvals/reminder-policy.yaml`; slice L-13, CR-053)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml


@dataclass(frozen=True, slots=True)
class ReminderPolicy:
    version: str
    enabled: bool
    windows_hours: tuple[int, ...]


@lru_cache(maxsize=1)
def reminder_policy() -> ReminderPolicy:
    doc = _yaml(CONFIG / "approvals" / "reminder-policy.yaml")
    windows = tuple(sorted({int(h) for h in doc.get("windows_hours") or ()}, reverse=True))
    if any(h <= 0 for h in windows):
        raise ValueError("a reminder window is a positive number of hours")
    return ReminderPolicy(str(doc.get("version", "")), bool(doc.get("enabled", False)), windows)
