"""No placeholder security controls in the production build (ZID-VERIFY-MASTER-001 MPR-03).

"No security/privacy/authority control path contains TODO/mock/bypass behavior in the production build."
A static scan of everything that ships: unfinished-work markers, disabled certificate or signature checks,
test doubles imported by production code, and hard-coded credentials. Anything that must exist for local
development lives behind a named, refused-in-production switch and is listed in ALLOWED with the reason.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
SHIPPED = [BACKEND / "app" / "src", *sorted((BACKEND / "libs").glob("*/src"))]

# pattern -> why it is banned
BANNED: dict[str, str] = {
    # a quoted "XXX" is ISO 4217's "no currency" code, not a marker
    r"""(?<![\"'])\b(TODO|FIXME|XXX|HACK)\b(?![\"'])""": "unfinished-work marker",
    r"verify\s*=\s*False": "TLS certificate verification disabled",
    r"check_hostname\s*=\s*False": "TLS hostname check disabled",
    r"CERT_NONE": "TLS certificate verification disabled",
    r"ssl\s*=\s*False": "TLS disabled",
    r"from unittest(\.mock)? import|import unittest\.mock|from mock import|MagicMock\(": "test double in production code",
    # lower-case identifiers only: UPPER_CASE names such as GRANT_REFRESH_TOKEN are protocol constants
    # an identifier containing a sensitive word assigned a long literal that mixes letters and digits
    r"""\b[a-z_]*(?:password|secret|api_key|apikey|token)[a-z_]*\s*=\s*["'](?=[^"'\s]*\d)(?=[^"'\s]*[A-Za-z])[^"'\s]{12,}["']""": "hard-coded credential",
    r"(?i)\bbackdoor\b|\bskip_auth\b|\bdisable_auth\b|\bauth_bypass\b|\bbypass_auth\b": "authentication bypass",
    r"(?i)alg[\"']?\s*[:=]\s*[\"']none[\"']": "unsigned token algorithm accepted",
}

# (relative path, pattern) -> reason it is acceptable. Keep this list short and every entry justified.
ALLOWED: dict[tuple[str, str], str] = {}


def shipped_files() -> list[Path]:
    return [p for root in SHIPPED for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_scan_covers_the_shipped_code() -> None:
    files = shipped_files()
    assert len(files) > 400, "the scan should cover every shipped module"


def test_no_placeholder_or_bypass_controls_ship() -> None:
    found: list[str] = []
    for path in shipped_files():
        rel = path.relative_to(BACKEND).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, why in BANNED.items():
            for m in re.finditer(pattern, text):
                if (rel, pattern) in ALLOWED:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{rel}:{line}: {why}: {m.group(0)[:60]!r}")
    assert not found, "placeholder or bypass controls in shipped code:\n" + "\n".join(found[:40])
