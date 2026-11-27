"""The rest of WF07 that can be built honestly (ZID-CP-WF07 v2.0 .09, .10, A07-10, A07-13, A07-14; CR-169), over the real routes against PostgreSQL.

What these hold: a stop by outside issuer (or one of its keys) stops the agents bound to it and withdraws those bindings; risk signals are places to look, never a score; an agent credential carries no authority and its verifier keeps
identity, validity and key possession apart, honours revocation and stopped agents, and accepts a presentation once, for one relying party; and the MCP front gives exactly the gateway's answers and offers no second path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from wf07_fixture import W, uid
from zid_crypto import LocalSigner, sign_jws
from zid_platform.modules.machine.domain.tool_gateway import GRANT_TYP

ISSUER = "https://idp.example.com/tenant-1"
IDP_AUD = "https://api.zoiko.test/agents"


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_a_stop_by_outside_issuer_withdraws_its_bindings_and_risk_signals_are_places_to_look_never_a_score(
    rt_url: str, owner_url: str
) -> None:
    w = W(rt_url, owner_url)
    key_a, key_b = LocalSigner("key-a"), LocalSigner("key-b")
    with w.client() as http:
        who = w.person(w.human)

        def bind(agent: str, subject: str, key: LocalSigner) -> Any:
            return http.post(
                f"/v1/machine/agents/{agent}/idp-bindings",
                headers=who,
                json={
                    "profile": "okta",
                    "issuer": ISSUER,
                    "subject": subject,
                    "audience": IDP_AUD,
                    "keys": [key.public_jwk()],
                },
            )

        assert bind(w.root, "obj-1", key_a).status_code == 201
        assert bind(w.child, "obj-2", key_b).status_code == 201
        # nobody has heard from either provider binding: a signal for each, and no score anywhere
        rep = http.get("/v1/machine/risk-signals", headers=who).json()
        assert (
            rep["score"] is None
            and rep["counts"]["by_kind"]["PROVIDER_NOT_HEARD_FROM"] == 2
            and "not proof" in rep["note"]
            and all(s["is_a_finding"] is False for s in rep["signals"])
        )
        # a malformed issuer stop is refused; a stop naming one key reaches only the agent bound with that key
        assert (
            http.post(
                "/v1/machine/revocations:plan",
                headers=who,
                json={"scope": "ISSUER", "subject": "http://idp.example.com"},
            ).status_code
            == 422
        )
        plan = http.post(
            "/v1/machine/revocations:plan",
            headers=who,
            json={"scope": "ISSUER", "subject": f"{ISSUER}#key-b"},
        )
        assert (
            plan.status_code == 200
            and plan.json()["would_stop"]["agents"] == [w.child]
            and plan.json()["key_id"] == "key-b"
        )
        whole = http.post(
            "/v1/machine/revocations:plan", headers=who, json={"scope": "ISSUER", "subject": ISSUER}
        ).json()
        assert set(whole["would_stop"]["agents"]) == {w.root, w.child}
        stop = http.post(
            "/v1/machine/revocations",
            headers=who,
            json={
                "scope": "ISSUER",
                "subject": f"{ISSUER}#key-b",
                "reason": "Provider reported key-b stolen",
            },
        )
        assert stop.status_code == 201, stop.text
        s = stop.json()
        assert (
            s["scope"] == "ISSUER"
            and s["subject"] == f"{ISSUER}#key-b"
            and s["targets"]["agent_ids"] == [w.child]
            and s["targets"]["bindings_revoked"] == 1
        )
        states = {
            r.a: r.s
            for r in w.db(
                "SELECT agent_principal_id::text AS a, status::text AS s FROM agent.agent_profile WHERE tenant_id = :t"
            )
        }
        assert (
            states[w.child] == "SUSPENDED" and states[w.root] == "ACTIVE"
        )  # only the agent bound with that key
        live = {
            r.k
            for r in w.db(
                "SELECT keys->0->>'kid' AS k FROM agent.idp_binding WHERE tenant_id = :t AND status = 'ACTIVE'"
            )
        }
        assert live == {"key-a"}  # the provider's trust in key-b is withdrawn too
        # the open stop shows up as a signal; reading signals changed nothing
        kinds = http.get("/v1/machine/risk-signals", headers=who).json()["counts"]["by_kind"]
        assert kinds["STOP_STILL_OPEN"] == 1
        assert http.get("/v1/machine/risk-signals", headers=w.person(w.other)).status_code == 200


def test_an_agent_credential_is_bound_to_its_key_carries_no_authority_and_the_verifier_keeps_what_it_proves_apart(
    rt_url: str, owner_url: str
) -> None:
    w = W(rt_url, owner_url)
    agent_key = LocalSigner("agent-key-1")
    other_key = LocalSigner("agent-key-1")  # same id, different key
    with w.client() as http:
        who = w.person(w.human)
        issue = lambda **kw: http.post(
            f"/v1/machine/agents/{w.root}/credentials",
            headers=who,
            json={"public_key": agent_key.public_jwk(), **kw},
        )
        # malformed and unsuitable requests: a private key, a stranger's mandate, too long a life, an unknown agent
        assert issue(public_key={**agent_key.public_jwk(), "d": "s"}).status_code == 422
        assert issue(mandate_id=str(uuid.uuid4())).status_code == 422
        assert issue(ttl_hours=721).status_code == 422
        assert (
            http.post(
                f"/v1/machine/agents/{uid()}/credentials",
                headers=who,
                json={"public_key": agent_key.public_jwk()},
            ).status_code
            == 404
        )
        made = issue(audience="relying-party-1", evidence_class="E2")
        assert made.status_code == 201, made.text
        c = made.json()
        assert (
            c["authority_evaluated"] is False
            and c["status"] == "ACTIVE"
            and c["credential"].count(".") == 2
            and "limits" in c
        )
        _hdr, claims = w.ring.verify(c["credential"], typ="zid-agent-credential+jwt")
        assert (
            claims["sub"] == f"urn:zoiko:agent:{w.root}"
            and claims["vc"]["authority_evaluated"] is False
            and claims["cnf"]["jkt"] == c["key_thumbprint"]
        )
        assert "permission" not in str(claims).lower() and "spend" not in str(claims).lower()
        cred_id = c["credential_id"]
        assert (
            "credential"
            not in http.get("/v1/machine/agent-credentials", headers=who).json()["items"][0]
        )  # the text comes back once

        def present(key: LocalSigner = agent_key, **over: Any) -> str:
            ts = int(datetime.now(UTC).timestamp())
            base = {
                "aud": "relying-party-1",
                "nonce": "nonce-123456",
                "cred": cred_id,
                "iat": ts,
                "jti": uid(),
            }
            return sign_jws(key, {**base, **over}, typ="zid-agent-presentation+jwt")

        def verify(**kw: Any) -> Any:
            body = {"credential": c["credential"], "relying_party": "relying-party-1", **kw}
            return http.post("/v1/machine/agent-credentials:verify", headers=who, json=body)

        # without a presentation it is valid but nobody has shown they hold the key: say so, never "approved"
        a = verify().json()
        assert (
            a["verdict"] == "VALID_BUT_POSSESSION_NOT_SHOWN"
            and a["indicators"]["key_possession"] is None
            and a["indicators"]["authority_evaluated"] is False
            and a["indicators"]["may_spend"] is None
        )
        # a presentation signed by the bound key, for this relying party and nonce, proves possession, once
        good = present()
        b = verify(presentation=good, nonce="nonce-123456").json()
        assert (
            b["verdict"] == "IDENTITY_VERIFIED_AUTHORITY_NOT_EVALUATED"
            and b["indicators"]["key_possession"] is True
            and b["reason_codes"] == []
            and "approved" not in str(b).lower()
        )
        assert verify(presentation=good, nonce="nonce-123456").json()["reason_codes"] == [
            "PRESENTATION_REPLAYED"
        ]
        # a stolen credential without the key, a presentation for another party, and a wrong nonce each fail with their own reason
        assert verify(presentation=present(other_key), nonce="nonce-123456").json()[
            "reason_codes"
        ] == ["PRESENTATION_SIGNATURE_INVALID"]
        assert verify(presentation=present(aud="relying-party-2"), nonce="nonce-123456").json()[
            "reason_codes"
        ] == ["PRESENTATION_AUDIENCE_MISMATCH"]
        assert verify(presentation=present(), nonce="another-nonce-1").json()["reason_codes"] == [
            "PRESENTATION_NONCE_MISMATCH"
        ]
        assert verify(presentation=present()).json()["verdict"] == "NOT_VALID"  # no nonce given
        # an altered or foreign credential fails its signature
        assert verify(credential=c["credential"][:-6] + "AAAAAA").json()["reason_codes"] == [
            "CREDENTIAL_SIGNATURE_INVALID"
        ]
        # a stopped agent's credential is not good, whatever its dates say; and revocation never un-revokes
        stop = http.post(
            "/v1/machine/revocations",
            headers=who,
            json={"scope": "AGENT", "subject": w.root, "reason": "Stopping the agent"},
        )
        assert stop.status_code == 201, stop.text
        stopped = verify(presentation=present(), nonce="nonce-123456").json()
        assert (
            stopped["verdict"] == "NOT_VALID"
            and "AGENT_SUSPENDED" in stopped["reason_codes"]
            and stopped["indicators"]["credential_valid"] is False
        )
        assert (
            http.post(
                f"/v1/machine/agents/{w.root}/credentials",
                headers=who,
                json={"public_key": agent_key.public_jwk()},
            ).status_code
            == 409
        )  # a stopped agent is given no credential
        gone = http.post(f"/v1/machine/agent-credentials/{cred_id}:revoke", headers=who)
        assert gone.status_code == 200 and gone.json()["status"] == "REVOKED"
        assert (
            http.post(f"/v1/machine/agent-credentials/{cred_id}:revoke", headers=who).status_code
            == 409
        )
        assert "CREDENTIAL_REVOKED" in verify().json()["reason_codes"]
        import pytest

        with pytest.raises(Exception, match="never returns|never deleted"):
            w.db("UPDATE agent.agent_credential SET status = 'ACTIVE' WHERE tenant_id = :t")
        with pytest.raises(Exception, match="never deleted"):
            w.db("DELETE FROM agent.agent_credential WHERE tenant_id = :t")
        with pytest.raises(Exception, match="does not change"):
            w.db("UPDATE agent.agent_credential SET jkt = 'x' WHERE tenant_id = :t")


def test_the_mcp_front_gives_the_gateways_answers_and_offers_no_second_path(
    rt_url: str, owner_url: str
) -> None:
    w = W(rt_url, owner_url)
    with w.client() as http:
        agent = w.bearer(w.child)

        def rpc(
            method: str,
            params: dict[str, Any] | None = None,
            rid: int | None = 1,
            headers: dict[str, str] | None = None,
        ) -> Any:
            body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if rid is not None:
                body["id"] = rid
            if params is not None:
                body["params"] = params
            return http.post("/v1/machine/mcp", headers=headers or agent, json=body)

        init = rpc("initialize", {"protocolVersion": "2026-07-28"}).json()["result"]
        assert (
            init["protocolVersion"] == "2026-07-28"
            and init["capabilities"] == {"tools": {"listChanged": False}}
            and "grants nothing" in init["instructions"]
        )
        assert rpc("ping").json()["result"] == {}
        assert rpc("notifications/initialized", rid=None).status_code == 202
        assert (
            rpc("resources/list").json()["error"]["code"] == -32601
            and rpc("prompts/list").json()["error"]["code"] == -32601
        )
        assert (
            http.post("/v1/machine/mcp", headers=agent, json={"method": "tools/list"}).json()[
                "error"
            ]["code"]
            == -32600
        )  # not JSON-RPC 2.0
        # who may use it: not a person, not a token that is not a workload token
        assert rpc("tools/list", headers=w.person(w.human)).status_code == 403
        assert rpc("tools/list", headers=w.bearer(w.child, scope=frozenset())).status_code == 403

        listed = rpc("tools/list").json()["result"]["tools"]
        assert (
            [t["name"] for t in listed] == ["docs.read"]
            and "grants nothing" in listed[0]["description"]
            and listed[0]["annotations"]["sideEffect"] == "NONE"
        )

        def call(name: str, **args: Any) -> dict[str, Any]:
            return rpc("tools/call", {"name": name, "arguments": args}).json()  # type: ignore[no-any-return]

        base = {
            "resource_type": "agent",
            "resource_id": "any",
            "arguments": {"invoice": "demo_492"},
        }
        ok = call("docs.read", **base)["result"]
        assert (
            ok["isError"] is False
            and ok["structuredContent"]["decision"] == "ALLOW"
            and ok["structuredContent"]["agent_chain"] == [w.child, w.root]
        )
        # the same call through the native route is the same grant: same tool, same digest, same chain
        native = http.post(
            "/v1/machine/tool-calls:authorize",
            headers=agent,
            json={
                "tool_id": "docs.read",
                "resource_type": "agent",
                "resource_id": "any",
                "arguments_digest": ARGS_OF({"invoice": "demo_492"}),
            },
        )
        assert native.status_code == 200
        _h, mcp_claims = w.ring.verify(ok["structuredContent"]["grant"], typ=GRANT_TYP)
        _h, nat_claims = w.ring.verify(native.json()["grant"], typ=GRANT_TYP)
        assert {
            k: mcp_claims[k]
            for k in ("tool_id", "args_sha256", "agent_chain", "action", "resource")
        } == {
            k: nat_claims[k]
            for k in ("tool_id", "args_sha256", "agent_chain", "action", "resource")
        }
        # the gateway's refusals come back as tool errors naming the reasons, never as success
        ghost = call("docs.ghost", **base)["result"]
        assert (
            ghost["isError"] is True
            and ghost["structuredContent"]["decision"] == "DENY"
            and "TOOL_NOT_REGISTERED" in ghost["structuredContent"]["reason_codes"]
        )
        # the agent is never an argument, and malformed calls are protocol errors
        assert call("docs.read", agent_id=w.root, **base)["error"]["code"] == -32602
        assert call("docs.read", resource_type="agent")["error"]["code"] == -32602
        assert rpc("tools/call", {"arguments": {}}).json()["error"]["code"] == -32602
        # a stopped parent takes the call with it: there is no path around the gateway
        assert (
            http.post(
                "/v1/machine/revocations",
                headers=w.person(w.human),
                json={"scope": "AGENT", "subject": w.root, "reason": "Stopping both"},
            ).status_code
            == 201
        )
        after = rpc("tools/call", {"name": "docs.read", "arguments": base})
        assert after.status_code in (401, 403) or after.json()["result"]["isError"] is True
        assert (
            w.db("SELECT count(*) AS n FROM agent.tool_grant WHERE tenant_id = :t")[0].n == 2
        )  # only the two calls made before the stop


def ARGS_OF(args: dict[str, Any]) -> str:
    from zid_platform.modules.machine.domain.tool_gateway import args_digest

    return args_digest(args)
