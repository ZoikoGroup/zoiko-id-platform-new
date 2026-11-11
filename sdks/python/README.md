# zoiko-tool-server

A small Python SDK for a tool server that accepts the Zoiko iD action grant. One runtime dependency: `cryptography`.

```python
from zoiko_tool_server import GrantError, ToolServer, fetch_jwks

tool = ToolServer(base_url=..., access_token=..., jwks=fetch_jwks(JWKS_URL), audience=MY_PRINCIPAL_ID, tenant_id=TENANT)
try:
    claims = tool.accept(request.headers.get("Authorization"), arguments)  # "ActionGrant <jwt>"
except GrantError as e:
    refuse(e.code)
else:
    run_tool(arguments)  # only now
```

`accept` verifies the grant offline, then redeems it once at the platform. See `examples/invoice_tool_server.py` and
`docs/sdk/tool-server-sdk.md`. Tests: `pytest sdks/python` (no platform needed).

## zoiko-credentials (credential trust client and consumer packs)

`zoiko_credentials` is the client for the credential-trust API (claim checks, eligibility and engagement decisions, consented
disclosure, appeals) plus thin packs for Zoikorum and Zoiko Rooms. The packs only narrow what the client does; the server enforces
every rule. A Zoikorum decision is signed, verified offline, and refused by the Rooms pack.

```python
from zoiko_credentials import CredentialsClient, ZoikorumPack

client = CredentialsClient("https://id.example", ACCESS_TOKEN)
zoikorum = ZoikorumPack(client, jwks=PINNED_JWKS)
decision = zoikorum.decide("US", "LAW", "legal.advice", subdivision="CA")
if decision.ready:   # READY only; a tier or a profile badge never authorizes work
    start_engagement(decision.token)
```

Nothing here is live until an operator adds real sources, packs and policies; until then every check is refused in a deployed
environment. Tests: `pytest sdks/python` (no platform needed).
