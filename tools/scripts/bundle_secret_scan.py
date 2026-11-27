#!/usr/bin/env python3
"""Scan what a browser downloads for secrets that must never be there (ZID-CP-WF10 v2.0, WF-10.04, row 4-05; CR-173).

    bundle_secret_scan.py <dir> [<dir> ...]

Everything under the given folders is read as bytes (JavaScript, source maps, HTML, JSON, text). A finding names the file, the line and the kind of secret, and NEVER prints the value:
a scan result that quoted the secret would put it in a CI log. Exit code 0 means nothing was found; 1 means at least one finding; 2 means a folder could not be read, or held no files
at all (an empty scan proves nothing, so it fails).

What it looks for: Verify API client secrets, access tokens and launch links (zps_, zat_, zvl_), private key blocks, payment-provider secret keys and webhook secrets, cloud access keys,
and a literal client secret assigned to a name. It is a pattern scan, not proof that no secret exists in a different shape.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("verify-api-client-secret", re.compile(r"(?<![A-Za-z0-9])zps_[A-Za-z0-9_-]{16,}")),
    ("verify-api-access-token", re.compile(r"(?<![A-Za-z0-9])zat_[A-Za-z0-9_-]{16,}")),
    ("verify-api-launch-link", re.compile(r"(?<![A-Za-z0-9])zvl_[A-Za-z0-9_-]{16,}")),
    # A header is a finding unless a template placeholder follows it at once: that is code that frames a key pair the person generates in their own browser
    # (the key body is made at run time), not a key. A header followed by anything else, a real key body included, is still a finding.
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----(?!(?:\\n|\r?\n)\$\{)")),
    ("payment-secret-key", re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("webhook-signing-secret", re.compile(r"(?<![A-Za-z0-9])whsec_[A-Za-z0-9+/=]{16,}")),
    ("cloud-access-key", re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("literal-client-secret", re.compile(r"""client_?secret["']?\s*[:=]\s*["'][A-Za-z0-9_\-+/=]{16,}["']""", re.IGNORECASE)),
)
MAX_BYTES = 20_000_000  # a single file larger than this is not a browser bundle; it is reported unread rather than silently skipped


def scan_text(text: str) -> list[tuple[int, str]]:
    """Every (line number, rule name) found. Values are deliberately not returned. The whole text is searched, not line by line, so a rule can look at what follows a line's end."""
    found: dict[tuple[int, int], str] = {}
    for rank, (name, pattern) in enumerate(RULES):
        for m in pattern.finditer(text):
            found.setdefault((text.count("\n", 0, m.start()) + 1, rank), name)
    return [(number, name) for (number, _rank), name in sorted(found.items())]


def scan_dir(root: Path) -> tuple[int, list[str]]:
    """(files read, findings as 'relative/path:line: rule')."""
    findings: list[str] = []
    files = 0
    for p in sorted(root.rglob("*"), key=lambda q: q.as_posix()):
        if not p.is_file():
            continue
        if p.stat().st_size > MAX_BYTES:
            findings.append(f"{p.relative_to(root).as_posix()}: file too large to scan")
            continue
        files += 1
        for number, rule in scan_text(p.read_bytes().decode("utf-8", errors="replace")):
            findings.append(f"{p.relative_to(root).as_posix()}:{number}: {rule}")
    return files, findings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    code = 0
    for d in args:
        root = Path(d)
        if not root.is_dir():
            print(f"{d}: not a folder")
            return 2
        files, findings = scan_dir(root)
        if files == 0:
            print(f"{d}: no files to scan, which proves nothing")
            return 2
        print(f"{d}: {files} files scanned, {len(findings)} finding(s)")
        for f in findings:
            print("  " + f)
        code = 1 if findings else code
    return code


if __name__ == "__main__":
    sys.exit(main())
