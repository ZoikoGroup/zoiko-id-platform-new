"""Our own chip reader: BAC, secure messaging and file reading (CR-108), proved against Doc 9303 part 11 appendix D and a simulated chip.

Appendix D gives one complete BAC exchange. Every intermediate value below that the standard prints is asserted: the key seed, both keys, the cryptogram and
MAC the reader sends, the chip's answer and the send sequence counter. The reader and the simulated chip are then run against each other.
"""

from __future__ import annotations

import asyncio

import pytest
from chip_simulator import SimulatedChip
from epassport_fixture import dg1, make_chip, mrz_td3
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application import epassport as ep

H = bytes.fromhex
# the ICAO worked example: document number L898902C<, born 1969-08-06, expires 1994-06-23
ICAO_INFO = cr.mrz_information("L898902C<", "690806", "940623")
RND_IC, RND_IFD = H("4608F91988702212"), H("781723860C06C226")
K_IFD = H("0B795240CB7049B01C19B33E32804F0B")
E_IC_M_IC = H("46B9342A41396CD7386BF5803104D7CEDC122B9132139BAF2EEDC94EE178534F2F2D235D074D7449")


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------------------------- the standard's vectors


def test_the_key_seed_and_both_basic_access_keys_match_the_standard() -> None:
    assert ICAO_INFO == b"L898902C<369080619406236"
    import hashlib

    assert hashlib.sha1(ICAO_INFO).digest()[:16] == H("239AB9CB282DAF66231DC5A4DF6BFBAE")
    kenc, kmac = cr.bac_keys(ICAO_INFO)
    assert kenc == H("AB94FDECF2674FDFB9B391F85D7F76F2")
    assert kmac == H("7962D9ECE03D1ACD4C76089DCE131543")


def test_the_cryptogram_and_mac_the_reader_sends_match_the_standard() -> None:
    kenc, kmac = cr.bac_keys(ICAO_INFO)
    data = cr.external_authenticate_data(kenc, kmac, RND_IFD, RND_IC, K_IFD)
    assert data[:32] == H(
        "72C29C2371CC9BDB65B779B8E8D37B29ECC154AA56A8799FAE2F498F76ED92F2"
    )  # E.IFD
    assert data[32:] == H("5F1448EEA8AD90A7")  # M.IFD


def test_the_chips_answer_gives_the_standards_session_and_sequence_counter() -> None:
    kenc, kmac = cr.bac_keys(ICAO_INFO)
    ch = cr.finish_authentication(kenc, kmac, RND_IFD, RND_IC, K_IFD, E_IC_M_IC)
    assert ch.ksenc == H("979EC13B1CBFE9DCD01AB0FED307EAE5")
    assert ch.ssc == H("887022120C06C226")
    assert ch.ksmac == H(
        "F1CB1F1FB5ADF208806B89DC579DC1F8"
    )  # printed in Appendix D.3 (an earlier version of this test could not confirm it)
    k_ic = H("0B4F80323EB3191CB04970CB4052790B")
    assert bytes(a ^ b for a, b in zip(K_IFD, k_ic, strict=True)) == H(
        "0036D272F5C350ACAC50C3F572D23600"
    )


def test_a_chip_answer_with_a_wrong_mac_or_other_randoms_is_refused() -> None:
    kenc, kmac = cr.bac_keys(ICAO_INFO)
    flipped = E_IC_M_IC[:-1] + bytes([E_IC_M_IC[-1] ^ 1])
    with pytest.raises(cr.ChipReadError) as e:
        cr.finish_authentication(kenc, kmac, RND_IFD, RND_IC, K_IFD, flipped)
    assert e.value.code == "BAC_RESPONSE_INVALID"
    with pytest.raises(cr.ChipReadError):
        cr.finish_authentication(kenc, kmac, bytes(8), RND_IC, K_IFD, E_IC_M_IC)  # not our random
    with pytest.raises(cr.ChipReadError):
        cr.finish_authentication(kenc, kmac, RND_IFD, RND_IC, K_IFD, E_IC_M_IC[:-1])  # wrong length


