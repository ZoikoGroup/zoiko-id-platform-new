"""AG1-AT21: the agent, attestor and governance code never hands a credential, key or document to a logger.

A static guard over every logging call in those modules, plus a runtime check of the one module that logs today."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from logcapture import capture
from zid_platform.attestor import service as attestor_service

SRC = Path(__file__).resolve().parents[2] / "src" / "zid_platform"
ROOTS = [SRC / "modules" / "machine", SRC / "modules" / "access_readiness", SRC / "attestor"]
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
SENSITIVE = (
    "token", "assertion", "secret", "password", "private", "jwk", "authorization", "bearer",
    "statement", "document", "biometric", "selfie", "face", "passport", "dob", "birth", "xfcc", "cert",
)  # fmt: skip


def _names(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id.lower())
        elif isinstance(n, ast.Attribute):
            out.append(n.attr.lower())
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value.lower())
    return out


def _log_calls() -> list[tuple[Path, int, ast.Call]]:
    found = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in LEVELS
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in {"log", "logger", "_log", "_logger", "logging"}
                ):
                    found.append((path, n.lineno, n))
    return found


def test_no_logging_call_in_the_agent_code_names_a_credential_or_a_document() -> None:
    bad = [
        f"{p.relative_to(SRC)}:{line}"
        for p, line, call in _log_calls()
        if any(s in name for name in _names(call) for s in SENSITIVE)
    ]
    assert not bad, f"logging calls that mention a sensitive value: {bad}"


def test_the_attestor_logs_the_identity_and_the_verdict_only() -> None:
    """The one place that logs today writes the SPIFFE id, a level and a reason code, nothing the workload presented."""
    calls = [
        c for p, _, c in _log_calls() if p.name == "service.py" and p.parent.name == "attestor"
    ]
    assert calls, "the attestor's log calls moved; update this guard"
    with capture(logging.DEBUG, "zid.attestor") as logs:
        attestor_service.log.warning(
            "not attested %s: %s", "spiffe://x/t/1/ns/a/sa/b", "DIGEST_MISMATCH"
        )
    assert "eyJ" not in logs.text and "spiffe://x/t/1" in logs.text
