"""Load the trust-source allowlist (`backend/config/trust/source-allowlist.yaml`; WF03 T02, T03, T38; CR-119). Strict: an unknown key or a malformed source refuses the whole file, never half of it."""

from __future__ import annotations

import yaml

from zid_platform.modules.evidence.domain.trust_store import (
    SourcePolicy,
    TrustSource,
    TrustStoreError,
)
from zid_platform.modules.evidence.infrastructure.trust_config import CONFIG

FILE = "trust/source-allowlist.yaml"


def parse(doc: object) -> SourcePolicy:
    if doc is None:
        return SourcePolicy()
    if not isinstance(doc, dict) or set(doc) - {"enforce", "sources"}:
        raise TrustStoreError("SOURCE_POLICY_INVALID", "only enforce and sources are allowed")
    enforce = doc.get("enforce", False)
    if not isinstance(enforce, bool):
        raise TrustStoreError("SOURCE_POLICY_INVALID", "enforce is true or false")
    sources: list[TrustSource] = []
    for item in doc.get("sources") or ():
        if not isinstance(item, dict) or set(item) - {"country", "authority", "kind"}:
            raise TrustStoreError(
                "SOURCE_POLICY_INVALID", "a source names country, authority and kind"
            )
        country, authority, kind = (
            str(item.get("country", "")).upper(),
            str(item.get("authority", "")).lower(),
            str(item.get("kind", "ROOT_LIST")),
        )
        if (
            not (2 <= len(country) <= 3 and country.isalpha())
            or not authority
            or "/" in authority
            or kind != "ROOT_LIST"
        ):
            raise TrustStoreError("SOURCE_POLICY_INVALID", f"source {country or '?'}")
        sources.append(TrustSource(country, authority, kind))
    return SourcePolicy(enforce, tuple(sources))


def load() -> SourcePolicy:
    path = CONFIG / FILE
    return (
        parse(yaml.safe_load(path.read_text(encoding="utf8"))) if path.exists() else SourcePolicy()
    )