def test_the_primitives_behave() -> None:
    assert cr.unpad(cr.pad(b"abc")) == b"abc" and len(cr.pad(b"12345678")) == 16
    with pytest.raises(cr.ChipReadError):
        cr.unpad(b"abc\x00\x00")
    key = H("AB94FDECF2674FDFB9B391F85D7F76F2")
    assert cr.decrypt(key, cr.encrypt(key, bytes(16))) == bytes(16)
    assert cr.mac(key, b"a") != cr.mac(key, b"b") and len(cr.mac(key, b"")) == 8
    assert all(b.bit_count() % 2 == 1 for b in cr.adjust_parity(bytes(range(16))))


def test_the_mrz_input_is_checked_before_anything_is_sent() -> None:
    for number, dob, exp in (
        ("", "690806", "940623"),
        ("L898902C<<", "690806", "940623"),
        ("L898902C<", "69080", "940623"),
        ("L898902C<", "690806", "94O623"),
        ("L89 902C<", "690806", "940623"),
    ):
        with pytest.raises(cr.ChipReadError) as e:
            cr.mrz_information(number, dob, exp)
        assert e.value.code == "MRZ_INPUT_INVALID"
    assert (
        cr.mrz_information("l898902c", "690806", "940623") == ICAO_INFO
    )  # case and filler do not matter


# ------------------------------------------------------------------------------------------------ the reader against a simulated chip

NUMBER, DOB, EXPIRY = "L898902C3", "740812", "320415"


def tlv(tag: int, value: bytes) -> bytes:
    return cr._tlv(tag, value)


def build(*, include_dg14: bool = False, **sim):
    photo = tlv(
        0x75, bytes(range(256)) * 12
    )  # three kilobytes: several reads and a two-byte length
    groups = {1: dg1(mrz_td3()), 2: photo}
    chip = make_chip(groups=groups)
    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: groups[1],
        cr.FILE_IDS["DG2"]: groups[2],
    }
    sim_chip = SimulatedChip(files, cr.mrz_information(NUMBER, DOB, EXPIRY), **sim)
    return chip, groups, sim_chip


def test_the_reader_reads_the_signed_object_and_groups_exactly_and_they_then_verify() -> None:
    chip, groups, sim = build()
    reader = cr.ChipReader(sim)

    async def go() -> cr.ChipRead:
        await reader.establish(NUMBER, DOB, EXPIRY)
        return await reader.read()

    got = run(go())
    assert got.sod == chip.sod and got.data_groups == groups
    assert set(got.skipped) == {"DG14", "DG15"}  # this chip has neither, and the reader says so
    # what it read is what the server's chip route judges
    verdict = ep.verify(got.sod, got.data_groups, csca=(chip.csca.cert,))
    assert verdict.status == "VERIFIED_LIMITED", verdict


def test_a_photo_can_be_left_unread() -> None:
    _chip, groups, sim = build()
    reader = cr.ChipReader(sim)

    async def go() -> cr.ChipRead:
        await reader.establish(NUMBER, DOB, EXPIRY)
        return await reader.read(include_photo=False)

    got = run(go())
    assert set(got.data_groups) == {1} and got.data_groups[1] == groups[1]


def test_the_wrong_document_details_give_no_session_and_name_the_likely_reasons() -> None:
    _chip, _groups, sim = build()
    reader = cr.ChipReader(sim)
    with pytest.raises(cr.ChipReadError) as e:
        run(reader.establish(NUMBER, "740813", EXPIRY))
    assert (
        e.value.code == "BAC_NOT_ACCEPTED" and "PACE" in e.value.detail and reader.channel is None
    )
    with pytest.raises(cr.ChipReadError) as e2:
        run(reader.read())  # nothing is read without a session
    assert e2.value.code == "NOT_AUTHENTICATED"


