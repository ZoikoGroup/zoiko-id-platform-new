"""PACE with the ECDH generic mapping, proved against ICAO Doc 9303 part 11 appendix G.1 (BrainpoolP256r1, AES-128) and against the crypto library (CR-108).

Every value below that the standard prints for its worked example is asserted: the password key, the decrypted nonce, the mapping (the Diffie-Hellman point H and the mapped
generator), the key agreement, the session keys, the inputs of the two authentication tokens and both tokens. The curve constants and the elliptic-curve arithmetic are checked
against the library, which knows the true curves.
"""

from __future__ import annotations

import random

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application import pace

H = lambda s: bytes.fromhex(s.replace(" ", ""))
I = lambda s: int(s.replace(" ", ""), 16)

# ---- Appendix G.1: the ECDH based example (the MRZ is the password) ----
MRZ_INFO = b"T22000129364081251010318"  # document number, date of birth and date of expiry, each with its check digit
K_PI = H("7E2D2A41 C74EA0B3 8CD36F86 3939BFA8 E9032AAD")
K = H("89DED1B2 6624EC1E 634C1989 302849DD")
S = H("3F00C4D3 9D153F2B 2A214A07 8D899B22")
Z = H("95A3A016 522EE98D 01E76CB6 B98B42C3")
OID = H("04007F00070202040202")  # id-PACE-ECDH-GM-AES-CBC-CMAC-128
BRAINPOOL_P256R1 = 13

IFD_MAP_SK = I("7F4EF07B 9EA82FD7 8AD689B3 8D0BC78C F21F249D 953BC46F 4C6E1925 9C010F99")
IFD_MAP_PK = H(
    "04 7ACF3EFC 982EC455 65A4B155 129EFBC7 4650DCBF A6362D89 6FC70262 E0C2CC5E 544552DC B6725218 799115B5 5C9BAA6D 9F6BC3A9 618E70C2 5AF71777 A9C4922D"
)
IC_MAP_PK = H(
    "04 824FBA91 C9CBE26B EF53A0EB E7342A3B F178CEA9 F45DE0B7 0AA60165 1FBA3F57 30D8C879 AAA9C9F7 3991E61B 58F4D52E B87A0A0C 709A49DC 63719363 CCD13C54"
)
H_POINT = (
    I("60332EF2 450B5D24 7EF6D386 8397D398 852ED6E8 CAF6FFEE F6BF85CA 57057FD5"),
    I("0840CA74 15BAF3E4 3BD414D3 5AA4608B 93A2CAF3 A4E3EA4E 82C9C13D 03EB7181"),
)
MAPPED_G = (
    I("8CED63C9 1426D4F0 EB1435E7 CB1D74A4 6723A0AF 21C89634 F65A9AE8 7A9265E2"),
    I("8C879506 743F8611 AC33645C 5B985C80 B5F09A0B 83407C1B 6A4D857A E76FE522"),
)

IFD_SK = I("A73FB703 AC1436A1 8E0CFA5A BB3F7BEC 7A070E7A 6788486B EE230C4A 22762595")
IFD_PK = H(
    "04 2DB7A64C 0355044E C9DF1905 14C625CB A2CEA487 54887122 F3A5EF0D 5EDD301C 3556F3B3 B186DF10 B857B58F 6A7EB80F 20BA5DC7 BE1D43D9 BF850149 FBB36462"
)
IC_SK = I("107CF586 96EF6155 053340FD 633392BA 81909DF7 B9706F22 6F32086C 7AFF974A")
IC_PK = H(
    "04 9E880F84 2905B8B3 181F7AF7 CAA9F0EF B743847F 44A306D2 D28C1D9E C65DF6DB 7764B222 77A2EDDC 3C265A9F 018F9CB8 52E111B7 68B32690 4B59A019 3776F094"
)
SHARED_X = H("28768D20 701247DA E81804C9 E780EDE5 82A9996D B4A31502 0B273319 7DB84925")
KSENC = H("F5F0E35C 0D7161EE 6724EE51 3A0D9A7F")
KSMAC = H("FE251C78 58B356B2 4514B3BD 5F4297D1")
TIFD_INPUT = H(
    "7F494F06 0A04007F 00070202 04020286 41049E88 0F842905 B8B3181F 7AF7CAA9 F0EFB743 847F44A3 06D2D28C 1D9EC65D F6DB7764 B22277A2 EDDC3C26 5A9F018F 9CB852E1 11B768B3 26904B59 A0193776 F094"
)
TIC_INPUT = H(
    "7F494F06 0A04007F 00070202 04020286 41042DB7 A64C0355 044EC9DF 190514C6 25CBA2CE A4875488 7122F3A5 EF0D5EDD 301C3556 F3B3B186 DF10B857 B58F6A7E B80F20BA 5DC7BE1D 43D9BF85 0149FBB3 6462"
)
TIFD = H("C2B0BD78 D94BA866")
TIC = H("3ABB9674 BCE93C08")

C = pace.CURVES[BRAINPOOL_P256R1]


def pt(data: bytes) -> tuple[int, int]:
    return (int.from_bytes(data[1:33], "big"), int.from_bytes(data[33:], "big"))


