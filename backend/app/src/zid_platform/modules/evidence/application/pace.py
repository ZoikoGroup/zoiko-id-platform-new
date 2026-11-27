"""PACE, the Password Authenticated Connection Establishment (ICAO Doc 9303 part 11, section 4.4; CR-108).

The inspection-system (terminal) side only, sans-I/O: bytes and integers in, bytes and integers out. `chip_reader.ChipMachine` drives it.

What is implemented:
- ECDH and DH (the MODP groups 0 to 2 of RFC 5114), each with the generic mapping, and the integrated mapping with AES, with AES session keys of 128, 192 or 256 bits;
  the generic mapping also with 3DES session keys (two DES keys, retail MAC). The password is the MRZ or the Card Access Number (CAN).
- The standardized curves of Table 12: NIST P-192, P-224, P-256, P-384, P-521 and Brainpool P192r1, P224r1, P256r1, P320r1, P384r1, P512r1.
- NOT implemented, and refused by name: the Chip Authentication Mapping, the integrated mapping with 3DES (the standard does not say how its pseudo-random function is
  built), explicit domain parameters. A chip that offers only those is reported as such, never guessed at.

How it is proved: every intermediate value of the standard's own worked examples is asserted in the tests: Appendix G.1 (ECDH generic mapping), G.2 (DH generic mapping),
H.1 (ECDH integrated mapping) and H.2 (DH integrated mapping): the password key, the decrypted nonce, the mapping, the key agreement, the session keys and both tokens.
The exchanged messages of G.1 are asserted byte for byte. The standard prints no 3DES example and no AES secure-messaging example, so those parts follow the written rules
and are checked only against a chip simulator written separately (see `chip_reader.AesChannel`). The Brainpool P192/P224/P320 and MODP constants come from RFC 5639 and
RFC 5114 and are verified by arithmetic in the tests (prime fields, generator on the curve, order of the generator, subgroup membership).

Elliptic-curve arithmetic is written here because the crypto library only exposes the x-coordinate of a key agreement and the mapping needs a full point. It is checked
against the library where the library has the curve. It is NOT constant time: the private values are single-use ephemeral keys protecting one read and are never reused,
but a process that is exposed to a local timing attacker should not run it.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC


class PaceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Curve:
    name: str
    p: int
    a: int
    b: int
    gx: int
    gy: int
    n: int

    @property
    def size(self) -> int:
        return (self.p.bit_length() + 7) // 8

    @property
    def g(self) -> Point:
        return (self.gx, self.gy)


Point = tuple[int, int] | None  # None is the point at infinity

# The standardized domain parameters of Table 12 of the standard (by their identifier) that the crypto library provides. Each constant was derived from the library's
# own curve and, for BrainpoolP256r1, equals the listing printed in Appendix G of the standard.
CURVES: dict[int, Curve] = {
    9: Curve(
        "BrainpoolP192R1",
        0xC302F41D932A36CDA7A3463093D18DB78FCE476DE1A86297,
        0x6A91174076B1E0E19C39C031FE8685C1CAE040E5C69A28EF,
        0x469A28EF7C28CCA3DC721D044F4496BCCA7EF4146FBF25C9,
        0xC0A0647EAAB6A48753B033C56CB0F0900A2F5C4853375FD6,
        0x14B690866ABD5BB88B5F4828C1490002E6773FA2FA299B8F,
        0xC302F41D932A36CDA7A3462F9E9E916B5BE8F1029AC4ACC1,
    ),
    11: Curve(
        "BrainpoolP224R1",
        0xD7C134AA264366862A18302575D1D787B09F075797DA89F57EC8C0FF,
        0x68A5E62CA9CE6C1C299803A6C1530B514E182AD8B0042A59CAD29F43,
        0x2580F63CCFE44138870713B1A92369E33E2135D266DBB372386C400B,
        0x0D9029AD2C7E5CF4340823B2A87DC68C9E4CE3174C1E6EFDEE12C07D,
        0x58AA56F772C0726F24C6B89E4ECDAC24354B9E99CAA3F6D3761402CD,
        0xD7C134AA264366862A18302575D0FB98D116BC4B6DDEBCA3A5A7939F,
    ),
    14: Curve(
        "BrainpoolP320R1",
        0xD35E472036BC4FB7E13C785ED201E065F98FCFA6F6F40DEF4F92B9EC7893EC28FCD412B1F1B32E27,
        0x3EE30B568FBAB0F883CCEBD46D3F3BB8A2A73513F5EB79DA66190EB085FFA9F492F375A97D860EB4,
        0x520883949DFDBC42D3AD198640688A6FE13F41349554B49ACC31DCCD884539816F5EB4AC8FB1F1A6,
        0x43BD7E9AFB53D8B85289BCC48EE5BFE6F20137D10A087EB6E7871E2A10A599C710AF8D0D39E20611,
        0x14FDD05545EC1CC8AB4093247F77275E0743FFED117182EAA9C77877AAAC6AC7D35245D1692E8EE1,
        0xD35E472036BC4FB7E13C785ED201E065F98FCFA5B68F12A32D482EC7EE8658E98691555B44C59311,
    ),
    8: Curve(
        "SECP192R1",
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFC,
        0x64210519E59C80E70FA7E9AB72243049FEB8DEECC146B9B1,
        0x188DA80EB03090F67CBF20EB43A18800F4FF0AFD82FF1012,
        0x07192B95FFC8DA78631011ED6B24CDD573F977A11E794811,
        0xFFFFFFFFFFFFFFFFFFFFFFFF99DEF836146BC9B1B4D22831,
    ),
    10: Curve(
        "SECP224R1",
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF000000000000000000000001,
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFE,
        0xB4050A850C04B3ABF54132565044B0B7D7BFD8BA270B39432355FFB4,
        0xB70E0CBD6BB4BF7F321390B94A03C1D356C21122343280D6115C1D21,
        0xBD376388B5F723FB4C22DFE6CD4375A05A07476444D5819985007E34,
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFF16A2E0B8F03E13DD29455C5C2A3D,
    ),
    12: Curve(
        "SECP256R1",
        0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
        0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
        0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
        0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
        0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
        0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
    ),
    13: Curve(
        "BrainpoolP256R1",
        0xA9FB57DBA1EEA9BC3E660A909D838D726E3BF623D52620282013481D1F6E5377,
        0x7D5A0975FC2C3057EEF67530417AFFE7FB8055C126DC5C6CE94A4B44F330B5D9,
        0x26DC5C6CE94A4B44F330B5D9BBD77CBF958416295CF7E1CE6BCCDC18FF8C07B6,
        0x8BD2AEB9CB7E57CB2C4B482FFC81B7AFB9DE27E1E3BD23C23A4453BD9ACE3262,
        0x547EF835C3DAC4FD97F8461A14611DC9C27745132DED8E545C1D54C72F046997,
        0xA9FB57DBA1EEA9BC3E660A909D838D718C397AA3B561A6F7901E0E82974856A7,
    ),
    15: Curve(
        "SECP384R1",
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF,
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFC,
        0xB3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875AC656398D8A2ED19D2A85C8EDD3EC2AEF,
        0xAA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A385502F25DBF55296C3A545E3872760AB7,
        0x3617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C00A60B1CE1D7E819D7A431D7C90EA0E5F,
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973,
    ),
    16: Curve(
        "BrainpoolP384R1",
        0x8CB91E82A3386D280F5D6F7E50E641DF152F7109ED5456B412B1DA197FB71123ACD3A729901D1A71874700133107EC53,
        0x7BC382C63D8C150C3C72080ACE05AFA0C2BEA28E4FB22787139165EFBA91F90F8AA5814A503AD4EB04A8C7DD22CE2826,
        0x04A8C7DD22CE28268B39B55416F0447C2FB77DE107DCD2A62E880EA53EEB62D57CB4390295DBC9943AB78696FA504C11,
        0x1D1C64F068CF45FFA2A63A81B7C13F6B8847A3E77EF14FE3DB7FCAFE0CBD10E8E826E03436D646AAEF87B2E247D4AF1E,
        0x8ABE1D7520F9C2A45CB1EB8E95CFD55262B70B29FEEC5864E19C054FF99129280E4646217791811142820341263C5315,
        0x8CB91E82A3386D280F5D6F7E50E641DF152F7109ED5456B31F166E6CAC0425A7CF3AB6AF6B7FC3103B883202E9046565,
    ),
    17: Curve(
        "BrainpoolP512R1",
        0xAADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA703308717D4D9B009BC66842AECDA12AE6A380E62881FF2F2D82C68528AA6056583A48F3,
        0x7830A3318B603B89E2327145AC234CC594CBDD8D3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CA,
        0x3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CADC083E67984050B75EBAE5DD2809BD638016F723,
        0x81AEE4BDD82ED9645A21322E9C4C6A9385ED9F70B5D916C1B43B62EEF4D0098EFF3B1F78E2D0D48D50D1687B93B97D5F7C6D5047406A5E688B352209BCB9F822,
        0x7DDE385D566332ECC0EABFA9CF7822FDF209F70024A57B1AA000C55B881F8111B2DCDE494A5F485E5BCA4BD88A2763AED1CA2B2FA8F0540678CD1E0F3AD80892,
        0xAADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA70330870553E5C414CA92619418661197FAC10471DB1D381085DDADDB58796829CA90069,
    ),
    18: Curve(
        "SECP521R1",
        0x01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF,
        0x01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC,
        0x0051953EB9618E1C9A1F929A21A0B68540EEA2DA725B99B315F3B8B489918EF109E156193951EC7E937B1652C0BD3BB1BF073573DF883D2C34F1EF451FD46B503F00,
        0x00C6858E06B70404E9CD9E3ECB662395B4429C648139053FB521F828AF606B4D3DBAA14B5E77EFE75928FE1DC127A2FFA8DE3348B3C1856A429BF97E7E31C2E5BD66,
        0x011839296A789A3BC0045C8A5FB42C7D1BD998F54449579B446817AFBD17273E662C97EE72995EF42640C550B9013FAD0761353C7086A272C24088BE94769FD16650,
        0x01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA51868783BF2F966B7FCC0148F709A5D03BB5C9B8899C47AEBB6FB71E91386409,
    ),
}


@dataclass(frozen=True, slots=True)
class DhGroup:
    """A MODP group with a prime-order subgroup (RFC 5114, Table 12 of the standard, identifiers 0 to 2): prime p, subgroup generator g, subgroup order q."""

    name: str
    p: int
    g: int
    q: int

    @property
    def size(self) -> int:
        return (self.p.bit_length() + 7) // 8


DH_GROUPS: dict[int, DhGroup] = {
    0: DhGroup(
        "MODP1024_160",
        0xB10B8F96A080E01DDE92DE5EAE5D54EC52C99FBCFB06A3C69A6A9DCA52D23B616073E28675A23D189838EF1E2EE652C013ECB4AEA906112324975C3CD49B83BFACCBDD7D90C4BD7098488E9C219A73724EFFD6FAE5644738FAA31A4FF55BCCC0A151AF5F0DC8B4BD45BF37DF365C1A65E68CFDA76D4DA708DF1FB2BC2E4A4371,
        0xA4D1CBD5C3FD34126765A442EFB99905F8104DD258AC507FD6406CFF14266D31266FEA1E5C41564B777E690F5504F213160217B4B01B886A5E91547F9E2749F4D7FBD7D3B9A92EE1909D0D2263F80A76A6A24C087A091F531DBF0A0169B6A28AD662A4D18E73AFA32D779D5918D08BC8858F4DCEF97C2A24855E6EEB22B3B2E5,
        0xF518AA8781A8DF278ABA4E7D64B7CB9D49462353,
    ),
    1: DhGroup(
        "MODP2048_224",
        0xAD107E1E9123A9D0D660FAA79559C51FA20D64E5683B9FD1B54B1597B61D0A75E6FA141DF95A56DBAF9A3C407BA1DF15EB3D688A309C180E1DE6B85A1274A0A66D3F8152AD6AC2129037C9EDEFDA4DF8D91E8FEF55B7394B7AD5B7D0B6C12207C9F98D11ED34DBF6C6BA0B2C8BBC27BE6A00E0A0B9C49708B3BF8A317091883681286130BC8985DB1602E714415D9330278273C7DE31EFDC7310F7121FD5A07415987D9ADC0A486DCDF93ACC44328387315D75E198C641A480CD86A1B9E587E8BE60E69CC928B2B9C52172E413042E9B23F10B0E16E79763C9B53DCF4BA80A29E3FB73C16B8E75B97EF363E2FFA31F71CF9DE5384E71B81C0AC4DFFE0C10E64F,
        0xAC4032EF4F2D9AE39DF30B5C8FFDAC506CDEBE7B89998CAF74866A08CFE4FFE3A6824A4E10B9A6F0DD921F01A70C4AFAAB739D7700C29F52C57DB17C620A8652BE5E9001A8D66AD7C17669101999024AF4D027275AC1348BB8A762D0521BC98AE247150422EA1ED409939D54DA7460CDB5F6C6B250717CBEF180EB34118E98D119529A45D6F834566E3025E316A330EFBB77A86F0C1AB15B051AE3D428C8F8ACB70A8137150B8EEB10E183EDD19963DDD9E263E4770589EF6AA21E7F5F2FF381B539CCE3409D13CD566AFBB48D6C019181E1BCFE94B30269EDFE72FE9B6AA4BD7B5A0F1C71CFFF4C19C418E1F6EC017981BC087F2A7065B384B890D3191F2BFA,
        0x801C0D34C58D93FE997177101F80535A4738CEBCBF389A99B36371EB,
    ),
    2: DhGroup(
        "MODP2048_256",
        0x87A8E61DB4B6663CFFBBD19C651959998CEEF608660DD0F25D2CEED4435E3B00E00DF8F1D61957D4FAF7DF4561B2AA3016C3D91134096FAA3BF4296D830E9A7C209E0C6497517ABD5A8A9D306BCF67ED91F9E6725B4758C022E0B1EF4275BF7B6C5BFC11D45F9088B941F54EB1E59BB8BC39A0BF12307F5C4FDB70C581B23F76B63ACAE1CAA6B7902D52526735488A0EF13C6D9A51BFA4AB3AD8347796524D8EF6A167B5A41825D967E144E5140564251CCACB83E6B486F6B3CA3F7971506026C0B857F689962856DED4010ABD0BE621C3A3960A54E710C375F26375D7014103A4B54330C198AF126116D2276E11715F693877FAD7EF09CADB094AE91E1A1597,
        0x3FB32C9B73134D0B2E77506660EDBD484CA7B18F21EF205407F4793A1A0BA12510DBC15077BE463FFF4FED4AAC0BB555BE3A6C1B0C6B47B1BC3773BF7E8C6F62901228F8C28CBB18A55AE31341000A650196F931C77A57F2DDF463E5E9EC144B777DE62AAAB8A8628AC376D282D6ED3864E67982428EBC831D14348F6F2F9193B5045AF2767164E1DFC967C1FB3F2E55A4BD1BFFE83B9C80D052B985D182EA0ADB2A3B7313D3FE14C8484B1E052588B9B7D2BBD2DF016199ECD06E1557CD0915B3353BBB64E0EC377FD028370DF92B52C7891428CDC67EB6184B523D1DB246C32F63078490F00EF8D647D148D47954515E2327CFEF98C582664B4C0F6CC41659,
        0x8CF83642A709A097B447997640129DA299B1A47D1EB3750BA308B0FE64F5FBD3,
    ),
}


# ------------------------------------------------------------------------------------------------------------- the group


def _inv(x: int, p: int) -> int:
    return pow(x, -1, p)


def add(c: Curve, p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        if (y1 + y2) % c.p == 0:
            return None
        lam = (3 * x1 * x1 + c.a) * _inv(2 * y1, c.p) % c.p
    else:
        lam = (y2 - y1) * _inv(x2 - x1, c.p) % c.p
    x3 = (lam * lam - x1 - x2) % c.p
    return (x3, (lam * (x1 - x3) - y1) % c.p)


def mul(c: Curve, k: int, point: Point) -> Point:
    """k times a point, by double and add. Not constant time (see the module note)."""
    if point is None or k % c.n == 0:
        return None
    k %= c.n
    result: Point = None
    addend = point
    while k:
        if k & 1:
            result = add(c, result, addend)
        addend = add(c, addend, addend)  # type: ignore[assignment]
        k >>= 1
    return result


def on_curve(c: Curve, point: Point) -> bool:
    if point is None:
        return False
    x, y = point
    return 0 <= x < c.p and 0 <= y < c.p and (y * y - (x * x * x + c.a * x + c.b)) % c.p == 0


def encode_point(c: Curve, point: Point) -> bytes:
    if point is None:
        raise PaceError("POINT_AT_INFINITY")
    return b"\x04" + point[0].to_bytes(c.size, "big") + point[1].to_bytes(c.size, "big")


def decode_point(c: Curve, data: bytes) -> tuple[int, int]:
    """An uncompressed point, which must lie on the curve (the standard recommends validating every ephemeral public key). Anything else is refused."""
    if len(data) != 1 + 2 * c.size or data[0] != 0x04:
        raise PaceError("PACE_KEY_INVALID", "not an uncompressed point of this curve's size")
    point = (int.from_bytes(data[1 : 1 + c.size], "big"), int.from_bytes(data[1 + c.size :], "big"))
    if not on_curve(c, point):
        raise PaceError("PACE_KEY_INVALID", "the point is not on the curve")
    return point


def scalar_below(seed: bytes, order: int) -> int:
    """A private value in [1, order-1] from random bytes (take at least eight more bytes than the order to make the bias negligible)."""
    return 1 + int.from_bytes(seed, "big") % (order - 1)


def scalar_from(seed: bytes, c: Curve) -> int:
    return scalar_below(seed, c.n)


# --------------------------------------------------------------------------------------------------------------- the keys


def adjust_parity(key: bytes) -> bytes:
    """DES keys carry odd parity in the low bit of each byte (section 9.7.1.1)."""
    return bytes(b if b.bit_count() % 2 == 1 else b ^ 1 for b in key)


def kdf(secret: bytes, counter: int, bits: int, *, tdes: bool = False) -> bytes:
    """KDF(K, c) of section 9.7.1: H(K || c), c a 32-bit big-endian counter, truncated to the key length. SHA-1 for 3DES and AES-128 (for 3DES the first sixteen octets
    are two DES keys, with their parity adjusted), SHA-256 for AES-192 and AES-256."""
    digest = hashlib.sha1 if bits == 128 else hashlib.sha256
    key = digest(secret + counter.to_bytes(4, "big")).digest()[: bits // 8]
    return adjust_parity(key) if tdes else key


def password_key(
    mrz_information: bytes, bits: int, *, tdes: bool = False, can: bool = False
) -> bytes:
    """K = KDF(f(password), 3), section 9.7.3. For the MRZ f is SHA-1 of the MRZ information (document number, birth date, expiry date, each with its check digit);
    for the CAN f is the CAN itself as an ISO/IEC 8859-1 string."""
    f = mrz_information if can else hashlib.sha1(mrz_information).digest()
    return kdf(f, 3, bits, tdes=tdes)


def _aes_cbc_zero(key: bytes, data: bytes, *, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16))
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def _reader() -> (
    Any
):  # the 3DES primitives live with the Basic Access Control code; imported when needed because that module imports this one
    from zid_platform.modules.evidence.application import chip_reader

    return chip_reader


def decrypt_nonce(key: bytes, z: bytes, *, tdes: bool = False) -> bytes:
    """s = D(K, z): CBC mode with an all-zero IV (section 4.4.3.3), AES or two-key 3DES."""
    if not z or len(z) % (8 if tdes else 16):
        raise PaceError("PACE_NONCE_INVALID")
    return _reader().decrypt(key, z) if tdes else _aes_cbc_zero(key, z, encrypt=False)


def encrypt_nonce(key: bytes, s: bytes, *, tdes: bool = False) -> bytes:
    return _reader().encrypt(key, s) if tdes else _aes_cbc_zero(key, s, encrypt=True)


def cmac8(key: bytes, data: bytes) -> bytes:
    """AES in CMAC mode with a MAC length of eight bytes (sections 4.4.3.4 and 9.8.7.2)."""
    mac = CMAC(algorithms.AES(key))
    mac.update(data)
    return mac.finalize()[:8]


def token_mac(key: bytes, data: bytes, *, tdes: bool = False) -> bytes:
    """The authentication code of a token: AES CMAC with an eight-byte output, or for 3DES the retail MAC (ISO/IEC 9797-1 algorithm 3, padding method 2), section 4.4.3.4."""
    return _reader().mac(key, data) if tdes else cmac8(key, data)


# ------------------------------------------------------------------------------------------------------------- protocol ids

_PREFIX = bytes.fromhex("04007F000702020402")  # id-PACE-ECDH-GM
GM_AES = {
    _PREFIX + bytes([2]): 128,
    _PREFIX + bytes([3]): 192,
    _PREFIX + bytes([4]): 256,
}  # id-PACE-ECDH-GM-AES-CBC-CMAC-128/192/256
_IM_PREFIX = bytes.fromhex("04007F000702020404")  # id-PACE-ECDH-IM
IM_AES = {
    _IM_PREFIX + bytes([2]): 128,
    _IM_PREFIX + bytes([3]): 192,
    _IM_PREFIX + bytes([4]): 256,
}  # id-PACE-ECDH-IM-AES-CBC-CMAC-128/192/256
NO_INTEGRATED_MAPPING = frozenset(
    {10}
)  # NIST P-224: the standard says this curve cannot be used with the integrated mapping (p is 1 mod 4)
PACE_PREFIX = bytes.fromhex(
    "04007F0007020204"
)  # id-PACE = {bsi-de protocols smartcard 4}; one more arc says the mapping, one the cipher
_MAPPINGS = {
    1: "DH generic mapping",
    2: "ECDH generic mapping",
    3: "DH integrated mapping",
    4: "ECDH integrated mapping",
    6: "ECDH chip authentication mapping",
}


@dataclass(frozen=True, slots=True)
class Variant:
    """What a PACE protocol identifier asks for: the group (DH or ECDH), the mapping, and the cipher of the session keys."""

    dh: bool
    integrated: bool
    tdes: bool
    bits: int  # the session key size in bits: 128 for 3DES (two DES keys)
    cam: bool = False  # the chip authentication mapping: the generic mapping, plus the chip proves it holds the private key of its static key (section 4.4.3.5)


CAM_MAPPING = 6  # id-PACE-ECDH-CAM


def variant(oid: bytes) -> Variant | None:
    """The variant a protocol identifier names, or None if this reader does not implement it (the integrated mapping with 3DES, for which the standard does not say how
    the pseudo-random function is built; the chip authentication mapping with anything but AES)."""
    if not (oid.startswith(PACE_PREFIX) and len(oid) == len(PACE_PREFIX) + 2):
        return None
    mapping, cipher = oid[len(PACE_PREFIX)], oid[-1]
    if mapping == CAM_MAPPING:
        # ECDH only, AES only (the standard lists three object identifiers: AES-128, 192 and 256)
        return Variant(False, False, False, 64 * cipher, cam=True) if cipher in (2, 3, 4) else None
    if mapping not in (1, 2, 3, 4) or cipher not in (1, 2, 3, 4):
        return None
    integrated = mapping in (3, 4)
    if integrated and cipher == 1:
        return None
    return Variant(
        dh=mapping in (1, 3),
        integrated=integrated,
        tdes=cipher == 1,
        bits=128 if cipher == 1 else 64 * cipher,
    )


def usable(oid: bytes, parameter_id: int | None) -> bool:
    """Whether this reader can run the protocol with those domain parameters."""
    v = variant(oid)
    if v is None or parameter_id is None:
        return False
    if v.dh:
        return parameter_id in DH_GROUPS
    return parameter_id in CURVES and not (v.integrated and parameter_id in NO_INTEGRATED_MAPPING)


def describe_unsupported(oid: bytes, parameter_id: int | None = None) -> str:
    """Why a PACEInfo cannot be used, in words, so that a chip that offers only such variants is reported by name and never guessed at."""
    if oid.startswith(PACE_PREFIX) and len(oid) == len(PACE_PREFIX) + 2:
        mapping, cipher = oid[len(PACE_PREFIX)], oid[-1]
        name = _MAPPINGS.get(mapping, "an unknown PACE mapping")
        v = variant(oid)
        if v is None:
            return f"{name} with 3DES session keys" if cipher == 1 else name
        if v.integrated and not v.dh and parameter_id in NO_INTEGRATED_MAPPING:
            return "ECDH integrated mapping on NIST P-224, which the standard excludes"
        if parameter_id is not None and not usable(oid, parameter_id):
            return f"{name} with domain parameters {parameter_id}"
        return name
    return "an algorithm this reader does not support"


# ----------------------------------------------------------------------------------------------- the integrated mapping

_C0 = {
    128: bytes.fromhex("a668892a7c41e3ca739f40b057d85904"),
    256: bytes.fromhex("d463d65234124ef7897054986dca0a174e28df758cbaa03f240616414d5a1676"),
}
_C1 = {
    128: bytes.fromhex("a4e136ac725f738b01c1f60217c188ad"),
    256: bytes.fromhex("54bd7255f0aaf831bec3423fcf39d69b6cbf066677d0faae5aadd99df8e53517"),
}


def nonce_length(bits: int) -> int:
    """Bytes of the chip's nonce s for the integrated mapping: the smallest multiple of the block size that holds a key (32 for AES-192, else the key size)."""
    return 32 if bits == 192 else bits // 8


