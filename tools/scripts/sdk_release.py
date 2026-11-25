#!/usr/bin/env python3
"""Build, sign and verify an SDK release (ZID-CP-WF10 v2.0, WF-10.06; CR-173).

    sdk_release.py manifest <package-dir> --name N --version V [--out manifest.json]
    sdk_release.py sign     manifest.json --key private.pem [--out release.json]
    sdk_release.py verify   release.json --key public.pem <package-dir>

A release is a MANIFEST (every file of the package with its SHA-256 and size, the dependencies the package declares, and its name and version) and a SIGNATURE over the manifest's
canonical bytes. Verifying recomputes the digests from the files on disk, so a changed, added or missing file, an altered manifest or a signature from another key is refused by name.

The manifest is a software bill of materials in the plain sense (files and declared dependencies). It is not a vulnerability scan and not a build attestation.

WHAT THIS DOES NOT DO. It does not hold a key and does not publish anything. The signing key must be a private key the company owns and keeps (an Ed25519 key in PEM form); the
company's registry account does the publishing. Until both exist no SDK is signed or published, and the SDK register says so.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

FORMAT = "zoiko-sdk-release/1"
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "dist", "build", "target", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "bin", "obj"}
SKIP_SUFFIX = (".pyc", ".pyo", ".log")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(data: bytes) -> str:
    return "sha-256:" + hashlib.sha256(data).hexdigest()


def package_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):  # the same order on every operating system, or the manifest differs between machines
        rel = p.relative_to(root)
        if p.is_file() and not any(part in SKIP_DIRS for part in rel.parts) and not p.name.endswith(SKIP_SUFFIX):
            out.append(p)
    return out


def declared_dependencies(root: Path) -> list[dict[str, str]]:
    """The dependencies the package says it needs, exactly as declared (no resolution, no scan)."""
    deps: list[dict[str, str]] = []
    py = root / "pyproject.toml"
    if py.is_file():
        project = tomllib.loads(py.read_text(encoding="utf-8")).get("project", {})
        for d in project.get("dependencies", []):
            deps.append({"ecosystem": "pypi", "declared": str(d)})
    pk = root / "package.json"
    if pk.is_file():
        data = json.loads(pk.read_text(encoding="utf-8"))
        for section in ("dependencies", "peerDependencies"):
            for name, spec in (data.get(section) or {}).items():
                deps.append({"ecosystem": "npm", "declared": f"{name}@{spec}"})
    return sorted(deps, key=lambda d: (d["ecosystem"], d["declared"]))


def build_manifest(root: Path, name: str, version: str) -> dict[str, Any]:
    files = [{"path": p.relative_to(root).as_posix(), "sha256": _sha(p.read_bytes()), "bytes": p.stat().st_size} for p in package_files(root)]
    if not files:
        raise ValueError("the package has no files")
    return {"format": FORMAT, "name": name, "version": version, "files": files, "declared_dependencies": declared_dependencies(root), "note": "A list of files and declared dependencies. It is not a vulnerability scan."}


def key_id_of_public(public_pem: bytes) -> str:
    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(public_pem)
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _sha(raw)


def sign(manifest: dict[str, Any], private_pem: bytes) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("the signing key must be an Ed25519 private key")
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    sig = key.sign(_canonical(manifest))
    return {"format": FORMAT, "manifest": manifest, "manifest_digest": _sha(_canonical(manifest)), "algorithm": "Ed25519", "key_id": key_id_of_public(pub), "signature": base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")}


def verify(release: dict[str, Any], public_pem: bytes, root: Path) -> list[str]:
    """Every reason this release cannot be trusted. Empty means: signed by this key, the manifest is untouched, and the files on disk are exactly the files listed."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    out: list[str] = []
    manifest = release.get("manifest")
    if release.get("format") != FORMAT or not isinstance(manifest, dict):
        return ["NOT_A_RELEASE"]
    if release.get("key_id") != key_id_of_public(public_pem):
        out.append("SIGNED_BY_ANOTHER_KEY")
    if release.get("manifest_digest") != _sha(_canonical(manifest)):
        out.append("MANIFEST_DIGEST_DIFFERS")
    try:
        pub = serialization.load_pem_public_key(public_pem)
        if not isinstance(pub, ed25519.Ed25519PublicKey):
            raise ValueError
        sig = base64.urlsafe_b64decode(str(release.get("signature", "")) + "=" * (-len(str(release.get("signature", ""))) % 4))
        pub.verify(sig, _canonical(manifest))
    except (InvalidSignature, ValueError, TypeError):
        out.append("SIGNATURE_DOES_NOT_VERIFY")
    listed = {f["path"]: f for f in manifest.get("files", [])}
    on_disk = {p.relative_to(root).as_posix(): p for p in package_files(root)}
    for path, f in listed.items():
        if path not in on_disk:
            out.append(f"FILE_MISSING:{path}")
        elif _sha(on_disk[path].read_bytes()) != f["sha256"]:
            out.append(f"FILE_CHANGED:{path}")
    out += [f"FILE_ADDED:{path}" for path in on_disk if path not in listed]
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("package")
    m.add_argument("--name", required=True)
    m.add_argument("--version", required=True)
    m.add_argument("--out", default="manifest.json")
    s = sub.add_parser("sign")
    s.add_argument("manifest")
    s.add_argument("--key", required=True, help="an Ed25519 private key in PEM form that the company owns")
    s.add_argument("--out", default="release.json")
    v = sub.add_parser("verify")
    v.add_argument("release")
    v.add_argument("package")
    v.add_argument("--key", required=True, help="the matching public key in PEM form")
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        Path(a.out).write_text(json.dumps(build_manifest(Path(a.package), a.name, a.version), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", a.out)
        return 0
    if a.cmd == "sign":
        manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
        Path(a.out).write_text(json.dumps(sign(manifest, Path(a.key).read_bytes()), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", a.out)
        return 0
    problems = verify(json.loads(Path(a.release).read_text(encoding="utf-8")), Path(a.key).read_bytes(), Path(a.package))
    print("\n".join(problems) if problems else "OK: signed by this key and every file matches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
