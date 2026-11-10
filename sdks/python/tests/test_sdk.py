from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import AUD, NOW, TENANT, Keys, b64, claims_for

from zoiko_tool_server import GrantError, ToolServer, args_digest, redeem, verify_grant

ARGS = {"invoice_id": "42", "recipient": "a@example.com", "amount": "10.00"}
DIGEST = args_digest(ARGS)


def verify(token: str, keys: Keys, **over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = {"audience": AUD, "tenant_id": TENANT, "arguments_digest": DIGEST, "now": NOW}
    kw.update(over)
    return verify_grant(token, keys.jwks(), **kw)


def code_of(token: str, keys: Keys, **over: Any) -> str:
    with pytest.raises(GrantError) as e:
        verify(token, keys, **over)
    return e.value.code


def test_good_grant(keys: Keys) -> None:
    claims = verify(keys.sign(claims_for(DIGEST)), keys)
    assert claims["jti"] == "nonce-1" and claims["on_behalf_of"] == "person-1"


def test_wrong_audience(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST)), keys, audience="other") == "TOOL_GRANT_AUDIENCE"


def test_wrong_tenant(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST)), keys, tenant_id="t2") == "TOOL_GRANT_TENANT"


def test_wrong_arguments(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST)), keys, arguments_digest=args_digest({"x": 1})) == "TOOL_GRANT_ARGUMENTS"


def test_expired_and_not_yet_valid(keys: Keys) -> None:
    token = keys.sign(claims_for(DIGEST))
    assert code_of(token, keys, now=NOW + timedelta(seconds=120)) == "TOOL_GRANT_EXPIRED"
    assert code_of(token, keys, now=NOW - timedelta(seconds=5)) == "TOOL_GRANT_NOT_YET_VALID"


def test_missing_jti_or_chain(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST, jti=None)), keys) == "TOOL_GRANT_MALFORMED"
    assert code_of(keys.sign(claims_for(DIGEST, agent_chain=None)), keys) == "TOOL_GRANT_MALFORMED"


def test_several_problems_are_all_reported(keys: Keys) -> None:
    with pytest.raises(GrantError) as e:
        verify(keys.sign(claims_for(DIGEST)), keys, audience="x", tenant_id="y")
    assert e.value.codes == ["TOOL_GRANT_AUDIENCE", "TOOL_GRANT_TENANT"]


def test_tampered_payload(keys: Keys) -> None:
    head, _, sig = keys.sign(claims_for(DIGEST)).split(".")
    forged = b64(json.dumps(claims_for(DIGEST, aud="evil")).encode())
    assert code_of(f"{head}.{forged}.{sig}", keys, audience="evil") == "TOOL_GRANT_INVALID"


def test_forged_signature(keys: Keys) -> None:
    attacker = Keys(kid=keys.kid)  # same kid, different key
    assert code_of(attacker.sign(claims_for(DIGEST)), keys) == "TOOL_GRANT_INVALID"


def test_wrong_typ(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST), typ="at+jwt"), keys) == "TOOL_GRANT_INVALID"


def test_alg_none_and_unknown_alg(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST), alg="none"), keys) == "TOOL_GRANT_INVALID"
    assert code_of(keys.sign(claims_for(DIGEST), alg="HS256"), keys) == "TOOL_GRANT_INVALID"


def test_unknown_kid(keys: Keys) -> None:
    assert code_of(keys.sign(claims_for(DIGEST), kid="nope"), keys) == "TOOL_GRANT_INVALID"


def test_garbage_token(keys: Keys) -> None:
    for junk in ("", "a.b", "a.b.c", "....", None):
        assert code_of(junk, keys) == "TOOL_GRANT_INVALID"  # type: ignore[arg-type]


def test_args_digest_matches_platform() -> None:
    root = Path(__file__).resolve().parents[3] / "backend" / "app" / "src"
    sys.path.insert(0, str(root))
    try:
        from zid_platform.modules.machine.domain.tool_gateway import args_digest as server_digest
    except ImportError:
        pytest.skip("backend not importable")
    finally:
        sys.path.remove(str(root))
    for sample in ({}, {"b": 1, "a": [1, 2, {"z": None}]}, {"name": "Zoë ☃", "n": 1.5}, ARGS):
        assert args_digest(sample) == server_digest(sample)


