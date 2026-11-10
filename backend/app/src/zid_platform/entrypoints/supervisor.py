"""Run one or more roles of the platform in a single container: `python -m zid_platform.entrypoints.supervisor`.

Each role is its own uvicorn **process** on its own port (cell and global must never share a
process — the global plane holds no personal data and keeps to its own schema, global_directory,
DEC-D6), in the one platform database. The supervisor forwards shutdown signals and exits if any role dies, so the container
runtime restarts the whole unit instead of leaving it half-up.

Configuration (environment):
  ZID_ROLES                 comma-separated roles to run (default "cell": one process, one port)
  ZID_SERVE_GLOBAL_PLANE    true: the cell process also serves the global plane (locator + discovery) on its port
  ZID_CELL_PORT / ZID_GLOBAL_PORT                 default 8000 / 8100
  ZID_CELL_DATABASE_URL     the platform database; ZID_GLOBAL_DATABASE_URL is optional and defaults to it
  ZID_CELL_ID               cell identity for the cell role (the global role is always "global")
  ZID_FORWARDED_ALLOW_IPS   addresses of the proxies whose X-Forwarded-For is believed (the ingress), comma-separated or "*"; unset
                            trusts only 127.0.0.1, so behind an ingress every caller looks like the ingress to the sign-up limits
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
    names = [r.strip() for r in environ.get("ZID_ROLES", "cell").split(",") if r.strip()]
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
        if not db and role is Role.GLOBAL:
            # one database serves both roles: they keep separate schemas (global_directory vs the cell schemas)
            db = environ.get("ZID_CELL_DATABASE_URL")
        if db:
            env["ZID_DATABASE_URL"] = db
        else:
            # a bare ZID_DATABASE_URL meant for one role is never inherited by another
            env.pop("ZID_DATABASE_URL", None)
        out.append(RoleProcess(role, port, env))
    if len({p.port for p in out}) != len(out):
        raise ValueError("two roles configured on the same port")
    return out


def uvicorn_command(rp: RoleProcess, environ: Mapping[str, str]) -> list[str]:
    """The command that runs one role (pure; unit-tested). The forwarded-address list is passed only when it is set, and `*` is refused
    outside the local and test environments: believing any sender's X-Forwarded-For would let a caller choose their own address."""
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "zid_platform.entrypoints.api:app",
        "--host",
        environ.get("ZID_BIND_HOST", "0.0.0.0"),
        "--port",
        str(rp.port),
        "--no-access-log",
    ]
    trusted = environ.get("ZID_FORWARDED_ALLOW_IPS", "").strip()
    if trusted:
        if trusted == "*" and environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
            raise ValueError(
                "ZID_FORWARDED_ALLOW_IPS=* is allowed only in local and test; name the ingress addresses"
            )
        cmd += ["--proxy-headers", "--forwarded-allow-ips", trusted]
    return cmd


def main() -> int:
    processes = []
    for rp in plan(os.environ):
        cmd = uvicorn_command(rp, os.environ)
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
