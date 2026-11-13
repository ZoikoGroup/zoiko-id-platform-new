"""Resolve an issuer NAME on a document and bind the certificate that signed it (ZID-INT-002 section 09, "issuer authentication"; T26).

Used by the document validator, through the credential port, so a document's `issuer` check is answered from the issuer trust graph (the registry
the owners reviewed) and not from the document itself. No outside service is asked.

- the name is resolved exactly as for credentials: equal after case, accent and punctuation folding, former names by period; a near miss is a
  LOOKALIKE and a person decides, and an unknown name is unknown;
- the SIGNER is bound only through a certificate the owners recorded for that issuer. A document whose name resolves but whose signature is
  absent, broken, untrusted or from some other certificate is therefore NOT issuer-authentic, however good the signature looks (T26).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.infrastructure.registries import issuer_registry


def resolve_and_bind(
    name: str,
    country: str | None,
    signer_sha256: str | None,
    *,
    on: date,
    registry: tg.Registry | None = None,
) -> dict[str, Any]:
    reg = registry or issuer_registry()
    res = tg.resolve(reg, name, country=country, on=on)
    out: dict[str, Any] = {
        "match": res.match.value,
        "issuer_id": res.issuer_id,
        "via_former_name": res.via_former_name,
        "candidates": list(res.candidates),
        "binding": "NOT_CHECKED",
    }
    if res.match is tg.Match.RESOLVED:
        if signer_sha256:
            out["binding"] = tg.bind_seal(
                reg, name, signer_sha256, None, country=country, on=on
            ).result.value
        else:
            out["binding"] = "NO_TRUSTED_SIGNATURE"
    return out
