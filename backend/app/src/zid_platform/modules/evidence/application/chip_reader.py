"""Our own reader for a passport or ID chip: PACE, Basic Access Control, secure messaging and file reading (ICAO Doc 9303 part 10 and 11; CR-108).

This is the chip side of what `epassport.py` judges. `epassport.verify` checks what a chip signed; this module is how the signed object and the data groups are got
off the chip. It is sans-I/O: it never touches NFC, USB or a socket. The caller gives it one function that carries a command APDU to the chip and brings the
response back (a phone's NFC bridge, a relay, or a desktop smart-card reader), so the protocol is written and tested once, here, in plain Python.

Two drivers share the primitives. `ChipMachine` is the complete one: an explicit state machine (no I/O, no coroutine, plain-data state that can be sealed and resumed by
any process) that tries PACE first and falls back to Basic Access Control. `ChipReader` is the simple async driver for Basic Access Control only; a tested equivalence
keeps the two from drifting.

What it does: look for EF.CardAccess and, if the chip offers PACE with the ECDH generic mapping and AES (`pace.py`), run it from the MRZ, which starts AES secure
messaging (`AesChannel`); otherwise run Basic Access Control, which starts 3DES secure messaging (`SecureChannel`); then select the LDS application and read EF.SOD
and the data groups.

How it is proved. Basic Access Control: every value of the standard's worked example (Appendix D), including the secure-messaging commands and responses byte for byte.
PACE: every value of the standard's worked example (Appendix G.1), and the whole exchange replayed through the machine, command by command, against the standard's
printed APDUs. NOT proved against a printed vector: the AES secure channel that follows PACE (the standard prints no example for it); it is checked against a chip
simulator written separately, and its CMAC is the one the PACE tokens prove.

What it does NOT do, and says so:
- The PACE variants named in `pace.py`: the integrated and chip-authentication mappings, DH, 3DES session keys, the CAN password, explicit domain parameters and three
  Brainpool curves. A chip that offers only those is read with Basic Access Control if it also accepts it, and otherwise refused with the variant it offers named.
- Chip Authentication. Active Authentication is driven (the server checks it).
- It trusts nothing it reads. Whether the data is genuine is `epassport.verify`'s job, against a country list a person approved.
- It has been proved against the standard's vectors and simulated chips. It has not been run on a physical passport: that is a device test nobody has done.

The 3DES here is the protocol's, not a recommendation: BAC is old and weak by design (the key comes from printed data). It protects a read the holder started with the
document in their hand, and nothing more.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from zid_platform.modules.evidence.application import pace
from zid_platform.modules.evidence.application.chip_authentication import CamEvidence
from zid_platform.modules.evidence.domain import mrz as mrz_rules

Transceive = Callable[[bytes], Awaitable[bytes]]

LDS_AID = bytes.fromhex("A0000002471001")
FILE_IDS: dict[str, bytes] = {
    "COM": bytes.fromhex("011E"),
    "SOD": bytes.fromhex("011D"),
    "DG1": bytes.fromhex("0101"),
    "DG2": bytes.fromhex("0102"),
    "DG14": bytes.fromhex("010E"),
    "DG15": bytes.fromhex("010F"),
}
GROUP_FILES = {1: "DG1", 2: "DG2", 14: "DG14", 15: "DG15"}
MAX_FILE = 2 * 1024 * 1024  # a sanity ceiling: a photo group is tens of kilobytes
CHUNK = 0xA0
SW_OK = b"\x90\x00"


class ChipReadError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------------------------------------------------- primitives


def adjust_parity(key: bytes) -> bytes:
    """DES keys carry odd parity in the low bit of each byte (Doc 9303 part 11, 9.7.1)."""
    return bytes(b if b.bit_count() % 2 == 1 else b ^ 1 for b in key)


def pad(data: bytes) -> bytes:
    """ISO/IEC 9797-1 padding method 2: 0x80 then zeros to a multiple of eight."""
    return data + b"\x80" + b"\x00" * (-(len(data) + 1) % 8)


def unpad(data: bytes) -> bytes:
    i = len(data)
    while i > 0 and data[i - 1] == 0:
        i -= 1
    if i == 0 or data[i - 1] != 0x80:
        raise ChipReadError("PADDING_INVALID")
    return data[: i - 1]


def _des3(key: bytes) -> TripleDES:
    if len(key) != 16:
        raise ChipReadError("KEY_LENGTH_INVALID")
    return TripleDES(key + key[:8])


def encrypt(key: bytes, data: bytes) -> bytes:
    """Two-key 3DES in CBC mode with a zero IV, no padding added (the caller pads)."""
    if len(data) % 8:
        raise ChipReadError("BLOCK_LENGTH_INVALID")
    enc = Cipher(_des3(key), modes.CBC(b"\x00" * 8)).encryptor()
    return enc.update(data) + enc.finalize()


def decrypt(key: bytes, data: bytes) -> bytes:
    if len(data) % 8:
        raise ChipReadError("BLOCK_LENGTH_INVALID")
    dec = Cipher(_des3(key), modes.CBC(b"\x00" * 8)).decryptor()
    return dec.update(data) + dec.finalize()


def _des(key8: bytes, data: bytes, *, encrypt_it: bool) -> bytes:
    """Single DES (as 3DES with one key three times): what the retail MAC is built from."""
    cipher = Cipher(TripleDES(key8 * 3), modes.ECB())  # nosec B305 - the retail MAC of ICAO 9303 BAC is defined over single DES blocks; the standard fixes the primitive
    op = cipher.encryptor() if encrypt_it else cipher.decryptor()
    return op.update(data) + op.finalize()


def mac(key: bytes, message: bytes) -> bytes:
    """ISO/IEC 9797-1 MAC algorithm 3 with padding method 2 and DES: CBC with the first key, then decrypt with the second and encrypt with the first."""
    k1, k2 = key[:8], key[8:16]
    padded = pad(message)
    h = b"\x00" * 8
    for i in range(0, len(padded), 8):
        block = bytes(a ^ b for a, b in zip(h, padded[i : i + 8], strict=True))
        h = _des(k1, block, encrypt_it=True)
    return _des(k1, _des(k2, h, encrypt_it=False), encrypt_it=True)


def derive(seed: bytes, counter: int) -> bytes:
    """The key derivation function of Doc 9303 part 11, 9.7.1: SHA-1 of the seed and a four-byte counter, first sixteen bytes, parity adjusted."""
    return adjust_parity(hashlib.sha1(seed + counter.to_bytes(4, "big")).digest()[:16])  # nosec B324 - ICAO 9303 key derivation is defined with SHA-1; it derives a session key, it is not a signature or a password hash


def mrz_information(document_number: str, birth_date: str, expiry_date: str) -> bytes:
    """Document number (nine characters, filler `<`), birth date and expiry date (YYMMDD), each followed by its check digit."""
    number = document_number.upper().ljust(9, "<")
    if len(number) != 9 or not number.replace("<", "").isalnum():
        raise ChipReadError("MRZ_INPUT_INVALID", "document number")
    for name, value in (("birth date", birth_date), ("expiry date", expiry_date)):
        if len(value) != 6 or not value.isdigit():
            raise ChipReadError("MRZ_INPUT_INVALID", name)
    parts = (number, birth_date, expiry_date)
    return "".join(p + str(mrz_rules.check_digit(p)) for p in parts).encode("ascii")


def bac_keys(info: bytes) -> tuple[bytes, bytes]:
    seed = hashlib.sha1(info).digest()[:16]  # nosec B324 - ICAO 9303 BAC key seed is defined with SHA-1 over the MRZ information
    return derive(seed, 1), derive(seed, 2)


# ------------------------------------------------------------------------------------------- the BAC mutual authentication


def external_authenticate_data(
    kenc: bytes, kmac: bytes, rnd_ifd: bytes, rnd_ic: bytes, k_ifd: bytes
) -> bytes:
    """E.IFD || M.IFD, the 40 bytes sent with EXTERNAL AUTHENTICATE."""
    e = encrypt(kenc, rnd_ifd + rnd_ic + k_ifd)
    return e + mac(kmac, e)


def finish_authentication(
    kenc: bytes, kmac: bytes, rnd_ifd: bytes, rnd_ic: bytes, k_ifd: bytes, response: bytes
) -> SecureChannel:
    """Check the chip's answer (E.IC || M.IC) and build the session. A wrong MAC, or randoms that are not ours, is refused."""
    if len(response) != 40:
        raise ChipReadError("BAC_RESPONSE_INVALID", "length")
    e_ic, m_ic = response[:32], response[32:]
    if not hmac.compare_digest(mac(kmac, e_ic), m_ic):
        raise ChipReadError("BAC_RESPONSE_INVALID", "MAC")
    plain = decrypt(kenc, e_ic)
    got_ic, got_ifd, k_ic = plain[:8], plain[8:16], plain[16:]
    if got_ic != rnd_ic or got_ifd != rnd_ifd:
        raise ChipReadError("BAC_RESPONSE_INVALID", "randoms")
    seed = bytes(a ^ b for a, b in zip(k_ifd, k_ic, strict=True))
    return SecureChannel(derive(seed, 1), derive(seed, 2), rnd_ic[4:] + rnd_ifd[4:])


