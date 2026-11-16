"""Active Authentication of a passport or eID chip, the ECDSA variant (ICAO Doc 9303 part 11; CR-102): is the chip holding the private key that
matches the public key in its signed data group 15, or is it a copy?

Passive authentication (`epassport.py`) proves the DATA was signed by the issuing country. It cannot tell an original chip from a perfect copy of
its contents. Active Authentication can: the reader sends a fresh random challenge, the chip signs it with a private key that never leaves it, and
the signature verifies against the public key in DG15. A copy has the public key but not the private one.

Rules, each of which is a test and all of which fail closed:
- the public key counts only if data group 15 was one of the groups the signed object vouched for: the caller passes `dg15_verified`, taken from the
  passive-authentication result, and without it the answer is NOT_ASSESSED, never a pass;
- the challenge is 8 bytes the SERVER made for this session and has not used before, so an old response cannot be replayed (the caller owns that
  list; this function checks the length and refuses an all-zero challenge);
- an elliptic-curve key and an RSA key are handled. The RSA signature is ISO/IEC 9796-2 scheme 1 with message recovery, as ICAO 9303 part 11 uses it:
  the signature, raised to the public exponent, gives a header byte 0x6A, the part of the chip message that is recovered, the hash of the
  whole message (that part followed by the reader challenge) and a trailer (0xBC for SHA-1, or a hash identifier byte then 0xCC). It is checked
  against both the recovered value and its complement, which the scheme allows. Any other key type answers NOT_ASSESSED, never a guess;
- the signature is the plain `r || s` form of BSI TR-03111, each half the size of the curve's order, and the hash is the one the chip's
  ActiveAuthenticationInfo names (passed in), by default chosen from the curve size;
- a signature that does not verify is CLONE_SUSPECTED, which is a reason for a person to look and nothing more.

The RSA check follows the standard as I read it and is tested against a signer written from the same reading, not against a real chip: it needs
a real passport vector before it is relied on. What this does not do: Chip Authentication or PACE (key agreement between the chip and the phone: a
server cannot check it, only the phone can), talk to a chip, or issue the challenge. It makes no claim about the
holder: a genuine chip in a thief's hand passes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa, utils
from cryptography.hazmat.primitives.serialization import load_der_public_key

CHALLENGE_BYTES = 8
_HASHES: dict[str, type[hashes.HashAlgorithm]] = {
    "SHA-256": hashes.SHA256,
    "SHA-384": hashes.SHA384,
    "SHA-512": hashes.SHA512,
}


@dataclass(frozen=True, slots=True)
class AaVerdict:
    status: str  # GENUINE_KEY_HELD | CLONE_SUSPECTED | NOT_ASSESSED
    reasons: tuple[str, ...]

    @property
    def clone_checked(self) -> bool:
        return self.status == "GENUINE_KEY_HELD"


def _unwrap(dg15: bytes) -> bytes:
    """DG15 is the SubjectPublicKeyInfo inside an application tag 0x6F; a bare SubjectPublicKeyInfo is accepted too."""
    if len(dg15) < 2 or dg15[0] != 0x6F:
        return dg15
    n = dg15[1]
    if n < 0x80:
        return dg15[2 : 2 + n] if len(dg15) >= 2 + n else b""
    width = n & 0x7F
    if width == 0 or width > 3 or len(dg15) < 2 + width:
        return b""
    length = int.from_bytes(dg15[2 : 2 + width], "big")
    start = 2 + width
    return dg15[start : start + length] if len(dg15) >= start + length else b""


# ISO/IEC 9796-2 trailers. 0xBC alone means SHA-1; otherwise a hash identifier byte then 0xCC.
_RSA_TRAILERS: dict[bytes, str] = {
    b"\xbc": "sha1",
    b"\x33\xcc": "sha1",
    b"\x34\xcc": "sha256",
    b"\x35\xcc": "sha512",
    b"\x36\xcc": "sha384",
    b"\x38\xcc": "sha224",
}


def _rsa_check(numbers: rsa.RSAPublicNumbers, challenge: bytes, response: bytes) -> AaVerdict:
    n, e = numbers.n, numbers.e
    k = (n.bit_length() + 7) // 8
    if len(response) != k:
        return AaVerdict("CLONE_SUSPECTED", ("AA_SIGNATURE_WRONG_LENGTH",))
    s = int.from_bytes(response, "big")
    if s >= n:
        return AaVerdict("CLONE_SUSPECTED", ("AA_SIGNATURE_INVALID",))
    recovered = pow(s, e, n)
    for candidate in (
        recovered,
        n - recovered,
    ):  # scheme 1 signs the smaller of the message and its complement
        em = candidate.to_bytes(k, "big") if candidate < (1 << (8 * k)) else b""
        if not em or em[0] != 0x6A:
            continue
        for trailer, name in _RSA_TRAILERS.items():
            if not em.endswith(trailer):
                continue
            h = hashlib.new(name)
            hlen = h.digest_size
            m1_len = k - 1 - hlen - len(trailer)
            if m1_len < 0:
                continue
            m1, digest = em[1 : 1 + m1_len], em[1 + m1_len : 1 + m1_len + hlen]
            h.update(m1 + challenge)
            if h.digest() == digest:
                return AaVerdict(
                    "GENUINE_KEY_HELD", ("CHIP_HOLDS_THE_PRIVATE_KEY", "HOLDER_NOT_BOUND")
                )
    return AaVerdict("CLONE_SUSPECTED", ("AA_SIGNATURE_INVALID",))


def verify(
    dg15: bytes,
    challenge: bytes,
    response: bytes,
    *,
    dg15_verified: bool,
    hash_name: str | None = None,
) -> AaVerdict:
    if not dg15_verified:
        return AaVerdict("NOT_ASSESSED", ("DG15_NOT_COVERED_BY_THE_SIGNED_OBJECT",))
    if len(challenge) != CHALLENGE_BYTES or not any(challenge):
        return AaVerdict("NOT_ASSESSED", ("CHALLENGE_INVALID",))
    try:
        key = load_der_public_key(_unwrap(dg15))
    except Exception:  # noqa: BLE001 - an unreadable key is "cannot assess", never a pass
        return AaVerdict("NOT_ASSESSED", ("DG15_UNREADABLE",))
    if isinstance(key, rsa.RSAPublicKey):
        return _rsa_check(key.public_numbers(), challenge, response)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        return AaVerdict("NOT_ASSESSED", ("AA_KEY_TYPE_NOT_SUPPORTED",))
    if hash_name is None:
        bits = key.curve.key_size
        hash_name = "SHA-256" if bits <= 256 else "SHA-384" if bits <= 384 else "SHA-512"
    cls = _HASHES.get(hash_name)
    if cls is None:
        return AaVerdict("NOT_ASSESSED", ("AA_HASH_NOT_SUPPORTED",))
    half = (key.curve.key_size + 7) // 8
    if len(response) != 2 * half:
        return AaVerdict("CLONE_SUSPECTED", ("AA_SIGNATURE_WRONG_LENGTH",))
    r, s = int.from_bytes(response[:half], "big"), int.from_bytes(response[half:], "big")
    try:
        key.verify(utils.encode_dss_signature(r, s), challenge, ec.ECDSA(cls()))
    except InvalidSignature:
        return AaVerdict("CLONE_SUSPECTED", ("AA_SIGNATURE_INVALID",))
    except Exception:  # noqa: BLE001
        return AaVerdict("NOT_ASSESSED", ("AA_FAILED",))
    return AaVerdict("GENUINE_KEY_HELD", ("CHIP_HOLDS_THE_PRIVATE_KEY", "HOLDER_NOT_BOUND"))
