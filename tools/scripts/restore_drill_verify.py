"""Verify the evidence in a RESTORED database with the platform's own verifier (the second half of the restore drill, `tools/scripts/restore_drill.sh`).

Runs inside the platform image, against a restored copy, never against the live database:

    python restore_drill_verify.py --db-url postgresql+asyncpg://... --keys /drill/keys.json [--tsa-ca /tsa/ca.pem]

For every evidence partition it recomputes the hash chain, every entry signature, every checkpoint's root, link and signature, and, with `--tsa-ca`, every external timestamp
against that root: exactly what the worker's scheduled integrity pass does on the live database (`ledger.verify_partition`). `--keys` is the JSON the key tool prints for
the signing keys (`python -m zid_platform.entrypoints.keys public`: public keys only; each entry's `key_id` becomes its `kid`).

Exit 0 only when every partition verifies and at least one partition was checked: a restore that holds no evidence proves nothing. One line of JSON per partition, then a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine


async def verify(db_url: str, keys: list[dict[str, Any]], tsa_ca: str | None) -> int:
    from zid_platform.modules.evidence.application import ledger
    from zid_platform.modules.evidence.infrastructure import ledger_store as store
    from zid_platform.modules.evidence.infrastructure.tsa_token import (
        TimestampVerifier,
        load_trust_roots,
    )

    verifier = (
        TimestampVerifier(load_trust_roots(tsa_ca), revocation="off")
        if tsa_ca
        else None
    )
    engine = create_async_engine(db_url)
    checked = failed = errored = 0
    stamped_ok = stamped_unverified = 0
    try:
        async with engine.connect() as conn:
            async with store.platform_scope(conn):
                partitions = await store.partitions_with_entries(conn)
            for pid in sorted(partitions):
                try:
                    report = await ledger.verify_partition(
                        conn, pid, keys=keys, emit_incident=False, timestamps=verifier
                    )
                except Exception as e:  # noqa: BLE001 - a partition that cannot be read is not verified
                    errored += 1
                    print(
                        json.dumps(
                            {
                                "partition_id": pid,
                                "ok": False,
                                "error": f"{type(e).__name__}: {e}"[:200],
                            }
                        )
                    )
                    continue
                checked += 1
                failed += 0 if report.ok else 1
                stamped_ok += report.timestamps_verified
                stamped_unverified += report.timestamps_unverified
                d = report.to_dict()
                d["problems"] = d["problems"][:3]
                print(json.dumps(d))
    finally:
        await engine.dispose()
    summary = {
        "partitions_checked": checked,
        "partitions_failed": failed,
        "partitions_errored": errored,
        "timestamps_verified": stamped_ok,
        "timestamps_unverified": stamped_unverified,
    }
    print(json.dumps({"summary": summary}))
    return 0 if checked > 0 and failed == 0 and errored == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--db-url", required=True, help="the RESTORED database (never the live one)"
    )
    p.add_argument("--keys", required=True, help="JSON from `keys public`")
    p.add_argument(
        "--tsa-ca", default=None, help="PEM root the external timestamps must chain to"
    )
    args = p.parse_args()
    raw = json.load(open(args.keys, encoding="utf-8"))
    keys = [{**k["jwk"], "kid": k["key_id"]} for k in raw]
    return asyncio.run(verify(args.db_url, keys, args.tsa_ca))


if __name__ == "__main__":
    sys.exit(main())
