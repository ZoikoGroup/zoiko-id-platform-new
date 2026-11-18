"""The stateless chip relay (CR-108): the server keeps nothing between steps, and every property still holds.

The device is played by a simulated chip. The key custody is the project's own envelope, with development keys.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Iterator

import pytest
from chip_simulator import SimulatedChip
from epassport_fixture import dg1, make_chip, mrz_td3
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.verification.application import chip_link as cl

NUMBER, DOB, VALID_TO = "L898902C3", "740812", "320415"
ME = cl.Owner("T1", "P1", "C1")
SESSION = "S1"


@pytest.fixture(autouse=True)
def runtime() -> Iterator[None]:
    configure_runtime(development_runtime("uk-link"))
    yield
    configure_runtime(None)


def run(coro):
    return asyncio.run(coro)


def sim_chip(**kw) -> SimulatedChip:
    photo = cr._tlv(0x75, bytes(range(256)) * 12)
    groups = {1: dg1(mrz_td3()), 2: photo, 15: bytes([0x6F, 0x03, 1, 2, 3])}
    chip = make_chip(groups=groups)
    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: groups[1],
        cr.FILE_IDS["DG2"]: photo,
        cr.FILE_IDS["DG15"]: groups[15],
    }
    sim = SimulatedChip(files, cr.mrz_information(NUMBER, DOB, VALID_TO), **kw)
    sim.groups = groups  # type: ignore[attr-defined]
    return sim


def start_args(**over):
    return {
        "document_number": NUMBER,
        "date_of_birth": DOB,
        "document_valid_to": VALID_TO,
        "challenge_hex": None,
        "include_photo": False,
        **over,
    }


async def drive(sim, owner=ME, session=SESSION, *, between=None, **over) -> cl.Step:
    step = await cl.start(owner, session, **start_args(**over))
    while step.kind == "apdu":
        if between:
            between()
        answer = await sim(step.apdu)
        step = await cl.step(owner, session, step.token, answer)
    return step


def test_a_whole_read_works_with_no_state_kept_between_steps() -> None:
    sim = sim_chip(aa_signer=lambda c: b"\xaa" * 8 + c)
    done = run(drive(sim, challenge_hex="0102030405060708"))
    assert done.kind == "read" and done.expires_in == int(cl.FINISH_WINDOW)
    assert done.summary == {
        "groups_read": [1, 15],
        "groups_missing": ["DG14"],
        "active_authentication_answered": True,
    }
    result = run(cl.take_result(ME, SESSION, done.token))
    assert result.chip.data_groups[1] == sim.groups[
        1
    ] and result.active_authentication == b"\xaa" * 8 + bytes.fromhex("0102030405060708")  # type: ignore[attr-defined]
    assert 2 not in result.chip.data_groups  # the photo was not asked for


def test_the_photo_is_read_when_asked_for_and_still_fits_in_a_token() -> None:
    sim = sim_chip()
    done = run(drive(sim, include_photo=True))
    result = run(cl.take_result(ME, SESSION, done.token))
    assert result.chip.data_groups[2] == sim.groups[2]  # type: ignore[attr-defined]


def test_another_process_with_the_same_keys_can_take_any_step_and_one_with_other_keys_cannot() -> (
    None
):
    sim = sim_chip()
    shared = dataclasses.replace(
        current_runtime()
    )  # another process of the same deployment (the same key provider)

    def other_process() -> None:
        configure_runtime(dataclasses.replace(shared))

    done = run(drive(sim, between=other_process))
    assert done.kind == "read"

    async def broken() -> None:
        step = await cl.start(ME, SESSION, **start_args())
        configure_runtime(development_runtime("uk-link"))  # a different deployment: other keys
        await cl.step(ME, SESSION, step.token, await sim_chip()(step.apdu))

    with pytest.raises(cl.LinkError) as e:
        run(broken())
    assert e.value.code == "CHIP_LINK_NOT_FOUND"


def test_a_token_opens_only_for_the_company_person_client_and_session_it_was_issued_to() -> None:
    sim = sim_chip()
    step = run(cl.start(ME, SESSION, **start_args()))
    answer = run(sim(step.apdu))
    for owner, session in (
        (cl.Owner("T2", "P1", "C1"), SESSION),
        (cl.Owner("T1", "P2", "C1"), SESSION),
        (cl.Owner("T1", "P1", "C2"), SESSION),
        (ME, "S2"),
    ):
        with pytest.raises(cl.LinkError) as e:
            run(cl.step(owner, session, step.token, answer))
        assert (
            e.value.code == "CHIP_LINK_NOT_FOUND"
        )  # someone else's token looks exactly like no token
        with pytest.raises(cl.LinkError):
            run(cl.take_result(owner, session, step.token))


def test_a_device_that_waits_too_long_gets_a_timeout_and_a_read_that_runs_too_long_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = sim_chip()
    step = run(cl.start(ME, SESSION, **start_args()))
    answer = run(sim(step.apdu))
    monkeypatch.setattr(cl, "STEP_TIMEOUT", 0.2)
    time.sleep(0.4)
    with pytest.raises(cl.LinkError) as e:
        run(cl.step(ME, SESSION, step.token, answer))
    assert e.value.code == "CHIP_LINK_TIMEOUT"

    monkeypatch.setattr(cl, "STEP_TIMEOUT", 30.0)
    monkeypatch.setattr(cl, "LIFETIME", 1.0)
    short = run(cl.start(ME, SESSION, **start_args()))
    time.sleep(1.3)
    with pytest.raises(cl.LinkError) as e2:
        run(cl.step(ME, SESSION, short.token, answer))
    assert e2.value.code == "CHIP_LINK_NOT_FOUND"  # expired inside the token


def test_the_number_of_commands_and_the_size_of_an_answer_are_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = sim_chip()
    step = run(cl.start(ME, SESSION, **start_args()))
    for bad in (b"", b"\x00" * (cl.MAX_RESPONSE + 1)):
        with pytest.raises(cl.LinkError) as e:
            run(cl.step(ME, SESSION, step.token, bad))
        assert e.value.code == "CHIP_RESPONSE_INVALID"
    monkeypatch.setattr(cl, "MAX_STEPS", 3)
    with pytest.raises(cl.LinkError) as e2:
        run(drive(sim))
    assert e2.value.code == "TOO_MANY_COMMANDS"


def test_a_finished_read_cannot_be_judged_early_or_stepped_again() -> None:
    first = run(cl.start(ME, SESSION, **start_args()))
    with pytest.raises(cl.LinkError) as e:
        run(cl.take_result(ME, SESSION, first.token))
    assert e.value.code == "CHIP_LINK_NOT_FINISHED"
    done = run(drive(sim_chip()))
    with pytest.raises(cl.LinkError) as e2:
        run(cl.step(ME, SESSION, done.token, b"\x90\x00"))
    assert e2.value.code == "CHIP_LINK_NOT_WAITING"


def test_replaying_an_old_step_makes_the_read_fail_and_never_succeed() -> None:
    sim = sim_chip()

    async def go() -> None:
        step = await cl.start(ME, SESSION, **start_args())
        old = []
        for _ in range(8):  # well into the read, where secure messaging is active
            answer = await sim(step.apdu)
            old.append((step.token, answer))
            step = await cl.step(ME, SESSION, step.token, answer)
        # a device replays an earlier (token, answer): the platform answers with the command it answered before. (An early plain step would only restart
        # the same read, since a chip lets Basic Access Control run again; once secure messaging is active the counter makes a replay useless.)
        replayed = await cl.step(ME, SESSION, *old[6])
        # ...but the chip has moved on, so sending that command to it again is refused and the read cannot continue
        answer = await sim(replayed.apdu)
        await cl.step(ME, SESSION, replayed.token, answer)

    with pytest.raises(cl.LinkError):
        run(go())


def test_wrong_details_and_pace_only_chips_end_the_read_with_a_named_reason() -> None:
    with pytest.raises(cl.LinkError) as e:
        run(drive(sim_chip(), date_of_birth="740813"))
    assert (
        e.value.code == "BAC_NOT_ACCEPTED"
        and "PACE" in e.value.detail
        and "740813" not in str(e.value)
    )
    with pytest.raises(cl.LinkError) as e2:
        run(drive(sim_chip(require_pace=True)))
    assert e2.value.code == "BAC_NOT_ACCEPTED"
    with pytest.raises(cl.LinkError) as e3:
        run(cl.start(ME, SESSION, **start_args(document_number="")))
    assert e3.value.code == "MRZ_INPUT_INVALID"
    with pytest.raises(cl.LinkError):
        run(cl.start(ME, SESSION, **start_args(challenge_hex="01")))  # a challenge is eight bytes


# ------------------------------------------------------------------------------------ a read over PACE through the sealed tokens


def test_a_read_over_pace_works_through_the_sealed_tokens_even_when_the_process_is_replaced_between_steps() -> (
    None
):
    from chip_simulator import PaceSimulatedChip

    photo = cr._tlv(0x75, bytes(range(256)) * 12)
    groups = {1: dg1(mrz_td3()), 2: photo, 15: bytes([0x6F, 0x03, 1, 2, 3])}
    chip = make_chip(groups=groups)
    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: groups[1],
        cr.FILE_IDS["DG2"]: photo,
        cr.FILE_IDS["DG15"]: groups[15],
    }
    sim = PaceSimulatedChip(
        files,
        cr.mrz_information(NUMBER, DOB, VALID_TO),
        curve_id=13,
        bits=192,
        aa_signer=lambda c: b"\xaa" * 8 + c,
    )
    shared = dataclasses.replace(current_runtime())

    def other_process() -> None:
        configure_runtime(dataclasses.replace(shared))

    done = run(
        drive(sim, between=other_process, challenge_hex="0102030405060708", include_photo=True)
    )
    result = run(cl.take_result(ME, SESSION, done.token))
    assert (
        result.chip.data_groups == groups
        and result.active_authentication == b"\xaa" * 8 + bytes.fromhex("0102030405060708")
    )
    assert not any(
        c[0] == 0x00 and c[1] in (0x82, 0x84) for c in sim.requests
    )  # no Basic Access Control command was sent: PACE did it

    # wrong document details over PACE end the read with the chip's refusal named, never with data
    with pytest.raises(cl.LinkError) as e:
        run(
            drive(
                PaceSimulatedChip(files, cr.mrz_information(NUMBER, DOB, VALID_TO)),
                date_of_birth="740813",
            )
        )
    assert (
        e.value.code == "PACE_NOT_ACCEPTED"
        and "wrong document details" in e.value.detail
        and "740813" not in str(e.value)
    )


# ------------------------------------------------------------------------------------------ the CAN, 3DES and DH through the relay


def _pace_files() -> tuple[dict, dict, object]:
    photo = cr._tlv(0x75, bytes(range(256)) * 12)
    groups = {1: dg1(mrz_td3()), 2: photo, 15: bytes([0x6F, 0x03, 1, 2, 3])}
    chip = make_chip(groups=groups)
    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: groups[1],
        cr.FILE_IDS["DG2"]: photo,
        cr.FILE_IDS["DG15"]: groups[15],
    }
    return files, groups, chip


@pytest.mark.parametrize("extra", [{}, {"tdes": True}, {"dh": True, "curve_id": 0}])
def test_a_read_with_only_the_can_works_through_the_sealed_tokens(extra: dict) -> None:
    from chip_simulator import PaceSimulatedChip

    files, groups, _chip = _pace_files()
    sim = PaceSimulatedChip(files, cr.mrz_information(NUMBER, DOB, VALID_TO), can="123456", **extra)
    done = run(
        drive(
            sim,
            document_number="",
            date_of_birth="",
            document_valid_to="",
            can="123456",
            include_photo=True,
        )
    )
    assert run(cl.take_result(ME, SESSION, done.token)).chip.data_groups == groups
    assert not any(c[1] in (0x82, 0x84) and c[0] == 0x00 for c in sim.requests)


def test_a_chip_that_cannot_do_pace_cannot_be_read_with_only_the_can_and_the_error_says_why() -> (
    None
):
    sim = sim_chip()
    with pytest.raises(cl.LinkError) as e:
        run(drive(sim, document_number="", date_of_birth="", document_valid_to="", can="123456"))
    assert e.value.code == "CAN_NEEDS_PACE" and "123456" not in str(e.value)


def test_the_request_takes_all_three_details_or_only_the_can_and_nothing_between() -> None:
    from pydantic import ValidationError
    from zid_platform.modules.verification.api.v2_self_run import ChipLinkStartIn

    ChipLinkStartIn(document_number=NUMBER, date_of_birth=DOB, document_valid_to=VALID_TO)
    ChipLinkStartIn(can="123456")
    ChipLinkStartIn(
        document_number=NUMBER, date_of_birth=DOB, document_valid_to=VALID_TO, can="123456"
    )
    for bad in (
        {},
        {"document_number": NUMBER},
        {"document_number": NUMBER, "date_of_birth": DOB},
        {"can": "12"},
        {"can": "12 3456"},
        {"can": "1" * 17},
    ):
        with pytest.raises(ValidationError):
            ChipLinkStartIn(**bad)
