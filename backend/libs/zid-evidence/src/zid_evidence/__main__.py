"""Offline evidence bundle verifier.

    python -m zid_evidence verify bundle.json [--keys trusted-jwks.json]

Exit status: 0 verified, 1 verification failed, 2 usage or unreadable input. Prints the
verification report as JSON. Without --keys the bundle's own keys are used and the report says so;
pin the published evidence keys with --keys for a verification that does not trust the bundle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zid_evidence.bundle import verify_bundle
from zid_evidence.jcs import CanonicalizationError, loads_strict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zid_evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify an exported evidence bundle offline")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--keys", type=Path, help="trusted JWK set (JSON) to verify signatures")
    args = parser.parse_args(argv)
    try:
        bundle = loads_strict(args.bundle.read_bytes())
        trusted = loads_strict(args.keys.read_bytes())["keys"] if args.keys else None
    except (OSError, CanonicalizationError, KeyError, TypeError) as e:
        sys.stderr.write(f"cannot read input: {e}\n")
        return 2
    if not isinstance(bundle, dict):
        sys.stderr.write("bundle must be a JSON object\n")
        return 2
    report = verify_bundle(bundle, trusted_keys=trusted)
    sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
