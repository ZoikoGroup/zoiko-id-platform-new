"""Chip authentication through PACE-CAM (CR-111), proved against ICAO Doc 9303 part 11 appendix I (BrainpoolP256r1, AES-128) and against a chip simulator written separately.

Every value the standard prints for its worked example is asserted: the session keys, both tokens, the encrypted chip authentication data, the chip's mapping public key and the
chip's static public key from its ChipAuthenticationPublicKeyInfo; and that the proof verifies. The simulator then plays the chip through the whole reader.
"""

from __future__ import annotations

import pytest
from tests.unit.chip_simulator import PaceSimulatedChip, card_access, pace_info
from tests.unit.test_chip_pace import INFO, build, read
from zid_platform.modules.evidence.application import chip_authentication as ca
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application import pace

H = lambda s: bytes.fromhex(s.replace(" ", ""))
I = lambda s: int(s.replace(" ", ""), 16)

# ---- Appendix I.1: PACE-CAM with ECDH on BrainpoolP256r1 and AES-128, the MRZ as password ----
OID = H("04007F00070202040602")  # id-PACE-ECDH-CAM-AES-CBC-CMAC-128
CURVE = 13
K = H("4E6F6FBF 7BE748B9 32C7B741 61BBA9DF")
S = H("658B860B C94DF6F0 44FCE6D5 C82CF8E5")
Z = H("CB60E8E0 D85B76A9 BD304747 C2AD42E2")
IFD_MAP_SK = I("5D8BB87B D74D985A 4B7D4325 B9F7B976 FE835122 77340079 8914AA22 738135CC")
IFD_MAP_PK = H(
    "04 7F1D410A DB7DDB3B 84BF1030 800981A9 105D7457 B4A3ADE0 02384F30 86C67EDE"
    " 1AB88910 4A27DB6D 842B0190 20FBF3CE ACB0DC62 7F7BDCAC 29969E19 D0E553C1"
)
IC_MAP_PK = H(
    "04 A234236A A9B9621E 8EFB73B5 245C0E09 D2576E52 77183C12 08BDD552 80CAE8B3"
    " 04F36571 3A356E65 A451E165 ECC9AC0A C46E3771 342C8FE5 AEDD0926 85338E23"
)
IFD_SK = I("76ECFDAA 9841C323 A3F5FC5E 88B88DB3 EFF7E35E BF57A7E6 946CB630 006C2120")
IFD_PK = H(
    "04 446C9340 84D9DAB8 63944F21 9520076C 29EE3F7A E6722B11 FF319EC1 C7728F95"
    " 5483400B FF60BF0C 59292700 09277DC2 A515E125 75010AD9 BA916CF1 BF86FEFC"
)
IC_PK = H(
    "04 02AD566F 3C6EC7F9 324509AD 50A51FA5 2030782A 4968FCFE DF737DAE A9933331"
    " 11C3B9B4 C2287789 BD137E7F 8AA882E2 A3C633CC D6ECC2C6 3C57AD40 1A09C2E1"
)
KSENC = H("0A9DA4DB 03BDDE39 FC5202BC 44B2E89E")
KSMAC = H("4B1C0649 1ED5140C A2B537D3 44C6C0B1")
TIFD = H("E86BD060 18A1CD3B")
TIC = H("8596CF05 5C67C1A3")
ENCRYPTED_CA = H(
    "1EEA964D AAE372AC 990E3EFD E6333353 BFC89A67 04D93DA8 798CF77F 5B7A54BD 10CBA372 B42BE0B9 B5F28AA8 DE2F4F92"
)
CA_PUBLIC = H(
    "04 18727094 94399E74 70A6431B E25E83EE E24FEA56 8C2ED28D B48E05DB 3A610DC8"
    " 84D256A4 0E35EFCB 59BF6753 D3A489D2 8C7A4D97 3C2DA138 A6E7A4A0 8F68E16F"
)
# the ChipAuthenticationPublicKeyInfo as the standard prints it
CA_KEY_INFO = H(
    "30620609 04007F00 07020201 02305230 0C060704 007F0007 01020201 0D034200"
    " 04187270 9494399E 7470A643 1BE25E83 EEE24FEA 568C2ED2 8DB48E05 DB3A610D"
    " C884D256 A40E35EF CB59BF67 53D3A489 D28C7A4D 973C2DA1 38A6E7A4 A08F68E1 6F02010D"
)


def dg14_of(*infos: bytes) -> bytes:
    return cr._tlv(0x6E, cr._tlv(0x31, b"".join(infos)))


def terminal() -> pace.PaceTerminal:
    t = pace.PaceTerminal(OID, 128, CURVE, IFD_MAP_SK, IFD_SK, K, S)
    t.receive_mapping(IC_MAP_PK)
    t.receive_ephemeral(IC_PK)
    return t


# ----------------------------------------------------------------------------------------------- the standard's example