# ------------------------------------------------------------------------------------------------------ the standard's example


def test_the_password_key_and_the_decrypted_nonce_match_the_standard() -> None:
    import hashlib

    assert hashlib.sha1(MRZ_INFO).digest() == K_PI  # f(MRZ)
    assert pace.password_key(MRZ_INFO, 128) == K  # K = KDF(f(MRZ), 3)
    assert pace.decrypt_nonce(K, Z) == S
    assert pace.encrypt_nonce(K, S) == Z


def test_the_mapping_gives_the_standards_diffie_hellman_point_and_mapped_generator() -> None:
    assert pace.mul(C, IFD_MAP_SK, C.g) == pt(IFD_MAP_PK)  # the terminal's mapping public key
    t = pace.PaceTerminal(OID, 128, BRAINPOOL_P256R1, IFD_MAP_SK, IFD_SK, K, S)
    assert t.mapping_public() == IFD_MAP_PK
    assert pace.mul(C, IFD_MAP_SK, pt(IC_MAP_PK)) == H_POINT  # H
    t.receive_mapping(IC_MAP_PK)
    assert (t.gx, t.gy) == MAPPED_G  # G' = s*G + H


def test_the_key_agreement_gives_the_standards_shared_secret_session_keys_and_tokens() -> None:
    t = pace.PaceTerminal(OID, 128, BRAINPOOL_P256R1, IFD_MAP_SK, IFD_SK, K, S)
    t.receive_mapping(IC_MAP_PK)
    assert t.ephemeral_public() == IFD_PK
    assert pace.mul(C, IC_SK, MAPPED_G) == pt(
        IC_PK
    )  # the chip's ephemeral public key, from its own private key
    assert pace.mul(C, IFD_SK, pt(IC_PK))[0].to_bytes(32, "big") == SHARED_X  # type: ignore[index]
    t.receive_ephemeral(IC_PK)
    assert (t.ksenc, t.ksmac) == (KSENC, KSMAC)
    assert pace.public_key_object(OID, IC_PK) == TIFD_INPUT
    assert pace.public_key_object(OID, IFD_PK) == TIC_INPUT
    assert t.token_ifd() == TIFD
    assert t.verify_token_ic(TIC)
    assert not t.verify_token_ic(TIFD) and not t.verify_token_ic(TIC[:-1] + bytes([TIC[-1] ^ 1]))


def test_a_terminal_started_fresh_reaches_the_same_place_with_the_standards_keys() -> None:
    keys = iter(
        [
            IFD_MAP_SK.to_bytes(32, "big").rjust(48, b"\x00"),
            IFD_SK.to_bytes(32, "big").rjust(48, b"\x00"),
        ]
    )
    t = pace.PaceTerminal.start(
        OID, BRAINPOOL_P256R1, MRZ_INFO, random=lambda n: next(keys)[-n:] if n <= 48 else next(keys)
    )
    # the scalar is 1 + seed mod (n-1), so a seed one less than the standard's key gives the standard's key
    assert t.k_pi == K
    t.sk_map, t.sk_eph = IFD_MAP_SK, IFD_SK
    t.receive_nonce(Z)
    assert t.s == S
    t.receive_mapping(IC_MAP_PK)
    t.receive_ephemeral(IC_PK)
    assert t.token_ifd() == TIFD
    # the whole state is plain data and resumes exactly
    again = pace.PaceTerminal.from_state(t.to_state())
    assert (again.ksenc, again.ksmac, again.token_ifd(), again.mapping_public()) == (
        KSENC,
        KSMAC,
        TIFD,
        IFD_MAP_PK,
    )


# ------------------------------------------------------------------------------------------------------------ refusals


def test_a_bad_point_a_repeated_key_or_an_unsupported_algorithm_is_refused() -> None:
    t = pace.PaceTerminal(OID, 128, BRAINPOOL_P256R1, IFD_MAP_SK, IFD_SK, K, S)
    off_curve = IC_MAP_PK[:-1] + bytes([IC_MAP_PK[-1] ^ 1])
    for bad in (off_curve, b"", b"\x04" + bytes(64), IC_MAP_PK[:-1], b"\x02" + IC_MAP_PK[1:33]):
        with pytest.raises(pace.PaceError) as e:
            t.receive_mapping(bad)
        assert e.value.code == "PACE_KEY_INVALID"
    t.receive_mapping(IC_MAP_PK)
    with pytest.raises(pace.PaceError) as e2:
        t.receive_ephemeral(IFD_PK)  # the chip's key equal to ours
    assert e2.value.code == "PACE_KEY_INVALID" and "equal" in e2.value.detail
    for oid in (
        H("04007F00070202040602"),
        H("04007F00070202040401"),
        H("04007F0007020203"),
        b"",
        OID[:-1] + b"\x07",
    ):
        with pytest.raises(pace.PaceError) as e3:
            pace.PaceTerminal.start(oid, BRAINPOOL_P256R1, MRZ_INFO)
        assert e3.value.code == "PACE_UNSUPPORTED"
    with pytest.raises(pace.PaceError) as e4:
        pace.PaceTerminal.start(
            OID, 3, MRZ_INFO
        )  # domain parameter identifiers 3 to 7 are reserved
    assert e4.value.code == "PACE_UNSUPPORTED"
    for z in (b"", bytes(15), bytes(17)):
        with pytest.raises(pace.PaceError):
            pace.decrypt_nonce(K, z)
    with pytest.raises(pace.PaceError):
        pace.PaceTerminal.from_state({"oid": "00"})


