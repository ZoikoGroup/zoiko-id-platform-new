"""A simulated passport chip for testing the reader (CR-108): the CHIP side of BAC and secure messaging, holding a set of files.

It is written from the same standard as the reader and shares its low-level primitives, so on its own it only shows the two ends agree. What makes the pair
trustworthy is that the primitives and the authentication exchange are separately checked against the worked example in Doc 9303 part 11 appendix D.
"""

from __future__ import annotations

from collections.abc import Callable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application import pace


class SimulatedChip:
    def __init__(
        self,
        files: dict[bytes, bytes],
        info: bytes,
        *,
        rnd_ic: bytes = bytes.fromhex("4608F91988702212"),
        k_ic: bytes = bytes.fromhex("0B4F80323EB3191CB04970CB4052790B"),
        require_pace: bool = False,
        outer_status_on_error: bytes = b"\x90\x00",
        tamper_response_mac: Callable[[int], bool] | None = None,
        aa_signer: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.files = files
        self.kenc, self.kmac = cr.bac_keys(info)
        self.rnd_ic, self.k_ic = rnd_ic, k_ic
        self.require_pace = require_pace
        self.outer_error = outer_status_on_error
        self.tamper = tamper_response_mac
        self.aa_signer = aa_signer
        self.app = False
        self.selected: bytes | None = None
        self.channel: cr.SecureChannel | None = None
        self.sent: list[bytes] = []  # every response the chip gave, for replay tests
        self._responses = 0

    async def __call__(self, apdu: bytes) -> bytes:
        out = self._handle(apdu)
        self.sent.append(out)
        return out

    # ------------------------------------------------------------------------------------------------------------ plain commands
    def _handle(self, apdu: bytes) -> bytes:
        cla, ins, p1 = apdu[0], apdu[1], apdu[2]
        if cla == 0x0C:
            return self._secure(apdu)
        if (cla, ins, p1) == (0x00, 0xA4, 0x04):
            self.app = apdu[5 : 5 + apdu[4]] == cr.LDS_AID
            return b"\x90\x00" if self.app else b"\x6a\x82"
        if not self.app:
            return b"\x6a\x82"
        if ins == 0x84:
            return b"\x69\x85" if self.require_pace else self.rnd_ic + b"\x90\x00"
        if ins == 0x82:
            return self._external_authenticate(apdu[5 : 5 + apdu[4]])
        return b"\x6d\x00"

    def _external_authenticate(self, data: bytes) -> bytes:
        if self.require_pace or len(data) != 40:
            return b"\x69\x82"
        e_ifd, m_ifd = data[:32], data[32:]
        if cr.mac(self.kmac, e_ifd) != m_ifd:
            return b"\x69\x82"  # wrong document details: the keys differ
        plain = cr.decrypt(self.kenc, e_ifd)
        rnd_ifd, got_ic, k_ifd = plain[:8], plain[8:16], plain[16:]
        if got_ic != self.rnd_ic:
            return b"\x69\x82"
        e = cr.encrypt(self.kenc, self.rnd_ic + rnd_ifd + self.k_ic)
        seed = bytes(a ^ b for a, b in zip(k_ifd, self.k_ic, strict=True))
        self.channel = cr.SecureChannel(
            cr.derive(seed, 1), cr.derive(seed, 2), self.rnd_ic[4:] + rnd_ifd[4:]
        )
        return e + cr.mac(self.kmac, e) + b"\x90\x00"

    # ------------------------------------------------------------------------------------------------------ protected commands
    def _secure(self, apdu: bytes) -> bytes:
        ch = self.channel
        if ch is None:
            return b"\x69\x82"
        header, lc = apdu[:4], apdu[4]
        body = apdu[5 : 5 + lc]
        objects = cr._parse(body)
        ch.ssc = (int.from_bytes(ch.ssc, "big") + 1).to_bytes(8, "big")
        covered = (cr._tlv(0x87, objects[0x87]) if 0x87 in objects else b"") + (
            cr._tlv(0x97, objects[0x97]) if 0x97 in objects else b""
        )
        if cr.mac(ch.ksmac, ch.ssc + cr.pad(header) + covered) != objects.get(0x8E):
            return b"\x69\x88"
        data = cr.unpad(cr.decrypt(ch.ksenc, objects[0x87][1:])) if 0x87 in objects else b""
        le = objects[0x97][0] if 0x97 in objects else 0
        out, status = self._process(header, data, le)
        return self._protect_response(out, status)

    def _process(self, header: bytes, data: bytes, le: int) -> tuple[bytes, bytes]:
        """What the chip does with an authenticated command: select an application or a file, read, or answer Active Authentication."""
        ins = header[1]
        out, status = b"", b"\x90\x00"
        if ins == 0xA4 and header[2] == 0x04:
            self.app = data == cr.LDS_AID
            status = b"\x90\x00" if self.app else b"\x6a\x82"
        elif ins == 0xA4:
            self.selected = data if data in self.files else None
            status = b"\x90\x00" if self.selected is not None else b"\x6a\x82"
        elif ins == 0xB0:
            offset = (header[2] << 8) | header[3]
            content = self.files.get(self.selected or b"")
            if content is None:
                status = b"\x69\x86"
            elif offset >= len(content):
                status = b"\x6b\x00"
            else:
                out = content[offset : offset + (le or 256)]
        elif ins == 0x88 and self.aa_signer is not None:
            out = self.aa_signer(
                data
            )  # INTERNAL AUTHENTICATE: sign the eight bytes with the chip's key
        else:
            status = b"\x6d\x00"
        return out, status

    def _protect_response(self, data: bytes, status: bytes) -> bytes:
        ch = self.channel
        assert ch is not None
        ch.ssc = (int.from_bytes(ch.ssc, "big") + 1).to_bytes(8, "big")
        do87 = cr._tlv(0x87, b"\x01" + cr.encrypt(ch.ksenc, cr.pad(data))) if data else b""
        do99 = cr._tlv(0x99, status)
        m = cr.mac(ch.ksmac, ch.ssc + do87 + do99)
        self._responses += 1
        if self.tamper is not None and self.tamper(self._responses):
            m = bytes([m[0] ^ 1]) + m[1:]
        outer = b"\x90\x00" if status == b"\x90\x00" else self.outer_error
        return do87 + do99 + cr._tlv(0x8E, m) + outer


# ----------------------------------------------------------------------------------------------------- a chip that offers PACE (CR-108)


def pace_info(oid: bytes, curve_id: int) -> bytes:
    """One PACEInfo: SEQUENCE { protocol, version 2, parameterId }."""
    body = cr._tlv(0x06, oid) + bytes([0x02, 0x01, 0x02, 0x02, 0x01, curve_id])
    return cr._tlv(0x30, body)


def card_access(*infos: bytes) -> bytes:
    """EF.CardAccess: a SET OF SecurityInfo."""
    return cr._tlv(0x31, b"".join(infos))


class PaceSimulatedChip(SimulatedChip):
    """The CHIP side of PACE (generic mapping, ECDH, AES) and of the AES secure messaging that follows, written separately from the reader's machine.

    It shares the elliptic-curve arithmetic and the key derivation with the production module, which the standard's worked example proves; what it adds is the
    chip's half of the protocol and the chip's half of the AES channel, so the machine's sequencing and its AES channel are exercised against an independent writer.
    """

    def __init__(
        self,
        files: dict[bytes, bytes],
        info: bytes,
        *,
        curve_id: int = 12,
        bits: int = 128,
        infos: tuple[bytes, ...] | None = None,
        accept_bac: bool = False,
        wrong_token: bool = False,
        integrated: bool = False,
        dh: bool = False,
        tdes: bool = False,
        cam: bool = False,
        ca_secret: int | None = None,
        send_ca_data: bool = True,
        can: str | None = None,
        rng_seed: int = 7,
        **kw,
    ) -> None:
        super().__init__(files, info, **kw)
        import random

        self.info = info
        self.curve_id, self.bits = curve_id, bits
        self.integrated, self.dh, self.tdes, self.can = integrated, dh, tdes, can
        if tdes:
            self.bits = 128  # two DES keys
        self.cam, self.send_ca_data = cam, send_ca_data
        mapping = 6 if cam else (3 if integrated else 1) if dh else (4 if integrated else 2)
        self.oid = (
            bytes.fromhex("04007F0007020204")
            + bytes([mapping])
            + bytes([1 if tdes else {128: 2, 192: 3, 256: 4}[bits]])
        )
        self.card_access = card_access(
            *(infos if infos is not None else (pace_info(self.oid, curve_id),))
        )
        self.accept_bac, self.wrong_token = accept_bac, wrong_token
        self.rng = random.Random(rng_seed)
        self.reading_card_access = False
        self.step = -1
        self.aes_keys: tuple[bytes, bytes] | None = None
        self.aes_ssc = b"\x00" * 16
        self.requests: list[bytes] = []  # every command the chip received, for the framing tests
        # the chip authentication mapping: the chip's static key pair (section 6.2), made apart from the mapping key
        self.ca_sk = (
            ca_secret if ca_secret is not None else (self._scalar_for(curve_id) if cam else 0)
        )

    def _scalar_for(self, curve_id: int) -> int:
        c = pace.CURVES[curve_id]
        return pace.scalar_from(self.rng.randbytes(c.size + 16), c)

    def static_public(self) -> bytes:
        """PK_IC, the chip's static public key, as an uncompressed point (what data group 14 carries)."""
        c = self._curve()
        return pace.encode_point(c, pace.mul(c, self.ca_sk, c.g))

    # ------------------------------------------------------------------------------------------------------------ dispatch
    def _handle(self, apdu: bytes) -> bytes:
        self.requests.append(apdu)
        cla, ins, p1, p2 = apdu[0], apdu[1], apdu[2], apdu[3]
        if cla == 0x0C and self.aes_keys is not None:
            return self._secure_aes(apdu)
        if ins == 0xA4 and p1 == 0x02 and cla == 0x00:  # select a file in the master file
            self.reading_card_access = apdu[5 : 5 + apdu[4]] == bytes.fromhex("011C")
            return b"\x90\x00" if self.reading_card_access else b"\x6a\x82"
        if ins == 0xB0 and cla == 0x00 and self.reading_card_access:
            offset = (p1 << 8) | p2
            return self.card_access[offset : offset + (apdu[4] or 256)] + b"\x90\x00"
        if ins == 0x22 and p1 == 0xC1 and p2 == 0xA4:
            return self._mse(apdu[5 : 5 + apdu[4]])
        if ins == 0x86:
            return self._general_authenticate(apdu)
        if not self.accept_bac and ins in (
            0x84,
            0x82,
        ):  # a chip that requires PACE refuses Basic Access Control
            return b"\x69\x82"
        return super()._handle(apdu)

    # ---------------------------------------------------------------------------------------------------------------- PACE
    def _curve(self) -> pace.Curve:
        return pace.CURVES[self.curve_id]

    def _scalar(self) -> int:
        if self.dh:
            g = pace.DH_GROUPS[self.curve_id]
            return pace.scalar_below(self.rng.randbytes(g.size + 16), g.q)
        return pace.scalar_from(self.rng.randbytes(self._curve().size + 16), self._curve())

    def _mse(self, data: bytes) -> bytes:
        objects = cr._parse(data)
        ref = objects.get(0x83)
        if (
            objects.get(0x80) != self.oid
            or ref not in (b"\x01", b"\x02")
            or (ref == b"\x02" and self.can is None)
        ):
            return b"\x6a\x80"
        if (
            0x84 in objects
        ):  # the terminal named the domain parameters (the chip offered more than one set)
            self.curve_id = objects[0x84][0]
        password = self.can.encode("latin-1") if ref == b"\x02" else self.info
        self.k_pi = pace.password_key(password, self.bits, tdes=self.tdes, can=ref == b"\x02")
        self.s = self.rng.randbytes(pace.nonce_length(self.bits) if self.integrated else 16)
        self.step = 0
        return b"\x90\x00"

    def _general_authenticate(self, apdu: bytes) -> bytes:
        body = (
            apdu[7 : 7 + int.from_bytes(apdu[5:7], "big")]
            if apdu[4] == 0 and len(apdu) > 7
            else apdu[5 : 5 + apdu[4]]
        )  # short or extended length
        objects = cr._parse(cr._parse(body)[0x7C])
        if self.dh:
            return self._dh_step(objects)
        c = self._curve()
        if self.step == 0:
            out = cr._tlv(0x80, pace.encrypt_nonce(self.k_pi, self.s, tdes=self.tdes))
        elif self.step == 1 and self.integrated:
            self.mapped = pace.encode_to_group(c, pace.rp(c, self.s, objects[0x81], self.bits))
            out = cr._tlv(0x82, b"")  # the chip answers the nonce with an empty data object
        elif self.step == 1:
            self.sk_map = self._scalar()
            h = pace.mul(c, self.sk_map, pace.decode_point(c, objects[0x81]))
            self.mapped = pace.add(c, pace.mul(c, int.from_bytes(self.s, "big"), c.g), h)
            out = cr._tlv(0x82, pace.encode_point(c, pace.mul(c, self.sk_map, c.g)))
        elif self.step == 2:
            sk = self._scalar()
            self.pk_ifd = pace.decode_point(c, objects[0x83])
            self.pk_ic = pace.encode_point(c, pace.mul(c, sk, self.mapped))
            secret = pace.mul(c, sk, self.pk_ifd)[0].to_bytes(c.size, "big")  # type: ignore[index]
            self._agree(secret)
            out = cr._tlv(0x84, self.pk_ic)
        elif self.step == 3:
            return self._tokens(objects, pace.encode_point(c, self.pk_ifd))
        else:
            return b"\x6a\x80"
        self.step += 1
        return cr._tlv(0x7C, out) + b"\x90\x00"

    def _dh_step(self, objects: dict[int, bytes]) -> bytes:
        g = pace.DH_GROUPS[self.curve_id]
        if self.step == 0:
            out = cr._tlv(0x80, pace.encrypt_nonce(self.k_pi, self.s, tdes=self.tdes))
        elif self.step == 1 and self.integrated:
            v = pace.rp_modulo(g.p, self.s, objects[0x81], self.bits)
            self.mapped = pow(v, (g.p - 1) // g.q, g.p)
            out = cr._tlv(0x82, b"")
        elif self.step == 1:
            self.sk_map = self._scalar()
            h = pow(pace.dh_decode(g, objects[0x81]), self.sk_map, g.p)
            self.mapped = pow(g.g, int.from_bytes(self.s, "big"), g.p) * h % g.p
            out = cr._tlv(0x82, pace.dh_encode(g, pow(g.g, self.sk_map, g.p)))
        elif self.step == 2:
            sk = self._scalar()
            self.pk_ifd = objects[0x83]
            self.pk_ic = pace.dh_encode(g, pow(self.mapped, sk, g.p))
            self._agree(pace.dh_encode(g, pow(pace.dh_decode(g, self.pk_ifd), sk, g.p)))
            out = cr._tlv(0x84, self.pk_ic)
        elif self.step == 3:
            return self._tokens(objects, self.pk_ifd)
        else:
            return b"\x6a\x80"
        self.step += 1
        return cr._tlv(0x7C, out) + b"\x90\x00"

    def _agree(self, secret: bytes) -> None:
        self.aes_keys = None
        self.pending = (
            pace.kdf(secret, 1, self.bits, tdes=self.tdes),
            pace.kdf(secret, 2, self.bits, tdes=self.tdes),
        )

    def _tokens(self, objects: dict[int, bytes], terminal_public: bytes) -> bytes:
        ksenc, ksmac = self.pending
        expected = pace.token_mac(
            ksmac, pace.public_key_object(self.oid, self.pk_ic, dh=self.dh), tdes=self.tdes
        )
        if objects.get(0x85) != expected:
            return b"\x63\x00"  # authentication failed: the password (the document details) was not the chip's
        token = pace.token_mac(
            ksmac, pace.public_key_object(self.oid, terminal_public, dh=self.dh), tdes=self.tdes
        )
        if self.wrong_token:
            token = bytes([token[0] ^ 1]) + token[1:]
        extra = b""
        if self.cam and self.send_ca_data:
            extra = cr._tlv(0x8A, self._encrypted_ca_data(ksenc))
        if self.tdes:
            self.channel = cr.SecureChannel(
                ksenc, ksmac, b"\x00" * 8
            )  # the chip's 3DES secure messaging, the same code as after Basic Access Control
        else:
            self.aes_keys, self.aes_ssc = (ksenc, ksmac), b"\x00" * 16
        self.step += 1
        return cr._tlv(0x7C, cr._tlv(0x86, token) + extra) + b"\x90\x00"

    def _encrypted_ca_data(self, ksenc: bytes) -> bytes:
        """A_IC = E(KSEnc, CA_IC) with CA_IC = SK_IC^-1 * SK_Map,IC mod n (section 4.4.3.5.1): AES-CBC, IV = E(KSEnc, FF..FF), padding method 2."""
        c = self._curve()
        ca = pow(self.ca_sk, -1, c.n) * self.sk_map % c.n
        plain = ca.to_bytes(c.size, "big") + b"\x80"
        plain += b"\x00" * (-len(plain) % 16)
        ecb = Cipher(algorithms.AES(ksenc), modes.ECB()).encryptor()
        iv = ecb.update(b"\xff" * 16) + ecb.finalize()
        enc = Cipher(algorithms.AES(ksenc), modes.CBC(iv)).encryptor()
        return enc.update(plain) + enc.finalize()

    # ----------------------------------------------------------------------------------------- AES secure messaging, chip side
    def _bump(self) -> bytes:
        self.aes_ssc = (int.from_bytes(self.aes_ssc, "big") + 1).to_bytes(16, "big")
        return self.aes_ssc

    def _iv(self, ssc: bytes) -> bytes:
        ksenc = self.aes_keys[0]  # type: ignore[index]
        enc = Cipher(algorithms.AES(ksenc), modes.ECB()).encryptor()
        return enc.update(ssc) + enc.finalize()

    def _secure_aes(self, apdu: bytes) -> bytes:
        ksenc, ksmac = self.aes_keys  # type: ignore[misc]
        header, lc = apdu[:4], apdu[4]
        objects = cr._parse(apdu[5 : 5 + lc])
        ssc = self._bump()
        covered = (cr._tlv(0x87, objects[0x87]) if 0x87 in objects else b"") + (
            cr._tlv(0x97, objects[0x97]) if 0x97 in objects else b""
        )
        if pace.cmac8(ksmac, cr._pad16(ssc + cr._pad16(header) + covered)) != objects.get(0x8E):
            return b"\x69\x88"
        data = b""
        if 0x87 in objects:
            dec = Cipher(algorithms.AES(ksenc), modes.CBC(self._iv(ssc))).decryptor()
            data = cr.unpad16(dec.update(objects[0x87][1:]) + dec.finalize())
        out, status = self._process(header, data, objects[0x97][0] if 0x97 in objects else 0)
        ssc = self._bump()
        do87 = b""
        if out:
            enc = Cipher(algorithms.AES(ksenc), modes.CBC(self._iv(ssc))).encryptor()
            do87 = cr._tlv(0x87, b"\x01" + enc.update(cr._pad16(out)) + enc.finalize())
        do99 = cr._tlv(0x99, status)
        m = pace.cmac8(ksmac, cr._pad16(ssc + do87 + do99))
        self._responses += 1
        if self.tamper is not None and self.tamper(self._responses):
            m = bytes([m[0] ^ 1]) + m[1:]
        return (
            do87
            + do99
            + cr._tlv(0x8E, m)
            + (b"\x90\x00" if status == b"\x90\x00" else self.outer_error)
        )
