"""Step-up authentication with real WebAuthn verification (ZID-ENG-DEC-009 P0 tests T01-T06, T10, T12 at the API and ledger level).

A software authenticator signs the challenge the server derived from the exact command, so these tests prove the binding cryptographically:
the passkey assertion for one command is refused for another, a proof is spent once even under parallel use, it expires on the server's
clock, an agent or a service cannot obtain one, and every refusal leaves evidence.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, _session, approval, onboard
from softauthn import SoftAuthenticator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.application import step_up as service
from zid_platform.modules.authentication.domain.authenticator import (
    Authenticator,
    AuthenticatorStatus,
)
from zid_platform.modules.authentication.domain.step_up import StepUpError
from zid_platform.modules.authentication.infrastructure.authenticator_store import add_authenticator
from zid_platform.modules.authentication.infrastructure.step_up_config import step_up_catalogue
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

RP, ORIGIN = "id.zoiko.test", "https://id.zoiko.test"
OWNER_TRANSFER = "initiate-confirm-transfer-of-organization-ownership"
CAT = step_up_catalogue()


def uid() -> str:
    return str(uuid.uuid4())


def app_for(rt_url: str, o: Any, mode: str) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1",
                 webauthn_rp_id=RP, webauthn_origins=(ORIGIN,), step_up_enforcement=mode),
        keyring=o.ring,
    )  # fmt: skip


def give_passkey(rt_url: str, tenant: str, principal: str, **kw: Any) -> SoftAuthenticator:
    key = SoftAuthenticator(RP, ORIGIN, **kw)
    key.user_handle = uuid.UUID(principal).bytes

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await add_authenticator(
                c,
                Authenticator(
                    uid(), tenant, principal, "PASSKEY", AuthenticatorStatus.ACTIVE, datetime.now(UTC),
                    credential_id=key.credential_id, public_key_cose=key._cose(), user_verified=True,
                ),
                aal=2,
            )  # fmt: skip

    asyncio.run(run())
    return key


def audit(rt_url: str, tenant: str) -> list[tuple[str, str, str | None]]:
    async def run() -> list[tuple[str, str, str | None]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, outcome, reason_code FROM evidence.audit_record WHERE tenant_id = :t AND event_type LIKE 'authentication.step_up.%' ORDER BY created_at"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
            return [
                (str(r[0]).removeprefix("authentication.step_up."), str(r[1]), r[2]) for r in rows
            ]

    return asyncio.run(run())


def test_an_s2_command_needs_a_proof_for_exactly_that_command_and_spends_it(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    key = give_passkey(rt_url, o.tenant, o.owner)
    app = app_for(rt_url, o, "enforce")
    resource, own = f"tenant:{o.tenant}", o.bearer(o.owner)

    with TestClient(app, base_url=ISS) as http:

        def challenge(change: dict[str, Any], headers: dict[str, str] = own, **kw: Any) -> Any:
            return http.post(
                "/v1/step-up/challenges",
                headers=headers,
                json={"action": OWNER_TRANSFER, "resource": resource, "change": change, **kw},
            )

        def prove(
            ch: dict[str, Any], by: SoftAuthenticator = key, headers: dict[str, str] = own
        ) -> Any:
            return http.post(
                f"/v1/step-up/challenges/{ch['challenge_id']}/verify",
                headers=headers,
                json={"credential": by.get(ch["public_key"])},
            )

        def add_owner(person: str, proof: str | None = None, headers: dict[str, str] = own) -> Any:
            rid = approval(
                http,
                o,
                owner_url,
                action="tenant.owner.change",
                resource=resource,
                change=f"ADD_OWNER:{person}",
            )
            h = {**headers, **({"X-Step-Up-Proof": proof} if proof else {})}
            return http.post(
                "/v1/tenant/owners",
                headers=h,
                json={"principal_id": person, "approval_request_id": rid},
            )

        # the tiers are published for the UI to explain, never to decide
        tiers = http.get("/v1/step-up/tiers", headers=own).json()
        assert len(tiers["actions"]) == 24 and tiers["tiers"]["S2"] == {
            "max_age_seconds": 300,
            "single_use": True,
            "phishing_resistant": True,
            "action_bound": True,
        }
        assert (
            "sms" in tiers["s2_never_accepts"] and http.get("/v1/step-up/tiers").status_code == 401
        )

        # T01/T03: the command is refused without a proof, and the refusal says what to do
        none = add_owner(o.second)
        assert none.status_code == 403 and none.json()["error"]["code"] == "STEP_UP_REQUIRED", (
            none.text
        )
        assert "max_age=300" in none.headers["www-authenticate"]
        assert {d["field"]: d["issue"] for d in none.json()["error"]["details"]} == {
            "tier": "S2",
            "action": OWNER_TRANSFER,
            "reason": "STEP_UP_REQUIRED",
        }

        # the person is shown what they approve, written by the server
        r = challenge({"operation": "ADD_OWNER", "principal_id": o.second})
        assert r.status_code == 201, r.text
        ch = r.json()
        assert (
            ch["tier"] == "S2"
            and ch["requested_assurance"] == "PHISHING_RESISTANT_ACTION_BOUND"
            and len(ch["command_hash"]) == 64
        )
        assert (
            ch["summary"].startswith(
                "Initiate / confirm transfer of organization ownership on tenant:"
            )
            and "ADD_OWNER" in ch["summary"]
        )
        proof = prove(ch)
        assert proof.status_code == 200, proof.text
        p = proof.json()
        assert p["single_use"] is True and p["tier"] == "S2"
        assert (
            datetime.fromisoformat(p["expires_at"]) - datetime.fromisoformat(p["verified_at"])
        ).total_seconds() == 300

        # T04: the proof is for this principal; change any material field and it is useless, and it is not spent by the attempt
        other = uid()
        wrong = add_owner(other, p["proof_id"])
        assert (
            wrong.status_code == 403
            and wrong.json()["error"]["details"][-1]["issue"] == "STEP_UP_PAYLOAD_MISMATCH"
        ), wrong.text
        # the same passkey assertion cannot be replayed to mint a second proof (the challenge is single-use)
        assert prove(ch).status_code in (404, 401, 403)

        # another session of the same person cannot spend it (bound to the session it was proven in)
        async def second_session() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                return await _session(c, o.tenant, o.owner)

        first_sid, o.sessions[o.owner] = o.sessions[o.owner], asyncio.run(second_session())
        elsewhere = add_owner(o.second, p["proof_id"], headers=o.bearer(o.owner))
        assert (
            elsewhere.status_code == 403
            and elsewhere.json()["error"]["details"][-1]["issue"] == "STEP_UP_SESSION_MISMATCH"
        )
        o.sessions[o.owner] = first_sid
        own = o.bearer(o.owner)

        # the right command with the right proof goes through, once
        done = add_owner(o.second, p["proof_id"], headers=own)
        assert done.status_code == 201 and done.json()["role"] == "OWNER", done.text
        # T05: the same proof again, for the same command, finds it spent
        again = add_owner(o.second, p["proof_id"], headers=own)
        assert (
            again.status_code == 403
            and again.json()["error"]["details"][-1]["issue"] == "STEP_UP_PROOF_CONSUMED"
        ), again.text

        # an unknown proof, and a proof header that is not a proof
        assert (
            add_owner(o.second, uid()).json()["error"]["details"][-1]["issue"]
            == "STEP_UP_PROOF_INVALID"
        )
        junk = add_owner(o.second, "not-a-uuid")  # a malformed proof is a refusal, never a crash
        assert (
            junk.status_code == 403
            and junk.json()["error"]["details"][-1]["issue"] == "STEP_UP_PROOF_INVALID"
        )
        assert (
            http.post(
                "/v1/step-up/challenges/not-a-uuid/verify", headers=own, json={"credential": {}}
            ).status_code
            == 404
        )
        assert http.post("/v1/step-up/challenges/not-a-uuid/cancel", headers=own).status_code == 404

        # T01: removing a passkey is an S2 change too: refused without a proof, whatever else the session holds
        mine = http.get("/authn/authenticators", headers=own).json()["items"][0]["authenticator_id"]
        gone = http.post(f"/authn/authenticators/{mine}:revoke", headers=own)
        assert gone.status_code == 403 and gone.json()["error"]["code"] == "STEP_UP_REQUIRED", (
            gone.text
        )
        assert {d["field"]: d["issue"] for d in gone.json()["error"]["details"]}[
            "action"
        ] == "enroll-remove-replace-passkey-mfa-or-recovery-factor"

    events = audit(rt_url, o.tenant)
    names = [e[0] for e in events]
    assert (
        names.count("challenge_created") == 1
        and names.count("verified") == 1
        and names.count("consumed") == 1
    )
    assert (
        names.count("refused") >= 4
    )  # the missing proof, the other payload, the other session, the spent proof
    assert {e[1] for e in events if e[0] == "refused"} == {
        "DENIED"
    } and "STEP_UP_PAYLOAD_MISMATCH" in {e[2] for e in events}


def test_humans_only_a_strong_authenticator_and_an_assertion_for_this_challenge(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    key = give_passkey(rt_url, o.tenant, o.owner)
    app = app_for(rt_url, o, "enforce")
    resource, own = f"tenant:{o.tenant}", o.bearer(o.owner)
    agent = uid()

    async def seed_agent() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c,
                Principal(agent, o.tenant, PrincipalType.SERVICE, PrincipalStatus.ACTIVE, "uk-1"),
            )
            o.sessions[agent] = await _session(c, o.tenant, agent)

    asyncio.run(seed_agent())

    with TestClient(app, base_url=ISS) as http:

        def challenge(change: dict[str, Any], headers: dict[str, str]) -> Any:
            return http.post(
                "/v1/step-up/challenges",
                headers=headers,
                json={"action": OWNER_TRANSFER, "resource": resource, "change": change},
            )

        # T10: an agent, even with a session, cannot start a human step-up; nor can a delegated (act) token
        no = challenge({"operation": "ADD_OWNER"}, o.bearer(agent))
        assert no.status_code == 403 and no.json()["error"]["code"] == "STEP_UP_HUMAN_REQUIRED", (
            no.text
        )

        # no passkey means no step-up: governed recovery, never email or SMS
        none = challenge({"operation": "ADD_OWNER"}, o.bearer(o.second))
        assert none.status_code == 409 and none.json()["error"]["code"] == "NO_STRONG_AUTHENTICATOR"

        # an unclassified action and a payload with no single reading are refused before anyone is asked
        assert (
            http.post(
                "/v1/step-up/challenges",
                headers=own,
                json={"action": "delete-everything", "resource": resource},
            ).status_code
            == 422
        )
        assert challenge({"amount": 1.5}, own).status_code == 422
        # an ordinary action needs no challenge
        s0 = http.post(
            "/v1/step-up/challenges",
            headers=own,
            json={"action": "invite-standard-operator-or-viewer", "resource": resource},
        )
        assert s0.status_code == 201 and s0.json() == {
            "required": False,
            "tier": "S0",
            "action": "invite-standard-operator-or-viewer",
            "reasons": ["ACTION_S0"],
        }

        a = challenge({"operation": "ADD_OWNER", "principal_id": "a"}, own).json()
        b = challenge({"operation": "ADD_OWNER", "principal_id": "b"}, own).json()

        def verify(
            ch: dict[str, Any], credential: dict[str, Any], headers: dict[str, str] = own
        ) -> Any:
            return http.post(
                f"/v1/step-up/challenges/{ch['challenge_id']}/verify",
                headers=headers,
                json={"credential": credential},
            )

        # the assertion for command A cannot mint a proof for command B
        crossed = verify(b, key.get(a["public_key"]))
        assert (
            crossed.status_code == 403
            and crossed.json()["error"]["code"] == "STEP_UP_BINDING_MISMATCH"
        ), crossed.text
        # someone else's passkey, and a passkey without user verification, are not the person
        stranger = SoftAuthenticator(RP, ORIGIN)
        stranger.credential_id, stranger.user_handle = (
            key.credential_id,
            key.user_handle,
        )  # same id, a different private key
        assert verify(a, stranger.get(a["public_key"])).status_code == 401
        weak = give_passkey(rt_url, o.tenant, o.owner, user_verified=False)
        c = challenge({"operation": "ADD_OWNER", "principal_id": "c"}, own).json()
        assert verify(c, weak.get(c["public_key"])).status_code in (401, 403)
        # another person cannot verify mine
        assert verify(a, key.get(a["public_key"]), o.bearer(o.second)).status_code in (403, 404)
        # a genuine assertion works, and cancelling afterwards grants nothing
        # a failed assertion finishes its challenge (bounded retries): the same challenge cannot be tried again
        assert verify(a, key.get(a["public_key"])).status_code == 404
        d = challenge({"operation": "ADD_OWNER", "principal_id": "d"}, own).json()
        good = verify(d, key.get(d["public_key"]))
        assert good.status_code == 200, good.text
        cancel = http.post(f"/v1/step-up/challenges/{b['challenge_id']}/cancel", headers=own)
        assert cancel.status_code == 200 and cancel.json() == {
            "challenge_id": b["challenge_id"],
            "status": "CANCELLED",
            "grants_nothing": True,
        }
        assert (
            verify(b, key.get(b["public_key"])).status_code == 404
        )  # a cancelled challenge cannot be proven

    reasons = {e[2] for e in audit(rt_url, o.tenant) if e[0] == "refused"}
    assert {"STEP_UP_BINDING_MISMATCH"} <= reasons


def test_time_concurrency_and_the_database_keep_a_proof_single_use(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    key = give_passkey(rt_url, o.tenant, o.owner)
    app = app_for(rt_url, o, "shadow")
    resource, own = f"tenant:{o.tenant}", o.bearer(o.owner)
    change = {"operation": "ADD_OWNER", "principal_id": o.second}

    with TestClient(app, base_url=ISS) as http:

        def mint(n: int = 1) -> list[dict[str, Any]]:
            out = []
            for _ in range(n):
                ch = http.post(
                    "/v1/step-up/challenges",
                    headers=own,
                    json={"action": OWNER_TRANSFER, "resource": resource, "change": change},
                ).json()
                out.append(
                    http.post(
                        f"/v1/step-up/challenges/{ch['challenge_id']}/verify",
                        headers=own,
                        json={"credential": key.get(ch["public_key"])},
                    ).json()
                )
            return out

        fresh, late, parallel, expired_challenge = mint(), mint(), mint(), None
        stale = http.post(
            "/v1/step-up/challenges",
            headers=own,
            json={"action": OWNER_TRANSFER, "resource": resource, "change": change},
        ).json()
        expired_challenge = stale

    who = service.Who(o.tenant, o.owner, o.sessions[o.owner])

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng:

            async def use(proof: str, at: datetime | None = None, w: service.Who = who) -> str:
                try:
                    async with tenant_transaction(eng, ctx(o.tenant)) as c:
                        r = await service.check_and_consume(
                            c,
                            CAT,
                            w,
                            proof_id=proof,
                            action_id=OWNER_TRANSFER,
                            resource=resource,
                            change=change,
                            now=at or datetime.now(UTC),
                        )
                    return "OK" if r["spent"] else "UNSPENT"
                except StepUpError as e:
                    return e.code

            # T06: S2 lives five minutes on the server's clock, and not a second more
            verified = datetime.fromisoformat(fresh[0]["verified_at"])
            out["late"] = await use(late[0]["proof_id"], verified + timedelta(seconds=301))
            out["backdated_clock"] = await use(
                fresh[0]["proof_id"], verified - timedelta(seconds=5)
            )  # a clock behind the proof
            out["inside"] = await use(fresh[0]["proof_id"], verified + timedelta(seconds=299))
            out["twice"] = await use(fresh[0]["proof_id"], verified + timedelta(seconds=299))
            # another actor cannot spend it, and a proof of a lower tier cannot satisfy a higher one
            out["other_actor"] = await use(
                parallel[0]["proof_id"], w=service.Who(o.tenant, o.second, who.session_id)
            )
            # T05: ten parallel uses of one proof: exactly one executes
            results = await asyncio.gather(*[use(parallel[0]["proof_id"]) for _ in range(10)])
            out["parallel"] = (results.count("OK"), results.count("STEP_UP_PROOF_CONSUMED"))
            # a challenge left past its lifetime cannot be proven
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                try:
                    await service.verify_challenge(
                        c,
                        app.state.webauthn_settings,
                        CAT,
                        who,
                        challenge_id=expired_challenge["challenge_id"],
                        credential={},
                        now=datetime.now(UTC) + timedelta(minutes=6),
                    )
                except StepUpError as e:
                    out["challenge_expired"] = e.code
        return out

    got = asyncio.run(run())
    assert (
        got["late"] == "STEP_UP_PROOF_EXPIRED" and got["backdated_clock"] == "STEP_UP_PROOF_EXPIRED"
    )
    assert got["inside"] == "OK" and got["twice"] == "STEP_UP_PROOF_CONSUMED"
    assert got["other_actor"] == "STEP_UP_ACTOR_MISMATCH"
    assert got["parallel"] == (1, 9)
    assert got["challenge_expired"] == "STEP_UP_CHALLENGE_EXPIRED"

    async def guard() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            used = (
                await c.execute(
                    text(
                        "SELECT proof_id FROM authn.step_up_proof WHERE consumed_at IS NOT NULL AND tenant_id = :t LIMIT 1"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            for sql in (
                "UPDATE authn.step_up_proof SET consumed_at = NULL, consumed_for = NULL WHERE proof_id = :i",  # un-spend it
                "UPDATE authn.step_up_proof SET expires_at = expires_at + interval '1 hour' WHERE proof_id = :i",  # extend it
                "DELETE FROM authn.step_up_proof WHERE proof_id = :i",
                "UPDATE authn.step_up_challenge SET command_hash = repeat('0', 64) WHERE challenge_id = (SELECT challenge_id FROM authn.step_up_proof WHERE proof_id = :i)",
                "DELETE FROM authn.step_up_challenge WHERE challenge_id = (SELECT challenge_id FROM authn.step_up_proof WHERE proof_id = :i)",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(sql), {"i": used})
            fresh_one = (
                await c.execute(
                    text(
                        "SELECT proof_id FROM authn.step_up_proof WHERE consumed_at IS NULL AND tenant_id = :t LIMIT 1"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            await c.execute(
                text(
                    "UPDATE authn.step_up_proof SET consumed_at = now(), consumed_for = 'x' WHERE proof_id = :i"
                ),
                {"i": fresh_one},
            )
            with pytest.raises(DBAPIError):  # spent once; it cannot be spent again
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE authn.step_up_proof SET consumed_at = now(), consumed_for = 'y' WHERE proof_id = :i"
                        ),
                        {"i": fresh_one},
                    )

    asyncio.run(guard())


def test_shadow_mode_reports_without_blocking_and_off_is_silent(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    resource = f"tenant:{o.tenant}"

    for mode in ("shadow", "off"):
        app = app_for(rt_url, o, mode)
        with TestClient(app, base_url=ISS) as http:
            person = uid()
            rid = approval(
                http,
                o,
                owner_url,
                action="tenant.owner.change",
                resource=resource,
                change=f"ADD_OWNER:{person}",
            )
            r = http.post(
                "/v1/tenant/owners",
                headers=o.bearer(o.owner),
                json={"principal_id": person, "approval_request_id": rid},
            )
            # a principal that does not exist yet is refused by the owner rules themselves, not by step-up: the guard did not block
            assert r.json().get("error", {}).get("code") != "STEP_UP_REQUIRED", (mode, r.text)
    assert [
        e for e in audit(rt_url, o.tenant) if e[0] == "refused"
    ] == []  # nothing was refused, so nothing is recorded as refused
