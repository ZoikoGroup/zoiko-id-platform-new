"""The chip relay: a phone or a browser carries card commands, our server runs the protocol, and the server keeps NOTHING between steps (CR-108).

The thing next to the passport (a phone's NFC, a desktop reader) only has to transmit bytes. The server asks for one command at a time. All of the protocol's state (the
access keys, the session keys, the counter, what has been read so far) is in a `ChipMachine`; between steps that state is SEALED (AES-256-GCM under a data key wrapped
by the tenant's key, the same envelope the stored uploads use) and handed to the device as an opaque token, which it returns with the chip's answer. So any server
process can take any step: there is no per-process state, no sticky routing and nothing to clean up.

What the sealing guarantees: the device cannot read the state or change it; the token opens only for the company, person, client and session it was issued to (anyone
else's looks exactly like no token); and it expires, on the server's clock. Time limits are in the token: the whole read has a lifetime, each answer has a time allowed,
and the number of commands is capped.

What it does not do: stop a device re-sending an OLD token with an OLD answer. That is harmless: the chip's secure-messaging counter has moved on, so the chip (and our own
check) would refuse the stale exchange, and the read just fails. A read is judged once because the SESSION is decided once, not because a token is used up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from zid_platform.modules.evidence.ports import (
    CamEvidence,
    ChipMachine,
    ChipRead,
    ChipReadError,
    SealError,
    open_sealed,
    seal,
)

PURPOSE = "chip-relay-state"
LIFETIME = 120.0  # seconds for the whole read, from its start
STEP_TIMEOUT = 30.0  # seconds the device has to answer a command
FINISH_WINDOW = (
    300.0  # seconds a finished read waits to be judged (a person may capture a selfie first)
)
MAX_STEPS = 600
MAX_RESPONSE = 4096  # bytes an answer may be (the largest read is 160 bytes plus protection)


class LinkError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Owner:
    tenant_id: str
    principal_id: str
    client_id: str


@dataclass(frozen=True, slots=True)
class RelayRead:
    """What a finished read leaves: the signed object, the groups, and the chip's answer to the Active Authentication challenge if one was run."""

    chip: ChipRead
    active_authentication: bytes | None
    chip_authentication: CamEvidence | None = (
        None  # CR-111: the chip's proof from PACE-CAM, judged later against data group 14 once passive authentication has said it was covered
    )


@dataclass(frozen=True, slots=True)
class Step:
    kind: Literal["apdu", "read"]
    token: str
    apdu: bytes = b""
    expires_in: int = 0
    summary: dict[str, Any] | None = (
        None  # what a finished read holds: which groups, whether Active Authentication was answered (never the data)
    )


def _binding(owner: Owner, session_id: str) -> dict[str, str]:
    return {"session": session_id, "principal": owner.principal_id, "client": owner.client_id}


async def _issue(
    owner: Owner, session_id: str, machine: ChipMachine, started: float, *, apdu: bytes
) -> Step:
    now = time.time()
    payload: dict[str, Any] = {"m": machine.to_state(), "t0": started, "t": now}
    ttl = FINISH_WINDOW if machine.done else max(1, int(started + LIFETIME - now))
    try:
        token = await seal(
            owner.tenant_id, PURPOSE, _binding(owner, session_id), payload, ttl_seconds=int(ttl)
        )
    except SealError as e:
        raise LinkError(
            "CHIP_LINK_TOO_LARGE" if e.code == "SEALED_TOO_LARGE" else "READER_ERROR"
        ) from e
    summary = None
    if machine.done:
        chip, answer = machine.result()
        summary = {
            "groups_read": sorted(chip.data_groups),
            "groups_missing": list(chip.skipped),
            "active_authentication_answered": answer is not None,
        }
    return Step("read" if machine.done else "apdu", token, apdu, int(ttl), summary)


async def _open(owner: Owner, session_id: str, token: str) -> dict[str, Any]:
    try:
        return await open_sealed(owner.tenant_id, PURPOSE, _binding(owner, session_id), token)
    except SealError as e:
        raise LinkError(
            "CHIP_LINK_NOT_FOUND"
        ) from e  # not ours, not for you, changed or expired: one answer for all of them


async def start(
    owner: Owner,
    session_id: str,
    *,
    document_number: str = "",
    date_of_birth: str = "",
    document_valid_to: str = "",
    can: str | None = None,
    challenge_hex: str | None,
    include_photo: bool,
) -> Step:
    """Open a read. The step it returns holds the first command and the token that goes back with the chip's answer."""
    try:
        machine = ChipMachine.start(
            document_number,
            date_of_birth,
            document_valid_to,
            include_photo=include_photo,
            aa_challenge=bytes.fromhex(challenge_hex) if challenge_hex else None,
            can=can,
        )
    except (ChipReadError, ValueError) as e:
        raise LinkError(getattr(e, "code", "READER_ERROR"), getattr(e, "detail", "")) from e
    return await _issue(owner, session_id, machine, time.time(), apdu=machine.next_command())


async def step(owner: Owner, session_id: str, token: str, response: bytes) -> Step:
    """Take the chip's answer. The result is the next command, or (kind `read`) the end of the read."""
    state = await _open(owner, session_id, token)
    if not response or len(response) > MAX_RESPONSE:
        raise LinkError("CHIP_RESPONSE_INVALID", "an answer is empty or too large")
    if time.time() - float(state["t"]) > STEP_TIMEOUT:
        raise LinkError("CHIP_LINK_TIMEOUT", "the device did not answer in time")
    try:
        machine = ChipMachine.from_state(state["m"])
        if machine.done:
            raise LinkError("CHIP_LINK_NOT_WAITING", "the read is already complete")
        if machine.steps >= MAX_STEPS:
            raise LinkError("TOO_MANY_COMMANDS")
        machine.feed(response)
        return await _issue(
            owner,
            session_id,
            machine,
            float(state["t0"]),
            apdu=b"" if machine.done else machine.next_command(),
        )
    except ChipReadError as e:
        raise LinkError(e.code, e.detail) from e


async def take_result(owner: Owner, session_id: str, token: str) -> RelayRead:
    """The finished read, for judging. Refused unless the read is complete."""
    state = await _open(owner, session_id, token)
    try:
        machine = ChipMachine.from_state(state["m"])
        if not machine.done:
            raise LinkError("CHIP_LINK_NOT_FINISHED", "the chip read has not finished")
        chip, answer = machine.result()
    except ChipReadError as e:
        raise LinkError(e.code, e.detail) from e
    return RelayRead(chip, answer, machine.chip_authentication())
