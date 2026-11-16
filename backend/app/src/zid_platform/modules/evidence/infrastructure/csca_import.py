"""Put the CSCA certificates of a verified ICAO master list into `config/trust/csca/` (CR-095). An OPERATOR tool: nothing here runs inside the service and
nothing is fetched.

    python -m zid_platform.modules.evidence.infrastructure.csca_import --master-list masterlist.ml --anchor country-csca.pem [--anchor ...] [--out DIR] [--dry-run]

You obtain the master list yourself (for example the German list the BSI publishes, or the ICAO PKD), and you supply, with `--anchor`, a certificate you have
confirmed out of band: the publishing country's CSCA or its master list signer. The tool prints each anchor's SHA-256 fingerprint so you can compare it with the
country's own publication, refuses to run without an anchor, writes nothing unless the list's signature verifies against an anchor, never overwrites a file and
never deletes one. Which countries you trust is a decision for your security owner and Legal; the tool only moves certificates that were signed for you to check.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from zid_platform.modules.evidence.application.csca_master_list import (
    MasterListResult,
    fingerprint,
    read_master_list,
)

_COUNTRY = re.compile(r"[^A-Z]")


def load_certificate(data: bytes) -> x509.Certificate:
    return (
        x509.load_pem_x509_certificate(data)
        if b"BEGIN CERTIFICATE" in data
        else x509.load_der_x509_certificate(data)
    )


def country_of(cert: x509.Certificate) -> str:
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
    code = _COUNTRY.sub("", str(attrs[0].value).upper()) if attrs else ""
    return code[:3] or "XX"


def write_certificates(result: MasterListResult, out: Path, *, dry_run: bool = False) -> list[Path]:
    """Only a VERIFIED result is written. A certificate already present (same fingerprint in the name) is left alone."""
    if not result.ok:
        return []
    written: list[Path] = []
    for cert in result.certificates:
        target = out / f"{country_of(cert)}-{fingerprint(cert)[:16]}.pem"
        if target.exists():
            continue
        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as fh:  # exclusive create: never overwrite
                fh.write(cert.public_bytes(Encoding.PEM))
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--master-list", required=True, type=Path, help="the master list (DER CMS) you downloaded"
    )
    ap.add_argument(
        "--anchor",
        required=True,
        action="append",
        type=Path,
        help="a certificate you confirmed out of band (PEM or DER); repeatable",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="the csca folder (default config/trust/csca)"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    anchors = [load_certificate(p.read_bytes()) for p in args.anchor]
    for a in anchors:
        print(
            f"anchor SHA-256: {fingerprint(a)}  {a.subject.rfc4514_string()}  (compare with the country's own publication)"
        )
    result = read_master_list(args.master_list.read_bytes(), anchors=anchors)
    print(f"master list: {result.status} {', '.join(result.reasons)}")
    if not result.ok:
        return 1
    out = args.out
    if out is None:
        from zid_platform.modules.evidence.infrastructure.trust_config import CONFIG

        out = CONFIG / "trust" / "csca"
    done = write_certificates(result, out, dry_run=args.dry_run)
    print(
        f"certificates in the list: {len(result.certificates)}; {'would write' if args.dry_run else 'written'}: {len(done)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
