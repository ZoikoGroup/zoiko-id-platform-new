"""Conformance vector loading (ZID-UAT-005 section 03: official golden vectors). Offline and pinned.

A vector directory carries `MANIFEST.yaml` listing each file and its SHA-256. A file whose digest differs from the pin is refused, a file in the
directory that is not pinned is ignored and reported (it is never used), and nothing is downloaded: the official sets (EU eSignature validation
tests, ETSI, W3C VC, OpenID4VC, NIST) are placed there by a person and pinned, and a result obtained from them counts as developer evidence until an
independent run repeats it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


class VectorError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def load(directory: Path) -> dict[str, Any]:
    """{'vectors': {path: bytes}, 'unpinned': [path], 'source': str}. Raises VectorError for a missing manifest or a digest mismatch."""
    manifest = directory / "MANIFEST.yaml"
    if not manifest.is_file():
        raise VectorError("MANIFEST_MISSING", str(directory))
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    pins = {str(f["path"]): str(f["sha256"]).lower() for f in doc.get("files", [])}
    out: dict[str, bytes] = {}
    for rel, want in pins.items():
        target = (directory / rel).resolve()
        if directory.resolve() not in target.parents:
            raise VectorError("PATH_ESCAPES_THE_VECTOR_DIRECTORY", rel)
        if not target.is_file():
            raise VectorError("PINNED_FILE_MISSING", rel)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != want:
            raise VectorError("DIGEST_MISMATCH", rel)
        out[rel] = data
    present = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != "MANIFEST.yaml"
    }
    return {
        "vectors": out,
        "unpinned": sorted(present - set(pins)),
        "source": str(doc.get("source", "")),
    }
