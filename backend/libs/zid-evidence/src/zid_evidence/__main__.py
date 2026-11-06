"""Offline evidence bundle verifier.

    python -m zid_evidence verify bundle.json [--keys trusted-jwks.json]
        [--tsa-roots roots.pem [--tsa-crl list.crl ...] [--tsa-revocation off|if_available|required] [--require-timestamp]]

Exit status: 0 verified, 1 verification failed, 2 usage or unreadable input. Prints the
verification report as JSON. Without --keys the bundle's own keys are used and the report says so;
pin the published evidence keys with --keys for a verification that does not trust the bundle.

A checkpoint may carry an RFC 3161 timestamp. With --tsa-roots (PEM of the timestamp authority roots you trust; needs
`pip install zid-evidence[timestamps]`) it is verified, and --tsa-crl adds revocation lists (PEM or DER, repeatable).
Without --tsa-roots a present timestamp is reported as "not verified". --require-timestamp fails a bundle whose
checkpoint is not anchored by a verified timestamp.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography import x509

from zid_evidence.bundle import TimestampOptions, verify_bundle
from zid_evidence.jcs import CanonicalizationError, loads_strict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zid_evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify an exported evidence bundle offline")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--keys", type=Path, help="trusted JWK set (JSON) to verify signatures")
    verify.add_argument(
        "--tsa-roots", type=Path, help="PEM file of trusted timestamp authority roots"
    )
    verify.add_argument(
        "--tsa-crl",
        type=Path,
        action="append",
        default=[],
        help="revocation list (PEM or DER); repeatable",
    )
    verify.add_argument(
        "--tsa-revocation", choices=("off", "if_available", "required"), default="if_available"
    )
    verify.add_argument(
        "--require-timestamp",
        action="store_true",
        help="fail unless the checkpoint is anchored and verified",
    )
    args = parser.parse_args(argv)
    try:
        bundle = loads_strict(args.bundle.read_bytes())
        trusted = loads_strict(args.keys.read_bytes())["keys"] if args.keys else None
        roots = (
            tuple(x509.load_pem_x509_certificates(args.tsa_roots.read_bytes()))
            if args.tsa_roots
            else None
        )
        crls = _load_crls(args.tsa_crl)
    except (OSError, CanonicalizationError, KeyError, TypeError, ValueError, ImportError) as e:
        sys.stderr.write(f"cannot read input: {e}\n")
        return 2
    if not isinstance(bundle, dict):
        sys.stderr.write("bundle must be a JSON object\n")
        return 2
    if roots is not None and not roots:
        sys.stderr.write("the timestamp trust list holds no certificates\n")
        return 2
    options = TimestampOptions(
        roots=roots, crls=crls, revocation=args.tsa_revocation, required=args.require_timestamp
    )
    report = verify_bundle(bundle, trusted_keys=trusted, timestamps=options)
    sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    return 0 if report.ok else 1


def _load_crls(paths: list[Path]) -> tuple[x509.CertificateRevocationList, ...]:
    if not paths:
        return ()
    from zid_evidence.timestamp import load_crls

    return tuple(crl for p in paths for crl in load_crls(p.read_bytes()))


if __name__ == "__main__":
    sys.exit(main())