# ------------------------------------------------------------------------------------------------------ secure messaging


def _tlv(tag: int, value: bytes) -> bytes:
    if len(value) < 0x80:
        return bytes([tag, len(value)]) + value
    if len(value) < 0x100:
        return bytes([tag, 0x81, len(value)]) + value
    return bytes([tag, 0x82]) + len(value).to_bytes(2, "big") + value


def _parse(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        tag, first = data[i], data[i + 1] if i + 1 < len(data) else -1
        if first < 0:
            raise ChipReadError("RESPONSE_MALFORMED")
        i += 2
        if first == 0x81:
            length, i = data[i], i + 1
        elif first == 0x82:
            length, i = int.from_bytes(data[i : i + 2], "big"), i + 2
        elif first < 0x80:
            length = first
        else:
            raise ChipReadError("RESPONSE_MALFORMED")
        if i + length > len(data):
            raise ChipReadError("RESPONSE_MALFORMED")
        out[tag] = data[i : i + length]
        i += length
    return out


@dataclass(slots=True)
class SecureChannel:
    """Secure messaging with 3DES and the send sequence counter. Every command and every response moves the counter, so a captured response cannot be replayed."""

    ksenc: bytes
    ksmac: bytes
    ssc: bytes = field(repr=False)

    def _bump(self) -> bytes:
        self.ssc = (int.from_bytes(self.ssc, "big") + 1).to_bytes(8, "big")
        return self.ssc

    def protect(
        self, cla: int, ins: int, p1: int, p2: int, data: bytes = b"", le: int | None = None
    ) -> bytes:
        header = bytes([cla | 0x0C, ins, p1, p2])
        ssc = self._bump()
        body = b""
        if data:
            body += _tlv(0x87, b"\x01" + encrypt(self.ksenc, pad(data)))
        if le is not None:
            body += _tlv(0x97, bytes([le]))
        m = mac(self.ksmac, ssc + pad(header) + body)
        body += _tlv(0x8E, m)
        return header + bytes([len(body)]) + body + b"\x00"

    def unprotect(self, response: bytes) -> tuple[bytes, bytes]:
        """(data, status) of a response. The status of a protected response is the one INSIDE it (after its MAC checked), whatever the outer status says.
        A response with no body is a plain status: the chip refused before it could protect anything. A protected response with a wrong MAC is refused."""
        if len(response) < 2:
            raise ChipReadError("RESPONSE_MALFORMED")
        body = response[:-2]
        if not body:
            return b"", response[-2:]
        objects = _parse(body)
        ssc = self._bump()
        if 0x8E not in objects or 0x99 not in objects:
            raise ChipReadError("SECURE_MESSAGING_INVALID", "missing object")
        covered = (_tlv(0x87, objects[0x87]) if 0x87 in objects else b"") + _tlv(
            0x99, objects[0x99]
        )
        if not hmac.compare_digest(mac(self.ksmac, ssc + covered), objects[0x8E]):
            raise ChipReadError("SECURE_MESSAGING_MAC_INVALID")
        status = objects[0x99]
        if len(status) != 2:
            raise ChipReadError("SECURE_MESSAGING_INVALID", "status")
        data = b""
        if 0x87 in objects:
            if not objects[0x87] or objects[0x87][0] != 0x01:
                raise ChipReadError("SECURE_MESSAGING_INVALID", "padding indicator")
            data = unpad(decrypt(self.ksenc, objects[0x87][1:]))
        return data, status


def _pad16(data: bytes) -> bytes:
    """ISO/IEC 9797-1 padding method 2 to the AES block size."""
    return data + b"\x80" + b"\x00" * (-(len(data) + 1) % 16)


def unpad16(data: bytes) -> bytes:
    i = len(data)
    while i > 0 and data[i - 1] == 0:
        i -= 1
    if i == 0 or data[i - 1] != 0x80:
        raise ChipReadError("PADDING_INVALID")
    return data[: i - 1]


def _aes_ecb(key: bytes, block: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # nosec B305 - a one-block AES step, never a message: the chip protocol defines it so (ICAO 9303 PACE)
    return enc.update(block) + enc.finalize()


@dataclass(slots=True)
class AesChannel:
    """Secure messaging with AES after PACE (section 9.8.7): AES in CBC mode with the IV E(KSEnc, SSC), AES in CMAC mode with an eight-byte MAC over the 128-bit send
    sequence counter and the data, the counter starting at zero and moving before every command and every response. The same message structure as `SecureChannel`.

    The standard prints no worked example for this channel, so it is built from the rules in the text and checked against a simulated chip written separately from it,
    not against a published vector. The CMAC itself is proved by the PACE authentication tokens."""

    ksenc: bytes
    ksmac: bytes
    ssc: bytes = field(default=b"\x00" * 16, repr=False)

    def _bump(self) -> bytes:
        self.ssc = (int.from_bytes(self.ssc, "big") + 1).to_bytes(16, "big")
        return self.ssc

    def _cipher(self, ssc: bytes) -> Cipher:  # type: ignore[type-arg]
        return Cipher(algorithms.AES(self.ksenc), modes.CBC(_aes_ecb(self.ksenc, ssc)))

    def protect(
        self, cla: int, ins: int, p1: int, p2: int, data: bytes = b"", le: int | None = None
    ) -> bytes:
        if ins % 2:
            raise ChipReadError("ODD_INSTRUCTION_UNSUPPORTED")
        header = bytes([cla | 0x0C, ins, p1, p2])
        ssc = self._bump()
        body = b""
        if data:
            enc = self._cipher(ssc).encryptor()
            body += _tlv(0x87, b"\x01" + enc.update(_pad16(data)) + enc.finalize())
        if le is not None:
            body += _tlv(0x97, bytes([le]))
        m = pace.cmac8(self.ksmac, _pad16(ssc + _pad16(header) + body))
        body += _tlv(0x8E, m)
        return header + bytes([len(body)]) + body + b"\x00"

    def unprotect(self, response: bytes) -> tuple[bytes, bytes]:
        if len(response) < 2:
            raise ChipReadError("RESPONSE_MALFORMED")
        body = response[:-2]
        if not body:
            return b"", response[-2:]
        objects = _parse(body)
        ssc = self._bump()
        if 0x8E not in objects or 0x99 not in objects:
            raise ChipReadError("SECURE_MESSAGING_INVALID", "missing object")
        covered = (_tlv(0x87, objects[0x87]) if 0x87 in objects else b"") + _tlv(
            0x99, objects[0x99]
        )
        if not hmac.compare_digest(pace.cmac8(self.ksmac, _pad16(ssc + covered)), objects[0x8E]):
            raise ChipReadError("SECURE_MESSAGING_MAC_INVALID")
        status = objects[0x99]
        if len(status) != 2:
            raise ChipReadError("SECURE_MESSAGING_INVALID", "status")
        data = b""
        if 0x87 in objects:
            if not objects[0x87] or objects[0x87][0] != 0x01 or len(objects[0x87]) % 16 != 1:
                raise ChipReadError("SECURE_MESSAGING_INVALID", "padding indicator")
            dec = self._cipher(ssc).decryptor()
            data = unpad16(dec.update(objects[0x87][1:]) + dec.finalize())
        return data, status


# --------------------------------------------------------------------------------------------------------------- the reader


@dataclass(frozen=True, slots=True)
class ChipRead:
    """What the server's chip routes take: the signed object and the data groups, as the chip sent them. Nothing is judged here."""

    sod: bytes
    data_groups: dict[int, bytes]
    skipped: tuple[str, ...] = ()


def _file_length(header: bytes) -> int:
    """The whole length of an EF from its first bytes: tag, then a BER length."""
    if len(header) < 2:
        raise ChipReadError("FILE_HEADER_INVALID")
    first = header[1]
    if first < 0x80:
        return 2 + first
    count = first & 0x7F
    if count not in (1, 2, 3) or len(header) < 2 + count:
        raise ChipReadError("FILE_HEADER_INVALID")
    return 2 + count + int.from_bytes(header[2 : 2 + count], "big")


class ChipReader:
    def __init__(
        self, transceive: Transceive, *, random: Callable[[int], bytes] = os.urandom
    ) -> None:
        self._send = transceive
        self._random = random
        self.channel: SecureChannel | None = None

    async def _plain(self, apdu: bytes) -> tuple[bytes, bytes]:
        raw = await self._send(apdu)
        if len(raw) < 2:
            raise ChipReadError("RESPONSE_MALFORMED")
        return raw[:-2], raw[-2:]

    async def establish(self, document_number: str, birth_date: str, expiry_date: str) -> None:
        """Select the LDS application and run BAC. A chip that will not do BAC is reported as such, with PACE named as the likely reason."""
        _, sw = await self._plain(bytes([0x00, 0xA4, 0x04, 0x0C, len(LDS_AID)]) + LDS_AID)
        if sw != SW_OK:
            raise ChipReadError("APPLICATION_NOT_FOUND", sw.hex())
        kenc, kmac = bac_keys(mrz_information(document_number, birth_date, expiry_date))
        challenge, sw = await self._plain(bytes.fromhex("0084000008"))
        if sw != SW_OK or len(challenge) != 8:
            raise ChipReadError(
                "BAC_NOT_ACCEPTED",
                "the chip gave no challenge; it may require PACE, which this reader does not support yet",
            )
        rnd_ifd, k_ifd = self._random(8), self._random(16)
        data = external_authenticate_data(kenc, kmac, rnd_ifd, challenge, k_ifd)
        answer, sw = await self._plain(bytes.fromhex("0082000028") + data + b"\x28")
        if sw != SW_OK:
            raise ChipReadError(
                "BAC_NOT_ACCEPTED",
                f"status {sw.hex()}: wrong document details, or the chip requires PACE, which this reader does not support yet",
            )
        self.channel = finish_authentication(kenc, kmac, rnd_ifd, challenge, k_ifd, answer)

    async def _secure(
        self, ins: int, p1: int, p2: int, data: bytes = b"", le: int | None = None
    ) -> tuple[bytes, bytes]:
        if self.channel is None:
            raise ChipReadError("NOT_AUTHENTICATED")
        raw = await self._send(self.channel.protect(0x00, ins, p1, p2, data, le))
        return self.channel.unprotect(raw)

    async def read_file(self, fid: bytes) -> bytes | None:
        """One elementary file, or None when the chip says it has none. Any other refusal is an error."""
        _, sw = await self._secure(0xA4, 0x02, 0x0C, fid)
        if sw in (b"\x6a\x82", b"\x6a\x83"):
            return None
        if sw != SW_OK:
            raise ChipReadError("SELECT_REFUSED", sw.hex())
        head, sw = await self._secure(0xB0, 0x00, 0x00, le=4)
        if sw != SW_OK:
            raise ChipReadError("READ_REFUSED", sw.hex())
        total = _file_length(head)
        if total > MAX_FILE:
            raise ChipReadError("FILE_TOO_LARGE")
        out = bytearray(head)
        while len(out) < total:
            take = min(CHUNK, total - len(out))
            part, sw = await self._secure(0xB0, len(out) >> 8, len(out) & 0xFF, le=take)
            if sw != SW_OK or not part:
                raise ChipReadError("READ_REFUSED", sw.hex())
            out += part
        return bytes(out[:total])

    async def internal_authenticate(self, challenge: bytes) -> bytes | None:
        """Active Authentication: ask the chip to sign eight bytes of ours with the key in DG15 (the server checks the answer against DG15). None when the chip has no
        such key or refuses; the answer is returned exactly as the chip sent it (an ECDSA chip sends r || s). Needs a session."""
        if len(challenge) != 8:
            raise ChipReadError("CHALLENGE_LENGTH_INVALID")
        data, sw = await self._secure(0x88, 0x00, 0x00, challenge, le=0)
        return data if sw == SW_OK and data else None

    async def read(self, *, include_photo: bool = True) -> ChipRead:
        """EF.SOD and the data groups the server's routes use. The signed object is mandatory; a group the chip does not have is skipped and named."""
        sod = await self.read_file(FILE_IDS["SOD"])
        if sod is None:
            raise ChipReadError("SOD_MISSING")
        groups: dict[int, bytes] = {}
        skipped: list[str] = []
        for number, name in GROUP_FILES.items():
            if number == 2 and not include_photo:
                continue
            content = await self.read_file(FILE_IDS[name])
            if content is None:
                skipped.append(name)
            else:
                groups[number] = content
        return ChipRead(sod, groups, tuple(skipped))


# --------------------------------------------------------------------------------------------- the resumable machine (for a stateless relay)

PLAN = ("SOD", "DG1", "DG2", "DG14", "DG15")
_NUMBER_OF = {name: number for number, name in GROUP_FILES.items()}


def _enc(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _dec(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


class ChipMachine:
    """The same read as `ChipReader`, as an explicit state machine with no I/O and no coroutine, so that its whole state is plain data: it can be sealed, handed to
    the device, and resumed by any server process. `next_command()` is the APDU to send next; `feed(response)` takes the chip's answer; `to_state()` and
    `from_state()` carry it between requests. A tested equivalence with `ChipReader` keeps the two from drifting.

    The state holds the access keys, the session keys and everything read so far: it is a secret, and it must only ever leave the process sealed."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._s = state

    @classmethod
    def start(
        cls,
        document_number: str,
        birth_date: str,
        expiry_date: str,
        *,
        include_photo: bool = False,
        aa_challenge: bytes | None = None,
        try_pace: bool = True,
        can: str | None = None,
        random: Callable[[int], bytes] = os.urandom,
    ) -> ChipMachine:
        """With `try_pace` (the default) the machine first looks for EF.CardAccess: a chip that has one supports PACE, which the standard prefers, and PACE is run when the
        chip offers a variant this reader implements. A chip with none (or only variants it does not implement) is read with Basic Access Control as before."""
        if aa_challenge is not None and len(aa_challenge) != 8:
            raise ChipReadError("CHALLENGE_LENGTH_INVALID")
        if can is not None and not (document_number or birth_date or expiry_date):
            # only the Card Access Number is known: it is a PACE password and nothing else, so a chip that cannot do PACE cannot be read this way
            if not (4 <= len(can) <= 16 and can.isascii() and can.isalnum()):
                raise ChipReadError("CAN_INVALID")
            if not try_pace:
                raise ChipReadError("CAN_NEEDS_PACE")
            info, kenc, kmac, pwd_ref = can.encode("latin-1"), b"", b"", 2
        else:
            info = mrz_information(document_number, birth_date, expiry_date)
            kenc, kmac = bac_keys(info)
            pwd_ref = 1
        return cls(
            {
                "phase": "CA_SELECT" if try_pace else "SELECT_APP",
                "sm": "3DES",
                "kenc": kenc.hex(),
                "kmac": kmac.hex(),
                "rnd_ifd": random(8).hex(),
                "k_ifd": random(16).hex(),
                "rnd_ic": "",
                "ksenc": "",
                "ksmac": "",
                "ssc": "",
                "plan": [n for n in PLAN if include_photo or n != "DG2"],
                "idx": 0,
                "buf": "",
                "total": 0,
                "files": {},
                "skipped": [],
                "challenge": aa_challenge.hex() if aa_challenge is not None else None,
                "aa": None,
                "steps": 0,
                "mrz": info.hex(),
                "pwd_ref": pwd_ref,
                "seed_map": random(96).hex(),
                "seed_eph": random(96).hex(),
                "ca": "",
                "ca_total": 0,
                "pace": None,
                "pace_pid": None,
                "pace_note": "",
            }
        )

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ChipMachine:
        needed = {
            "phase",
            "kenc",
            "kmac",
            "rnd_ifd",
            "k_ifd",
            "plan",
            "idx",
            "files",
            "skipped",
            "steps",
        }
        if not needed <= set(state) or state["phase"] not in _PHASES:
            raise ChipReadError("STATE_INVALID")
        return cls(dict(state))

    def to_state(self) -> dict[str, Any]:
        return dict(self._s)

    # -------------------------------------------------------------------------------------------------------------- reading it
    @property
    def phase(self) -> str:
        return str(self._s["phase"])

    @property
    def done(self) -> bool:
        return self.phase == "DONE"

    @property
    def steps(self) -> int:
        return int(self._s["steps"])

    @property
    def current_file(self) -> str | None:
        plan = self._s["plan"]
        return plan[self._s["idx"]] if self._s["idx"] < len(plan) else None

    def chip_authentication(self) -> CamEvidence | None:
        """What the chip authentication mapping left, when PACE ran with it and finished: for `chip_authentication.assess`, which needs data group 14 judged by passive
        authentication before it counts. None when PACE did not run or did not use that mapping."""
        state = self._s.get("pace")
        if not state or not self.done or not self._s.get("ksenc") or self._s.get("sm") != "AES":
            return None
        terminal = pace.PaceTerminal.from_state(state)
        if not terminal.cam or not terminal.ca_data:
            return None
        return CamEvidence(
            terminal.curve_id,
            terminal.pk_map_ic,
            terminal.ksenc,
            terminal.ca_data,
        )

    def result(self) -> tuple[ChipRead, bytes | None]:
        if not self.done:
            raise ChipReadError("READ_NOT_FINISHED")
        files = {name: _dec(text) for name, text in self._s["files"].items()}
        groups = {_NUMBER_OF[name]: data for name, data in files.items() if name in _NUMBER_OF}
        chip = ChipRead(files["SOD"], groups, tuple(self._s["skipped"]))
        answer = self._s["aa"]
        return chip, (_dec(answer) if answer else None)

    # ------------------------------------------------------------------------------------------------------------- the commands
    def _channel(self) -> SecureChannel | AesChannel:
        keys = (
            bytes.fromhex(self._s["ksenc"]),
            bytes.fromhex(self._s["ksmac"]),
            bytes.fromhex(self._s["ssc"]),
        )
        return AesChannel(*keys) if self._s.get("sm") == "AES" else SecureChannel(*keys)

    def _protect(
        self, ins: int, p1: int, p2: int, data: bytes = b"", le: int | None = None
    ) -> bytes:
        channel = self._channel()
        apdu = channel.protect(0x00, ins, p1, p2, data, le)
        self._s["ssc"] = (
            channel.ssc.hex()
        )  # the counter moved: the state a resumed machine starts from already includes this command
        return apdu

    def next_command(self) -> bytes:
        phase = self.phase
        if phase == "CA_SELECT":
            return bytes.fromhex("00A4020C02011C")  # EF.CardAccess, in the master file
        if phase == "CA_HEAD":
            return bytes.fromhex("00B0000004")
        if phase == "CA_BODY":
            have = len(_dec(self._s["ca"]))
            return bytes(
                [0x00, 0xB0, have >> 8, have & 0xFF, min(CHUNK, self._s["ca_total"] - have)]
            )
        if phase.startswith("PACE_"):
            return self._pace_command(phase)
        if phase == "SELECT_APP_SM":
            return self._protect(0xA4, 0x04, 0x0C, LDS_AID)
        if phase == "SELECT_APP":
            return bytes([0x00, 0xA4, 0x04, 0x0C, len(LDS_AID)]) + LDS_AID
        if phase == "CHALLENGE":
            return bytes.fromhex("0084000008")
        if phase == "AUTH":
            kenc, kmac = bytes.fromhex(self._s["kenc"]), bytes.fromhex(self._s["kmac"])
            data = external_authenticate_data(
                kenc,
                kmac,
                bytes.fromhex(self._s["rnd_ifd"]),
                bytes.fromhex(self._s["rnd_ic"]),
                bytes.fromhex(self._s["k_ifd"]),
            )
            return bytes.fromhex("0082000028") + data + b"\x28"
        if phase == "SELECT_FILE":
            return self._protect(0xA4, 0x02, 0x0C, FILE_IDS[str(self.current_file)])
        if phase == "READ_HEAD":
            return self._protect(0xB0, 0x00, 0x00, le=4)
        if phase == "READ_BODY":
            have = len(_dec(self._s["buf"]))
            return self._protect(
                0xB0, have >> 8, have & 0xFF, le=min(CHUNK, self._s["total"] - have)
            )
        if phase == "AA":
            return self._protect(0x88, 0x00, 0x00, bytes.fromhex(str(self._s["challenge"])), le=0)
        raise ChipReadError("READ_FINISHED")

    # --------------------------------------------------------------------------------------------------------------- the answers
    def feed(self, response: bytes) -> None:
        if len(response) < 2:
            raise ChipReadError("RESPONSE_MALFORMED")
        self._s["steps"] += 1
        phase = self.phase
        if phase in ("CA_SELECT", "CA_HEAD", "CA_BODY"):
            self._feed_card_access(phase, response[:-2], response[-2:])
        elif phase.startswith("PACE_"):
            self._feed_pace(phase, response[:-2], response[-2:])
        elif phase == "SELECT_APP_SM":
            _, status = self._unprotect(response)
            if status != SW_OK:
                raise ChipReadError("APPLICATION_NOT_FOUND", status.hex())
            self._s["phase"] = "SELECT_FILE"
        elif phase in ("SELECT_APP", "CHALLENGE", "AUTH"):
            self._feed_plain(response[:-2], response[-2:])
        elif phase == "AA":
            data, status = self._unprotect(response)
            self._s["aa"] = _enc(data) if status == SW_OK and data else None
            self._s["phase"] = "DONE"
        elif phase in ("SELECT_FILE", "READ_HEAD", "READ_BODY"):
            self._feed_file(phase, *self._unprotect(response))
        else:
            raise ChipReadError("READ_FINISHED")

    def _unprotect(self, response: bytes) -> tuple[bytes, bytes]:
        channel = self._channel()
        out = channel.unprotect(response)
        self._s["ssc"] = channel.ssc.hex()
        return out

    def _feed_plain(self, data: bytes, status: bytes) -> None:
        phase = self.phase
        if phase == "SELECT_APP":
            if status != SW_OK:
                raise ChipReadError("APPLICATION_NOT_FOUND", status.hex())
            self._s["phase"] = "CHALLENGE"
        elif phase == "CHALLENGE":
            if status != SW_OK or len(data) != 8:
                raise ChipReadError(
                    "BAC_NOT_ACCEPTED", self._pace_hint("the chip gave no challenge")
                )
            self._s["rnd_ic"] = data.hex()
            self._s["phase"] = "AUTH"
        else:
            if status != SW_OK:
                raise ChipReadError(
                    "BAC_NOT_ACCEPTED",
                    self._pace_hint(f"status {status.hex()}: wrong document details"),
                )
            channel = finish_authentication(
                bytes.fromhex(self._s["kenc"]), bytes.fromhex(self._s["kmac"]), bytes.fromhex(self._s["rnd_ifd"]), bytes.fromhex(self._s["rnd_ic"]),
                bytes.fromhex(self._s["k_ifd"]), data,
            )  # fmt: skip
            self._s["ksenc"], self._s["ksmac"], self._s["ssc"] = (
                channel.ksenc.hex(),
                channel.ksmac.hex(),
                channel.ssc.hex(),
            )
            self._s["phase"] = "SELECT_FILE"

    def _feed_file(self, phase: str, data: bytes, status: bytes) -> None:
        if phase == "SELECT_FILE":
            if status in (b"\x6a\x82", b"\x6a\x83"):  # the chip has no such file
                if self.current_file == "SOD":
                    raise ChipReadError("SOD_MISSING")
                self._s["skipped"].append(str(self.current_file))
                self._next_file()
            elif status != SW_OK:
                raise ChipReadError("SELECT_REFUSED", status.hex())
            else:
                self._s["phase"] = "READ_HEAD"
            return
        if status != SW_OK or (phase == "READ_BODY" and not data):
            raise ChipReadError("READ_REFUSED", status.hex())
        if phase == "READ_HEAD":
            total = _file_length(data)
            if total > MAX_FILE:
                raise ChipReadError("FILE_TOO_LARGE")
            self._s["total"], buffer = total, data
        else:
            buffer = _dec(self._s["buf"]) + data
        if len(buffer) >= self._s["total"]:
            self._s["files"][str(self.current_file)] = _enc(buffer[: self._s["total"]])
            self._s["buf"], self._s["total"] = "", 0
            self._next_file()
        else:
            self._s["buf"] = _enc(buffer)
            self._s["phase"] = "READ_BODY"

    def _pace_hint(self, head: str) -> str:
        note = self._s.get("pace_note")
        if note:
            return f"{head}; the chip offers PACE ({note}), which this reader does not support"
        return f"{head}; the chip may require PACE"

    # ------------------------------------------------------------------------------------------------ PACE (section 4.4)
    def _feed_card_access(self, phase: str, data: bytes, status: bytes) -> None:
        if phase == "CA_SELECT":
            # no EF.CardAccess (or the chip will not show it): the chip does not offer PACE, so read it with Basic Access Control
            if status == SW_OK:
                self._s["phase"] = "CA_HEAD"
            else:
                self._fall_back_to_bac()
            return
        if status != SW_OK or (phase == "CA_BODY" and not data):
            raise ChipReadError("CARD_ACCESS_UNREADABLE", status.hex())
        if phase == "CA_HEAD":
            total = _file_length(data)
            if total > MAX_CARD_ACCESS:
                raise ChipReadError("FILE_TOO_LARGE")
            self._s["ca_total"], buffer = total, data
        else:
            buffer = _dec(self._s["ca"]) + data
        if len(buffer) < self._s["ca_total"]:
            self._s["ca"] = _enc(buffer)
            self._s["phase"] = "CA_BODY"
            return
        self._s["ca"] = ""
        self._choose_pace(buffer[: self._s["ca_total"]])

    def _choose_pace(self, card_access: bytes) -> None:
        infos = parse_card_access(card_access)
        usable = [(oid, pid) for oid, pid in infos if pace.usable(oid, pid)]
        if not usable:
            others = [(oid, pid) for oid, pid in infos if oid.startswith(pace.PACE_PREFIX)]
            self._s["pace_note"] = pace.describe_unsupported(*others[0]) if others else ""
            self._fall_back_to_bac()  # nothing this reader implements
            return
        oid, pid = usable[0]
        terminal = pace.PaceTerminal.start(
            oid,
            pid,  # type: ignore[arg-type]
            bytes.fromhex(self._s["mrz"]),
            can=self._s.get("pwd_ref") == 2,
            random=_seeded(bytes.fromhex(self._s["seed_map"]), bytes.fromhex(self._s["seed_eph"])),
        )
        self._s["pace"] = terminal.to_state()
        # the identifier of the domain parameters is sent only when the chip's choice is ambiguous (section 4.4.4.1)
        offered = sum(1 for o, p in infos if p is not None and o.startswith(pace.PACE_PREFIX))
        self._s["pace_pid"] = pid if offered > 1 else None
        self._s["phase"] = "PACE_MSE"

    def _fall_back_to_bac(self) -> None:
        if self._s.get("pwd_ref") == 2:
            note = self._s.get("pace_note")
            raise ChipReadError(
                "CAN_NEEDS_PACE",
                f"the chip offers PACE only as {note}"
                if note
                else "the chip offers no PACE this reader can run",
            )
        self._s["phase"] = "SELECT_APP"

    def _terminal(self) -> pace.PaceTerminal:
        return pace.PaceTerminal.from_state(self._s["pace"])

    def _pace_command(self, phase: str) -> bytes:
        t = self._terminal()
        if phase == "PACE_MSE":
            data = _tlv(0x80, t.oid) + _tlv(0x83, bytes([self._s.get("pwd_ref", 1)]))
            if self._s["pace_pid"] is not None:
                data += _tlv(0x84, bytes([self._s["pace_pid"]]))
            return bytes([0x00, 0x22, 0xC1, 0xA4, len(data)]) + data
        if phase == "PACE_NONCE":
            return bytes.fromhex("10860000027C0000")
        if phase == "PACE_MAP":
            return self._general_authenticate(0x10, 0x81, t.mapping_public())
        if phase == "PACE_KEY":
            return self._general_authenticate(0x10, 0x83, t.ephemeral_public())
        if phase == "PACE_AUTH":
            return self._general_authenticate(0x00, 0x85, t.token_ifd())
        raise ChipReadError("READ_FINISHED")

    @staticmethod
    def _general_authenticate(cla: int, tag: int, value: bytes) -> bytes:
        data = _tlv(0x7C, _tlv(tag, value))
        if (
            len(data) > 255
        ):  # a 2048-bit DH value does not fit a short APDU: extended length (three-byte Lc, two-byte Le)
            return (
                bytes([cla, 0x86, 0x00, 0x00, 0x00])
                + len(data).to_bytes(2, "big")
                + data
                + b"\x00\x00"
            )
        return bytes([cla, 0x86, 0x00, 0x00, len(data)]) + data + b"\x00"

    @staticmethod
    def _dynamic_optional(data: bytes, tag: int) -> bytes | None:
        try:
            return _parse(_parse(data)[0x7C]).get(tag)
        except (KeyError, ChipReadError, IndexError) as e:
            raise ChipReadError("PACE_RESPONSE_INVALID", "the answer has no dynamic data") from e

    @staticmethod
    def _dynamic(data: bytes, tag: int) -> bytes:
        try:
            return _parse(_parse(data)[0x7C])[tag]
        except (KeyError, ChipReadError, IndexError) as e:
            raise ChipReadError("PACE_RESPONSE_INVALID", f"missing data object {tag:02X}") from e

    def _feed_pace(self, phase: str, data: bytes, status: bytes) -> None:
        if status != SW_OK:
            wrong = " (wrong document details)" if status == b"\x63\x00" else ""
            raise ChipReadError("PACE_NOT_ACCEPTED", f"{phase} status {status.hex()}{wrong}")
        t = self._terminal()
        try:
            if phase == "PACE_MSE":
                self._s["phase"] = "PACE_NONCE"
            elif phase == "PACE_NONCE":
                t.receive_nonce(self._dynamic(data, 0x80))
                self._s["phase"] = "PACE_MAP"
            elif phase == "PACE_MAP":
                t.receive_mapping(self._dynamic(data, 0x82))  # empty for the integrated mapping
                self._s["phase"] = "PACE_KEY"
            elif phase == "PACE_KEY":
                t.receive_ephemeral(self._dynamic(data, 0x84))
                self._s["phase"] = "PACE_AUTH"
            else:
                if not t.verify_token_ic(self._dynamic(data, 0x86)):
                    raise ChipReadError(
                        "PACE_TOKEN_INVALID", "the chip did not prove it holds the same keys"
                    )
                t.receive_ca_data(
                    self._dynamic_optional(data, 0x8A)
                )  # the chip authentication mapping: the chip's proof that it holds its static key (CR-111)
                self._s["ksenc"], self._s["ksmac"], self._s["ssc"] = (
                    t.ksenc.hex(),
                    t.ksmac.hex(),
                    (b"\x00" * (8 if t.tdes else 16)).hex(),
                )
                self._s["sm"] = "3DES" if t.tdes else "AES"
                self._s["phase"] = "SELECT_APP_SM"
        except pace.PaceError as e:
            raise ChipReadError(e.code, e.detail) from e
        self._s["pace"] = t.to_state()

    def _next_file(self) -> None:
        self._s["idx"] += 1
        if self._s["idx"] < len(self._s["plan"]):
            self._s["phase"] = "SELECT_FILE"
        elif self._s["challenge"] is not None and "DG15" in self._s["files"]:
            self._s["phase"] = "AA"
        else:
            self._s["phase"] = "DONE"


_PHASES = frozenset(
    {
        "CA_SELECT",
        "CA_HEAD",
        "CA_BODY",
        "PACE_MSE",
        "PACE_NONCE",
        "PACE_MAP",
        "PACE_KEY",
        "PACE_AUTH",
        "SELECT_APP_SM",
        "SELECT_APP",
        "CHALLENGE",
        "AUTH",
        "SELECT_FILE",
        "READ_HEAD",
        "READ_BODY",
        "AA",
        "DONE",
    }
)

MAX_CARD_ACCESS = 8192


def _seeded(seed_map: bytes, seed_eph: bytes) -> Callable[[int], bytes]:
    """PaceTerminal.start asks for two random values in turn; these are the two the machine drew when it began, so a resumed machine needs no randomness."""
    seeds = iter((seed_map, seed_eph))
    return lambda n: next(seeds)[:n]


def _der_items(data: bytes) -> list[tuple[int, bytes]]:
    """The (tag, value) pairs of one level of DER (one-byte tags: all SecurityInfos use). Anything that does not parse is an error."""
    out: list[tuple[int, bytes]] = []
    i = 0
    while i < len(data):
        if i + 2 > len(data):
            raise ChipReadError("CARD_ACCESS_INVALID")
        tag, first = data[i], data[i + 1]
        i += 2
        if first < 0x80:
            length = first
        elif first in (0x81, 0x82):
            count = first & 0x7F
            if i + count > len(data):
                raise ChipReadError("CARD_ACCESS_INVALID")
            length, i = int.from_bytes(data[i : i + count], "big"), i + count
        else:
            raise ChipReadError("CARD_ACCESS_INVALID")
        if i + length > len(data):
            raise ChipReadError("CARD_ACCESS_INVALID")
        out.append((tag, data[i : i + length]))
        i += length
    return out


def parse_card_access(data: bytes) -> list[tuple[bytes, int | None]]:
    """(protocol identifier, parameterId) of every SecurityInfo in EF.CardAccess that looks like a PACEInfo. The file is unprotected, so it is read only to choose from;
    nothing in it is trusted (a wrong choice makes PACE fail, it never makes anything pass)."""
    root = _der_items(data)
    if len(root) != 1 or root[0][0] != 0x31:
        raise ChipReadError("CARD_ACCESS_INVALID")
    infos: list[tuple[bytes, int | None]] = []
    for tag, body in _der_items(root[0][1]):
        if tag != 0x30:
            continue
        fields = _der_items(body)
        if not fields or fields[0][0] != 0x06:
            continue
        integers = [int.from_bytes(v, "big") for t, v in fields[1:] if t == 0x02]
        infos.append((fields[0][1], integers[1] if len(integers) > 1 else None))
    return infos
