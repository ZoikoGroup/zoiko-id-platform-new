"""The agent tool gateway refuses what it should and says where to learn why (ZID-CP-WF10 v2.0, WF-10.11; CR-173; RFC 9728).

What these hold: a refused call to the gateway points at the protected-resource metadata; a token minted for another audience is refused; a token for another issuer is refused; an
unsigned or altered token is refused; and the metadata document names the same resource the gateway serves.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wf07_fixture import AUD, ISS, W
from zid_platform.modules.session.ports import issue_access_token

MCP = "/v1/machine/mcp"
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_a_refused_gateway_call_points_at_the_resource_metadata_and_a_token_for_another_audience_or_issuer_is_refused(
    rt_url: str, owner_url: str
) -> None:
    w = W(rt_url, owner_url)
    with w.client() as http:
        bare = http.post(MCP, json=PING)
        assert bare.status_code == 401
        challenge = bare.headers["www-authenticate"]
        assert (
            challenge.startswith("Bearer")
            and 'resource_metadata="' in challenge
            and challenge.endswith('/.well-known/oauth-protected-resource/v1/machine/mcp"')
        )
        meta = http.get("/.well-known/oauth-protected-resource/v1/machine/mcp").json()
        assert meta["resource"].endswith(MCP) and challenge.split('resource_metadata="')[1].rstrip(
            '"'
        ).endswith("/.well-known/oauth-protected-resource/v1/machine/mcp")

        sub = w.root
        good = http.post(MCP, headers=w.bearer(sub), json=PING)
        assert good.status_code == 200, good.text  # the same token shape works when it is right

        def forged(**over: object) -> dict[str, str]:
            args: dict[str, object] = {
                "issuer": ISS,
                "audience": AUD,
                "subject": sub,
                "tenant_id": w.tenant,
                "client_id": "agent-runtime",
                "now": datetime.now(UTC),
                "session_id": w.sessions[sub],
                "scope": frozenset({"workload"}),
            }
            tok = issue_access_token(w.ring, **{**args, **over})  # type: ignore[arg-type]
            return {"Authorization": f"Bearer {tok}"}

        wrong_audience = http.post(
            MCP, headers=forged(audience="https://another-resource.example"), json=PING
        )
        assert (
            wrong_audience.status_code == 401
            and 'resource_metadata="' in wrong_audience.headers["www-authenticate"]
        )
        assert (
            http.post(
                MCP, headers=forged(issuer="https://someone-else.example"), json=PING
            ).status_code
            == 401
        )
        tampered = good.request.headers["authorization"][:-4] + "AAAA"
        assert http.post(MCP, headers={"Authorization": tampered}, json=PING).status_code == 401
        assert (
            http.post(MCP, headers={"Authorization": "Bearer not-a-token"}, json=PING).status_code
            == 401
        )
        other_tenant = http.post(
            MCP,
            headers=forged(tenant_id="00000000-0000-0000-0000-000000000001"),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert (
            other_tenant.status_code in (401, 403)
            or "tools" in other_tenant.text
            and w.child not in other_tenant.text
        )  # a token for another tenant never lists this tenant's tools