def test_redeem_200(stub: Any) -> None:
    base = stub(200, {"tool_id": "invoice.send"})
    assert redeem(base, "tok", "g.r.ant", DIGEST) == {"tool_id": "invoice.send"}
    path, auth, body = stub.state["seen"][0]
    assert path == "/v1/machine/tool-grants:redeem" and auth == "Bearer tok"
    assert body == {"grant": "g.r.ant", "arguments_digest": DIGEST}


def test_redeem_409_used(stub: Any) -> None:
    base = stub(409, {"error": {"code": "TOOL_GRANT_USED"}})
    with pytest.raises(GrantError) as e:
        redeem(base, "tok", "g", DIGEST)
    assert e.value.code == "TOOL_GRANT_USED"


def test_redeem_403_audience(stub: Any) -> None:
    base = stub(403, {"error": {"code": "TOOL_GRANT_AUDIENCE", "details": []}})
    with pytest.raises(GrantError) as e:
        redeem(base, "tok", "g", DIGEST)
    assert e.value.code == "TOOL_GRANT_AUDIENCE"


def test_redeem_unreachable_fails_closed() -> None:
    with pytest.raises(GrantError) as e:
        redeem("http://127.0.0.1:1", "tok", "g", DIGEST, timeout=1)
    assert e.value.code == "TOOL_GRANT_REDEEM_FAILED"


def make_server(keys: Keys, base: str) -> ToolServer:
    return ToolServer(base_url=base, access_token="tok", jwks=keys.jwks(), audience=AUD, tenant_id=TENANT)


def test_accept_does_both(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(200, {}))
    token = keys.sign(claims_for(DIGEST, exp=int(NOW.timestamp()) + 10**9))
    assert tool.accept(f"ActionGrant {token}", ARGS)["decision_id"] == "dec-1"
    assert len(stub.state["seen"]) == 1


def test_accept_does_not_redeem_a_bad_grant(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(200, {}))
    token = keys.sign(claims_for(DIGEST, aud="other", exp=int(NOW.timestamp()) + 10**9))
    with pytest.raises(GrantError):
        tool.accept(f"ActionGrant {token}", ARGS)
    assert stub.state["seen"] == []  # nothing spent on a grant that failed offline


def test_accept_header_forms(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(200, {}))
    for header in (None, "", "Bearer x.y.z", "ActionGrant", "ActionGrant   "):
        with pytest.raises(GrantError) as e:
            tool.accept(header, ARGS)
        assert e.value.code == "TOOL_GRANT_MISSING"


def test_accept_replay_refused(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(409, {"error": {"code": "TOOL_GRANT_USED"}}))
    token = keys.sign(claims_for(DIGEST, exp=int(NOW.timestamp()) + 10**9))
    with pytest.raises(GrantError) as e:
        tool.accept(f"ActionGrant {token}", ARGS)
    assert e.value.code == "TOOL_GRANT_USED"


# --- the example server, end to end against the stub platform ---------------------------------------------------


def call_example(tool: ToolServer, header: str | None, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from invoice_tool_server import make_handler

    server = HTTPServer(("127.0.0.1", 0), make_handler(tool))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"Content-Type": "application/json", **({"Authorization": header} if header else {})}
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/invoices/send", json.dumps(body).encode(), headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    finally:
        server.shutdown()
        server.server_close()


def future(keys: Keys, digest: str, **over: Any) -> str:
    return keys.sign(claims_for(digest, exp=int(NOW.timestamp()) + 10**9, **over))


def test_example_acts_only_after_accept(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(200, {}))
    status, body = call_example(tool, f"ActionGrant {future(keys, DIGEST)}", ARGS)
    assert (status, body["sent"], body["on_behalf_of"]) == (200, "42", "person-1")


def test_example_refuses_changed_body_without_redeeming(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(200, {}))
    status, body = call_example(tool, f"ActionGrant {future(keys, DIGEST)}", {**ARGS, "recipient": "evil@example.com"})
    assert (status, body) == (403, {"error": "TOOL_GRANT_ARGUMENTS"})
    assert stub.state["seen"] == []


def test_example_refuses_missing_header_and_replay(keys: Keys, stub: Any) -> None:
    tool = make_server(keys, stub(409, {"error": {"code": "TOOL_GRANT_USED"}}))
    assert call_example(tool, None, ARGS) == (403, {"error": "TOOL_GRANT_MISSING"})
    assert call_example(tool, f"ActionGrant {future(keys, DIGEST)}", ARGS) == (409, {"error": "TOOL_GRANT_USED"})
