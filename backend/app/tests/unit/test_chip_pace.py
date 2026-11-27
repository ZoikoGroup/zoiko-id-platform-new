"""The resumable machine with PACE (CR-108): the exchange framed exactly as the standard prints it, then full reads on every supported curve and AES size.

The first test drives the real machine with the chip's responses from Doc 9303 part 11 appendix G.1 and asserts that every command it sends is, byte for byte, the
command the standard shows. The rest read a simulated PACE chip (written separately) end to end, with the machine's whole state turned into plain data and back after every
command, and cover the ways PACE must fail or fall back.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from chip_simulator import PaceSimulatedChip, SimulatedChip, card_access, pace_info
from epassport_fixture import dg1, make_chip, mrz_td3
from test_pace import (
    IC_MAP_PK,
    IC_PK,
    IFD_MAP_PK,
    IFD_MAP_SK,
    IFD_PK,
    IFD_SK,
    OID,
    TIC,
    TIFD,
    Z,
)
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application import epassport as ep
from zid_platform.modules.evidence.application import pace

H = bytes.fromhex
NUMBER, DOB, VALID_TO = "L898902C3", "740812", "320415"
INFO = cr.mrz_information(NUMBER, DOB, VALID_TO)


def run(coro):
    return asyncio.run(coro)


def build(**kw):
    photo = cr._tlv(0x75, bytes(range(256)) * 12)
    groups = {1: dg1(mrz_td3()), 2: photo, 15: bytes([0x6F, 0x03, 1, 2, 3])}
    chip = make_chip(groups=groups)
    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: groups[1],
        cr.FILE_IDS["DG2"]: photo,
        cr.FILE_IDS["DG15"]: groups[15],
    }
    return chip, groups, PaceSimulatedChip(files, INFO, **kw)


def read(
    sim,
    *,
    number=NUMBER,
    dob=DOB,
    valid=VALID_TO,
    include_photo=True,
    try_pace=True,
    challenge=None,
    between=None,
    can=None,
):
    """What a stateless relay does: the whole state is plain data between every command."""
    machine = cr.ChipMachine.start(
        number,
        dob,
        valid,
        include_photo=include_photo,
        aa_challenge=challenge,
        try_pace=try_pace,
        can=can,
        random=lambda n: bytes(range(n)),
    )
    command = machine.next_command()
    while True:
        state = json.loads(json.dumps(machine.to_state()))
        if between:
            between(state)
        answer = run(sim(command))
        machine = cr.ChipMachine.from_state(state)
        machine.feed(answer)
        if machine.done:
            return machine
        command = machine.next_command()


# ------------------------------------------------------------------------------------ the standard's exchange, command by command


def test_the_machine_sends_exactly_the_commands_the_standard_shows_and_ends_with_aes_secure_messaging() -> (
    None
):
    card_access_file = H(
        "3114 3012060A 04007F00 07020204 02020201 0202010D"
    )  # a SET holding the standard's PACEInfo (Appendix G.1)
    keys = iter(
        [
            bytes(8),  # RND.IFD (unused by PACE)
            bytes(16),  # K.IFD (unused by PACE)
            (IFD_MAP_SK - 1).to_bytes(48, "big")
            + bytes(
                48
            ),  # the terminal's mapping key of the example (the scalar is 1 + seed mod (n-1))
            (IFD_SK - 1).to_bytes(48, "big")
            + bytes(48),  # the terminal's key-agreement key of the example
        ]
    )
    m = cr.ChipMachine.start("T22000129", "640812", "101031", random=lambda n: next(keys))

    def step(expect: bytes, answer: bytes) -> None:
        assert m.next_command() == expect, (m.phase, m.next_command().hex(), expect.hex())
        m.feed(answer)

    step(H("00A4020C02011C"), H("9000"))  # select EF.CardAccess
    step(H("00B0000004"), card_access_file[:4] + H("9000"))
    step(H("00B0000412"), card_access_file[4:] + H("9000"))
    # Appendix G.1, every command and response as printed
    step(H("0022C1A40F800A04007F000702020402028301" + "01"), H("9000"))  # MSE:Set AT
    step(H("10860000027C0000"), H("7C1280" + "10") + Z + H("9000"))  # the encrypted nonce
    step(
        H("10860000457C438141") + IFD_MAP_PK + H("00"), H("7C438241") + IC_MAP_PK + H("9000")
    )  # map the nonce
    step(
        H("10860000457C438341") + IFD_PK + H("00"), H("7C438441") + IC_PK + H("9000")
    )  # key agreement
    step(
        H("008600000C7C0A8508") + TIFD + H("00"), H("7C0A8608") + TIC + H("9000")
    )  # mutual authentication
    assert m.phase == "SELECT_APP_SM" and m.to_state()["sm"] == "AES"
    assert (m.to_state()["ksenc"], m.to_state()["ksmac"]) == (
        "F5F0E35C0D7161EE6724EE513A0D9A7F".lower(),
        "FE251C7858B356B24514B3BD5F4297D1".lower(),
    )
    nxt = m.next_command()
    assert (
        nxt[0] == 0x0C and nxt[1] == 0xA4 and nxt[2] == 0x04
    )  # the application is selected under AES secure messaging
    assert (
        m.to_state()["ssc"] == (1).to_bytes(16, "big").hex()
    )  # the counter started at zero and moved once


# -------------------------------------------------------------------------------------------------- a whole read over PACE


@pytest.mark.parametrize(
    ("curve_id", "bits"),
    [
        (12, 128),
        (13, 128),
        (13, 192),
        (15, 256),
        (16, 128),
        (17, 256),
        (18, 256),
        (8, 128),
        (10, 192),
        (9, 128),
        (11, 192),
        (14, 256),
    ],
)
def test_a_whole_read_over_pace_gives_what_the_chip_holds_and_it_verifies(
    curve_id: int, bits: int
) -> None:
    chip, groups, sim = build(curve_id=curve_id, bits=bits)
    machine = read(sim, include_photo=True)
    result, _ = machine.result()
    assert result.sod == chip.sod and result.data_groups == groups
    assert machine.to_state()["sm"] == "AES" and machine.to_state()["pace"] is not None
    assert (
        ep.verify(result.sod, result.data_groups, csca=(chip.csca.cert,)).status
        == "VERIFIED_LIMITED"
    )
    # PACE was used: the chip never saw a Basic Access Control command
    assert not any(
        c[1] in (0x84, 0x82) and c[0] == 0x00 and len(c) in (5, 46) and c[:2] != b"\x10\x86"
        for c in sim.requests
        if c[1] == 0x82 and c[0] == 0
    )


def test_active_authentication_runs_under_the_aes_channel_too() -> None:
    _chip, groups, sim = build(aa_signer=lambda c: b"\xaa" * 8 + c)
    challenge = bytes.fromhex("0102030405060708")
    result, answer = read(sim, include_photo=False, challenge=challenge).result()
    assert answer == b"\xaa" * 8 + challenge and result.data_groups[1] == groups[1]


def test_the_domain_parameter_id_is_sent_only_when_the_chip_offers_more_than_one_choice() -> None:
    oid = H("04007F000702020402") + bytes([2])
    _chip, _groups, one = build(curve_id=12)
    read(one, include_photo=False)
    mse = next(c for c in one.requests if c[1] == 0x22)
    assert not mse.endswith(H("84010C"))  # unambiguous: omitted, as in the standard's example
    _chip, _groups, many = build(curve_id=12, infos=(pace_info(oid, 13), pace_info(oid, 12)))
    machine = read(many, include_photo=False)
    assert machine.to_state()["pace"]["curve_id"] == 13  # the chip's first usable choice
    mse = next(c for c in many.requests if c[1] == 0x22)
    assert mse[5 : 5 + mse[4]].endswith(H("84010D"))  # ambiguous: the identifier is named


# ---------------------------------------------------------------------------------------------- falling back and failing


def test_a_chip_with_no_card_access_file_is_read_with_basic_access_control_as_before() -> None:
    sim = SimulatedChip({}, INFO)  # a BAC chip: it has no EF.CardAccess
    _chip, groups, bac = build()
    bac_only = SimulatedChip(bac.files, INFO)
    machine = read(bac_only, include_photo=True)
    assert machine.to_state()["sm"] == "3DES" and machine.to_state()["pace"] is None
    assert machine.result()[0].data_groups == groups
    assert sim is not None


def test_a_chip_that_offers_only_variants_this_reader_lacks_falls_back_and_names_them_if_that_fails() -> (
    None
):
    mapping = (
        H("04007F000702020404") + bytes([1])
    )  # id-PACE-ECDH-IM-3DES-CBC-CBC, the integrated mapping with 3DES (the standard does not say how to build its function)
    infos = (pace_info(mapping, 12),)
    # it also accepts BAC: the read goes through Basic Access Control
    _chip, groups, sim = build(infos=infos, accept_bac=True)
    machine = read(sim, include_photo=True)
    assert machine.to_state()["sm"] == "3DES" and machine.result()[0].data_groups == groups
    # it does not: the refusal says which PACE variant the chip offered, so the next step is clear
    _chip, _groups, only_pace = build(infos=infos, accept_bac=False)
    with pytest.raises(cr.ChipReadError) as e:
        read(only_pace)
    assert (
        e.value.code == "BAC_NOT_ACCEPTED"
        and "ECDH integrated mapping with 3DES session keys" in e.value.detail
    )
    # unsupported domain parameters (id 3, reserved) are named the same way
    oid = H("04007F000702020402") + bytes([2])
    _chip, _groups, p192 = build(infos=(pace_info(oid, 3),), accept_bac=False)
    with pytest.raises(cr.ChipReadError) as e2:
        read(p192)
    assert "domain parameters 3" in e2.value.detail


def test_wrong_document_details_fail_the_pace_authentication_without_revealing_anything() -> None:
    _chip, _groups, sim = build()
    with pytest.raises(cr.ChipReadError) as e:
        read(sim, dob="740813")
    assert (
        e.value.code == "PACE_NOT_ACCEPTED"
        and "wrong document details" in e.value.detail
        and "740813" not in str(e.value)
    )
    assert not any(c[0] == 0x0C for c in sim.requests)  # nothing was ever read


def test_a_chip_that_cannot_prove_it_holds_the_keys_is_refused() -> None:
    _chip, _groups, sim = build(wrong_token=True)
    with pytest.raises(cr.ChipReadError) as e:
        read(sim)
    assert e.value.code == "PACE_TOKEN_INVALID"


def test_a_tampered_aes_response_stops_the_read() -> None:
    _chip, _groups, sim = build(tamper_response_mac=lambda n: n == 2)
    with pytest.raises(cr.ChipReadError) as e:
        read(sim)
    assert e.value.code == "SECURE_MESSAGING_MAC_INVALID"


def test_pace_can_be_switched_off_for_a_chip_that_dislikes_the_probe() -> None:
    _chip, _groups, sim = build()  # requires PACE
    with pytest.raises(cr.ChipReadError) as e:
        read(sim, try_pace=False)
    assert e.value.code == "BAC_NOT_ACCEPTED"
    assert not any(
        c[1] == 0xA4 and c[2] == 0x02 for c in sim.requests
    )  # EF.CardAccess was never asked for


def test_a_malformed_card_access_file_fails_closed() -> None:
    _chip, _groups, sim = build()
    sim.card_access = H("3105 060101 0201")  # a SET that does not parse
    with pytest.raises(cr.ChipReadError) as e:
        read(sim)
    assert e.value.code == "CARD_ACCESS_INVALID"
    for bad in (b"", b"\x30\x00", b"\x31\x05\x06"):
        with pytest.raises(cr.ChipReadError):
            cr.parse_card_access(bad)


def test_the_whole_state_stays_plain_data_and_holds_the_pace_keys_only_as_secrets_to_be_sealed() -> (
    None
):
    seen: list[dict] = []
    _chip, _groups, sim = build()
    read(sim, include_photo=False, between=seen.append)
    phases = {s["phase"] for s in seen}
    assert {
        "CA_SELECT",
        "CA_HEAD",
        "CA_BODY",
        "PACE_MSE",
        "PACE_NONCE",
        "PACE_MAP",
        "PACE_KEY",
        "PACE_AUTH",
        "SELECT_APP_SM",
        "SELECT_FILE",
    } <= phases
    assert all(isinstance(s, dict) for s in seen) and any(
        s["pace"] and s["pace"]["ksenc"] for s in seen
    )


# ----------------------------------------------------------------------------------------------------- the AES channel itself


def test_the_aes_channel_round_trips_every_data_length_against_the_chip_side() -> None:
    _chip, _groups, sim = build()
    machine = read(sim, include_photo=False)
    ksenc, ksmac = (
        bytes.fromhex(machine.to_state()["ksenc"]),
        bytes.fromhex(machine.to_state()["ksmac"]),
    )
    assert len(ksenc) == 16 and len(ksmac) == 16
    # a fresh channel pair at counter zero, the terminal's protect read by an independent chip-side implementation
    sim.aes_keys, sim.aes_ssc = (ksenc, ksmac), b"\x00" * 16
    sim.selected = None
    channel = cr.AesChannel(ksenc, ksmac)
    for fid in (cr.FILE_IDS["DG1"], cr.FILE_IDS["SOD"]):
        data, status = channel.unprotect(run(sim(channel.protect(0x00, 0xA4, 0x02, 0x0C, fid))))
        assert (data, status) == (b"", H("9000"))
        head, status = channel.unprotect(run(sim(channel.protect(0x00, 0xB0, 0x00, 0x00, le=4))))
        assert status == H("9000") and head == sim.files[fid][:4]
    assert channel.ssc == (8).to_bytes(16, "big")  # four commands and four responses


def test_the_aes_channel_refuses_a_changed_or_replayed_response_and_an_odd_instruction() -> None:
    ksenc, ksmac = bytes(range(16)), bytes(range(16, 32))
    channel = cr.AesChannel(ksenc, ksmac)
    channel.protect(0x00, 0xA4, 0x02, 0x0C, b"\x01\x1e")
    sim = PaceSimulatedChip({cr.FILE_IDS["COM"]: bytes([0x60, 0x02, 1, 2])}, INFO)
    sim.aes_keys, sim.aes_ssc = (ksenc, ksmac), b"\x00" * 16
    chip_channel = cr.AesChannel(ksenc, ksmac)
    reply = run(sim(chip_channel.protect(0x00, 0xA4, 0x02, 0x0C, cr.FILE_IDS["COM"])))
    for i in range(len(reply) - 2):
        fresh = cr.AesChannel(ksenc, ksmac, (1).to_bytes(16, "big"))
        with pytest.raises(cr.ChipReadError):
            fresh.unprotect(reply[:i] + bytes([reply[i] ^ 1]) + reply[i + 1 :])
    good = cr.AesChannel(ksenc, ksmac, (1).to_bytes(16, "big"))
    assert good.unprotect(reply) == (b"", H("9000"))
    with pytest.raises(cr.ChipReadError):
        good.unprotect(reply)  # replayed: the counter has moved
    with pytest.raises(cr.ChipReadError) as e:
        cr.AesChannel(ksenc, ksmac).protect(0x00, 0xB1, 0, 0, b"\x01")
    assert e.value.code == "ODD_INSTRUCTION_UNSUPPORTED"


def test_the_parameter_helpers_describe_what_they_refuse() -> None:
    assert (
        pace.describe_unsupported(H("04007F00070202040401"))
        == "ECDH integrated mapping with 3DES session keys"
    )
    assert (
        pace.describe_unsupported(H("04007F00070202040601"))
        == "ECDH chip authentication mapping with 3DES session keys"
    )
    assert pace.describe_unsupported(OID, 3) == "ECDH generic mapping with domain parameters 3"
    assert pace.describe_unsupported(H("04007F00070202040102")) == "DH generic mapping"
    assert pace.describe_unsupported(b"\x01\x02") == "an algorithm this reader does not support"
    assert card_access(pace_info(OID, 13))[:2] == b"\x31\x14"


# ------------------------------------------------------------------------------------------------ the integrated mapping


@pytest.mark.parametrize(
    ("curve_id", "bits"),
    [(8, 128), (12, 128), (13, 128), (13, 192), (15, 256), (16, 192), (17, 256), (18, 128)],
)
def test_a_whole_read_over_the_integrated_mapping_gives_what_the_chip_holds_and_it_verifies(
    curve_id: int, bits: int
) -> None:
    chip, groups, sim = build(curve_id=curve_id, bits=bits, integrated=True)
    machine = read(sim, include_photo=True)
    result, _ = machine.result()
    assert result.sod == chip.sod and result.data_groups == groups
    assert machine.to_state()["sm"] == "AES"
    assert (
        ep.verify(result.sod, result.data_groups, csca=(chip.csca.cert,)).status
        == "VERIFIED_LIMITED"
    )


def test_the_integrated_mapping_sends_the_nonce_t_in_clear_and_no_mapping_public_key() -> None:
    _chip, _groups, sim = build(integrated=True)
    read(sim)
    steps = [c for c in sim.requests if c[1] == 0x86]
    assert [c[0] for c in steps] == [
        0x10,
        0x10,
        0x10,
        0x00,
    ]  # nonce, map, key agreement (chained), tokens
    mapping = cr._parse(cr._parse(steps[1][5 : 5 + steps[1][4]])[0x7C])
    assert (
        list(mapping) == [0x81] and len(mapping[0x81]) == 16
    )  # t, a key-sized random string, and nothing else
    mse = next(c for c in sim.requests if c[1] == 0x22)
    assert H("04007F00070202040402") in mse  # id-PACE-ECDH-IM-AES-CBC-CMAC-128 named in MSE:Set AT


def test_nist_p224_with_the_integrated_mapping_is_not_used_and_the_chip_is_read_another_way() -> (
    None
):
    oid = H("04007F00070202040402")
    _chip, groups, sim = build(infos=(pace_info(oid, 10),), integrated=True, accept_bac=True)
    machine = read(sim)
    assert machine.to_state()["sm"] == "3DES" and machine.result()[0].data_groups == groups
    _chip, _groups, only = build(infos=(pace_info(oid, 10),), integrated=True, accept_bac=False)
    with pytest.raises(cr.ChipReadError) as e:
        read(only)
    assert "P-224" in e.value.detail


def test_a_chip_that_answers_the_integrated_nonce_with_data_is_refused() -> None:
    _chip, _groups, sim = build(integrated=True)
    real = sim._general_authenticate

    def lying(apdu: bytes) -> bytes:
        out = real(apdu)
        return (
            cr._tlv(0x7C, cr._tlv(0x82, b"\x04" + bytes(64))) + b"\x90\x00"
            if sim.step == 2
            else out
        )

    sim._general_authenticate = lying  # type: ignore[method-assign]
    with pytest.raises(cr.ChipReadError) as e:
        read(sim)
    assert e.value.code == "PACE_RESPONSE_INVALID"


# ------------------------------------------------------------------------------- DH groups, 3DES session keys and the CAN password


def _whole_read_agrees(sim, chip, groups, secure: str) -> None:
    machine = read(sim, include_photo=True)
    result, _ = machine.result()
    assert result.sod == chip.sod and result.data_groups == groups
    assert machine.to_state()["sm"] == secure
    assert (
        ep.verify(result.sod, result.data_groups, csca=(chip.csca.cert,)).status
        == "VERIFIED_LIMITED"
    )


@pytest.mark.parametrize("group_id", [0, 1, 2])
@pytest.mark.parametrize("integrated", [False, True])
def test_a_whole_read_over_a_dh_group_gives_what_the_chip_holds_and_it_verifies(
    group_id: int, integrated: bool
) -> None:
    chip, groups, sim = build(
        curve_id=group_id, dh=True, integrated=integrated, bits=128 if group_id == 0 else 256
    )
    _whole_read_agrees(sim, chip, groups, "AES")
    mse = next(c for c in sim.requests if c[1] == 0x22)
    assert H("04007F0007020204") + bytes([3 if integrated else 1]) in mse


@pytest.mark.parametrize("curve_id", [8, 9, 11, 12, 13, 14, 17, 18])
def test_a_whole_read_with_3des_session_keys_gives_what_the_chip_holds_and_it_verifies(
    curve_id: int,
) -> None:
    chip, groups, sim = build(curve_id=curve_id, tdes=True)
    _whole_read_agrees(
        sim, chip, groups, "3DES"
    )  # the same 3DES secure messaging as after Basic Access Control, keyed by PACE with a zero counter
    assert not any(c[1] == 0x84 for c in sim.requests)  # no Basic Access Control challenge


def test_a_dh_group_with_3des_session_keys_works_too() -> None:
    chip, groups, sim = build(curve_id=0, dh=True, tdes=True)
    _whole_read_agrees(sim, chip, groups, "3DES")


def test_the_can_alone_reads_a_chip_that_accepts_it_and_sends_the_password_reference_two() -> None:
    chip, groups, sim = build(can="123456")
    machine = read(sim, number="", dob="", valid="", can="123456", include_photo=True)
    result, _ = machine.result()
    assert (
        result.sod == chip.sod
        and result.data_groups == groups
        and machine.to_state()["sm"] == "AES"
    )
    mse = next(c for c in sim.requests if c[1] == 0x22)
    assert mse.endswith(bytes.fromhex("83010 2".replace(" ", "")))  # password: CAN


def test_the_can_works_with_3des_and_with_a_dh_group() -> None:
    for kw, secure in (({"tdes": True}, "3DES"), ({"dh": True, "curve_id": 0}, "AES")):
        _chip, groups, sim = build(can="48151623", **kw)
        machine = read(sim, number="", dob="", valid="", can="48151623", include_photo=True)
        assert machine.result()[0].data_groups == groups and machine.to_state()["sm"] == secure


def test_a_wrong_can_or_a_chip_that_does_not_take_one_is_named_and_nothing_is_read() -> None:
    _chip, _groups, sim = build(can="123456")
    with pytest.raises(cr.ChipReadError) as e:
        read(sim, number="", dob="", valid="", can="654321")
    assert e.value.code == "PACE_NOT_ACCEPTED" and "wrong document details" in e.value.detail
    assert (
        "654321" not in e.value.detail and "123456" not in e.value.detail
    )  # the password is not echoed
    _chip, _groups, no_can = build()  # a chip with no CAN refuses password reference 2
    with pytest.raises(cr.ChipReadError) as e2:
        read(no_can, number="", dob="", valid="", can="123456")
    assert e2.value.code == "PACE_NOT_ACCEPTED"


def test_the_can_needs_pace_so_a_chip_without_it_is_refused_by_name_and_not_probed_with_the_wrong_keys() -> (
    None
):
    _c, _groups, with_pace = build()
    plain = SimulatedChip(with_pace.files, INFO)  # a chip with no EF.CardAccess
    with pytest.raises(cr.ChipReadError) as e:
        read(plain, number="", dob="", valid="", can="123456")  # a chip with no EF.CardAccess
    assert e.value.code == "CAN_NEEDS_PACE"
    assert (
        plain.sent == [b"\x6a\x82"]
    )  # one probe for EF.CardAccess, then it stopped: no Basic Access Control attempt with the wrong keys
    mapping = H("04007F000702020404") + bytes([1])  # a variant this reader lacks
    _chip, _groups, only = build(infos=(pace_info(mapping, 12),), can="123456")
    with pytest.raises(cr.ChipReadError) as e2:
        read(only, number="", dob="", valid="", can="123456")
    assert e2.value.code == "CAN_NEEDS_PACE" and "integrated mapping" in e2.value.detail
    with pytest.raises(cr.ChipReadError) as e3:
        cr.ChipMachine.start("", "", "", can="12 34")
    assert e3.value.code == "CAN_INVALID"
    with pytest.raises(cr.ChipReadError) as e4:
        cr.ChipMachine.start("", "", "", can="123456", try_pace=False)
    assert e4.value.code == "CAN_NEEDS_PACE"


def test_the_state_of_a_dh_read_with_a_can_stays_plain_data() -> None:
    _chip, _groups, sim = build(curve_id=0, dh=True, can="123456")
    seen: list[dict] = []
    read(sim, number="", dob="", valid="", can="123456", between=seen.append)
    assert len(seen) > 10
    json.dumps(seen[-1])  # sealed between requests: it must serialise
