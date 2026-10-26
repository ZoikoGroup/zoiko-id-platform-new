"""Tenant settings: areas, closed vocabularies and validation (ZID-UX-001 §19, §20). Pure domain, no I/O.

- AUTHENTICATION: a tenant may only make its sessions *shorter* than the platform's defaults, never
  longer (the platform ceiling is not the tenant's to raise).
- NOTIFICATIONS: a tenant may mute only non-mandatory notices; CRITICAL and HIGH can never be suppressed.
- BRANDING: presentation only, inside anti-spoofing rules: a tenant cannot name itself after the platform,
  use arbitrary colours, or point support at anything but an https URL.
Unknown fields are rejected, so a setting can never smuggle behaviour in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

AREAS = ("AUTHENTICATION", "NOTIFICATIONS", "BRANDING")

# Platform defaults (session/domain/session.py SessionPolicy); a tenant may only tighten them.
PLATFORM_ABSOLUTE_HOURS = 12
PLATFORM_IDLE_MINUTES = 30
MIN_ABSOLUTE_HOURS = 1
MIN_IDLE_MINUTES = 5

ACCENTS = ("blue", "teal", "green", "violet", "orange", "slate")
RESERVED_NAMES = re.compile(r"zoiko|official\s+support|platform\s+admin", re.IGNORECASE)
URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{2,5})?(/[^\s<>\"']*)?$")

DEFAULTS: dict[str, dict[str, Any]] = {
    "AUTHENTICATION": {"session_absolute_hours": None, "session_idle_minutes": None},
    "NOTIFICATIONS": {"muted_kinds": []},
    "BRANDING": {"display_name": "", "accent": "blue", "support_url": ""},
}


class SettingsError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _only(value: Mapping[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise SettingsError("VALIDATION_FAILED", f"unknown setting: {', '.join(extra)}")


def _bounded_int(value: Any, name: str, low: int, high: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError("VALIDATION_FAILED", f"{name} must be a whole number")
    if not low <= value <= high:
        raise SettingsError(
            "VALIDATION_FAILED",
            f"{name} must be between {low} and {high} (a tenant can only shorten the platform default)",
        )
    return value


def validate(
    area: str,
    value: Mapping[str, Any],
    *,
    mandatory_kinds: frozenset[str],
    known_kinds: frozenset[str],
) -> dict[str, Any]:
    """The normalized value for `area`, or SettingsError. Missing fields take their default."""
    if area not in AREAS:
        raise SettingsError("SETTINGS_AREA_UNKNOWN", area)
    out = dict(DEFAULTS[area])
    if area == "AUTHENTICATION":
        _only(value, set(out))
        hours = _bounded_int(
            value.get("session_absolute_hours"),
            "session_absolute_hours",
            MIN_ABSOLUTE_HOURS,
            PLATFORM_ABSOLUTE_HOURS,
        )
        idle = _bounded_int(
            value.get("session_idle_minutes"),
            "session_idle_minutes",
            MIN_IDLE_MINUTES,
            PLATFORM_IDLE_MINUTES,
        )
        if hours is not None and idle is not None and idle > hours * 60:
            raise SettingsError(
                "VALIDATION_FAILED", "the idle timeout cannot exceed the session lifetime"
            )
        out.update(session_absolute_hours=hours, session_idle_minutes=idle)
    elif area == "NOTIFICATIONS":
        _only(value, set(out))
        kinds = value.get("muted_kinds", [])
        if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
            raise SettingsError("VALIDATION_FAILED", "muted_kinds must be a list of alert kinds")
        unknown = sorted(set(kinds) - known_kinds)
        if unknown:
            raise SettingsError("VALIDATION_FAILED", f"unknown alert kind: {', '.join(unknown)}")
        locked = sorted(set(kinds) & mandatory_kinds)
        if locked:
            raise SettingsError(
                "NOTIFICATION_MANDATORY",
                f"mandatory security notices cannot be muted: {', '.join(locked)}",
            )
        out["muted_kinds"] = sorted(set(kinds))
    else:  # BRANDING
        _only(value, set(out))
        name = str(value.get("display_name", "")).strip()
        if name and not 2 <= len(name) <= 80:
            raise SettingsError("VALIDATION_FAILED", "the display name must be 2 to 80 characters")
        if any(not ch.isprintable() for ch in name):
            raise SettingsError("VALIDATION_FAILED", "the display name has control characters")
        if RESERVED_NAMES.search(name):
            raise SettingsError(
                "BRANDING_SPOOFING", "a tenant cannot present itself as the platform or its staff"
            )
        accent = value.get("accent", out["accent"])
        if accent not in ACCENTS:
            raise SettingsError("VALIDATION_FAILED", f"accent must be one of {', '.join(ACCENTS)}")
        url = str(value.get("support_url", "")).strip()
        if url and (len(url) > 200 or not URL.match(url)):
            raise SettingsError("VALIDATION_FAILED", "the support link must be an https:// URL")
        out.update(display_name=name, accent=accent, support_url=url)
    return out