# ------------------------------------------------------------------------------------- the curves and the arithmetic


def lib_curve(curve_id: int):
    return {
        8: ec.SECP192R1,
        10: ec.SECP224R1,
        12: ec.SECP256R1,
        13: ec.BrainpoolP256R1,
        15: ec.SECP384R1,
        16: ec.BrainpoolP384R1,
        17: ec.BrainpoolP512R1,
        18: ec.SECP521R1,
    }[curve_id]()


def lib_point(curve_id: int, k: int) -> tuple[int, int]:
    n = ec.derive_private_key(k, lib_curve(curve_id)).public_key().public_numbers()
    return (n.x, n.y)


LIBRARY_CURVES = (
    8,
    10,
    12,
    13,
    15,
    16,
    17,
    18,
)  # the curves the crypto library has; the other three are checked by arithmetic below


@pytest.mark.parametrize("curve_id", LIBRARY_CURVES)
def test_each_curve_constant_is_the_libraries_curve(curve_id: int) -> None:
    c = pace.CURVES[curve_id]
    assert c.g == lib_point(
        curve_id, 1
    )  # the generator, which the library validates is on its curve
    for k in (2, 3, 5, 7, 100, 12345):
        x, y = lib_point(curve_id, k)
        assert (y * y - (x**3 + c.a * x + c.b)) % c.p == 0  # p, a and b are the curve's
    nx, ny = lib_point(curve_id, c.n - 1)
    assert nx == c.gx and (ny + c.gy) % c.p == 0  # (n-1)G = -G, so n is the group order
    with pytest.raises(ValueError):
        ec.derive_private_key(c.n, lib_curve(curve_id))  # and no scalar at or above it is valid
    assert (
        pace.on_curve(c, c.g)
        and not pace.on_curve(c, (c.gx, c.gy ^ 1))
        and not pace.on_curve(c, None)
    )


def test_brainpool_p256r1_equals_the_listing_printed_in_the_standard() -> None:
    c = pace.CURVES[13]
    assert c.p == I("A9FB57DB A1EEA9BC 3E660A90 9D838D72 6E3BF623 D5262028 2013481D 1F6E5377")
    assert c.a == I("7D5A0975 FC2C3057 EEF67530 417AFFE7 FB8055C1 26DC5C6C E94A4B44 F330B5D9")
    assert c.b == I("26DC5C6C E94A4B44 F330B5D9 BBD77CBF 95841629 5CF7E1CE 6BCCDC18 FF8C07B6")
    assert c.g == (
        I("8BD2AEB9 CB7E57CB 2C4B482F FC81B7AF B9DE27E1 E3BD23C2 3A4453BD 9ACE3262"),
        I("547EF835 C3DAC4FD 97F8461A 14611DC9 C2774513 2DED8E54 5C1D54C7 2F046997"),
    )
    assert c.n == I("A9FB57DB A1EEA9BC 3E660A90 9D838D71 8C397AA3 B561A6F7 901E0E82 974856A7")


@pytest.mark.parametrize("curve_id", LIBRARY_CURVES)
def test_the_arithmetic_agrees_with_the_library_on_random_scalars_and_sums(curve_id: int) -> None:
    c = pace.CURVES[curve_id]
    rng = random.Random(curve_id)
    for _ in range(3):
        k1, k2 = rng.randrange(1, c.n), rng.randrange(1, c.n)
        assert pace.mul(c, k1, c.g) == lib_point(curve_id, k1)
        assert pace.add(c, lib_point(curve_id, k1), lib_point(curve_id, k2)) == lib_point(
            curve_id, (k1 + k2) % c.n
        )
        assert pace.mul(c, k2, pace.mul(c, k1, c.g)) == lib_point(curve_id, (k1 * k2) % c.n)
    assert pace.mul(c, c.n, c.g) is None and pace.mul(c, 0, c.g) is None
    assert pace.add(c, c.g, (c.gx, (-c.gy) % c.p)) is None  # P + (-P)
    assert pace.add(c, c.g, c.g) == lib_point(curve_id, 2)  # doubling


@pytest.mark.parametrize("curve_id", sorted(pace.CURVES))
def test_a_full_exchange_agrees_on_every_curve_and_key_size(curve_id: int) -> None:
    """Two terminals playing both sides with different curves and AES key sizes: both reach the same session keys and accept each other's token."""
    c = pace.CURVES[curve_id]
    for bits, last in ((128, 2), (192, 3), (256, 4)):
        oid = H("04007F00070202040200")[:-1] + bytes([last])
        rng = random.Random(curve_id * 7 + bits)
        a = pace.PaceTerminal.start(oid, curve_id, MRZ_INFO, random=lambda n, r=rng: r.randbytes(n))
        b = pace.PaceTerminal.start(oid, curve_id, MRZ_INFO, random=lambda n, r=rng: r.randbytes(n))
        s = rng.randbytes(16 if bits == 128 else 32)[:16]  # a nonce of one block
        for t in (a, b):
            t.s = s
        a.receive_mapping(b.mapping_public())
        b.receive_mapping(a.mapping_public())
        assert (a.gx, a.gy) == (b.gx, b.gy) and pace.on_curve(c, (a.gx, a.gy))
        a.receive_ephemeral(b.ephemeral_public())
        b.receive_ephemeral(a.ephemeral_public())
        assert (a.ksenc, a.ksmac) == (b.ksenc, b.ksmac) and len(a.ksenc) == bits // 8
        assert b.verify_token_ic(a.token_ifd()) and a.verify_token_ic(b.token_ifd())


