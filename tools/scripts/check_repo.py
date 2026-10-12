"""Repository structure checks (run in CI; standard library only).

    python3 tools/scripts/check_repo.py          # exit 1 if any check fails

Checks:
  1. relative links in Markdown files resolve
  2. every CODEOWNERS path exists
  3. every uv workspace member exists and has a pyproject.toml
  4. migration streams match the modules in backend/database/table-ownership.yaml
  5. no stale .gitkeep in folders that have real content
  6. no empty folders (git does not track them; they vanish on clone)
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".hypothesis",
        ".ruff_cache", ".next", ".turbo", "build", "dist"}
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def walk(pattern: str) -> list[Path]:
    return [p for p in ROOT.rglob(pattern) if not SKIP & set(p.relative_to(ROOT).parts)]


def check_links() -> list[str]:
    out = []
    for md in walk("*.md"):
        for target in LINK.findall(md.read_text(encoding="utf-8")):
            if not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not (md.parent / target).resolve().exists():
                out.append(f"broken link: {md.relative_to(ROOT)} -> {target}")
    return out


def check_codeowners() -> list[str]:
    out = []
    for line in (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            path = line.split()[0].strip("/")
            if not (ROOT / path).exists():
                out.append(f"CODEOWNERS path does not exist: /{path}")
    return out


def check_workspace() -> list[str]:
    out = []
    backend = ROOT / "backend"
    members = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["workspace"]["members"]
    for pattern in members:
        matches = [m for m in backend.glob(pattern) if m.is_dir()]
        if not matches:
            out.append(f"uv workspace member matches nothing: {pattern}")
        out += [f"workspace member without pyproject.toml: {m.relative_to(ROOT)}"
                for m in matches if not (m / "pyproject.toml").exists()]
    return out


def check_migration_ownership() -> list[str]:
    registry = (ROOT / "backend/database/table-ownership.yaml").read_text(encoding="utf-8")
    owners = set(re.findall(r"^\s+module: (\w+)$", registry, re.MULTILINE))
    streams = {p.name for p in (ROOT / "backend/app/migrations").iterdir() if p.is_dir()}
    diff = sorted(owners ^ streams)
    return [f"migration streams and table owners differ: {diff}"] if diff else []


def check_placeholders() -> list[str]:
    out = []
    for keep in walk(".gitkeep"):
        if any(p.name != ".gitkeep" for p in keep.parent.iterdir()):
            out.append(f"stale .gitkeep (folder has content): {keep.parent.relative_to(ROOT)}")
    for d in walk("*"):
        if d.is_dir() and not any(d.iterdir()):
            out.append(f"empty folder (lost on clone; add .gitkeep or remove): {d.relative_to(ROOT)}")
    return out


CHECKS = [check_links, check_codeowners, check_workspace, check_migration_ownership, check_placeholders]


def main() -> int:
    failures = 0
    for check in CHECKS:
        problems = check()
        status = "ok" if not problems else f"{len(problems)} problem(s)"
        print(f"[{'PASS' if not problems else 'FAIL'}] {check.__name__}: {status}")
        for p in problems:
            print(f"       - {p}")
        failures += len(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
