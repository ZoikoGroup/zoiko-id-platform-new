"""Secret scan for the repository, images' build context and IaC (ZID-CRYPTO-001 §15, §28).

    python3 tools/scripts/secret_scan.py [path ...]    # exit 1 when a production-like secret is found

Standard library only; `check_repo.py` runs it over the repository in CI. It fails the gate on:

  - private key blocks (PEM / OpenSSH), cloud and SaaS access tokens with their well-known prefixes
    (AWS, Stripe live, GitHub, Slack, Google, npm, PyPI), and JWT-shaped bearer tokens;
  - connection strings that carry a password for a non-local host;
  - assignments of long, high-entropy literals to names like password/secret/token/api_key
    (outside test code, where fixtures may use throw-away values).

Suppress a reviewed false positive by putting `zid-secret-scan: allow` on the same line. Documentation
placeholders (`example`, `changeme`, `<...>`, `${...}`, `xxxx`) are ignored. This is a pattern gate,
not a replacement for the CI secret scanner on history (gitleaks); it keeps new secrets out of the
tree and proves the gate rejects them.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".hypothesis", ".ruff_cache", ".next", ".turbo", "build", "dist", "test-results",
}  # fmt: skip
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx", ".pptx", ".xlsx", ".woff", ".woff2"}
MAX_BYTES = 2_000_000
ALLOW = "zid-secret-scan: allow"

# Always secrets, in any file (tests included).
STRONG: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws-secret-access-key": re.compile(
        r"(?i)aws_?secret_?access_?key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}\b"
    ),
    "stripe-live-key": re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "gcp-service-account": re.compile(r"\"type\"\s*:\s*\"service_account\""),
    "npm-token": re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    "pypi-token": re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}"),
}
# Outside test code only: literals whose name says they are credentials.
URL_PASSWORD = re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@\"']+:([^\s@/\"']{6,})@([^\s/:\"']+)")
ASSIGNMENT = re.compile(
    r"(?i)\b[\w.\-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)"
    r"[\w.\-]*[\"']?\s*[:=]\s*[\"']([A-Za-z0-9/+=_\-.!@#$%^&*]{20,})[\"']"
)
PLACEHOLDER = re.compile(
    r"(?i)example|changeme|change-me|placeholder|dummy|sample|your[_-]|<[^>]+>|\$\{|\{\{|xxxx|"
    r"0000|redacted|not-a-secret|fake|test|local|dev-|\.\.\.|secret_?ref|ref:"
)
IDENTIFIER = re.compile(r"[a-z]+(?:[._\-][a-z]+)*")  # event names, dotted paths: words, not secrets
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "postgres", "db", "kafka", "spicedb", "openbao")


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def is_test_path(path: str) -> bool:
    parts = set(Path(path).parts)
    name = Path(path).name
    return bool(parts & {"tests", "test", "__tests__", "fixtures"}) or name.startswith("test_")


def scan_text(text: str, path: str = "") -> list[tuple[int, str]]:
    """`(line, rule)` findings for one file's text; `path` decides whether test-fixture leniency applies."""
    findings: list[tuple[int, str]] = []
    lenient = is_test_path(path)
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for rule, pattern in STRONG.items():
            if pattern.search(line):
                findings.append((number, rule))
        if lenient:
            continue
        for m in URL_PASSWORD.finditer(line):
            host = m.group(2).lower()
            if not PLACEHOLDER.search(m.group(0)) and host not in LOCAL_HOSTS:
                findings.append((number, "credential-in-url"))
        for m in ASSIGNMENT.finditer(line):
            value = m.group(1)
            if (
                not PLACEHOLDER.search(m.group(0))
                and not IDENTIFIER.fullmatch(value)
                and _entropy(value) >= 3.5
            ):
                findings.append((number, "high-entropy-credential-literal"))
    return findings


def tracked_files(root: Path = ROOT) -> list[Path]:
    """Files git tracks under `root`; every file under it when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        return list(iter_files([root]))
    return [
        p
        for name in out.split("\0")
        if name and (p := root / name).is_file() and p.suffix.lower() not in BINARY
        and not SKIP_DIRS & set(Path(name).parts)
    ]


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if base.is_file():
            yield base
            continue
        for p in base.rglob("*"):
            rel = p.relative_to(base).parts
            if p.is_file() and not SKIP_DIRS & set(rel) and p.suffix.lower() not in BINARY:
                yield p


def scan_paths(paths: Iterable[Path], root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for file in iter_files(paths):
        try:
            if file.stat().st_size > MAX_BYTES:
                continue
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            shown = str(file.resolve().relative_to(root))
        except ValueError:
            shown = str(file)
        shown = shown.replace("\\", "/")
        findings += [Finding(shown, n, rule) for n, rule in scan_text(text, shown)]
    return findings


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    findings = scan_paths([Path(a) for a in args] or [ROOT])
    for f in findings:
        print(f)
    print(f"secret scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