# ---- Appendix H.1: the ECDH integrated mapping example (BrainpoolP256r1, AES-128) ----
IM_OID = H("04007F00070202040402")  # id-PACE-ECDH-IM-AES-CBC-CMAC-128
IM_K_PI = H("591468CD A83D6521 9CCCB856 0233600F")
IM_Z = H("143DC40C 08C8E891 FBED7DED B92B64AD")
IM_S = H("2923BE84 E16CD6AE 529049F1 F1BBE9EB")
IM_T = H("5DD4CBFC 96F5453B 130D890A 1CDBAE32")
IM_R = H(
    "E4447E2D FB3586BA C05DDB00 156B57FB B2179A39 49294C97 25418980 0C517BAA 8DA0FF39 7ED8C445 D3E421E4 FEB57322"
)
IM_RP = I("A2F8FF2D F50E52C6 599F386A DCB595D2 29F6A167 ADE2BE5F 2C3296AD D5B7430E")
IM_MAPPED = (
    I("8E82D315 59ED0FDE 92A4D049 8ADD3C23 BABA94FB 77691E31 E90AEA77 FB17D427"),
    I("4C1AE14B D0C3DBAC 0C871B7F 36081693 64437CA3 0AC243A0 89D3F266 C1E60FAD"),
)
IM_SK_IFD = I("A73FB703 AC1436A1 8E0CFA5A BB3F7BEC 7A070E7A 6788486B EE230C4A 22762595")
IM_PK_IFD = H(
    "04 89CBA23F FE96AA18 D824627C 3E934E54 A9FD0B87 A95D1471 DC1C0ABF DCD640D4 6755DE9B 7B778280 B6BEBD57 439ADFEB 0E21FD4E D6DF4257 8C13418A 59B34C37"
)
IM_PK_IC = H(
    "04 67F78E5F 7F768608 2B293E8D 087E0569 16D0F74B C01A5F89 57D0DE45 691E51E8 932B69A9 62B52A09 85AD2C0A 271EE6A1 3A8ADDDC D1A3A994 B9DED257 F4D22753"
)
IM_SECRET = H("4F150FDE 1D4F0E38 E95017B8 91BAE171 33A0DF45 B0D3E18B 60BA7BEA FDC2C713")
IM_KENC = H("0D3FEB33 251A6370 893D62AE 8DAAF51B")
IM_KMAC = H("B01E89E3 D9E8719E 586B50B4 A7506E0B")
IM_TIC = H("75D4D96E 8D5B0308")
IM_TIFD = H("450F02B8 6F6A0909")


def _im_terminal() -> pace.PaceTerminal:
    terminal = pace.PaceTerminal.start(
        IM_OID, BRAINPOOL_P256R1, b"unused", random=lambda n: bytes(range(n))
    )
    terminal.k_pi, terminal.t, terminal.sk_eph = IM_K_PI, IM_T, IM_SK_IFD
    return terminal


def test_the_pseudo_random_function_and_the_point_encoding_match_the_integrated_mapping_example() -> (
    None
):
    c = pace.CURVES[BRAINPOOL_P256R1]
    assert pace.decrypt_nonce(IM_K_PI, IM_Z) == IM_S
    assert (
        pace.rp(c, IM_S, IM_T, 128) == IM_RP
    )  # Rp(s,t), computed from the printed R(s,t) = AES-CBC chain
    assert pace.encode_to_group(c, IM_RP) == IM_MAPPED
    assert pace.on_curve(c, IM_MAPPED)


def test_the_terminal_reproduces_the_integrated_mapping_example_to_the_tokens() -> None:
    terminal = _im_terminal()
    assert (
        terminal.integrated and terminal.mapping_public() == IM_T
    )  # the nonce t goes out in clear
    terminal.receive_nonce(IM_Z)
    assert terminal.s == IM_S
    terminal.receive_mapping(b"")  # the chip's answer to the nonce is an empty data object
    assert (terminal.gx, terminal.gy) == IM_MAPPED
    assert terminal.ephemeral_public() == IM_PK_IFD
    terminal.receive_ephemeral(IM_PK_IC)
    assert (terminal.ksenc, terminal.ksmac) == (IM_KENC, IM_KMAC)
    assert pace.kdf(IM_SECRET, 1, 128) == IM_KENC and pace.kdf(IM_SECRET, 2, 128) == IM_KMAC
    assert terminal.token_ifd() == IM_TIFD
    assert terminal.verify_token_ic(IM_TIC)
    assert (
        pace.public_key_object(IM_OID, IM_PK_IFD)
        .hex()
        .startswith("7f494f060a04007f0007020204040286410489cba23f")
    )


