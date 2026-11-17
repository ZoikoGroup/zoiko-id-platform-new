"""The issuer trust list for registry extracts: who may sign them, and which addresses are theirs (ZID-DTV-WF-02 K03, section 7). Loaded from versioned configuration, never from an upload.

`config/kyb/issuer-trust.yaml` ships EMPTY on purpose: nothing is trusted until Legal and Security have recorded, for each issuer, the keys it signs with (from its own publication) and the exact hosts
its QR codes may point at. A malformed entry refuses the whole load: a trust list that cannot be read in full is no trust list, and a half-loaded one could trust more than was approved.

issuers:
  - id: uk-companies-house
    hosts: [find-and-update.company-information.service.gov.uk]      # exact names, lower case, no wildcard
    path_prefixes: [/company/]
    keys:
      - {id: ch-2026-01, algorithm: Ed25519, valid_from: 2026-01-01T00:00:00+00:00, valid_to: null, revoked_at: null, public_key_pem: "-----BEGIN PUBLIC KEY-----..."}
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives import serialization

from zid_platform.modules.organization.domain.extract_authenticity import (
    ALLOWED_ALGORITHMS,
    Issuer,
    TrustedKey,
)

FILE = Path(
    os.environ.get("ZID_KYB_ISSUER_TRUST")
    or Path(__file__).resolve().parents[6] / "config" / "kyb" / "issuer-trust.yaml"
)
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class TrustListError(ValueError):
    pass


def _when(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse(doc: Mapping[str, Any] | None) -> dict[str, Issuer]:
    """The issuers in a document, or TrustListError. Nothing partial is ever returned."""
    out: dict[str, Issuer] = {}
    for entry in (doc or {}).get("issuers") or []:
        if not isinstance(entry, Mapping) or not _ID.match(str(entry.get("id", ""))):
            raise TrustListError("ISSUER_ID_INVALID")
        issuer_id = str(entry["id"])
        if issuer_id in out:
            raise TrustListError(f"ISSUER_REPEATED:{issuer_id}")
        hosts = frozenset(str(h) for h in entry.get("hosts") or [])
        for h in hosts:
            if h != h.lower() or not _HOST.match(h) or "*" in h:
                raise TrustListError(f"HOST_INVALID:{h}")  # exact, lower case, ASCII, no wildcard
        prefixes = tuple(str(p) for p in entry.get("path_prefixes") or ["/"])
        if any(not p.startswith("/") for p in prefixes):
            raise TrustListError("PATH_PREFIX_INVALID")
        keys: list[TrustedKey] = []
        for k in entry.get("keys") or []:
            try:
                algorithm = str(k["algorithm"])
                pem = str(k["public_key_pem"]).encode()
                serialization.load_pem_public_key(pem)
                valid_from = _when(k["valid_from"], "valid_from")
                if valid_from is None:
                    raise TrustListError("KEY_VALID_FROM_REQUIRED")
                key = TrustedKey(
                    str(k["id"]),
                    algorithm,
                    pem,
                    valid_from,
                    _when(k.get("valid_to"), "valid_to"),
                    _when(k.get("revoked_at"), "revoked_at"),
                )
            except TrustListError:
                raise
            except (KeyError, ValueError, TypeError) as e:
                raise TrustListError(f"KEY_UNREADABLE:{issuer_id}") from e
            if key.algorithm not in ALLOWED_ALGORITHMS:
                raise TrustListError(f"ALGORITHM_NOT_ALLOWED:{key.algorithm}")
            keys.append(key)
        out[issuer_id] = Issuer(issuer_id, tuple(keys), hosts, prefixes)
    return out


def load() -> dict[str, Issuer]:
    """The deployment's trust list, read on each call so a change takes effect without a restart. A missing file is an empty list (nothing trusted)."""
    try:
        text = FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse(yaml.safe_load(text))
