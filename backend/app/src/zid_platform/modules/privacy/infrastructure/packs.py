"""Jurisdiction-pack registry: versioned, counsel-approved configuration (DEC-F3 §3).

`backend/config/jurisdiction-packs/*.yaml` (or ZID_JURISDICTION_PACKS). The `# version:` and
`# approval_ref:` headers are part of the pack; a field counsel has not set reads `COUNSEL`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import yaml

from zid_platform.modules.privacy.domain.model import COUNSEL, JurisdictionPack, PrivacyError

PACKS_DIR = Path(
    os.environ.get("ZID_JURISDICTION_PACKS")
    or Path(__file__).resolve().parents[6] / "config" / "jurisdiction-packs"
)


def _header(text: str, key: str) -> str | None:
    m = re.search(rf"^# {key}: *(\S[^#\n]*)", text, re.MULTILINE)
    value = m.group(1).strip() if m else None
    return None if value in (None, "null", "~") else value


def parse_pack(text: str) -> JurisdictionPack:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("pack_id"), str):
        raise PrivacyError("PRIVACY_PACK_INVALID", "pack_id is required")
    raw = doc.get("rights_response_deadlines")
    deadlines: Mapping[str, int] | None
    if raw == COUNSEL or raw is None:
        deadlines = None
    elif isinstance(raw, dict) and all(isinstance(v, int) for v in raw.values()):
        deadlines = {str(k).upper(): int(v) for k, v in raw.items()}
    else:
        raise PrivacyError("PRIVACY_PACK_INVALID", "rights_response_deadlines: map of type -> days")
    transfers = doc.get("international_transfers") or {}
    return JurisdictionPack(
        pack_id=doc["pack_id"],
        version=_header(text, "version") or "0.0.0",
        approval_ref=_header(text, "approval_ref"),
        framework=str(doc.get("framework", "")),
        rights=frozenset(str(r) for r in doc.get("rights", []) or []),
        deadlines=deadlines,
        transfer_mechanisms=tuple(transfers.get("supported_mechanisms", []) or []),
        raw=doc,
    )


@lru_cache(maxsize=1)
def packs() -> dict[str, JurisdictionPack]:
    out = {}
    for f in sorted(PACKS_DIR.glob("*.yaml")):
        p = parse_pack(f.read_text(encoding="utf-8"))
        out[p.pack_id] = p
    return out


def pack(pack_id: str) -> JurisdictionPack:
    try:
        return packs()[pack_id]
    except KeyError as e:
        raise PrivacyError("PRIVACY_PACK_UNKNOWN", pack_id) from e


__all__ = ["PACKS_DIR", "pack", "packs", "parse_pack"]
