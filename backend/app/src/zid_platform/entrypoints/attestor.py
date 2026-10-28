"""The workload attestor process.

    python -m zid_platform.entrypoints.attestor keygen --out attestor.pem --kid attestor-1
    python -m zid_platform.entrypoints.attestor run --registrations workloads.json --key attestor.pem --kid attestor-1 \\
        --cell https://uk-1.example/v1/machine/attestations:verify --docker payments-agent=spiffe://...

`keygen` writes a development private key and prints the public JWK to put in the cell's `ZID_ATTESTOR_KEYS_FILE` (add
`"zid_max_level": 3` only for an attestor that verifies hardware evidence). Deployed attestors sign with a KMS key
(`--gcp-kms-key`), never a file. `run` inspects each target and keeps its statement renewed.

Each `--docker NAME=SPIFFE_ID` or `--k8s NAMESPACE/POD=SPIFFE_ID` pairs a runtime object with a registration entry; the
registration file decides what that object is allowed to be, and the attestor trusts nothing the workload says."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from zid_crypto import LocalSigner, Signer


def _signer(args: argparse.Namespace) -> Signer:
    if args.gcp_kms_key:
        from google.cloud import kms
        from zid_crypto.gcp_kms import GcpKmsSigner

        return GcpKmsSigner(kms.KeyManagementServiceClient(), args.gcp_kms_key, kid=args.kid)
    if not args.key:
        raise SystemExit("give --key (a development PEM) or --gcp-kms-key")
    return LocalSigner.from_pem(args.kid, Path(args.key).read_text(encoding="utf-8"))


def _keygen(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite {out}", file=sys.stderr)
        return 1
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    jwk = LocalSigner.from_pem(args.kid, pem.decode()).public_jwk()
    print(json.dumps({"keys": [jwk]}, indent=2))
    return 0


def _run(args: argparse.Namespace) -> int:
    from zid_platform.attestor import registration, service
    from zid_platform.attestor.inspectors import DockerInspector, KubernetesInspector

    entries = {e.spiffe_id: e for e in registration.load(Path(args.registrations))}
    targets: list[tuple[registration.Entry, str]] = []
    for spec in [*(args.docker or []), *(args.k8s or [])]:
        target, _, sid = spec.partition("=")
        if sid not in entries:
            print(f"{sid or spec} is not in the registration file", file=sys.stderr)
            return 2
        targets.append((entries[sid], target))
    if not targets:
        print("nothing to attest: give --docker or --k8s", file=sys.stderr)
        return 2
    if args.docker and args.k8s:
        print("run one attestor per runtime kind", file=sys.stderr)
        return 2
    inspector = (
        DockerInspector()
        if args.docker
        else KubernetesInspector(
            args.k8s_api,
            Path(args.k8s_token_file).read_text(encoding="utf-8").strip(),
            args.k8s_ca or True,
        )
    )
    service.run(
        _signer(args),
        targets,
        inspector,
        args.cell,
        renew_every=timedelta(seconds=args.renew_seconds),
        lifetime=timedelta(seconds=args.lifetime_seconds),
        rounds=args.rounds,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="attestor", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="write a development key and print its public JWK")
    k.add_argument("--out", required=True)
    k.add_argument("--kid", required=True)
    r = sub.add_parser("run", help="inspect workloads and keep their attestations renewed")
    r.add_argument("--registrations", required=True)
    r.add_argument("--kid", required=True)
    r.add_argument("--key")
    r.add_argument("--gcp-kms-key")
    r.add_argument(
        "--cell", required=True, help="the cell's /v1/machine/attestations:verify address"
    )
    r.add_argument("--docker", action="append", metavar="NAME=SPIFFE_ID")
    r.add_argument("--k8s", action="append", metavar="NAMESPACE/POD=SPIFFE_ID")
    r.add_argument("--k8s-api", default="https://kubernetes.default.svc")
    r.add_argument(
        "--k8s-token-file", default="/var/run/secrets/kubernetes.io/serviceaccount/token"
    )
    r.add_argument("--k8s-ca", default="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    r.add_argument("--lifetime-seconds", type=int, default=900)
    r.add_argument("--renew-seconds", type=int, default=300)
    r.add_argument("--rounds", type=int, help="stop after this many rounds (default: run forever)")
    args = p.parse_args(argv)
    return _keygen(args) if args.cmd == "keygen" else _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
