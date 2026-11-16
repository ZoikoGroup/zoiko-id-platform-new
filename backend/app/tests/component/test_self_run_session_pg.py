"""Self-run identity verification over HTTP against PostgreSQL (CR-095, CR-102): a session no provider handles, judged from the person's chip.

Off by default; the chip alone proves the document and never the holder; a chip that cannot be judged keeps the session waiting; a decided session
never changes; a profile that needs binding is not satisfied by a chip alone.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.trust import TrustConfig
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from epassport_fixture import make_chip

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def chip_body(chip) -> dict:  # type: ignore[no-untyped-def]
    return {
        "sod_base64": b64(chip.sod),
        "data_groups": {str(n): b64(g) for n, g in chip.data_groups.items()},
    }


def requirements(*, binding: bool) -> dict:
    return {
        "required_claims": ["legal_name", "birthdate"], "accepted_evidence": ["GOVERNMENT_ID"], "binding_required": binding,
        "liveness_required": binding, "biometric_jurisdictions": ["GB"], "jurisdictions": ["GB"], "freshness_days": 365,
        "manual_review": True, "review_approvals": 1, "reliance_purposes": ["onboarding"],
    }  # fmt: skip


def world(rt_url: str, *, enabled: bool = True):  # type: ignore[no-untyped-def]
    tenant, alice, admin = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-sr"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(
                c, tenant, [admin], ["verification.profile.manage"], ["verification_profile:*"]
            )
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(sid, tenant, admin, now - timedelta(seconds=10), "native:webauthn", 2, pr.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=1), now),
            )  # fmt: skip
            return sid

    sid = asyncio.run(seed())
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", self_run_enabled=enabled),
        keyring=ring,
    )  # fmt: skip

    def bearer(
        scope: str = "", *, with_session: bool = False, client: str = "onboarding"
    ) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=client, now=datetime.now(UTC),
            session_id=sid if with_session else None, scope=frozenset(scope.split()),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    return (
        app,
        alice,
        bearer(with_session=True),
        bearer("verification:execute verification:read", client="onboarding-svc"),
    )


def new_profile(http: TestClient, adm: dict[str, str], *, binding: bool) -> str:
    body = {
        "name": f"self-run-{uid()[:6]}",
        "level": "IAL-Z2" if binding else "IAL-Z1",
        "requirements": requirements(binding=binding),
    }
    made = http.post("/verification-profiles", headers=adm, json=body)
    assert made.status_code == 201, made.text
    pid = made.json()["profile_id"]
    assert http.post(f"/verification-profiles/{pid}:activate", headers=adm).status_code == 200
    return pid


def start(http: TestClient, svc: dict[str, str], pid: str, alice: str):  # type: ignore[no-untyped-def]
    return http.post(
        "/v2/self-run/verifications", headers=svc,
        json={"profile_id": pid, "subject_principal_id": alice, "purpose": "onboarding", "jurisdiction": "GB"},
    )  # fmt: skip


def test_the_routes_answer_404_until_the_deployment_switches_self_run_on(rt_url: str) -> None:
    app, alice, _adm, svc = world(rt_url, enabled=False)
    with TestClient(app, base_url=ISS) as http:
        out = start(http, svc, uid(), alice)
        assert out.status_code == 404 and out.json()["code"] == "self_run_not_enabled", out.text
        assert (
            http.post(
                f"/v2/self-run/verifications/{uid()}/chip", headers=svc, json=chip_body(make_chip())
            ).status_code
            == 404
        )


def test_a_chip_alone_verifies_a_profile_that_needs_no_binding_and_never_a_profile_that_does(
    rt_url: str,
) -> None:
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        no_binding = new_profile(http, adm, binding=False)
        needs_binding = new_profile(http, adm, binding=True)

        started = start(http, svc, no_binding, alice)
        assert started.status_code == 201, started.text
        vid = started.json()["verification_id"]
        assert (
            started.json()["status"] == "IN_PROGRESS" and started.json()["adapter_id"] == "self-run"
        )
        assert started.json()["awaiting"] == "CHIP_EVIDENCE"

        # no country signing CA is configured, so the chip cannot be judged and the session keeps waiting: never an approval
        waiting = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        )
        assert waiting.status_code == 200, waiting.text
        assert (
            waiting.json()["status"] == "IN_PROGRESS"
            and waiting.json()["chip_status"] == "INDETERMINATE"
        )
        assert "SELF_RUN_CHIP_INDETERMINATE" in waiting.json()["reason_codes"]
        assert waiting.json()["holder_bound"] is False

        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        done = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        )
        assert done.status_code == 200, done.text
        out = done.json()
        assert out["status"] == "VERIFIED" and out["chip_status"] == "VERIFIED_LIMITED"
        assert (
            "CHIP_PASSIVE_AUTH_ONLY" in out["reason_codes"]
            and "SELF_RUN_BIOMETRICS_OFF" in out["reason_codes"]
        )
        assert (
            "ERIKSSON" not in done.text and "pretend face image" not in done.text
        )  # nothing from the chip is echoed

        # a decided session never changes, and a session is judged once
        again = http.post(
            f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=chip_body(chip)
        )
        assert (
            again.status_code == 409 and again.json()["code"] == "session_not_awaiting_evidence"
        ), again.text

        # the profile that needs a bound holder is NOT satisfied by a chip alone
        vid2 = start(http, svc, needs_binding, alice).json()["verification_id"]
        bound = http.post(
            f"/v2/self-run/verifications/{vid2}/chip", headers=svc, json=chip_body(chip)
        ).json()
        assert bound["status"] != "VERIFIED", bound


def test_a_changed_data_group_fails_and_chip_data_that_is_not_base64_is_refused(
    rt_url: str,
) -> None:
    chip = make_chip()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)
        vid = start(http, svc, pid, alice).json()["verification_id"]
        bad_b64 = http.post(
            f"/v2/self-run/verifications/{vid}/chip",
            headers=svc,
            json={"sod_base64": "!!!notbase64!!!", "data_groups": {"1": "AAAA"}},
        )
        assert bad_b64.status_code == 422, bad_b64.text
        forged = chip_body(chip)
        forged["data_groups"] = {**forged["data_groups"], "1": b64(b"\x61\x5b" + b"\x00" * 91)}
        out = http.post(f"/v2/self-run/verifications/{vid}/chip", headers=svc, json=forged)
        assert out.status_code == 200, out.text
        assert out.json()["status"] == "FAILED" and out.json()["chip_status"] == "NOT_VERIFIED"
        missing = http.post(
            f"/v2/self-run/verifications/{uid()}/chip", headers=svc, json=chip_body(chip)
        )
        assert missing.status_code in (404, 409), missing.text
        assert (
            http.post(f"/v2/self-run/verifications/{vid}/chip", json=chip_body(chip)).status_code
            == 401
        )


# ---------------------------------------------------------------------------------------- Active Authentication and the face path (CR-102)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from epassport_fixture import dg1, mrz_td3

PHOTO = b"\xff\xd8\xff\xe0 pretend face image bytes \xff\xd9"


def aa_chip():  # type: ignore[no-untyped-def]
    key = ec.generate_private_key(ec.SECP256R1())
    dg15 = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return key, make_chip(groups={1: dg1(mrz_td3()), 2: PHOTO, 15: dg15})


def sign(key, challenge_hex: str) -> str:  # type: ignore[no-untyped-def]
    r, s = utils.decode_dss_signature(
        key.sign(bytes.fromhex(challenge_hex), ec.ECDSA(hashes.SHA256()))
    )
    return b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def test_a_chip_that_signs_the_sessions_challenge_is_checked_and_a_copy_is_a_suspected_clone(
    rt_url: str,
) -> None:
    key, chip = aa_chip()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=False)

        started = start(http, svc, pid, alice).json()
        challenge = started["aa_challenge"]
        assert len(challenge) == 16 and challenge != "0" * 16  # 8 random bytes in hex
        genuine = http.post(
            f"/v2/self-run/verifications/{started['verification_id']}/chip", headers=svc,
            json={**chip_body(chip), "active_authentication": {"response_base64": sign(key, challenge)}},
        )  # fmt: skip
        assert genuine.status_code == 200, genuine.text
        assert (
            genuine.json()["status"] == "VERIFIED"
            and "SELF_RUN_CLONE_CHECKED" in genuine.json()["reason_codes"]
        )

        # the same data on a chip that does not hold the private key (a copy) answers with a signature made by another key
        copy_key = ec.generate_private_key(ec.SECP256R1())
        started2 = start(http, svc, pid, alice).json()
        cloned = http.post(
            f"/v2/self-run/verifications/{started2['verification_id']}/chip", headers=svc,
            json={**chip_body(chip), "active_authentication": {"response_base64": sign(copy_key, started2["aa_challenge"])}},
        )  # fmt: skip
        assert cloned.status_code == 200, cloned.text
        out = cloned.json()
        assert out["status"] != "VERIFIED" and "SELF_RUN_CLONE_SUSPECTED" in out["reason_codes"], (
            out
        )

        # without the answer the clone check is said not to have been done, never assumed
        started3 = start(http, svc, pid, alice).json()
        plain = http.post(
            f"/v2/self-run/verifications/{started3['verification_id']}/chip",
            headers=svc,
            json=chip_body(chip),
        ).json()
        assert "SELF_RUN_CLONE_NOT_CHECKED" in plain["reason_codes"]


def test_a_spent_challenge_is_replaced_so_a_captured_answer_cannot_be_replayed(rt_url: str) -> None:
    key, chip = aa_chip()
    app, alice, adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        pid = new_profile(http, adm, binding=False)
        started = start(http, svc, pid, alice).json()
        first = started["aa_challenge"]
        captured = sign(key, first)
        # no country is configured yet, so the chip cannot be judged and the session keeps waiting with a NEW challenge
        waiting = http.post(
            f"/v2/self-run/verifications/{started['verification_id']}/chip", headers=svc,
            json={**chip_body(chip), "active_authentication": {"response_base64": captured}},
        ).json()  # fmt: skip
        assert (
            waiting["status"] == "IN_PROGRESS"
            and waiting["aa_challenge"]
            and waiting["aa_challenge"] != first
        )
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        replay = http.post(
            f"/v2/self-run/verifications/{started['verification_id']}/chip", headers=svc,
            json={**chip_body(chip), "active_authentication": {"response_base64": captured}},
        ).json()  # fmt: skip
        assert (
            replay["status"] != "VERIFIED" and "SELF_RUN_CLONE_SUSPECTED" in replay["reason_codes"]
        )


def test_an_accepted_engine_binds_the_holder_to_the_chip_photo_and_no_engine_means_no_binding(
    rt_url: str, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
    from test_self_run_verification import accepted
    from zid_platform.modules.verification.domain import biometric_engine as be
    from zid_platform.modules.verification.infrastructure import self_run_config

    _key, chip = aa_chip()
    app, alice, adm, svc = world(rt_url)
    face = {"probe_base64": b64(b"live face"), "liveness_base64": b64(b"live capture")}
    with TestClient(app, base_url=ISS) as http:
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        pid = new_profile(http, adm, binding=True)

        # no engine accepted: the media is ignored and nothing binds the holder
        v1 = start(http, svc, pid, alice).json()["verification_id"]
        off = http.post(
            f"/v2/self-run/verifications/{v1}/chip",
            headers=svc,
            json={**chip_body(chip), "face": face},
        ).json()
        assert "SELF_RUN_BIOMETRICS_OFF" in off["reason_codes"] and off["status"] != "VERIFIED"

        # an engine whose evaluation record is complete, and an adapter for exactly that engine
        monkeypatch.setattr(self_run_config, "raw", lambda: accepted())
        http.app.state.biometric_engine = be.FakeEngine(0.95, 0.97, "deepface-arcface", "1.4.0")
        v2 = start(http, svc, pid, alice).json()["verification_id"]
        bound = http.post(
            f"/v2/self-run/verifications/{v2}/chip",
            headers=svc,
            json={**chip_body(chip), "face": face},
        )
        assert bound.status_code == 200, bound.text
        assert "SELF_RUN_FACE_MATCHED_TO_CHIP_PHOTO" in bound.json()["reason_codes"], bound.json()
        assert bound.json()["status"] == "VERIFIED", bound.json()
        assert "live face" not in bound.text and "pretend face image" not in bound.text

        # a face that does not match the chip photo fails and is never favourable
        http.app.state.biometric_engine = be.FakeEngine(0.2, 0.97, "deepface-arcface", "1.4.0")
        v3 = start(http, svc, pid, alice).json()["verification_id"]
        no = http.post(
            f"/v2/self-run/verifications/{v3}/chip",
            headers=svc,
            json={**chip_body(chip), "face": face},
        ).json()
        assert no["status"] == "FAILED" and "SELF_RUN_FACE_NO_MATCH" in no["reason_codes"]
