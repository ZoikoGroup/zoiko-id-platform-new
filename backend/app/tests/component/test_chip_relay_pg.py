"""The chip relay over HTTP against PostgreSQL (CR-108): a device carries card commands, our server runs the protocol and judges what comes back.

The device is played by a simulated chip. Everything the server does is the real path: the reader, Basic Access Control, secure messaging, Active Authentication
against the session's challenge, passive authentication against the configured country CA, and the profile's decision. The server keeps no state between steps: the
protocol's state travels in a sealed token, so any process can take the next step.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_self_run_session_pg import ISS, aa_chip, new_profile, start, world
from zid_platform.modules.evidence.application import chip_reader as cr
from zid_platform.modules.evidence.application.runtime import configure_runtime, current_runtime
from zid_platform.modules.evidence.application.trust import TrustConfig
from zid_platform.modules.verification.application import chip_link

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from chip_simulator import SimulatedChip
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

NUMBER, DOB, VALID_TO = "L898902C3", "740812", "320415"  # the fixture passport's printed details
MRZ = {"document_number": NUMBER, "date_of_birth": DOB, "document_valid_to": VALID_TO}


def chip_and_sim(**sim_args):
    key, chip = aa_chip()

    def signer(challenge: bytes) -> bytes:
        r, s = utils.decode_dss_signature(key.sign(challenge, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    files = {
        cr.FILE_IDS["SOD"]: chip.sod,
        cr.FILE_IDS["DG1"]: chip.data_groups[1],
        cr.FILE_IDS["DG15"]: chip.data_groups[15],
    }
    sim = SimulatedChip(
        files, cr.mrz_information(NUMBER, DOB, VALID_TO), aa_signer=signer, **sim_args
    )
    return chip, sim


def url(vid: str, tail: str = "") -> str:
    return f"/v2/self-run/verifications/{vid}/chip-link{tail}"


def drive(
    http: TestClient, headers: dict, vid: str, sim: SimulatedChip, first, *, limit: int = 500
):
    """What a device does: send each command to the chip, hand back the answer with the token that came with it, until the read ends or a request is refused."""
    step = first
    for _ in range(limit):
        assert step.status_code in (200, 201), step.text
        body = step.json()
        if body["state"] != "READING":
            return step
        answer = asyncio.run(sim(base64.b64decode(body["apdu_base64"])))
        step = http.post(
            url(vid, "/step"),
            headers=headers,
            json={"token": body["token"], "response_base64": base64.b64encode(answer).decode()},
        )
        if step.status_code not in (200, 201):
            return step
    raise AssertionError("the read never ended")


def test_a_device_that_only_carries_commands_gets_the_chip_read_checked_and_judged(
    rt_url: str,
) -> None:
    chip, sim = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]

        opened = http.post(url(vid), headers=svc, json=MRZ)
        assert opened.status_code == 201 and opened.json()["state"] == "READING", opened.text
        assert NUMBER not in opened.text and "token" in opened.json()
        done = drive(http, svc, vid, sim, opened)
        read = done.json()
        assert read["state"] == "READ" and read["groups_read"] == [1, 15], read
        assert (
            read["active_authentication_answered"] is True
        )  # the server ran Active Authentication with the session's own challenge
        assert set(read["groups_missing"]) == {"DG14"} and read["expires_in"] > 0

        judged = http.post(url(vid, "/finish"), headers=svc, json={"token": read["token"]})
        assert judged.status_code == 200, judged.text
        out = judged.json()
        assert out["status"] == "VERIFIED" and out["chip_status"] == "VERIFIED_LIMITED", out
        assert (
            "CHIP_PASSIVE_AUTH_ONLY" in out["reason_codes"]
            and "SELF_RUN_CLONE_CHECKED" in out["reason_codes"]
        )
        for secret in (NUMBER, DOB, VALID_TO, "ERIKSSON"):
            assert (
                secret not in judged.text and secret not in done.text
            )  # nothing the chip held or the person typed comes back

        # the session is decided once: the same token judges nothing a second time, and no new read can start
        again = http.post(url(vid, "/finish"), headers=svc, json={"token": read["token"]})
        assert (
            again.status_code == 409 and again.json()["code"] == "session_not_awaiting_evidence"
        ), again.text
        late = http.post(url(vid), headers=svc, json=MRZ)
        assert late.status_code == 409 and late.json()["code"] == "session_not_awaiting_evidence"


def test_the_server_keeps_nothing_between_steps_so_any_process_can_take_the_next_one(
    rt_url: str,
) -> None:
    chip, sim = chip_and_sim()
    chip_b, sim_b = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert, chip_b.csca.cert))
        pid = new_profile(http, adm, binding=False)
        assert not hasattr(
            http.app.state, "chip_links"
        )  # there is no registry of reads to be lost or to need sticky routing

        # between every round the process is "replaced" by another one of the same deployment (the same keys), and two reads are interleaved
        vid_a = start(http, svc, pid, alice).json()["verification_id"]
        vid_b = start(http, svc, pid, alice).json()["verification_id"]
        reads = {
            "a": [vid_a, sim, http.post(url(vid_a), headers=svc, json=MRZ).json()],
            "b": [vid_b, sim_b, http.post(url(vid_b), headers=svc, json=MRZ).json()],
        }
        steps = 0
        while any(r[2]["state"] == "READING" for r in reads.values()):
            configure_runtime(dataclasses.replace(current_runtime()))
            for entry in reads.values():
                vid, device, body = entry
                if body["state"] != "READING":
                    continue
                answer = asyncio.run(device(base64.b64decode(body["apdu_base64"])))
                nxt = http.post(
                    url(vid, "/step"),
                    headers=svc,
                    json={
                        "token": body["token"],
                        "response_base64": base64.b64encode(answer).decode(),
                    },
                )
                assert nxt.status_code in (200, 201), nxt.text
                entry[2] = nxt.json()
                steps += 1
        a, b = reads["a"][2], reads["b"][2]
        assert steps > 20 and a["state"] == b["state"] == "READ"
        # a token is for its own session: the other read's token judges nothing here
        wrong = http.post(url(vid_a, "/finish"), headers=svc, json={"token": b["token"]})
        assert wrong.status_code == 404 and wrong.json()["code"] == "chip_link_not_found"
        for vid, body in ((vid_a, a), (vid_b, b)):
            assert (
                http.post(url(vid, "/finish"), headers=svc, json={"token": body["token"]}).json()[
                    "status"
                ]
                == "VERIFIED"
            )


def test_a_clone_that_cannot_sign_is_a_suspected_clone_through_the_relay_too(rt_url: str) -> None:
    chip, sim = chip_and_sim()
    other = ec.generate_private_key(ec.SECP256R1())

    def wrong(challenge: bytes) -> bytes:
        r, s = utils.decode_dss_signature(other.sign(challenge, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    sim.aa_signer = wrong
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        read = drive(http, svc, vid, sim, http.post(url(vid), headers=svc, json=MRZ)).json()
        out = http.post(url(vid, "/finish"), headers=svc, json={"token": read["token"]}).json()
        assert out["status"] != "VERIFIED" and "SELF_RUN_CLONE_SUSPECTED" in out["reason_codes"], (
            out
        )


def test_a_face_sent_with_no_accepted_engine_is_ignored_and_the_reason_says_so(rt_url: str) -> None:
    chip, sim = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        read = drive(http, svc, vid, sim, http.post(url(vid), headers=svc, json=MRZ)).json()
        face = {
            "probe_base64": base64.b64encode(b"live face").decode(),
            "liveness_base64": base64.b64encode(b"live capture").decode(),
        }
        out = http.post(
            url(vid, "/finish"), headers=svc, json={"token": read["token"], "face": face}
        )
        assert out.status_code == 200 and "SELF_RUN_BIOMETRICS_OFF" in out.json()["reason_codes"], (
            out.text
        )
        assert "live face" not in out.text


def test_wrong_document_details_and_a_pace_only_chip_end_the_read_with_a_named_reason(
    rt_url: str,
) -> None:
    chip, sim = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        wrong = drive(
            http,
            svc,
            vid,
            sim,
            http.post(url(vid), headers=svc, json={**MRZ, "date_of_birth": "740813"}),
        )
        assert (
            wrong.status_code == 422
            and wrong.json()["code"] == "bac_not_accepted"
            and "PACE" in wrong.text
        ), wrong.text
        assert "740813" not in wrong.text

        _chip2, pace = chip_and_sim(require_pace=True)
        vid2 = start(http, svc, pid, alice).json()["verification_id"]
        refused = drive(http, svc, vid2, pace, http.post(url(vid2), headers=svc, json=MRZ))
        assert refused.status_code == 422 and refused.json()["code"] == "bac_not_accepted"
        # the session is still waiting: nothing was judged, and nothing was approved
        assert http.get(f"/verifications/{vid}", headers=svc).json()["status"] == "IN_PROGRESS"


def test_a_token_belongs_to_the_client_that_was_given_it(rt_url: str) -> None:
    chip, sim = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        opened = http.post(url(vid), headers=svc, json=MRZ).json()
        answer = base64.b64encode(
            asyncio.run(sim(base64.b64decode(opened["apdu_base64"])))
        ).decode()
        # a different client of the same company (and this token has no verification scope), then a token carried to another session
        assert http.post(
            url(vid, "/step"),
            headers=adm,
            json={"token": opened["token"], "response_base64": answer},
        ).status_code in (403, 404)
        other_vid = start(http, svc, pid, alice).json()["verification_id"]
        carried = http.post(
            url(other_vid, "/step"),
            headers=svc,
            json={"token": opened["token"], "response_base64": answer},
        )
        assert carried.status_code == 404 and carried.json()["code"] == "chip_link_not_found"
        # a token changed in any way opens nothing
        flipped = opened["token"][:-6] + (
            "AAAAAA" if opened["token"][-6:] != "AAAAAA" else "BBBBBB"
        )
        assert (
            http.post(
                url(vid, "/step"), headers=svc, json={"token": flipped, "response_base64": answer}
            ).status_code
            == 404
        )
        assert (
            http.post(url(vid, "/finish"), headers=svc, json={"token": flipped}).status_code == 404
        )


def test_the_relay_is_limited_in_time_and_in_the_size_of_what_it_accepts(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    chip, _sim = chip_and_sim()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)

        # a device that stops answering gets a timeout, never a result
        vid = start(http, svc, pid, alice).json()["verification_id"]
        first = http.post(url(vid), headers=svc, json=MRZ).json()
        monkeypatch.setattr(chip_link, "STEP_TIMEOUT", 0.3)
        time.sleep(0.6)
        late = http.post(
            url(vid, "/step"),
            headers=svc,
            json={
                "token": first["token"],
                "response_base64": base64.b64encode(b"\x90\x00").decode(),
            },
        )
        assert late.status_code == 408 and late.json()["code"] == "chip_link_timeout", late.text
        monkeypatch.setattr(chip_link, "STEP_TIMEOUT", 30.0)

        # a read that outlives its lifetime is gone
        monkeypatch.setattr(chip_link, "LIFETIME", 1.0)
        vid2 = start(http, svc, pid, alice).json()["verification_id"]
        opened = http.post(url(vid2), headers=svc, json=MRZ).json()
        time.sleep(1.4)
        expired = http.post(
            url(vid2, "/step"),
            headers=svc,
            json={"token": opened["token"], "response_base64": "kAA="},
        )
        assert expired.status_code == 404
        monkeypatch.setattr(chip_link, "LIFETIME", 120.0)

        # input that is not what it should be is refused before anything runs
        vid3 = start(http, svc, pid, alice).json()["verification_id"]
        for bad in (
            {**MRZ, "document_number": "L898 902C"},
            {**MRZ, "date_of_birth": "7408"},
            {**MRZ, "document_valid_to": "32O415"},
            {**MRZ, "extra": 1},
            {"document_number": NUMBER},
        ):
            assert http.post(url(vid3), headers=svc, json=bad).status_code == 422, bad
        opened3 = http.post(url(vid3), headers=svc, json=MRZ).json()
        assert (
            http.post(
                url(vid3, "/step"),
                headers=svc,
                json={"token": opened3["token"], "response_base64": "!!!notbase64"},
            ).status_code
            == 422
        )
        big = base64.b64encode(b"\x00" * 5000).decode()
        assert (
            http.post(
                url(vid3, "/step"),
                headers=svc,
                json={"token": opened3["token"], "response_base64": big},
            ).status_code
            == 422
        )
        assert (
            http.post(
                url(vid3, "/step"), headers=svc, json={"token": "short", "response_base64": "AAAA"}
            ).status_code
            == 422
        )


def test_the_relay_answers_404_until_self_run_is_switched_on(rt_url: str) -> None:
    app, _alice, _adm, svc = world(rt_url, enabled=False)
    with TestClient(app, base_url=ISS) as http:
        out = http.post(url("00000000-0000-0000-0000-000000000000"), headers=svc, json=MRZ)
        assert out.status_code == 404 and out.json()["code"] == "self_run_not_enabled"
