"""A small secret the server hands to a client and takes back, sealed so the client can neither read it nor change it (CR-108).

The chip relay keeps no state on the server: the protocol's state travels in one of these between steps, so any server process can take the next step. It is the
same envelope the stored uploads use (AES-256-GCM under a fresh data key, the data key wrapped by the tenant's key through the evidence runtime) with its own purpose
label, so a sealed value for one purpose is never opened for another.

What it guarantees: the client cannot read it; it cannot be changed; it opens only for the tenant and the binding it was sealed for (for the relay: the session, the
person and the client); and it stops opening after its time is up, checked on the server's clock. What it does not do: stop a client from presenting an OLD sealed
value again within its lifetime. A caller that cares (the relay does not: an old state meets a chip that has moved on) must add its own counter.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from zid_crypto.providers import KeyRef, zeroize

from zid_platform.modules.evidence.application.runtime import current_runtime

VERSION = 1
MAX_SEALED = 360_000  # characters: a sealed value that holds a photo is the largest thing sent


class SealError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _context(tenant_id: str, purpose: str, binding: Mapping[str, str]) -> dict[str, str]:
    return {
        "tenant": tenant_id,
        "purpose": purpose,
        **{f"b_{k}": str(v) for k, v in sorted(binding.items())},
    }


def _aad(tenant_id: str, purpose: str, binding: Mapping[str, str]) -> bytes:
    parts = "|".join(f"{k}={v}" for k, v in sorted(binding.items()))
    return f"zid-sealed-v{VERSION}|{purpose}|{tenant_id}|{parts}".encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


async def seal(
    tenant_id: str,
    purpose: str,
    binding: Mapping[str, str],
    payload: Mapping[str, object],
    *,
    ttl_seconds: int,
) -> str:
    """Seal `payload` (JSON-safe) for this tenant, purpose and binding, valid for `ttl_seconds` from now. Fails closed: with no key there is nothing to seal."""
    rt = current_runtime()
    plaintext = json.dumps(
        {"exp": int(time.time()) + ttl_seconds, "data": payload}, separators=(",", ":")
    ).encode()
    dek = bytearray(secrets.token_bytes(32))
    try:
        wrapped, exact = await rt.provider.wrap_data_key(
            rt.kek_for(tenant_id), dek, _context(tenant_id, purpose, binding)
        )
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(bytes(dek)).encrypt(nonce, plaintext, _aad(tenant_id, purpose, binding))
    finally:
        zeroize(dek)
    token = _b64(
        json.dumps(
            {
                "v": VERSION,
                "k": exact.key_id,
                "kv": exact.version or 1,
                "w": _b64(wrapped),
                "n": _b64(nonce),
                "c": _b64(ciphertext),
            },
            separators=(",", ":"),
        ).encode()
    )
    if len(token) > MAX_SEALED:
        raise SealError("SEALED_TOO_LARGE")
    return token


async def open_sealed(
    tenant_id: str, purpose: str, binding: Mapping[str, str], token: str
) -> dict[str, object]:
    """The payload, if the value is ours, unchanged, unexpired and for exactly this tenant, purpose and binding. Anything else is one refusal: SEALED_INVALID,
    so a caller learns nothing about which check failed."""
    if not token or len(token) > MAX_SEALED:
        raise SealError("SEALED_INVALID")
    rt = current_runtime()
    try:
        box = json.loads(_unb64(token))
        if box.get("v") != VERSION:
            raise SealError("SEALED_INVALID")
        kek = KeyRef(rt.subject_kek.purpose, box["k"], box["kv"])
        dek = await rt.provider.unwrap_data_key(
            kek, _unb64(box["w"]), _context(tenant_id, purpose, binding)
        )
    except SealError:
        raise
    except Exception as e:  # any failure to read or unwrap is the same refusal
        raise SealError("SEALED_INVALID") from e
    try:
        plain = AESGCM(bytes(dek)).decrypt(
            _unb64(box["n"]), _unb64(box["c"]), _aad(tenant_id, purpose, binding)
        )
        body = json.loads(plain)
    except (InvalidTag, ValueError, KeyError) as e:
        raise SealError("SEALED_INVALID") from e
    finally:
        zeroize(dek)
    if int(body.get("exp", 0)) <= int(time.time()):
        raise SealError("SEALED_EXPIRED")
    data = body.get("data")
    if not isinstance(data, dict):
        raise SealError("SEALED_INVALID")
    return data
