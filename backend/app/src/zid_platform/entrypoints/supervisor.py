"""Run one or more roles of the platform in a single container: `python -m zid_platform.entrypoints.supervisor`.

Each role is its own uvicorn **process** on its own port (cell and global must never share a
process — the global plane holds no personal data and uses its own database, ZID-TENANCY-001 §2,
DEC-D6). The supervisor forwards shutdown signals and exits if any role dies, so the container
runtime restarts the whole unit instead of leaving it half-up.

Configuration (environment):
  ZID_ROLES                 comma-separated roles to run (default "cell,global"; production cells use "cell")
  ZID_CELL_PORT / ZID_GLOBAL_PORT                 default 8000 / 8100
  ZID_CELL_DATABASE_URL / ZID_GLOBAL_DATABASE_URL database per role
  ZID_CELL_ID               cell identity for the cell role (the global role is always "global")
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass

from zid_platform.roles import Role

DEFAULT_PORTS = {Role.CELL: 8000, Role.GLOBAL: 8100}
# Per-role settings consumed by the supervisor; never passed through to any role process.
ROLE_SCOPED = frozenset(
    f"ZID_{r.value.upper()}_{s}" for r in Role for s in ("DATABASE_URL", "PORT")
)


@dataclass(frozen=True, slots=True)
class RoleProcess:
    role: Role
    port: int
    env: dict[str, str]


def plan(environ: Mapping[str, str]) -> list[RoleProcess]:
    """Build the per-role process definitions from the environment (pure; unit-tested)."""
    names = [r.strip() for r in environ.get("ZID_ROLES", "cell,global").split(",") if r.strip()]
    if not names:
        raise ValueError("ZID_ROLES is empty")
    roles = [Role(n) for n in names]  # raises ValueError on an unknown role
    if len(set(roles)) != len(roles):
        raise ValueError("ZID_ROLES lists a role twice")
    out = []
    for role in roles:
        key = role.value.upper()
        port = int(environ.get(f"ZID_{key}_PORT", DEFAULT_PORTS[role]))
        env = {k: v for k, v in environ.items() if k not in ROLE_SCOPED}
        env.update(
            ZID_ROLE=role.value,
            ZID_CELL_ID="global" if role is Role.GLOBAL else environ.get("ZID_CELL_ID", "local-1"),
        )
        db = environ.get(f"ZID_{key}_DATABASE_URL")
        if db:
            env["ZID_DATABASE_URL"] = db
        else:
            # never let one role inherit the other role's database
            env.pop("ZID_DATABASE_URL", None)
        out.append(RoleProcess(role, port, env))
    if len({p.port for p in out}) != len(out):
        raise ValueError("two roles configured on the same port")
    return out


def main() -> int:
    processes = []
    for rp in plan(os.environ):
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "zid_platform.entrypoints.api:app",
            "--host",
            os.environ.get("ZID_BIND_HOST", "0.0.0.0"),
            "--port",
            str(rp.port),
            "--no-access-log",
        ]
        processes.append((rp, subprocess.Popen(cmd, env=rp.env)))
        print(f'{{"msg":"role started","role":"{rp.role.value}","port":{rp.port}}}', flush=True)

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    exit_code = 0
    while not stopping:
        for rp, proc in processes:
            if proc.poll() is not None:
                print(
                    f'{{"msg":"role exited","role":"{rp.role.value}","code":{proc.returncode}}}',
                    flush=True,
                )
                exit_code, stopping = proc.returncode or 1, True
                break
        time.sleep(0.5)

    for _, proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in processes:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