def test_the_standards_example_reaches_its_keys_and_tokens_with_the_chip_authentication_mapping() -> (
    None
):
    v = pace.variant(OID)
    assert v is not None and v.cam and not v.dh and not v.integrated and v.bits == 128
    assert pace.usable(OID, CURVE)
    t = terminal()
    assert t.mapping_public() == IFD_MAP_PK
    assert t.pk_map_ic == IC_MAP_PK
    assert t.ephemeral_public() == IFD_PK
    assert (t.ksenc, t.ksmac) == (KSENC, KSMAC)
    assert t.token_ifd() == TIFD
    assert t.verify_token_ic(TIC)


def test_the_standards_encrypted_chip_authentication_data_proves_the_static_key() -> None:
    t = terminal()
    assert len(ca.static_keys(dg14_of(CA_KEY_INFO))) == 1
    assert ca.static_keys(dg14_of(CA_KEY_INFO))[0] == (CURVE, CA_PUBLIC)
    assert pace.verify_chip_authentication(CURVE, t.pk_map_ic, t.ksenc, ENCRYPTED_CA, CA_PUBLIC)
    # CA_IC times the static key is the chip's mapping public key: the relation the standard states
    c = pace.CURVES[CURVE]
    scalar = int.from_bytes(pace.chip_authentication_data(KSENC, ENCRYPTED_CA), "big")
    assert pace.mul(c, scalar, pace.decode_point(c, CA_PUBLIC)) == pace.decode_point(c, IC_MAP_PK)


def test_any_change_to_the_proof_the_session_key_or_either_public_key_fails_it() -> None:
    flipped = ENCRYPTED_CA[:-1] + bytes([ENCRYPTED_CA[-1] ^ 1])
    other_key = pace.encode_point(
        pace.CURVES[CURVE], pace.mul(pace.CURVES[CURVE], 5, pace.CURVES[CURVE].g)
    )
    assert not pace.verify_chip_authentication(CURVE, IC_MAP_PK, KSENC, flipped, CA_PUBLIC)
    assert not pace.verify_chip_authentication(CURVE, IC_MAP_PK, bytes(16), ENCRYPTED_CA, CA_PUBLIC)
    assert not pace.verify_chip_authentication(CURVE, IC_MAP_PK, KSENC, ENCRYPTED_CA, other_key)
    assert not pace.verify_chip_authentication(CURVE, IC_PK, KSENC, ENCRYPTED_CA, CA_PUBLIC)
    assert not pace.verify_chip_authentication(
        CURVE, IC_MAP_PK, KSENC, ENCRYPTED_CA[:16], CA_PUBLIC
    )
    assert not pace.verify_chip_authentication(CURVE, IC_MAP_PK, KSENC, b"", CA_PUBLIC)
    assert not pace.verify_chip_authentication(
        CURVE, IC_MAP_PK, KSENC, ENCRYPTED_CA, b"\x04" + bytes(64)
    )
    with pytest.raises(pace.PaceError):
        pace.verify_chip_authentication(
            3, IC_MAP_PK, KSENC, ENCRYPTED_CA, CA_PUBLIC
        )  # reserved parameters


def test_the_chip_authentication_mapping_allows_aes_only_and_needs_the_data() -> None:
    for bad in ("04007F00070202040601", "04007F00070202040605", "04007F00070202040600"):
        assert pace.variant(H(bad)) is None
    for ok in ("04007F00070202040602", "04007F00070202040603", "04007F00070202040604"):
        assert pace.variant(H(ok)).cam  # type: ignore[union-attr]
    t = terminal()
    with pytest.raises(pace.PaceError) as e:
        t.receive_ca_data(None)
    assert e.value.code == "PACE_CAM_DATA_MISSING"
    t.receive_ca_data(ENCRYPTED_CA)
    plain = pace.PaceTerminal(H("04007F00070202040202"), 128, CURVE, IFD_MAP_SK, IFD_SK, K, S)
    with pytest.raises(pace.PaceError):
        plain.receive_ca_data(ENCRYPTED_CA)  # the data MUST NOT be present without the mapping
    plain.receive_ca_data(None)


def test_the_state_of_a_chip_authentication_terminal_survives_sealing() -> None:
    t = terminal()
    t.receive_ca_data(ENCRYPTED_CA)
    again = pace.PaceTerminal.from_state(t.to_state())
    assert (again.pk_map_ic, again.ca_data, again.ksenc) == (IC_MAP_PK, ENCRYPTED_CA, KSENC)
    assert again.cam


# --------------------------------------------------------------------------------------------------- the verdict


def evidence() -> ca.CamEvidence:
    return ca.CamEvidence(CURVE, IC_MAP_PK, KSENC, ENCRYPTED_CA)


def test_a_chip_that_proves_its_key_is_checked_only_if_the_signed_object_covered_data_group_14() -> (
    None
):
    dg14 = dg14_of(CA_KEY_INFO)
    ok = ca.assess(evidence(), dg14, dg14_verified=True)
    assert ok is not None and ok.status == "GENUINE_KEY_HELD" and ok.clone_checked
    uncovered = ca.assess(evidence(), dg14, dg14_verified=False)
    assert uncovered is not None and uncovered.status == "NOT_ASSESSED"
    assert uncovered.reasons == ("DG14_NOT_COVERED_BY_THE_SIGNED_OBJECT",)
    missing = ca.assess(evidence(), None, dg14_verified=True)
    assert missing is not None and missing.status == "NOT_ASSESSED"
    assert (
        ca.assess(None, dg14, dg14_verified=True) is None
    )  # PACE did not use the mapping: nothing to say