def test_a_chip_that_only_offers_pace_is_reported_as_such_never_guessed_at() -> None:
    _chip, _groups, sim = build(require_pace=True)
    with pytest.raises(cr.ChipReadError) as e:
        run(cr.ChipReader(sim).establish(NUMBER, DOB, EXPIRY))
    assert e.value.code == "BAC_NOT_ACCEPTED" and "PACE" in e.value.detail


def test_a_chip_without_the_application_is_reported() -> None:
    async def nothing(_apdu: bytes) -> bytes:
        return b"\x6a\x82"

    with pytest.raises(cr.ChipReadError) as e:
        run(cr.ChipReader(nothing).establish(NUMBER, DOB, EXPIRY))
    assert e.value.code == "APPLICATION_NOT_FOUND"


def test_a_response_with_a_wrong_mac_stops_the_read() -> None:
    _chip, _groups, sim = build(tamper_response_mac=lambda n: n == 3)
    reader = cr.ChipReader(sim)

    async def go() -> None:
        await reader.establish(NUMBER, DOB, EXPIRY)
        await reader.read()

    with pytest.raises(cr.ChipReadError) as e:
        run(go())
    assert e.value.code == "SECURE_MESSAGING_MAC_INVALID"


def test_a_captured_response_cannot_be_replayed_because_the_counter_moves() -> None:
    _chip, _groups, sim = build()
    reader = cr.ChipReader(sim)

    async def go() -> None:
        await reader.establish(NUMBER, DOB, EXPIRY)
        await reader.read_file(cr.FILE_IDS["DG1"])

    run(go())
    assert reader.channel is not None
    old = sim.sent[3]  # an earlier protected response
    with pytest.raises(cr.ChipReadError) as e:
        reader.channel.unprotect(old)
    assert e.value.code == "SECURE_MESSAGING_MAC_INVALID"


def test_a_missing_signed_object_is_an_error_but_a_missing_group_is_not() -> None:
    _chip, _groups, sim = build()
    del sim.files[cr.FILE_IDS["SOD"]]
    reader = cr.ChipReader(sim)

    async def go() -> None:
        await reader.establish(NUMBER, DOB, EXPIRY)
        await reader.read()

    with pytest.raises(cr.ChipReadError) as e:
        run(go())
    assert e.value.code == "SOD_MISSING"


@pytest.mark.parametrize("outer", [b"\x90\x00", b"\x6a\x82"])
def test_a_protected_error_status_is_read_from_inside_the_response_whatever_the_outer_status_is(
    outer: bytes,
) -> None:
    _chip, _groups, sim = build(outer_status_on_error=outer)
    reader = cr.ChipReader(sim)

    async def go() -> bytes | None:
        await reader.establish(NUMBER, DOB, EXPIRY)
        first = await reader.read_file(cr.FILE_IDS["DG14"])  # the chip has none
        again = await reader.read_file(
            cr.FILE_IDS["DG1"]
        )  # and the session is still in step afterwards
        assert again is not None
        return first

    assert run(go()) is None


def test_a_file_header_that_cannot_be_a_length_is_refused() -> None:
    for head in (b"", b"\x61", b"\x61\x85\x00\x00\x00\x00", b"\x61\x82\x01"):
        with pytest.raises(cr.ChipReadError):
            cr._file_length(head)
    assert cr._file_length(b"\x61\x05\x00\x00") == 7
    assert cr._file_length(b"\x75\x82\x0b\xb8") == 4 + 3000


# ------------------------------------------------------------------------------------ the resumable machine (a stateless relay's engine)

import json


