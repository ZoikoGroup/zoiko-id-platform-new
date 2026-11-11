"""Run the Zoiko iD backend locally in ONE terminal (development only).

Starts the cell role as one process on one port (8000). It also serves the global plane (locator + discovery,
no personal data, own schema global_directory) from the same database, DEC-D6. Output is prefixed with the role
name, and Ctrl+C stops it.

Usage (from backend/, with UV_PROJECT_ENVIRONMENT set as in the README):
    python -m uv run python ../tools/scripts/run_local.py            # cell (+ the global plane), port 8000
    python -m uv run python ../tools/scripts/run_local.py --only cell
    python -m uv run python ../tools/scripts/run_local.py --no-reload

Database: start it with `docker compose up -d postgres`. URLs are built from ZID_DB_PASSWORD and
ZID_DB_HOST_PORT (same as docker-compose.yml) or overridden with ZID_CELL_DATABASE_URL .
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

from localenv import BACKEND_ENV, load_backend_env

# backend/.env (copied from backend/.env.example) configures every role; shell variables win.
_ENV_LOADED = load_backend_env()

# Same settings as docker-compose.yml, so the backend finds the Docker Postgres:
#   ZID_DB_PASSWORD  (fallback zid_local_only — laptop only)   ZID_DB_HOST_PORT (default 5432)
_PW = os.environ.get("ZID_DB_PASSWORD", "zid_local_only")
_PORT = os.environ.get("ZID_DB_HOST_PORT", "5432")
LOCAL_DB = f"postgresql+asyncpg://zid:{_PW}@localhost:{_PORT}/{{db}}"
_CELL_DB = os.environ.get("ZID_CELL_DATABASE_URL", LOCAL_DB.format(db="zid_cell"))
# One process on one port: the cell also serves the global plane (locator + discovery) from the same database.
ROLES = {
    "cell": {"port": 8000, "cell_id": "local-1", "db": _CELL_DB},
}
_TTY = sys.stdout.isatty()  # colors only in a real terminal, never in log files
COLORS = {"cell": "\033[36m"} if _TTY else {"cell": ""}
RESET = "\033[0m" if _TTY else ""


def stream(role: str, proc: subprocess.Popen[bytes]) -> None:
    prefix = f"{COLORS[role]}[{role:<6}]{RESET} "
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip("\r\n")  # avoid doubled CRLF on Windows
        sys.stdout.write(f"{prefix}{line}\n")
        sys.stdout.flush()


def start(role: str, reload: bool) -> subprocess.Popen[bytes]:
    cfg = ROLES[role]
    env = {**os.environ, "ZID_ROLE": role, "ZID_CELL_ID": cfg["cell_id"], "ZID_DATABASE_URL": cfg["db"],
           "ZID_ENVIRONMENT": "local", "PYTHONUNBUFFERED": "1",
           "ZID_SERVE_GLOBAL_PLANE": "true"}
    cmd = [sys.executable, "-m", "uvicorn", "zid_platform.entrypoints.api:app",
           "--host", "127.0.0.1", "--port", str(cfg["port"]), "--no-access-log"]
    if reload:
        cmd.append("--reload")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=stream, args=(role, proc), daemon=True).start()
    return proc


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Zoiko iD backend locally in one terminal.")
    parser.add_argument("--only", choices=sorted(ROLES), help="run a single role")
    parser.add_argument("--no-reload", action="store_true", help="disable auto-reload on code changes")
    args = parser.parse_args()

    roles = [args.only] if args.only else list(ROLES)
    if not _ENV_LOADED:
        print(f"note: {BACKEND_ENV} not found; copy backend/.env.example to enable sign-in and passkeys.",
              flush=True)
    procs = {role: start(role, reload=not args.no_reload) for role in roles}
    for role in roles:
        print(f"{COLORS[role]}[{role:<6}]{RESET} http://localhost:{ROLES[role]['port']}  "
              f"(docs: /docs, readiness: /health/ready)", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            for role, proc in procs.items():
                if proc.poll() is not None:  # one role died: stop everything and report it
                    print(f"[{role}] exited with code {proc.returncode}; stopping.", flush=True)
                    return proc.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
        return 0
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
