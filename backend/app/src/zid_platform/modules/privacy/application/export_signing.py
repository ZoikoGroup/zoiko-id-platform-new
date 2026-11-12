"""Signing and verifying a marked export (ZID-PRI-004 AT-047). The rules are in `domain/export_mark.py`; this adds only the signature."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from zid_crypto import CryptoError, Signer, sign_jws, verify_jws

from zid_platform.modules.privacy.domain import export_mark as mark


def sign(payload: Mapping[str, Any], signer: Signer) -> str:
    return sign_jws(signer, payload, typ=mark.EXPORT_TYP)


def verify(
    token: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    now: datetime,
    expected_recipient: str | None = None,
) -> dict[str, Any]:
    try:
        _header, payload = verify_jws(token, keys=keys, typ=mark.EXPORT_TYP)
    except CryptoError as e:
        raise mark.ExportError("EXPORT_SIGNATURE_INVALID", str(e)) from e
    return mark.check(payload, now=now, expected_recipient=expected_recipient)