def machine_read(
    sim,
    *,
    include_photo=True,
    aa_challenge=None,
    number=NUMBER,
    dob=DOB,
    expiry=EXPIRY,
    between=None,
):
    """What a stateless relay does: after every command the whole state is turned into plain data (as if sealed and handed to a device) and a new machine is
    built from it for the next answer, so nothing lives in a process between steps."""
    machine = cr.ChipMachine.start(
        number,
        dob,
        expiry,
        include_photo=include_photo,
        aa_challenge=aa_challenge,
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


def test_the_machine_reads_exactly_what_the_async_reader_reads() -> None:
    chip, groups, sim_a = build()
    _chip2, _groups2, sim_b = build()
    reader = cr.ChipReader(sim_a, random=lambda n: bytes(range(n)))

    async def go() -> cr.ChipRead:
        await reader.establish(NUMBER, DOB, EXPIRY)
        return await reader.read()

    expected = run(go())
    sim_b.files = dict(sim_a.files)
    chip_read, answer = machine_read(sim_b).result()
    assert (chip_read.sod, chip_read.data_groups, chip_read.skipped) == (
        expected.sod,
        expected.data_groups,
        expected.skipped,
    )
    assert chip_read.data_groups == groups and answer is None
    assert (
        ep.verify(chip_read.sod, chip_read.data_groups, csca=(chip.csca.cert,)).status
        == "VERIFIED_LIMITED"
    )


def test_the_machine_leaves_the_photo_unread_unless_asked() -> None:
    _chip, groups, sim = build()
    chip_read, _ = machine_read(sim, include_photo=False).result()
    assert set(chip_read.data_groups) == {1} and chip_read.data_groups[1] == groups[1]


def test_the_machine_runs_active_authentication_with_the_challenge_it_was_given() -> None:
    _chip, _groups, sim = build()
    sim.files[cr.FILE_IDS["DG15"]] = bytes([0x6F, 0x03, 1, 2, 3])
    sim.aa_signer = lambda challenge: b"\xaa" * 8 + challenge
    challenge = bytes.fromhex("0102030405060708")
    chip_read, answer = machine_read(sim, include_photo=False, aa_challenge=challenge).result()
    assert answer == b"\xaa" * 8 + challenge and 15 in chip_read.data_groups
    # a chip with no such key answers nothing and the read still completes
    sim.aa_signer = None
    _, none = machine_read(sim, include_photo=False, aa_challenge=challenge).result()
    assert none is None


def test_the_machine_state_is_plain_data_and_every_step_resumes_from_it() -> None:
    _chip, _groups, sim = build()
    seen: list[dict] = []
    machine_read(sim, between=seen.append)
    assert len(seen) > 10 and all(isinstance(s, dict) for s in seen)
    assert {s["phase"] for s in seen} >= {
        "SELECT_APP",
        "CHALLENGE",
        "AUTH",
        "SELECT_FILE",
        "READ_HEAD",
        "READ_BODY",
    }
    # the secret material is in the state, which is exactly why it must only ever leave the process sealed
    assert any(s["ksenc"] for s in seen) and seen[0]["kenc"]
    for bad in ({}, {"phase": "NOPE"}, {**seen[0], "phase": "NOPE"}):
        with pytest.raises(cr.ChipReadError) as e:
            cr.ChipMachine.from_state(bad)
        assert e.value.code == "STATE_INVALID"


def test_the_machine_refuses_what_the_async_reader_refuses() -> None:
    _chip, _groups, sim = build()
    with pytest.raises(cr.ChipReadError) as e:
        machine_read(sim, dob="740813")
    assert e.value.code == "BAC_NOT_ACCEPTED" and "PACE" in e.value.detail
    _chip, _groups, pace = build(require_pace=True)
    with pytest.raises(cr.ChipReadError) as e2:
        machine_read(pace)
    assert e2.value.code == "BAC_NOT_ACCEPTED"
    _chip, _groups, tampered = build(tamper_response_mac=lambda n: n == 3)
    with pytest.raises(cr.ChipReadError) as e3:
        machine_read(tampered)
    assert e3.value.code == "SECURE_MESSAGING_MAC_INVALID"
    _chip, _groups, no_sod = build()
    del no_sod.files[cr.FILE_IDS["SOD"]]
    with pytest.raises(cr.ChipReadError) as e4:
        machine_read(no_sod)
    assert e4.value.code == "SOD_MISSING"


def test_the_machine_cannot_be_driven_before_it_starts_or_after_it_ends() -> None:
    _chip, _groups, sim = build()
    machine = machine_read(sim, include_photo=False)
    with pytest.raises(cr.ChipReadError) as e:
        machine.next_command()
    assert e.value.code == "READ_FINISHED"
    with pytest.raises(cr.ChipReadError):
        machine.feed(b"\x90\x00")
    fresh = cr.ChipMachine.start(NUMBER, DOB, EXPIRY)
    with pytest.raises(cr.ChipReadError) as e2:
        fresh.result()
    assert e2.value.code == "READ_NOT_FINISHED"
    with pytest.raises(cr.ChipReadError):
        fresh.feed(b"\x90")  # not even a status
    with pytest.raises(cr.ChipReadError):
        cr.ChipMachine.start(NUMBER, DOB, EXPIRY, aa_challenge=b"short")


# ------------------------------------------------------------ Appendix D.4: secure messaging, command by command, as the standard prints it


def test_secure_messaging_produces_and_reads_exactly_the_standards_commands_and_responses() -> None:
    ch = cr.SecureChannel(
        H("979EC13B1CBFE9DCD01AB0FED307EAE5"),
        H("F1CB1F1FB5ADF208806B89DC579DC1F8"),
        H("887022120C06C226"),
    )

    # 1. SELECT EF.COM
    assert ch.protect(0x00, 0xA4, 0x02, 0x0C, H("011E")) == H(
        "0CA4020C158709016375432908C044F68E08BF8B92D635FF24F800"
    )
    assert ch.ssc == H("887022120C06C227")
    assert ch.unprotect(H("990290008E08FA855A5D4C50A8ED9000")) == (b"", H("9000"))
    assert ch.ssc == H("887022120C06C228")

    # 2. READ BINARY of the first four bytes
    assert ch.protect(0x00, 0xB0, 0x00, 0x00, le=4) == H("0CB000000D9701048E08ED6705417E96BA5500")
    data, status = ch.unprotect(H("8709019FF0EC34F992265199029000" + "8E08AD55CC17140B2DED9000"))
    assert (data, status) == (H("60145F01"), H("9000"))
    assert ch.ssc == H("887022120C06C22A")

    # 3. READ BINARY of the remaining 18 bytes from offset 4
    assert ch.protect(0x00, 0xB0, 0x00, 0x04, le=0x12) == H(
        "0CB000040D9701128E082EA28A70F3C7B53500"
    )
    data, status = ch.unprotect(
        H("871901FB9235F4E4037F2327DCC8964F1F9B8C30F42C8E2FFF224A990290008E08C8B2787EAEA07D749000")
    )
    assert (data, status) == (H("04303130365F36063034303030305C026175"), H("9000"))
    assert ch.ssc == H("887022120C06C22C")
    assert H("60145F01") + data == H(
        "60145F0104303130365F36063034303030305C026175"
    )  # the standard's EF.COM


def test_a_standards_response_with_one_changed_byte_is_refused() -> None:
    good = H("990290008E08FA855A5D4C50A8ED9000")
    for i in range(len(good) - 2):
        bad = good[:i] + bytes([good[i] ^ 1]) + good[i + 1 :]
        fresh = cr.SecureChannel(
            H("979EC13B1CBFE9DCD01AB0FED307EAE5"),
            H("F1CB1F1FB5ADF208806B89DC579DC1F8"),
            H("887022120C06C227"),
        )
        with pytest.raises(cr.ChipReadError):
            fresh.unprotect(bad)
