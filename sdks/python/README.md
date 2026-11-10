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