def test_the_integrated_mapping_refuses_data_from_the_chip_a_short_nonce_and_nist_p224() -> None:
    terminal = _im_terminal()
    terminal.receive_nonce(IM_Z)
    with pytest.raises(pace.PaceError):
        terminal.receive_mapping(b"\x01")
    with pytest.raises(pace.PaceError):
        pace.rp(pace.CURVES[BRAINPOOL_P256R1], IM_S, IM_T[:8], 128)
    with pytest.raises(pace.PaceError):
        pace.PaceTerminal.start(
            IM_OID, 10, b"unused"
        )  # NIST P-224 cannot be used with the integrated mapping
    assert "P-224" in pace.describe_unsupported(IM_OID, 10)


@pytest.mark.parametrize("curve_id", [8, 12, 13, 15, 16, 17, 18])
@pytest.mark.parametrize("last", [2, 3, 4])
def test_the_encoding_gives_a_point_in_the_subgroup_on_every_supported_curve_and_key_size(
    curve_id: int, last: int
) -> None:
    c = pace.CURVES[curve_id]
    bits = {2: 128, 3: 192, 4: 256}[last]
    rng = random.Random(curve_id * 10 + last)
    for _ in range(3):
        point = pace.encode_to_group(
            c, pace.rp(c, rng.randbytes(pace.nonce_length(bits)), rng.randbytes(bits // 8), bits)
        )
        assert (
            pace.on_curve(c, point) and pace.mul(c, c.n, point) is None
        )  # order divides n: a point of the prime-order subgroup


def test_the_state_of_an_integrated_terminal_survives_sealing() -> None:
    terminal = _im_terminal()
    terminal.receive_nonce(IM_Z)
    again = pace.PaceTerminal.from_state(terminal.to_state())
    assert again.t == IM_T and again.integrated
    again.receive_mapping(b"")
    assert (again.gx, again.gy) == IM_MAPPED


# ---- the curves and groups the library does not have: constants from RFC 5639 and RFC 5114, proved by arithmetic ----


def is_prime(n: int) -> bool:
    """Miller-Rabin with fixed bases, plenty for a test that the constants are what they claim."""
    if n < 4 or n % 2 == 0:
        return n in (2, 3)
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


@pytest.mark.parametrize(("curve_id", "bits"), [(9, 192), (11, 224), (14, 320)])
def test_the_brainpool_curves_the_library_lacks_are_the_curves_the_rfc_names(
    curve_id: int, bits: int
) -> None:
    c = pace.CURVES[curve_id]
    assert c.p.bit_length() == bits and c.p % 4 == 3 and is_prime(c.p) and is_prime(c.n)
    assert pace.on_curve(c, c.g) and not pace.on_curve(c, (c.gx, c.gy ^ 1))
    assert (
        pace.mul(c, c.n, c.g) is None
    )  # the generator has order n (n is prime, G is not the identity)
    assert pace.mul(c, c.n - 1, c.g) == (c.gx, (-c.gy) % c.p)
    assert (
        abs(c.n - (c.p + 1)) <= 2 * int(c.p**0.5) + 2
    )  # Hasse: the order is close to p + 1, and with a prime n the cofactor is one
    rng = random.Random(bits)
    for _ in range(
        3
    ):  # the arithmetic is a group: (k1 k2) G = k2 (k1 G) = k1 (k2 G), and sums of multiples add up
        k1, k2 = rng.randrange(1, c.n), rng.randrange(1, c.n)
        left = pace.mul(c, k2, pace.mul(c, k1, c.g))
        assert left == pace.mul(c, k1, pace.mul(c, k2, c.g)) == pace.mul(c, k1 * k2 % c.n, c.g)
        assert pace.add(c, pace.mul(c, k1, c.g), pace.mul(c, k2, c.g)) == pace.mul(
            c, (k1 + k2) % c.n, c.g
        )
    assert c.a == I(
        {
            9: "6A91174076B1E0E19C39C031FE8685C1CAE040E5C69A28EF",
            11: "68A5E62CA9CE6C1C299803A6C1530B514E182AD8B0042A59CAD29F43",
            14: "3EE30B568FBAB0F883CCEBD46D3F3BB8A2A73513F5EB79DA66190EB085FFA9F492F375A97D860EB4",
        }[curve_id]
    )  # a spot check against the RFC's printed A


@pytest.mark.parametrize("group_id", [0, 1, 2])
def test_the_modp_groups_are_the_groups_of_rfc_5114(group_id: int) -> None:
    g = pace.DH_GROUPS[group_id]
    assert (g.p.bit_length(), g.q.bit_length()) == [(1024, 160), (2048, 224), (2048, 256)][group_id]
    assert is_prime(g.p) and is_prime(g.q) and (g.p - 1) % g.q == 0
    assert pow(g.g, g.q, g.p) == 1 and g.g not in (
        0,
        1,
        g.p - 1,
    )  # the generator has order q, so it generates the prime-order subgroup
    assert (
        not pace.dh_valid(g, 1) and not pace.dh_valid(g, g.p - 1) and not pace.dh_valid(g, 2)
    )  # 2 is not in the subgroup
    assert pace.dh_valid(g, pow(g.g, 12345, g.p))


# ---- the DH worked examples, G.2 and H.2 (the public values are the ones in the standard's printed APDUs and tables) ----
DG_Z = H("854D8DF5827FA6852D1A4FA701CDDDCA")
DG_S = H("FA5B7E3E49753A0DB9178B7B9BD898C8")
DG_SK_MAP_IFD = H("5265030F751F4AD18B08AC565FC7AC952E41618D")
DG_SK_EPH_IFD = H("89CCD99B0E8D3B1F11E1296DCA68EC53411CF2CA")
DG_MAP_PK_IFD = H(
    "23FB3749EA030D2A25B278D2A562047ADE3F01B74F17A15402CB7352CA7D2B3EB71C343DB13D1DEBCE9A3666DBCFC920B49174A602CB47965CAA73DC702489A44D41DB914DE9613DC5E98C94160551C0DF86274B9359BC0490D01B03AD54022DCB4F57FAD6322497D7A1E28D46710F461AFE710FBBBC5F8BA166F4311975EC6C"
)
DG_MAP_PK_IC = H(
    "78879F57225AA8080D52ED0FC890A4B25336F699AA89A2D3A189654AF70729E623EA5738B26381E4DA19E004706FACE7B235C2DBF2F38748312F3C98C2DD4882A41947B324AA1259AC22579DB93F7085655AF30889DBB845D9E6783FE42C9F2449400306254C8AE8EE9DD812A804C0B66E8CAFC14F84D8258950A91B44126EE6"
)
DG_EPH_PK_IFD = H(
    "907D89E2D425A178AA81AF4A7774EC8E388C115CAE67031E85EECE520BD911551B9AE4D04369F29A02626C86FBC6747CC7BC352645B6161A2A42D44EDA80A08FA8D61B76D3A154AD8A5A51786B0BC07147057871A922212C5F67F43173172236B7747D1671E6D692A3C7D40A0C3C5CE397545D015C175EB5130551EDBC2EE5D4"
)
DG_EPH_PK_IC = H(
    "075693D9AE941877573E634B6E644F8E60AF17A0076B8B123D9201074D36152BD8B3A213F53820C42ADC79AB5D0AEEC3AEFB91394DA476BD97B9B14D0A65C1FC71A0E019CB08AF55E1F729005FBA7E3FA5DC41899238A250767A6D46DB974064386CD456743585F8E5D90CC8B4004B1F6D866C79CE0584E49687FF61BC29AEA1"
)
DG_SECRET = H(
    "6BABC7B3A72BCD7EA385E4C62DB2625BD8613B24149E146A629311C4CA6698E38B834B6A9E9CD7184BA8834AFF5043D436950C4C1E7832367C10CB8C314D40E5990B0DF7013E64B4549E2270923D06F08CFF6BD3E977DDE6ABE4C31D55C0FA2E465E553E77BDF75E3193D3834FC26E8EB1EE2FA1E4FC97C18C3F6CFFFE2607FD"
)
DG_KENC = H("2F7F46ADCC9E7E521B45D192FAFA9126")
DG_KMAC = H("805A1D27D45A5116F73C54469462B7D8")
DG_TIFD = H("B46DD9BD4D98381F")
DG_TIC = H("917F37B5C0E6D8D1")
DI_K_PI = H("591468CDA83D65219CCCB8560233600F")
DI_Z = H("9ABB8864CA0FF1551E620D1EF4E13510")
DI_S = H("FA5B7E3E49753A0DB9178B7B9BD898C8")
DI_T = H("B3A6DB3C870C3E99245E0D1C06B747DE")
DI_R = H(
    "EAB98D13E09052952AA729907C3C946184DEA0FE74AD2B3AF506F0A83018459C38099CD1F7FF4EA0A078DB1FAC1365505E3DC85500EF95E20B4EEF2E88489233BEE0546B472F994B618D168702406791DEEF3CB4810932EC278F3533FDB860EB4835C36FA4F1BF3FA0B828A718C96BDE88FBA38A3E6C35AAA10959251EB5FC710FC187258995944C0F926E249373F485"
)
DI_SK_IFD = H(
    "4BD0E54740F9A028E6A515BFDAF967848C4F5F5FFF65AA0915947FFD1A0DF2FA6981271BC905F3551457B7E03AC3B8066DE4AA406C1171FB43DD939C4BA16175103BA3DEE16419AA248118F90CC36A3D6F4C373652E0C3CCE7F0F1D0C5425B3600F0F0D6A67F004C8BBA33F2B4733C7252445C1DFC4F1107203F71D2EFB28161"
)
DI_PK_IFD = H(
    "0F0CC62945A8029251FB7EF3C094E12EC68E4EF07F27CB9D9CD04C5C4250FAE0E4F8A951557E929AEB48E5C6DD47F2F5CD7C351A9BD2CD722C07EDE166770F08FFCB370262CF308DD7B07F2E0DA9CAAA1492344C852906919538C98A4BA4187E76CE9D87832386D319CE2E043C3343AEAE6EDBA1A9894DC5094D22F7FE1351D5"
)
DI_PK_IC = H(
    "928D9A0F9DBA450F13FC859C6F290D1D36E42431138A4378500BEB4E0401854CFF111F71CB6DC1D0335807A11388CC8EAA87B07907AAD9FBA6B169AF6D8C26AF8DDDC39ADC3AD2E3FF882B84D23E9768E95A80E4746FB07A9767679FE92133B4D379935C771BD7FBED6C7BB4B1708B275EA75679524CDC9C6A91370CC662A2F3"
)
DI_SECRET = H(
    "419410D6C0A17A4C07C54872CE1CBCEB0A2705C1A434C8A89A4CFE41F1D78124CA7EC52BDE7615E5345E48AB1ABB6E7D1D59A57F3174084D3CA4570397C1F62228BDFDB2DA191EA2239E2C060DBE3BBC23C2FCD0AF12E0F9E0B99FCF91FF1959011D5798B2FCBC1F14FCC24E441F4C8F9B08D977E9498560E63E7FFAB3134EA7"
)
DI_KENC = H("01AFC10CF87BE36D8179E87370171F07")
DI_KMAC = H("23F0FBD05FD6C7B8B88F4C8309669061")
DI_TIC = H("C2F04230187E1525")
DI_TIFD = H("55D61977CBF5307E")


# ---- Appendix G.2 (DH generic mapping) and H.2 (DH integrated mapping): the 1024-bit MODP group with a 160-bit subgroup (RFC 5114 group 0), AES-128 ----
DG_OID = H("04007F00070202040102")  # id-PACE-DH-GM-AES-CBC-CMAC-128
DI_OID = H("04007F00070202040302")  # id-PACE-DH-IM-AES-CBC-CMAC-128


def _dh_terminal(
    oid: bytes, k_pi: bytes, sk_map: int, sk_eph: int, t: bytes = b""
) -> pace.PaceTerminal:
    terminal = pace.PaceTerminal.start(oid, 0, b"unused", random=lambda n: bytes(range(n)))
    terminal.k_pi, terminal.sk_map, terminal.sk_eph = k_pi, sk_map, sk_eph
    if t:
        terminal.t = t
    return terminal


def test_the_dh_generic_mapping_reproduces_the_standards_example_to_the_tokens() -> None:
    terminal = _dh_terminal(
        DG_OID, K, int.from_bytes(DG_SK_MAP_IFD, "big"), int.from_bytes(DG_SK_EPH_IFD, "big")
    )
    assert terminal.dh and not terminal.integrated and not terminal.tdes
    terminal.receive_nonce(DG_Z)
    assert terminal.s == DG_S
    assert terminal.mapping_public() == DG_MAP_PK_IFD  # g^sk, the terminal's mapping public value
    terminal.receive_mapping(
        DG_MAP_PK_IC
    )  # g' = g^s * h, h the shared value of the two mapping keys
    assert (
        terminal.ephemeral_public() == DG_EPH_PK_IFD
    )  # only a correct mapped generator gives the printed public value
    terminal.receive_ephemeral(DG_EPH_PK_IC)
    assert pace.kdf(DG_SECRET, 1, 128) == DG_KENC == terminal.ksenc
    assert pace.kdf(DG_SECRET, 2, 128) == DG_KMAC == terminal.ksmac
    assert terminal.token_ifd() == DG_TIFD and terminal.verify_token_ic(DG_TIC)
    assert (
        pace.public_key_object(DG_OID, DG_EPH_PK_IC, dh=True)
        .hex()
        .upper()
        .startswith("7F49818F060A04007F00070202040102848180")
    )


def test_the_dh_integrated_mapping_reproduces_the_standards_example_to_the_tokens() -> None:
    g = pace.DH_GROUPS[0]
    terminal = _dh_terminal(DI_OID, DI_K_PI, 1, int.from_bytes(DI_SK_IFD, "big"), t=DI_T)
    assert terminal.dh and terminal.integrated
    assert terminal.mapping_public() == DI_T
    terminal.receive_nonce(DI_Z)
    assert terminal.s == DI_S
    assert int.from_bytes(DI_R, "big") % g.p == pace.rp_modulo(
        g.p, DI_S, DI_T, 128
    )  # nine AES blocks, reduced modulo p
    terminal.receive_mapping(b"")
    assert terminal.ephemeral_public() == DI_PK_IFD
    terminal.receive_ephemeral(DI_PK_IC)
    assert pace.kdf(DI_SECRET, 1, 128) == DI_KENC == terminal.ksenc
    assert pace.kdf(DI_SECRET, 2, 128) == DI_KMAC == terminal.ksmac
    assert terminal.token_ifd() == DI_TIFD and terminal.verify_token_ic(DI_TIC)


def test_dh_refuses_values_outside_the_prime_order_subgroup_and_a_repeated_key() -> None:
    g = pace.DH_GROUPS[0]
    terminal = _dh_terminal(
        DG_OID, K, int.from_bytes(DG_SK_MAP_IFD, "big"), int.from_bytes(DG_SK_EPH_IFD, "big")
    )
    terminal.receive_nonce(DG_Z)
    for bad in (
        (1).to_bytes(g.size, "big"),
        (g.p - 1).to_bytes(g.size, "big"),
        (2).to_bytes(g.size, "big"),  # not of order q: a small-subgroup attempt
        DG_MAP_PK_IC[:-1],
        DG_MAP_PK_IC + b"\\x00",
    ):
        with pytest.raises(pace.PaceError) as e:
            terminal.receive_mapping(bad)
        assert e.value.code == "PACE_KEY_INVALID"
    terminal.receive_mapping(DG_MAP_PK_IC)
    with pytest.raises(pace.PaceError) as e2:
        terminal.receive_ephemeral(DG_EPH_PK_IFD)  # the chip's key equal to ours
    assert "equal" in e2.value.detail


@pytest.mark.parametrize("group_id", [0, 1, 2])
@pytest.mark.parametrize("integrated", [False, True])
@pytest.mark.parametrize("last", [2, 3, 4])
def test_a_dh_exchange_agrees_on_every_group_mapping_and_key_size(
    group_id: int, integrated: bool, last: int
) -> None:
    bits = {2: 128, 3: 192, 4: 256}[last]
    oid = H("04007F0007020204") + bytes([3 if integrated else 1, last])
    rng = random.Random(group_id * 100 + last + integrated)
    a = pace.PaceTerminal.start(oid, group_id, b"x", random=lambda n, r=rng: r.randbytes(n))
    b = pace.PaceTerminal.start(oid, group_id, b"x", random=lambda n, r=rng: r.randbytes(n))
    s = rng.randbytes(pace.nonce_length(bits) if integrated else 16)
    t = rng.randbytes(bits // 8)
    for x in (a, b):
        x.s, x.t = s, t
    a.receive_mapping(b"" if integrated else b.mapping_public())
    b.receive_mapping(b"" if integrated else a.mapping_public())
    assert a.gx == b.gx and a.gx != 1
    a.receive_ephemeral(b.ephemeral_public())
    b.receive_ephemeral(a.ephemeral_public())
    assert (a.ksenc, a.ksmac) == (b.ksenc, b.ksmac) and len(a.ksenc) == bits // 8
    assert b.verify_token_ic(a.token_ifd()) and a.verify_token_ic(b.token_ifd())


# ---- 3DES session keys and the CAN password: the standard prints no example, so the rules are asserted and the exchange is run against a separate chip side ----
TDES_OID = H("04007F00070202040201")  # id-PACE-ECDH-GM-3DES-CBC-CBC


def test_3des_session_keys_follow_the_written_rules() -> None:
    secret = bytes(range(32))
    key = pace.kdf(secret, 1, 128, tdes=True)
    assert key == pace.adjust_parity(
        __import__("hashlib").sha1(secret + (1).to_bytes(4, "big")).digest()[:16]
    )
    assert len(key) == 16 and all(
        b.bit_count() % 2 == 1 for b in key
    )  # two DES keys with odd parity
    assert pace.kdf(secret, 1, 128, tdes=False) != key or all(b.bit_count() % 2 == 1 for b in key)
    mac = pace.token_mac(key, b"abc", tdes=True)
    assert len(mac) == 8 and mac == cr.mac(
        key, b"abc"
    )  # the retail MAC of the basic access control code
    nonce = bytes(range(16))
    assert pace.decrypt_nonce(key, pace.encrypt_nonce(key, nonce, tdes=True), tdes=True) == nonce
    with pytest.raises(pace.PaceError):
        pace.decrypt_nonce(key, bytes(7), tdes=True)


def test_the_can_is_hashed_never_and_the_mrz_always() -> None:
    can = b"123456"
    assert pace.password_key(can, 128, can=True) == pace.kdf(
        can, 3, 128
    )  # f(CAN) is the CAN itself
    import hashlib

    assert pace.password_key(MRZ_INFO, 128) == pace.kdf(hashlib.sha1(MRZ_INFO).digest(), 3, 128)
    assert pace.password_key(can, 128, can=True) != pace.password_key(can, 128)
    assert pace.password_key(can, 128, tdes=True, can=True) == pace.adjust_parity(
        pace.kdf(can, 3, 128)
    )


@pytest.mark.parametrize("curve_id", sorted(pace.CURVES))
def test_a_3des_exchange_agrees_on_every_curve(curve_id: int) -> None:
    rng = random.Random(curve_id)
    a = pace.PaceTerminal.start(TDES_OID, curve_id, b"x", random=lambda n, r=rng: r.randbytes(n))
    b = pace.PaceTerminal.start(TDES_OID, curve_id, b"x", random=lambda n, r=rng: r.randbytes(n))
    s = rng.randbytes(16)
    for x in (a, b):
        x.s = s
    a.receive_mapping(b.mapping_public())
    b.receive_mapping(a.mapping_public())
    a.receive_ephemeral(b.ephemeral_public())
    b.receive_ephemeral(a.ephemeral_public())
    assert (a.ksenc, a.ksmac) == (b.ksenc, b.ksmac) and len(a.ksenc) == 16 and a.tdes
    assert b.verify_token_ic(a.token_ifd()) and a.verify_token_ic(b.token_ifd())
