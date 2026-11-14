"""CR-094 against PostgreSQL and the real route: a signed one-time assertion becomes a short-lived token for the agent, and nothing else does.

Synthetic keys only. The agent is registered, approved and activated exactly as in CR-093 (no container image anywhere); then the client signs.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import CELL
from fastapi.testclient import TestClient
from gateway_fixture import AUD, ISS
from test_api_agents_pg import AUD as CLIENT_AUDIENCES
from test_api_agents_pg import Registry
from zid_crypto import LocalSigner, jwk_thumbprint, sign_jws
from zid_platform.main import create_app
from zid_platform.modules.machine.application import api_agents as svc
from zid_platform.modules.machine.domain import api_agents as dom
from zid_platform.settings import Settings

ROUTE = "/v1/machine/api-agent-token"
ADDRESS = ISS + ROUTE


def assertion(signer: LocalSigner, client_id: str, tenant: str, **over: Any) -> str:
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": client_id, "sub": client_id, "aud": ADDRESS, "resource": CLIENT_AUDIENCES[0], "tenant_id": tenant,
        "jti": "jti-" + uuid.uuid4().hex, "iat": now, "exp": now + 30,
    }  # fmt: skip
    return sign_jws(signer, {**claims, **over}, typ=dom.ASSERTION_TYP)


def payload(token: str) -> dict[str, Any]:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))  # type: ignore[no-any-return]


def test_the_signed_assertion_path_issues_a_bound_short_lived_token_and_refuses_everything_else(
    rt_url: str, owner_url: str
) -> None:
    r = Registry(rt_url, owner_url)
    w = r.w
    keyring = w.ring
    api = TestClient(
        create_app(
            Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_uuid=CELL),
            keyring=keyring,
        )
    )
    signer = LocalSigner("client-key-1")
    made = r.api_agent("Token reader", ["docs.read"], key=signer.public_jwk())
    agent_id, client_id = made["agent_id"], made["client"]["client_id"]

    def post(token: str) -> Any:
        return api.post(ROUTE, json={"assertion": token})

    # not active yet: a correct assertion earns nothing
    early = post(assertion(signer, client_id, r.t))
    assert early.status_code == 403 and early.json()["error"]["code"] == "AGENT_NOT_ACTIVE"

    r.activate(agent_id)
    good = assertion(signer, client_id, r.t)
    ok = post(good)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    claims = payload(body["access_token"])
    jkt = jwk_thumbprint(signer.public_jwk())
    assert body["token_type"] == "DPoP" and body["principal_id"] == agent_id and body["cnf"] == {"jkt": jkt}
    assert claims["sub"] == agent_id and claims["tenant_id"] == r.t and claims["cnf"] == {"jkt": jkt}
    assert claims["scope"] == "workload" and claims["risk_class"] == dom.API_TOKEN_RISK_CLASS
    assert claims["exp"] - claims["iat"] <= dom.API_TOKEN_LIFETIME_SECONDS  # short-lived
    assert body["attestation_cap"] == dom.API_ATTESTATION_CAP

    # single use: the same assertion again is a replay
    again = post(good)
    assert again.status_code == 403 and again.json()["error"]["code"] == "ASSERTION_REPLAYED"

    # signed by another key that claims the same name: refused, with the one answer every unknown caller gets
    forger = LocalSigner("client-key-1")
    bad = post(assertion(forger, client_id, r.t))
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "ASSERTION_INVALID"
    assert post(assertion(signer, "zid-api-nobody0000000000000", r.t)).status_code == 401
    assert post(assertion(signer, client_id, str(uuid.uuid4()))).status_code == 401  # another company: nothing there
    assert post("not.a.jws" + "x" * 30).status_code == 401
    assert api.post(ROUTE, json={"assertion": "x" * 30, "extra": 1}).status_code == 422

    # the claims are exact
    wrong_aud = post(assertion(signer, client_id, r.t, aud=ISS + "/other"))
    assert wrong_aud.status_code == 403 and wrong_aud.json()["error"]["code"] == "ASSERTION_AUDIENCE_MISMATCH"
    unlisted = post(assertion(signer, client_id, r.t, resource="https://elsewhere.example.test"))
    assert unlisted.json()["error"]["code"] == "ASSERTION_RESOURCE_NOT_REGISTERED"
    now = int(datetime.now(UTC).timestamp())
    long_lived = post(assertion(signer, client_id, r.t, exp=now + 3600))
    assert long_lived.json()["error"]["code"] == "ASSERTION_TOO_LONG_LIVED"
    a_key_in_the_assertion = post(assertion(signer, client_id, r.t))  # a header that carries its own key is never trusted
    assert a_key_in_the_assertion.status_code == 200
    embedded = sign_jws(signer, {"iss": client_id, "tenant_id": r.t}, typ=dom.ASSERTION_TYP, extra_header={"jwk": signer.public_jwk()})
    assert post(embedded).status_code == 401

    # a refused assertion's id is burned too: every id seen so far is in the table, once
    ids = r.w.owner("SELECT count(*), count(DISTINCT jti) FROM agent.api_assertion_jti WHERE tenant_id = :t")[0]
    assert ids[0] == ids[1] and ids[0] >= 6

    # the tool cap still applies to what this token can reach, and the gateway needs no new rule: nothing in the token widens it
    assert r.w.owner("SELECT count(*) FROM agent.workload_binding WHERE tenant_id = :t")[0][0] == 0

    # evidence of the issue and of the refusals
    kinds = {x[0] for x in r.w.owner("SELECT DISTINCT action FROM audit.audit_event WHERE tenant_id = :t AND action LIKE 'agent_registry.api_credential_%'")}
    assert kinds == {"agent_registry.api_credential_issued", "agent_registry.api_credential_refused"}

    # a revoked binding stops the credential at once, and a suspended agent with it
    r.run(lambda c: svc.revoke_api_binding(c, r.t, agent_id, reason="KEY_LEAKED", actor=w.human))
    stopped = post(assertion(signer, client_id, r.t))
    assert stopped.status_code == 403
    assert {"AGENT_NOT_ACTIVE", "API_BINDING_INACTIVE"} & {stopped.json()["error"]["code"]}
    assert r.status(agent_id) == "SUSPENDED"  # nothing left ACTIVE for the lifecycle sweep to find
