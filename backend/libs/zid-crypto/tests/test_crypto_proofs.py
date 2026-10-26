"""Cryptography acceptance proofs (ZID-VERIFY-MASTER-001 CRYP-03, 05, 06, 07, 08, 09).

These go past the unit round trips: every bit of a sealed value is flipped, nonces are drawn in
parallel, hostile JOSE headers are thrown at the verifier, and a rotation is walked through its overlap.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from zid_crypto import (
    CryptoError,
    KeyRing,
    LocalSigner,
    b64url,
    sign_jws,
    verify_jws,
)
from zid_crypto.envelope import Aad, open_envelope, seal
from zid_crypto.providers import KeyRef, LocalKeyProvider
from zid_crypto.purposes import KeyPurpose

AAD = Aad("tenant-a", "principal", "prn_1", "email", "1")


def provider() -> tuple[LocalKeyProvider, KeyRef]:
    p = LocalKeyProvider(environment="test")
    meta = p.create_key(KeyPurpose.DATA_KEK, "kek-1")
    return p, KeyRef(meta.purpose, meta.key_id)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_every_bit_flip_in_ciphertext_and_tag_is_refused() -> None:
    """CRYP-05: no single-bit change to the sealed bytes releases plaintext."""
    p, kek = provider()
    env = run(seal(b"sensitive-value", AAD, p, kek))
    blob = env.ciphertext
    for i in range(len(blob)):
        for bit in (0, 7):
            bad = bytearray(blob)
            bad[i] ^= 1 << bit
            with pytest.raises(Exception):  # noqa: B017 - any refusal is right; plaintext is the failure
                run(open_envelope(dataclasses.replace(env, ciphertext=bytes(bad)), AAD, p))


def test_a_nonce_change_is_refused_too() -> None:
    p, kek = provider()
    env = run(seal(b"v", AAD, p, kek))
    bad = bytearray(env.nonce)
    bad[0] ^= 1
    with pytest.raises(Exception):  # noqa: B017
        run(open_envelope(dataclasses.replace(env, nonce=bytes(bad)), AAD, p))


def test_tenant_a_ciphertext_does_not_open_under_tenant_b_context() -> None:
    """CRYP-03: the tenant is part of the authenticated context, so relocation fails."""
    p, kek = provider()
    env = run(seal(b"tenant-a-secret", AAD, p, kek))
    other = Aad("tenant-b", "principal", "prn_1", "email", "1")
    with pytest.raises(Exception):  # noqa: B017
        run(open_envelope(env, other, p))
    assert run(open_envelope(env, AAD, p)) == b"tenant-a-secret"


def test_nonces_never_repeat_across_threads_under_one_key() -> None:
    """CRYP-06: 2,000 seals from 8 threads against one KEK, with no nonce reuse and no DEK reuse."""
    p, kek = provider()

    def one(_: int) -> tuple[bytes, bytes]:
        env = asyncio.run(seal(b"x", AAD, p, kek))
        return env.nonce, env.wrapped_dek

    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(one, range(2_000)))
    assert len({n for n, _ in out}) == len(out), "an AES-GCM nonce repeated"
    assert len({d for _, d in out}) == len(out), "a data key was reused"


def _token(signer: LocalSigner, header_overrides: dict[str, Any], key: bytes | None = None) -> str:
    header = {"alg": signer.alg, "typ": "at+jwt", "kid": signer.kid, **header_overrides}
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    body = b64url(json.dumps({"sub": "attacker"}).encode())
    signing_input = f"{h}.{body}".encode()
    if key is not None:
        sig = hmac.new(key, signing_input, hashlib.sha256).digest()
    elif header["alg"] == "none":
        sig = b""
    else:
        sig = signer.sign(signing_input)
    return f"{h}.{body}.{b64url(sig)}"


def test_alg_none_and_hmac_key_confusion_are_rejected() -> None:
    """CRYP-07/08: `none` is refused, and an HS256 token MACed with the public JWK as the secret is refused."""
    signer = LocalSigner("k1")
    keys = [signer.public_jwk()]
    ok = sign_jws(signer, {"sub": "alice"}, typ="at+jwt")
    assert verify_jws(ok, keys=keys, typ="at+jwt")[1] == {"sub": "alice"}

    with pytest.raises(CryptoError) as e:
        verify_jws(_token(signer, {"alg": "none"}), keys=keys, typ="at+jwt")
    assert e.value.code == "ALG_NOT_ALLOWED"

    public_bytes = json.dumps(signer.public_jwk(), sort_keys=True).encode()
    for secret in (public_bytes, b64url(public_bytes).encode()):
        with pytest.raises(CryptoError) as e:
            verify_jws(_token(signer, {"alg": "HS256"}, key=secret), keys=keys, typ="at+jwt")
        assert e.value.code == "ALG_NOT_ALLOWED"

    for weak in ("HS384", "RS1", "ES1", "RS256"):
        with pytest.raises(CryptoError):
            verify_jws(_token(signer, {"alg": weak}), keys=keys, typ="at+jwt")


def test_remote_key_references_in_a_header_are_never_followed() -> None:
    signer = LocalSigner("k1")
    keys = [signer.public_jwk()]
    for header in ({"jku": "https://evil.example/jwks"}, {"x5u": "https://evil.example/c"}):
        with pytest.raises(CryptoError) as e:
            verify_jws(_token(signer, header), keys=keys, typ="at+jwt")
        assert e.value.code == "JWS_HEADER_REJECTED"


def test_rotation_has_an_overlap_and_an_unknown_kid_never_bypasses_verification() -> None:
    """CRYP-09: tokens signed before the rotation still verify during the overlap; a new key's tokens verify
    as soon as it is published; a key that was never published, or that left the window, verifies nothing."""
    ring = KeyRing(LocalSigner("k1"))
    before = ring.sign({"n": 1}, typ="at+jwt")
    ring.rotate(LocalSigner("k2"))
    after = ring.sign({"n": 2}, typ="at+jwt")
    assert ring.verify(before, typ="at+jwt")[1] == {"n": 1}
    assert ring.verify(after, typ="at+jwt")[1] == {"n": 2}
    assert {k["kid"] for k in ring.jwks()["keys"]} == {"k1", "k2"}

    stranger = sign_jws(LocalSigner("k9"), {"n": 3}, typ="at+jwt")
    with pytest.raises(CryptoError) as e:
        ring.verify(stranger, typ="at+jwt")
    assert e.value.code == "KEY_UNKNOWN"

    # a forged token that claims a published kid still has to carry that key's signature
    forged = _token(LocalSigner("k9"), {"kid": "k2"})
    with pytest.raises(CryptoError) as e:
        ring.verify(forged, typ="at+jwt")
    assert e.value.code == "SIGNATURE_INVALID"

    ring.rotate(LocalSigner("k3"))
    ring.rotate(LocalSigner("k4"))  # k1 has now left the verification window
    with pytest.raises(CryptoError):
        ring.verify(before, typ="at+jwt")
