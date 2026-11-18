"""Chip authentication through PACE-CAM (ICAO Doc 9303 part 11, section 4.4.3.5; CR-111): is the chip holding the private key that matches the public key in its signed data group 14,
or is it a copy?

Passive authentication proves the DATA was signed by the issuing country; it cannot tell an original chip from a perfect copy of its contents. With the chip authentication
mapping the chip proves, during PACE itself, that it holds the static private key SK_IC: it returns CA_IC = SK_IC^-1 * SK_Map,IC (encrypted under the session key), and the
terminal checks that CA_IC times the static public key PK_IC gives the mapping public key the chip used. A copy has PK_IC but not SK_IC, so it cannot do this.

Rules, each a test, all failing closed:
- the static public key counts only if data group 14 was one of the groups the signed object vouched for (`dg14_verified`, from passive authentication); otherwise the answer is
  NOT_ASSESSED, never a pass. A key an attacker put on a cloned chip proves nothing if the signed object does not cover it;
- only a key on the same domain parameters as the PACE run is tried (a standardized parameter identifier, as in the standard's example); a key with explicit parameters is not
  assessed rather than guessed at;
- a chip whose proof does not verify against any key of that curve is CLONE_SUSPECTED, which is a reason for a person to look and nothing more;
- a genuine chip in a thief's hand passes: this says nothing about the holder.

Tested against every value of the standard's worked example (appendix I: the session key, the encrypted data, the mapping public key and the static public key) and against a chip
simulator written separately. NOT tested on a real passport.
"""

from __future__ import annotations

from dataclasses import dataclass

from zid_platform.modules.evidence.application import pace
from zid_platform.modules.evidence.application.active_auth import AaVerdict

ID_PK_ECDH = bytes.fromhex("04007F000702020102")  # id-PK-ECDH, the chip authentication public key
STANDARDIZED_DOMAIN_PARAMETERS = bytes.fromhex("04007F00070102")  # id-standardizedDomainParameters


@dataclass(frozen=True, slots=True)
class CamEvidence:
    """What the PACE run left for the check: the domain parameters, the chip's mapping public key as received, the session encryption key and the chip's encrypted
    chip authentication data. A session key is a secret: this never leaves the process that read the chip."""

    curve_id: int
    pk_map_ic: bytes
    ksenc: bytes
    encrypted: bytes


class DerError(ValueError):
    pass


def _items(data: bytes) -> list[tuple[int, bytes]]:
    """One level of DER with one-byte tags (every tag here is). Anything that does not parse is an error."""
    out: list[tuple[int, bytes]] = []
    i = 0
    while i < len(data):
        if i + 2 > len(data):
            raise DerError("truncated")
        tag, first = data[i], data[i + 1]
        i += 2
        if first < 0x80:
            length = first
        elif first in (0x81, 0x82):
            width = first & 0x7F
            if i + width > len(data):
                raise DerError("truncated")
            length, i = int.from_bytes(data[i : i + width], "big"), i + width
        else:
            raise DerError("length form")
        if i + length > len(data):
            raise DerError("truncated")
        out.append((tag, data[i : i + length]))
        i += length
    return out


def static_keys(dg14: bytes) -> list[tuple[int | None, bytes]]:
    """(standardized domain parameter id or None, uncompressed point) of every ChipAuthenticationPublicKeyInfo in data group 14 whose key is an elliptic-curve key. A key
    with explicit domain parameters has id None. Anything else in the file is skipped; a file that does not parse gives no keys."""
    try:
        top = _items(dg14)
        if len(top) != 1 or top[0][0] != 0x6E:
            return []
        sets = _items(top[0][1])
        if len(sets) != 1 or sets[0][0] != 0x31:
            return []
        keys: list[tuple[int | None, bytes]] = []
        for tag, body in _items(sets[0][1]):
            if tag != 0x30:
                continue
            fields = _items(body)
            if len(fields) < 2 or fields[0] != (0x06, ID_PK_ECDH) or fields[1][0] != 0x30:
                continue
            spki = _items(fields[1][1])
            if len(spki) != 2 or spki[0][0] != 0x30 or spki[1][0] != 0x03:
                continue
            algorithm = _items(spki[0][1])
            bits = spki[1][1]
            if len(bits) < 2 or bits[0] != 0:  # a whole number of bytes
                continue
            parameter: int | None = None
            if len(algorithm) == 2 and algorithm[0] == (0x06, STANDARDIZED_DOMAIN_PARAMETERS):
                parameter = (
                    int.from_bytes(algorithm[1][1], "big") if algorithm[1][0] == 0x02 else None
                )
            keys.append((parameter, bits[1:]))
        return keys
    except DerError:
        return []


def assess(
    evidence: CamEvidence | None, dg14: bytes | None, *, dg14_verified: bool
) -> AaVerdict | None:
    """None when no chip authentication was run (the PACE mapping was not the chip authentication mapping), else a verdict in the same words as Active Authentication so the
    clone check reads the same either way."""
    if evidence is None:
        return None
    if not dg14_verified or not dg14:
        return AaVerdict("NOT_ASSESSED", ("DG14_NOT_COVERED_BY_THE_SIGNED_OBJECT",))
    keys = static_keys(dg14)
    if not keys:
        return AaVerdict("NOT_ASSESSED", ("CA_KEY_NOT_FOUND",))
    candidates = [point for parameter, point in keys if parameter == evidence.curve_id]
    if not candidates:
        return AaVerdict("NOT_ASSESSED", ("CA_KEY_CURVE_NOT_THE_PACE_CURVE",))
    for point in candidates:
        try:
            if pace.verify_chip_authentication(
                evidence.curve_id, evidence.pk_map_ic, evidence.ksenc, evidence.encrypted, point
            ):
                return AaVerdict("GENUINE_KEY_HELD", ("CHIP_AUTHENTICATION_CAM",))
        except pace.PaceError:
            continue
    return AaVerdict("CLONE_SUSPECTED", ("CAM_PROOF_INVALID",))
