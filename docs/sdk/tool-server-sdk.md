# Tool server SDK (Python)

`sdks/python` (`zoiko-tool-server`) is for a service that hosts a tool an AI agent calls. The platform issues a
short-lived, single-use action grant per call; this SDK is how the tool server accepts it.

## Three steps, in this order

1. Verify offline. `verify_grant(token, jwks, audience=..., tenant_id=..., arguments_digest=...)` checks the
   signature (ES256 or Ed25519 only, `typ` must be `action-grant+jwt`, key chosen by `kid`) and the claims.
2. Redeem once. `redeem(base_url, access_token, grant, digest)` posts to `/v1/machine/tool-grants:redeem` with the
   tool server's own token. The platform marks the grant used and re-checks the delegation chain. Verification alone
   does not make a grant single-use; redeem does.
3. Act. Only after both succeeded.

`ToolServer.accept(authorization_header, arguments)` does 1 and 2 and raises `GrantError` otherwise. The header is
`Authorization: ActionGrant <jwt>`. Compute the digest from the same parsed object you then execute (`args_digest`
uses canonical JSON: sorted keys, no spaces, UTF-8, SHA-256), so the approved payload and the executed payload are one.
`audience` is the tool server's own principal id, as registered for the tool.

See `sdks/python/examples/invoice_tool_server.py` for a complete stdlib server.

## Failure codes (`GrantError.code`; `.codes` lists all reasons found offline)

| Code | Meaning |
| --- | --- |
| `TOOL_GRANT_MISSING` | No `ActionGrant` header. |
| `TOOL_GRANT_INVALID` | Not a valid token: bad signature, wrong `typ`, `alg` not allowed, unknown `kid`, malformed. |
| `TOOL_GRANT_AUDIENCE` | Issued for another tool server. |
| `TOOL_GRANT_TENANT` | Issued for another tenant. |
| `TOOL_GRANT_ARGUMENTS` | The arguments you would run differ from those approved. |
| `TOOL_GRANT_EXPIRED` | Past `exp` (grants live about two minutes). |
| `TOOL_GRANT_NOT_YET_VALID` | Before `nbf`. |
| `TOOL_GRANT_MALFORMED` | Missing `jti` or `agent_chain`. |
| `TOOL_GRANT_USED` | Already redeemed (HTTP 409): a replay. |
| `TOOL_GRANT_REFUSED` or the platform's own code | Redeem refused (HTTP 403 or other). |
| `TOOL_GRANT_REDEEM_FAILED` | Platform unreachable or unreadable. Fail closed: do not act. |

## What the SDK does not do

- It does not rotate JWKS. `fetch_jwks(url)` fetches once; cache it, and refetch on an unknown `kid` or on a schedule.
- It does not keep nonces. The platform's redeem is the single-use authority; a local cache would add a second,
  weaker one.
- It does not retry redeem. A timeout leaves the grant in an unknown state; ask the agent for a new grant.
- It does not decide policy. A valid grant says the platform allowed this one call; your tool's own business rules
  still apply.
- It does not check clock skew beyond the grant's own `nbf` and `exp`; keep the server clock in sync.
