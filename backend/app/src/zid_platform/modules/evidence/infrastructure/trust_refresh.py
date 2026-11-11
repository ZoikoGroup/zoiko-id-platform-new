"""Download the EU trusted lists into `config/trust/` (ZID-INT-002 section 09; DTV-03). An OPERATOR tool: nothing here runs inside the service.

    python -m zid_platform.modules.evidence.infrastructure.trust_refresh --lotl-signer lotl-signer.pem [--anchors-from-lists] [--out DIR]

The chain of trust is the standard one, and it starts with a certificate YOU confirm:
1. `--lotl-signer` is the certificate that signs the EU list of lists. It is published in the Official Journal of the European Union (the
   current "pivot" LOTL entry lists it); compare its SHA-256 fingerprint with the Journal before you pass it. This tool cannot establish
   that for you and refuses to run without it.
2. The list of lists is downloaded and its XML signature is verified against that certificate. If it does not verify, nothing is written.
3. Each national list it points to is downloaded and verified against the signer certificates the VERIFIED list of lists names for it. A list
   that does not verify is not written, and the territory is reported.
4. With `--anchors-from-lists`, the CA certificates that a verified list names as granted qualified-certificate services are written to
   `anchors/`. That makes every such CA a trust anchor for chains: a policy decision, which is why it is opt-in.

Only https is used, redirects are not followed, each download is size-capped, and a failure of any one list never stops the others. Nothing
is deleted. The service itself never fetches anything.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from zid_platform.modules.evidence.application import trust

LOTL_URL = "https://ec.europa.eu/tools/lotl/eu-lotl.xml"
MAX_BYTES = 40_000_000
Fetch = Callable[[str], bytes]


def https_fetch(url: str) -> bytes:
    """One bounded https GET: no redirects, no other scheme, no more than MAX_BYTES."""
    if not url.startswith("https://"):
        raise ValueError("only https is fetched")
    with (
        httpx.Client(timeout=httpx.Timeout(120.0), follow_redirects=False) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        out = bytearray()
        for chunk in resp.iter_bytes():
            out += chunk
            if len(out) > MAX_BYTES:
                raise ValueError("response too large")
        return bytes(out)


@dataclass
class Report:
    lotl_verified: bool = False
    written: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    anchors: int = 0
    signers: int = 0


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def refresh(
    out: Path,
    lotl_signer: x509.Certificate,
    *,
    fetch: Fetch = https_fetch,
    lotl_url: str = LOTL_URL,
    anchors_from_lists: bool = False,
    now: datetime | None = None,
) -> Report:
    now = now or datetime.now(UTC)
    report = Report()
    try:
        lotl = fetch(lotl_url)
    except (httpx.HTTPError, ValueError, OSError) as e:
        report.failed["LOTL"] = f"download failed: {type(e).__name__}"
        return report
    pointers = trust.list_pointers(lotl, [lotl_signer])
    if not pointers:
        report.failed["LOTL"] = (
            "the list of lists did not verify against the signer you gave (or names no list); nothing was written"
        )
        return report
    report.lotl_verified = True
    for folder in ("trusted-lists", "tl-signers", "anchors"):
        (out / folder).mkdir(parents=True, exist_ok=True)
    seen_signers: set[str] = set()
    seen_anchors: set[str] = set()
    for p in pointers:
        if p.territory == "EU":  # the list of lists itself
            continue
        try:
            xml = fetch(p.location)
        except (httpx.HTTPError, ValueError, OSError) as e:
            report.failed[p.territory] = f"download failed: {type(e).__name__}"
            continue
        parsed = trust.parse_trusted_list(xml, list(p.signers), p.territory)
        if not parsed.authenticated:
            report.failed[p.territory] = parsed.problem or "not verified"
            continue
        (out / "trusted-lists" / f"{p.territory}.xml").write_bytes(xml)
        report.written.append(p.territory)
        for cert in p.signers:
            fp = _fingerprint(cert)
            if fp not in seen_signers:
                seen_signers.add(fp)
                (out / "tl-signers" / f"{p.territory}-{fp[:12]}.pem").write_bytes(
                    cert.public_bytes(Encoding.PEM)
                )
        if anchors_from_lists:
            for ca in trust.qualified_ca_certificates(parsed, now):
                fp = _fingerprint(ca)
                if fp not in seen_anchors:
                    seen_anchors.add(fp)
                    (out / "anchors" / f"{p.territory}-{fp[:16]}.pem").write_bytes(
                        ca.public_bytes(Encoding.PEM)
                    )
    report.signers, report.anchors = len(seen_signers), len(seen_anchors)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--lotl-signer",
        required=True,
        type=Path,
        help="PEM or DER of the certificate that signs the EU list of lists",
    )
    ap.add_argument("--out", type=Path, default=trust_root(), help="the config/trust folder")
    ap.add_argument("--lotl-url", default=LOTL_URL)
    ap.add_argument("--anchors-from-lists", action="store_true")
    args = ap.parse_args(argv)
    data = args.lotl_signer.read_bytes()
    cert = (
        x509.load_pem_x509_certificate(data)
        if b"BEGIN CERTIFICATE" in data
        else x509.load_der_x509_certificate(data)
    )
    print(f"LOTL signer SHA-256: {_fingerprint(cert)}  (compare with the Official Journal)")
    r = refresh(args.out, cert, lotl_url=args.lotl_url, anchors_from_lists=args.anchors_from_lists)
    print(f"list of lists verified: {r.lotl_verified}")
    print(f"lists written: {len(r.written)}  signers: {r.signers}  anchors: {r.anchors}")
    for territory, why in sorted(r.failed.items()):
        print(f"NOT WRITTEN {territory}: {why}")
    return 0 if r.lotl_verified else 1


def trust_root() -> Path:
    from zid_platform.modules.evidence.infrastructure.trust_config import CONFIG

    return CONFIG / "trust"


if __name__ == "__main__":
    sys.exit(main())