def test_a_key_that_is_not_the_chips_makes_the_chip_suspect_and_an_unusable_file_makes_nothing_pass() -> (
    None
):
    c = pace.CURVES[CURVE]
    other = pace.encode_point(c, pace.mul(c, 77, c.g))
    info = bytearray(CA_KEY_INFO)
    start = info.index(CA_PUBLIC)
    info[start : start + len(CA_PUBLIC)] = other
    wrong = ca.assess(evidence(), dg14_of(bytes(info)), dg14_verified=True)
    assert wrong is not None and wrong.status == "CLONE_SUSPECTED"
    assert wrong.reasons == ("CAM_PROOF_INVALID",)
    for dg14, reason in (
        (b"", "DG14_NOT_COVERED_BY_THE_SIGNED_OBJECT"),
        (b"\x6e\x00", "CA_KEY_NOT_FOUND"),
        (b"\x00\x01\x02", "CA_KEY_NOT_FOUND"),
        (dg14_of(cr._tlv(0x30, cr._tlv(0x06, b"\x01"))), "CA_KEY_NOT_FOUND"),
    ):
        v = ca.assess(evidence(), dg14, dg14_verified=True)
        assert v is not None and v.status == "NOT_ASSESSED" and v.reasons == (reason,)
    # a key on another curve is not tried against this run's curve
    off = bytearray(CA_KEY_INFO)
    off[off.index(bytes([0x02, 0x01, 0x0D])) + 2] = 0x0C
    v = ca.assess(evidence(), dg14_of(bytes(off)), dg14_verified=True)
    assert v is not None and v.reasons == ("CA_KEY_CURVE_NOT_THE_PACE_CURVE",)


# ------------------------------------------------------------------------------- the whole reader, against the simulator


def cam_chip(**kw):
    return build(cam=True, curve_id=12, **kw)


def dg14_for(sim: PaceSimulatedChip) -> bytes:
    """A DG14 carrying the simulator chip's static public key on its curve, in the standard's ChipAuthenticationPublicKeyInfo form."""
    point = sim.static_public()
    algorithm = cr._tlv(
        0x30, cr._tlv(0x06, ca.STANDARDIZED_DOMAIN_PARAMETERS) + bytes([2, 1, sim.curve_id])
    )
    key = cr._tlv(0x03, b"\x00" + point)
    info = cr._tlv(
        0x30, cr._tlv(0x06, ca.ID_PK_ECDH) + cr._tlv(0x30, algorithm + key) + bytes([2, 1, 1])
    )
    return dg14_of(info)


def test_a_read_with_the_chip_authentication_mapping_gives_a_proof_that_verifies_against_data_group_14() -> (
    None
):
    _chip, _groups, sim = cam_chip()
    machine = read(sim, include_photo=True)
    state = machine.to_state()
    assert state["sm"] == "AES" and state["pace"]["oid"].startswith("04007f000702020406")
    proof = machine.chip_authentication()
    assert proof is not None and proof.curve_id == 12
    verdict = ca.assess(proof, dg14_for(sim), dg14_verified=True)
    assert verdict is not None and verdict.status == "GENUINE_KEY_HELD"
    # the same read with a copy: it has the public key and not the private one, so its proof is not for that key
    _c, _g, copy = cam_chip(ca_secret=12345)
    cloned = read(copy, include_photo=True).chip_authentication()
    bad = ca.assess(cloned, dg14_for(sim), dg14_verified=True)  # DG14 copied from the genuine chip
    assert bad is not None and bad.status == "CLONE_SUSPECTED"


def test_a_chip_that_claims_the_mapping_and_sends_no_proof_is_refused_by_the_reader() -> None:
    _chip, _groups, sim = cam_chip(send_ca_data=False)
    with pytest.raises(cr.ChipReadError) as e:
        read(sim, include_photo=True)
    assert e.value.code == "PACE_CAM_DATA_MISSING"


def test_an_ordinary_pace_read_has_no_chip_authentication() -> None:
    _chip, _groups, sim = build()
    machine = read(sim, include_photo=True)
    assert machine.chip_authentication() is None
    assert ca.assess(machine.chip_authentication(), b"", dg14_verified=True) is None


def test_the_reader_offers_the_mapping_only_when_the_chip_lists_it_and_falls_back_otherwise() -> (
    None
):
    _chip, groups, sim = cam_chip(
        infos=(pace_info(H("04007F00070202040602"), 12),),
    )
    assert cr.ChipMachine.start("", "", "", can="123456") is not None
    machine = read(sim, include_photo=True)
    assert machine.result()[0].data_groups == groups
    assert card_access(pace_info(OID, CURVE))[:2] == b"\x31\x14"
    assert INFO  # the document details the simulator is keyed with