def rp_modulo(p: int, s: bytes, t: bytes, bits: int) -> int:
    """The pseudo-random function Rp(s, t) of section 4.4.3.3.2 (Figure 2): AES in CBC mode with a zero IV, keyed first by t and then by each derived key in turn;
    n blocks are chosen so that n times the block length is at least log2 p plus 64 bits. The result is read as a number and reduced modulo p."""
    group = (
        256 if bits > 128 else 128
    )  # the constants and the output block are 256 bits for AES-192 and AES-256
    c0, c1 = _C0[group], _C1[group]
    if len(t) != bits // 8 or len(s) != nonce_length(bits):
        raise PaceError("PACE_NONCE_INVALID", "the nonces have the wrong length")
    n = -(-(p.bit_length() + 64) // group)
    key = _aes_cbc_zero(t, s, encrypt=True)[: bits // 8]
    out = b""
    for _ in range(n):
        out += _aes_cbc_zero(key, c1, encrypt=True)
        key = _aes_cbc_zero(key, c0, encrypt=True)[: bits // 8]
    return int.from_bytes(out, "big") % p


def rp(c: Curve, s: bytes, t: bytes, bits: int) -> int:
    return rp_modulo(c.p, s, t, bits)


def encode_to_group(c: Curve, t: int) -> tuple[int, int]:
    """fG of Appendix B.2 (affine coordinates): a point of the prime-order subgroup from a field element 0 < t < p; the cofactor is one on every supported curve.
    The standard defines it only for p = 3 mod 4."""
    p = c.p
    if p % 4 != 3 or c.a % p == 0 or not 0 < t < p:
        raise PaceError("PACE_UNSUPPORTED", "the point encoding needs p = 3 mod 4 and 0 < t < p")
    alpha = -t * t % p
    x2 = -c.b * pow(c.a, p - 2, p) * (1 + pow((alpha + alpha * alpha) % p, p - 2, p)) % p
    x3 = alpha * x2 % p
    h2 = (x2**3 + c.a * x2 + c.b) % p
    u = pow(t, 3, p) * h2 % p
    a = pow(h2, p - 1 - (p + 1) // 4, p)
    point = (x2, a * h2 % p) if a * a * h2 % p == 1 else (x3, a * u % p)
    if not on_curve(c, point):
        raise PaceError("PACE_KEY_INVALID", "the encoded point is not on the curve")
    return point


def _der_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return bytes([0x81, n])
    return bytes([0x82]) + n.to_bytes(2, "big")


def _tlv(tag: bytes, value: bytes) -> bytes:
    return tag + _der_length(len(value)) + value


def public_key_object(oid: bytes, encoded_point: bytes, *, dh: bool = False) -> bytes:
    """The data an authentication token covers: a public key data object 7F49 holding the protocol's identifier and the plain public value (section 4.4.3.4): tag 86 for
    an elliptic-curve point, tag 84 for a DH public value."""
    return _tlv(b"\x7f\x49", _tlv(b"\x06", oid) + _tlv(b"\x84" if dh else b"\x86", encoded_point))


# ---------------------------------------------------------------------------------------------------- the DH (modular) groups


def dh_valid(g: DhGroup, y: int) -> bool:
    """Public-key validation (RFC 2631): 1 < y < p - 1 and y has order q, so a peer cannot confine the secret to a small subgroup."""
    return 1 < y < g.p - 1 and pow(y, g.q, g.p) == 1


def dh_encode(g: DhGroup, y: int) -> bytes:
    return y.to_bytes(g.size, "big")  # an unsigned integer as long as the prime


def dh_decode(g: DhGroup, data: bytes) -> int:
    if len(data) != g.size:
        raise PaceError("PACE_KEY_INVALID", "the public value has the wrong length")
    y = int.from_bytes(data, "big")
    if not dh_valid(g, y):
        raise PaceError("PACE_KEY_INVALID", "the public value is not a valid group element")
    return y


# ----------------------------------------------------------------------------------------------------------- the terminal


@dataclass(slots=True)
class PaceTerminal:
    """The terminal's half of PACE: ECDH or DH, generic or integrated mapping, AES or 3DES session keys. The caller feeds the chip's messages in order; every value is plain
    data, so the whole state can be sealed between steps (`to_state` / `from_state`)."""

    oid: bytes
    bits: int
    curve_id: int  # the domain parameters: a curve (ECDH) or a MODP group (DH)
    sk_map: int
    sk_eph: int
    k_pi: bytes = b""  # the key derived from the password
    s: bytes = b""  # the decrypted nonce
    gx: int = 0  # the mapped generator (for DH the group element, with gy = 0)
    gy: int = 0
    pk_ic: bytes = b""  # the chip's ephemeral public value, as received
    ksenc: bytes = b""
    ksmac: bytes = b""
    t: bytes = b""  # the terminal's nonce for the integrated mapping, sent in clear
    pk_map_ic: bytes = b""  # CAM: the chip's mapping public key, as received (what the chip's static key must reproduce)
    ca_data: bytes = b""  # CAM: the encrypted chip authentication data, tag 8A of the last answer

    @property
    def variant(self) -> Variant:
        v = variant(self.oid)
        if v is None:
            raise PaceError("PACE_UNSUPPORTED", describe_unsupported(self.oid))
        return v

    @property
    def integrated(self) -> bool:
        return self.variant.integrated

    @property
    def dh(self) -> bool:
        return self.variant.dh

    @property
    def tdes(self) -> bool:
        return self.variant.tdes

    @property
    def cam(self) -> bool:
        return self.variant.cam

    @classmethod
    def start(
        cls,
        oid: bytes,
        curve_id: int,
        password: bytes,
        *,
        can: bool = False,
        random: Callable[[int], bytes] = os.urandom,
    ) -> PaceTerminal:
        """`password` is the MRZ information, or with `can` the Card Access Number."""
        v = variant(oid)
        if v is None:
            raise PaceError("PACE_UNSUPPORTED", describe_unsupported(oid))
        if not usable(oid, curve_id):
            raise PaceError("PACE_UNSUPPORTED", describe_unsupported(oid, curve_id))
        order = DH_GROUPS[curve_id].q if v.dh else CURVES[curve_id].n
        size = DH_GROUPS[curve_id].size if v.dh else CURVES[curve_id].size
        first = random(size + 16)
        terminal = cls(
            oid,
            v.bits,
            curve_id,
            scalar_below(first, order),
            scalar_below(random(size + 16), order),
            password_key(password, v.bits, tdes=v.tdes, can=can),
        )
        if v.integrated:
            terminal.t = first[
                : v.bits // 8
            ]  # the mapping private key is not used; the same random draw gives the nonce t
        return terminal

    @property
    def curve(self) -> Curve:
        return CURVES[self.curve_id]

    @property
    def group(self) -> DhGroup:
        return DH_GROUPS[self.curve_id]

    # step 1: the encrypted nonce
    def receive_nonce(self, z: bytes) -> None:
        self.s = decrypt_nonce(self.k_pi, z, tdes=self.tdes)

    # step 2: map the nonce
    def mapping_public(self) -> bytes:
        """What the terminal sends in the mapping step: its mapping public key (generic mapping) or the nonce t in clear (integrated mapping)."""
        if self.integrated:
            return self.t
        if self.dh:
            g = self.group
            return dh_encode(g, pow(g.g, self.sk_map, g.p))
        c = self.curve
        return encode_point(c, mul(c, self.sk_map, c.g))

    def receive_mapping(self, answer: bytes) -> None:
        """Generic mapping: H = the Diffie-Hellman value of the two mapping keys, then G' = s*G + H (ECDH) or g' = g^s * h (DH). Integrated mapping: G' = fG(Rp(s, t)) or
        g' = Rp(s, t)^((p-1)/q); the chip's answer is empty."""
        if self.integrated:
            if answer:
                raise PaceError("PACE_RESPONSE_INVALID", "the chip answered the nonce with data")
            if self.dh:
                g = self.group
                mapped = pow(rp_modulo(g.p, self.s, self.t, self.bits), (g.p - 1) // g.q, g.p)
                if mapped == 1:
                    raise PaceError("PACE_KEY_INVALID", "the mapped generator is the identity")
                self.gx, self.gy = mapped, 0
            else:
                c = self.curve
                self.gx, self.gy = encode_to_group(c, rp(c, self.s, self.t, self.bits))
            return
        if self.dh:
            g = self.group
            h = pow(dh_decode(g, answer), self.sk_map, g.p)
            mapped = pow(g.g, int.from_bytes(self.s, "big"), g.p) * h % g.p
            if mapped == 1:
                raise PaceError("PACE_KEY_INVALID", "the mapped generator is the identity")
            self.gx, self.gy = mapped, 0
            return
        c = self.curve
        if self.cam:
            self.pk_map_ic = answer
        h_point = mul(c, self.sk_map, decode_point(c, answer))
        mapped_point = add(c, mul(c, int.from_bytes(self.s, "big"), c.g), h_point)
        if mapped_point is None:
            raise PaceError("PACE_KEY_INVALID", "the mapped generator is the point at infinity")
        self.gx, self.gy = mapped_point

    # step 3: the key agreement on the mapped generator
    def ephemeral_public(self) -> bytes:
        if self.dh:
            g = self.group
            return dh_encode(g, pow(self.gx, self.sk_eph, g.p))
        c = self.curve
        return encode_point(c, mul(c, self.sk_eph, (self.gx, self.gy)))

    def receive_ephemeral(self, pk_ic: bytes) -> None:
        if self.dh:
            g = self.group
            peer = dh_decode(g, pk_ic)
            if pk_ic == self.ephemeral_public():
                raise PaceError("PACE_KEY_INVALID", "the two ephemeral public keys are equal")
            secret = dh_encode(g, pow(peer, self.sk_eph, g.p))
        else:
            c = self.curve
            ec_peer = decode_point(c, pk_ic)
            if pk_ic == self.ephemeral_public():
                raise PaceError("PACE_KEY_INVALID", "the two ephemeral public keys are equal")
            shared = mul(c, self.sk_eph, ec_peer)
            if shared is None:
                raise PaceError("PACE_KEY_INVALID", "the shared point is the point at infinity")
            secret = shared[0].to_bytes(
                c.size, "big"
            )  # only the x-coordinate is used (section 9.7.1)
        self.pk_ic = pk_ic
        self.ksenc = kdf(secret, 1, self.bits, tdes=self.tdes)
        self.ksmac = kdf(secret, 2, self.bits, tdes=self.tdes)

    # step 4: the authentication tokens
    def token_ifd(self) -> bytes:
        """TIFD = MAC(KSMAC, public key object over the CHIP's ephemeral public value)."""
        return token_mac(
            self.ksmac, public_key_object(self.oid, self.pk_ic, dh=self.dh), tdes=self.tdes
        )

    def verify_token_ic(self, token: bytes) -> bool:
        """TIC = MAC(KSMAC, public key object over the TERMINAL's ephemeral public value). Compared in constant time."""
        import hmac

        expected = token_mac(
            self.ksmac,
            public_key_object(self.oid, self.ephemeral_public(), dh=self.dh),
            tdes=self.tdes,
        )
        return hmac.compare_digest(expected, token)

    def receive_ca_data(self, data: bytes | None) -> None:
        """Section 4.4.5: the encrypted chip authentication data MUST be present with the chip authentication mapping and MUST NOT be present otherwise."""
        if self.cam and not data:
            raise PaceError(
                "PACE_CAM_DATA_MISSING", "the chip did not send its chip authentication data"
            )
        if not self.cam and data:
            raise PaceError(
                "PACE_RESPONSE_INVALID",
                "chip authentication data without the chip authentication mapping",
            )
        self.ca_data = data or b""

    # ------------------------------------------------------------------------------------------------------- plain state
    def to_state(self) -> dict[str, object]:
        return {
            "oid": self.oid.hex(),
            "bits": self.bits,
            "curve_id": self.curve_id,
            "sk_map": f"{self.sk_map:x}",
            "sk_eph": f"{self.sk_eph:x}",
            "k_pi": self.k_pi.hex(),
            "s": self.s.hex(),
            "gx": f"{self.gx:x}",
            "gy": f"{self.gy:x}",
            "pk_ic": self.pk_ic.hex(),
            "ksenc": self.ksenc.hex(),
            "ksmac": self.ksmac.hex(),
            "t": self.t.hex(),
            "pk_map_ic": self.pk_map_ic.hex(),
            "ca_data": self.ca_data.hex(),
        }

    @classmethod
    def from_state(cls, state: dict[str, object]) -> PaceTerminal:
        try:
            return cls(
                bytes.fromhex(str(state["oid"])),
                int(state["bits"]),  # type: ignore[call-overload]
                int(state["curve_id"]),  # type: ignore[call-overload]
                int(str(state["sk_map"]), 16),
                int(str(state["sk_eph"]), 16),
                bytes.fromhex(str(state["k_pi"])),
                bytes.fromhex(str(state["s"])),
                int(str(state["gx"]), 16),
                int(str(state["gy"]), 16),
                bytes.fromhex(str(state["pk_ic"])),
                bytes.fromhex(str(state["ksenc"])),
                bytes.fromhex(str(state["ksmac"])),
                bytes.fromhex(str(state.get("t", ""))),
                bytes.fromhex(str(state.get("pk_map_ic", ""))),
                bytes.fromhex(str(state.get("ca_data", ""))),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise PaceError("STATE_INVALID") from e


# ------------------------------------------------------------------------------------------------ chip authentication (CAM)


def chip_authentication_data(ksenc: bytes, encrypted: bytes) -> bytes:
    """CA_IC = D(KSEnc, A_IC) (section 4.4.3.5): AES in CBC mode with the IV E(KSEnc, 0xFF..FF), padding method 2 removed. The result is the FE2OS encoding of an integer
    modulo the group order, so it must be exactly the size of the order (section 4.4.5.6)."""
    if not encrypted or len(encrypted) % 16 or len(ksenc) not in (16, 24, 32):
        raise PaceError(
            "PACE_CAM_DATA_INVALID", "the encrypted chip authentication data is malformed"
        )
    ecb = Cipher(algorithms.AES(ksenc), modes.ECB()).encryptor()
    iv = ecb.update(b"\xff" * 16) + ecb.finalize()
    cbc = Cipher(algorithms.AES(ksenc), modes.CBC(iv)).decryptor()
    padded = cbc.update(encrypted) + cbc.finalize()
    end = padded.rstrip(b"\x00")
    if not end.endswith(b"\x80") or len(padded) - len(end) > 15:
        raise PaceError(
            "PACE_CAM_DATA_INVALID", "the padding of the chip authentication data is not valid"
        )
    return end[:-1]


def verify_chip_authentication(
    curve_id: int, pk_map_ic: bytes, ksenc: bytes, encrypted: bytes, ca_public: bytes
) -> bool:
    """Section 4.4.3.5.2: PK_Map,IC must equal KA(CA_IC, PK_IC, D_IC), which for ECDH is CA_IC times the chip's static public point. Only a chip that holds the private key of
    PK_IC can make a CA_IC that does this, because CA_IC = SK_IC^-1 * SK_Map,IC mod n. True only when it matches exactly.

    This proves the chip holds the key. Whether that key belongs to this passport is for passive authentication to say: PK_IC must come from a data group the signed object covered."""
    if curve_id not in CURVES:
        raise PaceError("PACE_UNSUPPORTED", f"domain parameters {curve_id}")
    c = CURVES[curve_id]
    try:
        scalar_bytes = chip_authentication_data(ksenc, encrypted)
        if len(scalar_bytes) != c.size:
            return False
        scalar = int.from_bytes(scalar_bytes, "big")
        if not 1 <= scalar < c.n:
            return False
        mapped = decode_point(c, pk_map_ic)
        static = decode_point(c, ca_public)
    except PaceError:
        return False
    return mul(c, scalar, static) == mapped
