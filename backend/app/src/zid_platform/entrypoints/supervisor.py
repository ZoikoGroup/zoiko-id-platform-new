"""Run the platform in a single container: `python -m zid_platform.entrypoints.supervisor`.

One process, one port, one database. The `cell` role is the only role this container runs; it also serves the global plane (locator +
discovery, schema `global_directory`) on the same port, from the same database. There is no second process, no second port and no second
database: a configuration that asks for one is refused at start-up rather than half-honoured.

Configuration (environment):
  ZID_CELL_PORT             8000 (the only value; to publish another host port, map it in Docker)
  ZID_CELL_DATABASE_URL     the one platform database
  ZID_CELL_ID               cell identity
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

PORT = 8000  # the one port
# Per-role settings consumed by the supervisor; never passed through to the process.
ROLE_SCOPED = frozenset(
    f"ZID_{r.value.upper()}_{s}" for r in Role for s in ("DATABASE_URL", "PORT")
)
# Settings of the retired second plane process: refused, never silently ignored.
REFUSED = ("ZID_GLOBAL_PORT", "ZID_GLOBAL_DATABASE_URL")


@dataclass(frozen=True, slots=True)
class RoleProcess:
    role: Role
    port: int
    env: dict[str, str]


def plan(environ: Mapping[str, str]) -> list[RoleProcess]:
    """The one process definition, from the environment (pure; unit-tested): the cell, on one port, in one database, serving the global plane."""
    names = [r.strip() for r in environ.get("ZID_ROLES", "cell").split(",") if r.strip()]
    if names != ["cell"]:
        raise ValueError(
            "ZID_ROLES must be exactly 'cell': one process on one port (the cell also serves the global plane)"
        )
    for name in REFUSED:
        if environ.get(name):
            raise ValueError(
                f"{name} is not supported: the platform runs on one port and one database"
            )
    env = {k: v for k, v in environ.items() if k not in ROLE_SCOPED}
    env.update(
        ZID_ROLE=Role.CELL.value,
        ZID_CELL_ID=environ.get("ZID_CELL_ID", "local-1"),
        ZID_SERVE_GLOBAL_PLANE="true",
    )
    db = environ.get("ZID_CELL_DATABASE_URL")
    if db:
        env["ZID_DATABASE_URL"] = db
    else:
        env.pop("ZID_DATABASE_URL", None)  # a bare ZID_DATABASE_URL is never inherited
    if environ.get("ZID_CELL_PORT", str(PORT)) != str(PORT):
        raise ValueError(
            f"the platform listens on port {PORT} only; map a different host port in Docker instead"
        )
    return [RoleProcess(Role.CELL, PORT, env)]


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
