"""Check a timestamp authority with the platform's OWN client and verifier, before a deployment relies on it.

    python tools/scripts/tsa_check.py --url http://tsa:8318/ --ca /tsa/ca.pem
    docker run --rm --network <compose network> -v ./tools/scripts:/t:ro -v <tsa volume>:/tsa:ro zid/platform:prodlocal python /t/tsa_check.py --url http://tsa:8318/ --ca /tsa/ca.pem

It asks the authority for a timestamp over a fresh digest exactly as the cell does (DER TimeStampReq, certReq on), and requires that

  1. the reply is accepted and verified by `TimestampVerifier` against the root in `--ca` (the same trust file `ZID_EVIDENCE_TSA_TRUST_FILE` names), and
  2. the SAME authority is REFUSED by a verifier that trusts a different, unrelated root (so "accepted" means the token really chains to the root you named).

Exit 0 only when both hold. It sends one hash of random bytes and nothing else. Run it against the authority the owners chose before `ZID_EVIDENCE_TSA_URL` is set in production,
and again after any change of the authority's certificate. Needs the backend environment (`zid_platform`, `cryptography`).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import os
import sys
import tempfile
from pathlib import Path

try:  # from a checkout the backend's source is on the path; mounted alone into a container, the installed package is used
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "app" / "src"))
except IndexError:
    pass


def _unrelated_root(path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "an unrelated root")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


async def check(url: str, ca: str, revocation: str) -> int:
    from zid_evidence import Digest
    from zid_platform.modules.evidence.infrastructure.tsa import Rfc3161Client
    from zid_platform.modules.evidence.infrastructure.tsa_token import (
        TimestampRejected,
        TimestampVerifier,
        load_trust_roots,
    )

    digest = Digest("sha-256", hashlib.sha256(os.urandom(32)).digest())
    try:
        ref = await Rfc3161Client(
            url, verifier=TimestampVerifier(load_trust_roots(ca), revocation=revocation)
        ).timestamp(digest)
    except Exception as e:  # noqa: BLE001 - any failure is the answer
        print(f"FAIL  the authority's reply was not accepted under {ca}: {type(e).__name__}: {e}")
        return 1
    print(
        f"PASS  a token over a fresh digest was accepted and verified against {ca} ({len(ref)} characters)"
    )
    with tempfile.TemporaryDirectory() as tmp:
        other = Path(tmp) / "other.pem"
        _unrelated_root(other)
        try:
            await Rfc3161Client(
                url, verifier=TimestampVerifier(load_trust_roots(str(other)), revocation=revocation)
            ).timestamp(digest)
        except TimestampRejected:
            print("PASS  the same authority is refused by a verifier that trusts an unrelated root")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  expected a refusal under an unrelated root, got {type(e).__name__}: {e}")
            return 1
    print("FAIL  the token was accepted under an unrelated root: the check proves nothing")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--url", required=True, help="the authority's address (what ZID_EVIDENCE_TSA_URL will hold)"
    )
    p.add_argument(
        "--ca",
        required=True,
        help="PEM file of the root a token must chain to (what ZID_EVIDENCE_TSA_TRUST_FILE will hold)",
    )
    p.add_argument(
        "--revocation", choices=("off", "if_available", "required"), default="if_available"
    )
    args = p.parse_args()
    return asyncio.run(check(args.url, args.ca, args.revocation))


if __name__ == "__main__":
    raise SystemExit(main())
