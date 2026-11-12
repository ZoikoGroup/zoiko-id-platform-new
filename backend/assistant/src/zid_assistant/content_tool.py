"""Publisher tooling for the content snapshot (ADR-03; CSR-008 section 02).

    python -m zid_assistant.content_tool validate [content_dir]     parse + cross-check; exit 1 on any problem
    python -m zid_assistant.content_tool digest   [content_dir]     print the snapshot digest
    python -m zid_assistant.content_tool keygen   <private.pem> <public.pem>
    python -m zid_assistant.content_tool sign     <private.pem> <signature_out> [content_dir]
    python -m zid_assistant.content_tool verify   <public.pem> <signature_file> [content_dir]

A production process refuses to start with an unsigned or sample snapshot. Signing is a publisher act (Product, OD-004), not a build step.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from zid_assistant.application.snapshot import (
    ContentError,
    build_knowledge,
    sign_digest,
    verify_digest,
)
from zid_assistant.settings import AssistantSettings


def _dir(args: list[str], i: int) -> Path:
    return Path(args[i]) if len(args) > i else AssistantSettings().content_dir


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else ""
    try:
        if cmd in ("validate", "digest"):
            k = build_knowledge(_dir(a, 1), allow_local_http=True)
            if cmd == "digest":
                print(k.snapshot.digest)
            else:
                print(
                    f"ok: {len(k.intents)} intents, {len(k.answers)} answers, {len(k.snapshot.sources)} sources, {len(k.snapshot.destinations)} links, sample={k.snapshot.sample}"
                )
            return 0
        if cmd == "keygen" and len(a) == 3:
            priv = Ed25519PrivateKey.generate()
            Path(a[1]).write_bytes(
                priv.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            Path(a[2]).write_bytes(
                priv.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )
            return 0
        if cmd == "sign" and len(a) >= 3:
            k = build_knowledge(_dir(a, 3), allow_local_http=True)
            Path(a[2]).write_text(
                sign_digest(k.snapshot.digest, Path(a[1]).read_bytes()) + "\n", encoding="utf-8"
            )
            return 0
        if cmd == "verify" and len(a) >= 3:
            k = build_knowledge(_dir(a, 3), allow_local_http=True)
            ok = verify_digest(
                k.snapshot.digest,
                Path(a[2]).read_text(encoding="utf-8").strip(),
                Path(a[1]).read_bytes(),
            )
            print("signature ok" if ok else "SIGNATURE DOES NOT VERIFY")
            return 0 if ok else 1
    except ContentError as e:
        for p in e.problems:
            print(f"problem: {p}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
