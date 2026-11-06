"""Operate the software key store (`ZID_KEY_PROVIDER=db`, CR-060). Platform-operations steps, run where the master key file is.

    python -m zid_platform.entrypoints.keys init-master --file /run/secrets/zid_master_key [--id m1]
    python -m zid_platform.entrypoints.keys status        [--plane cell|global]    # key ids, versions, states; never material
    python -m zid_platform.entrypoints.keys public        [--plane cell|global]    # public keys of every signing key (to pin elsewhere)
    python -m zid_platform.entrypoints.keys rotate-signing --key-id signing_token-eu-1 [--plane ...]
    python -m zid_platform.entrypoints.keys rewrap        [--plane ...]            # after adding a new first line to the master key file

Environment: ZID_KEY_MASTER_FILE; the database is ZID_KEY_STORE_DATABASE_URL, else ZID_CELL_DATABASE_URL (plane cell) or
ZID_GLOBAL_DATABASE_URL (plane global), else ZID_DATABASE_URL. The role needs SELECT, INSERT and UPDATE on the key table.

Master key rotation: put a new `<id> <base64>` line FIRST in the file (keep the old line below it), restart the processes, run
`rewrap`, then remove the old line only after every backup that was wrapped under it has aged out or been destroyed: a backup is
only as erased as its master key is gone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from zid_crypto.software_keys import MasterKeys, SoftwareKeyProvider, SoftwareSigners

from zid_platform.db_keystore import PostgresKeyStore


def _store(plane: str) -> PostgresKeyStore:
    url = (
        os.environ.get("ZID_KEY_STORE_DATABASE_URL")
        or os.environ.get(f"ZID_{plane.upper()}_DATABASE_URL")
        or os.environ.get("ZID_DATABASE_URL")
    )
    if not url:
        raise SystemExit(
            "set ZID_KEY_STORE_DATABASE_URL (or ZID_CELL_DATABASE_URL / ZID_GLOBAL_DATABASE_URL)"
        )
    return PostgresKeyStore(url, "global_directory" if plane == "global" else "keys")


def _masters() -> MasterKeys:
    path = os.environ.get("ZID_KEY_MASTER_FILE")
    if not path:
        raise SystemExit("set ZID_KEY_MASTER_FILE")
    return MasterKeys.from_file(path)


def init_master(path: Path, master_id: str) -> int:
    if path.exists():
        print(f"{path} already exists: refusing to overwrite a master key", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "# Zoiko iD master key(s): one `<id> <base64 of 32 bytes>` per line, the first is current. Root-only. Back it up OFFLINE:\n"
        )
        f.write(
            "# without it every stored key is unrecoverable, and with it a copy of the database is readable.\n"
        )
        f.write(MasterKeys.generate(master_id) + "\n")
    print(f"wrote {path} (mode 600). Keep an offline copy, split between two people.")
    return 0


def status(plane: str) -> int:
    rows = _store(plane).load_all()
    for r in rows:
        print(
            f"{r.key_id:48} v{r.version:<3} {r.purpose:20} {r.algorithm:12} {r.state:10} {'material' if r.wrapped else 'ERASED'}"
        )
    print(f"{len(rows)} key versions")
    return 0


def public(plane: str) -> int:
    out = [
        {"key_id": r.key_id, "version": r.version, "state": r.state, "jwk": r.public_jwk}
        for r in _store(plane).load_all()
        if r.public_jwk
    ]
    print(json.dumps(out, indent=2))
    return 0


def rotate_signing(plane: str, key_id: str) -> int:
    version = SoftwareSigners(store=_store(plane), masters=_masters()).rotate(key_id)
    print(
        f"{key_id}: new ACTIVE version {version}; the previous one is RETIRED and its public key stays published. Restart the processes."
    )
    return 0


def rewrap(plane: str) -> int:
    changed = SoftwareKeyProvider(store=_store(plane), masters=_masters()).rewrap_all()
    print(f"re-wrapped {changed} rows under the current master key")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-master")
    init.add_argument("--file", type=Path, required=True)
    init.add_argument("--id", default="m1")
    for name in ("status", "public", "rotate-signing", "rewrap"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--plane", choices=("cell", "global"), default="cell")
        if name == "rotate-signing":
            cmd.add_argument("--key-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "init-master":
        return init_master(args.file, args.id)
    if args.command == "status":
        return status(args.plane)
    if args.command == "public":
        return public(args.plane)
    if args.command == "rotate-signing":
        return rotate_signing(args.plane, args.key_id)
    return rewrap(args.plane)


if __name__ == "__main__":
    raise SystemExit(main())
