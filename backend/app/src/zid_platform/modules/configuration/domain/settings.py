"""Tenant settings: areas, closed vocabularies and validation (ZID-UX-001 §19, §20). Pure domain, no I/O.

- AUTHENTICATION: a tenant may only make its sessions *shorter* than the platform's defaults, never
  longer (the platform ceiling is not the tenant's to raise).
- NOTIFICATIONS: a tenant may mute only non-mandatory notices; CRITICAL and HIGH can never be suppressed.
- BRANDING: presentation only, inside anti-spoofing rules: a tenant cannot name itself after the platform,
  use arbitrary colours, or point support at anything but an https URL.
Unknown fields are rejected, so a setting can never smuggle behaviour in.
"""

from __future__ import annotations

import base64
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
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_CONTRAST = 4.5  # WCAG AA for the white text on a button of the company's colour
LOGO_MAX_BYTES = 48 * 1024
LOGO_PREFIXES = {"data:image/png;base64,": b"\x89PNG\r\n\x1a\n", "data:image/jpeg;base64,": b"\xff\xd8\xff", "data:image/webp;base64,": b"RIFF"}
CUSTOM_THEME_FIELDS = ("accent_hex", "welcome_text", "logo")  # beyond the fixed accents: a plan feature
URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{2,5})?(/[^\s<>\"']*)?$")

DEFAULTS: dict[str, dict[str, Any]] = {
    "AUTHENTICATION": {"session_absolute_hours": None, "session_idle_minutes": None},
    "NOTIFICATIONS": {"muted_kinds": []},
    "BRANDING": {"display_name": "", "accent": "blue", "support_url": "", "accent_hex": "", "welcome_text": "", "logo": ""},
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


def contrast_with_white(hex_colour: str) -> float:
    """WCAG contrast ratio of a #rrggbb colour against white."""

    def channel(i: int) -> float:
        c = int(hex_colour[1 + 2 * i : 3 + 2 * i], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
    return 1.05 / (luminance + 0.05)


def _logo(raw: str) -> str:
    """An inline PNG, JPEG or WebP (never SVG, which can carry script) of at most 48 KB, so nothing is fetched from anywhere when a page shows it."""
    if not raw:
        return ""
    for prefix, magic in LOGO_PREFIXES.items():
        if raw.startswith(prefix):
            try:
                data = base64.b64decode(raw[len(prefix) :], validate=True)
            except ValueError as e:
                raise SettingsError("VALIDATION_FAILED", "the logo is not valid image data") from e
            if len(data) > LOGO_MAX_BYTES:
                raise SettingsError("VALIDATION_FAILED", "the logo must be at most 48 KB")
            if not data.startswith(magic):
                raise SettingsError("VALIDATION_FAILED", "the logo is not the kind of image it says it is")
            return raw
    raise SettingsError("VALIDATION_FAILED", "the logo must be a PNG, JPEG or WebP image")


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
        hex_colour = str(value.get("accent_hex", "")).strip()
        if hex_colour:
            if not HEX.match(hex_colour):
                raise SettingsError("VALIDATION_FAILED", "the brand colour must look like #1a73e8")
            if contrast_with_white(hex_colour) < MIN_CONTRAST:
                raise SettingsError("VALIDATION_FAILED", "the brand colour is too light for white text on it; choose a darker one")
            hex_colour = hex_colour.lower()
        welcome = str(value.get("welcome_text", "")).strip()
        if len(welcome) > 160 or any(not ch.isprintable() for ch in welcome) or "<" in welcome or ">" in welcome:
            raise SettingsError("VALIDATION_FAILED", "the welcome text is at most 160 plain characters")
        if RESERVED_NAMES.search(welcome):
            raise SettingsError("BRANDING_SPOOFING", "a tenant cannot present itself as the platform or its staff")
        out.update(display_name=name, accent=accent, support_url=url, accent_hex=hex_colour, welcome_text=welcome, logo=_logo(str(value.get("logo", ""))))
    return out
