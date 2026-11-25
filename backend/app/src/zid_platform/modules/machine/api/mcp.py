"""A thin MCP-style front for the tool gateway (ZID-CP-WF07 v2.0 A07-10; D07; CR-169).

    POST /v1/machine/mcp     JSON-RPC 2.0: initialize, ping, tools/list, tools/call

This decides NOTHING. A `tools/call` is turned into the same request the native route makes and goes through the same gateway, so the chain, the sponsor, the tool registry, the mandate, the policy decision, the bound human
approval, the freeze and the one-use grant are exactly what they are for a native call. There is no second path: MCP and the native API cannot give different answers, and `tools/list` is only a convenience (it lists what the agent's
chain names and the registry has ACTIVE; a tool being listed grants nothing).

Authentication is the agent's own workload token, as for the native route; the agent is never a field of the request. Written against the MCP authorization revision named in the wireframe (2026-07-28) and tested with a scripted client,
not a real MCP host: it is not claimed to interoperate with any particular client. Resources, prompts and sampling are not offered. A refusal comes back as a tool error that names the reasons; it is never dressed as success.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.decision.ports import AccessQuery, evaluate_access
from zid_platform.modules.machine.application import tool_gateway as gateway
from zid_platform.modules.machine.domain.agents import MachineError, ToolStatus
from zid_platform.modules.machine.domain.tool_gateway import ARGS_DIGEST, args_digest
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.evidence.ports import sign_evidence
from zid_platform.protected import catalog

router = APIRouter(prefix="/v1/machine", tags=["agent-tool-gateway"])
metadata_router = APIRouter(tags=["agent-tool-gateway"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
PROTOCOL = "2026-07-28"
SERVER = {"name": "zoiko-id-tool-gateway", "version": "1"}
OPTIONAL_CALL_FIELDS = ("represented_principal_id", "purpose", "mandate_id", "approval_request_id", "reservation_id")
INPUT_SCHEMA = {
    "type": "object",
    "required": ["resource_type", "resource_id", "arguments"],
    "properties": {
        "resource_type": {"type": "string"}, "resource_id": {"type": "string"}, "arguments": {"type": "object", "description": "the exact arguments the tool will run; the grant is bound to their digest"},
        "represented_principal_id": {"type": "string"}, "purpose": {"type": "string"}, "mandate_id": {"type": "string"}, "approval_request_id": {"type": "string"}, "reservation_id": {"type": "string"},
        "value": {"type": "object", "properties": {"amount": {"type": "string"}, "currency": {"type": "string"}}},
    },
    "additionalProperties": False,
}  # fmt: skip


class McpRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    jsonrpc: str | None = None
    id: int | str | None = None
    method: str | None = None
    params: dict[str, Any] | None = None


def _error(rid: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _result(rid: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result}, headers={"Cache-Control": "no-store"})


def _tool_error(rid: Any, codes: list[str], extra: dict[str, Any] | None = None) -> JSONResponse:
    body = {"decision": "DENY", "reason_codes": codes, **(extra or {})}
    return _result(rid, {"isError": True, "content": [{"type": "text", "text": "This tool call is not allowed: " + ", ".join(codes)}], "structuredContent": body})


async def _list_tools(request: Request, caller: Caller) -> list[dict[str, Any]]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        chain = await gateway.load_chain(conn, caller.ctx.principal_id or "")
        registry = await machine_store.load_tools(conn)
    listed = set.intersection(*(set(a.allowed_tools) for a in chain)) if chain else set()
    return [
        {
            "name": t.tool_id, "description": f"Gateway-checked tool for {t.canonical_action}. Listing it grants nothing: every call is decided at the gateway.", "inputSchema": INPUT_SCHEMA,
            "annotations": {"sideEffect": t.side_effect.value, "requiresHumanApproval": t.requires_human_approval, "requiresMandate": t.requires_mandate},
        }
        for t in sorted(registry.values(), key=lambda x: x.tool_id)
        if t.tool_id in listed and t.status is ToolStatus.ACTIVE
    ]  # fmt: skip


def _call_from(agent_id: str, params: dict[str, Any]) -> gateway.ToolCall | str:
    """The native call this MCP call means, or a plain reason it cannot be made."""
    name, args = params.get("name"), params.get("arguments")
    if not isinstance(name, str) or not isinstance(args, dict):
        return "a tool call names the tool and gives its arguments"
    inner = args.get("arguments")
    if not isinstance(args.get("resource_type"), str) or not isinstance(args.get("resource_id"), str) or not isinstance(inner, dict):
        return "give resource_type, resource_id and arguments"
    if set(args) - set(INPUT_SCHEMA["properties"]):  # type: ignore[arg-type]
        return "unknown argument: only the documented fields are accepted (the agent is never one of them)"
    value = args.get("value")
    if value is not None and not (isinstance(value, dict) and isinstance(value.get("amount"), str) and isinstance(value.get("currency"), str) and set(value) <= {"amount", "currency"}):
        return "value is an amount as text and a currency"
    digest = args_digest(inner)
    assert ARGS_DIGEST.match(digest)
    extra = {k: args[k] for k in OPTIONAL_CALL_FIELDS if isinstance(args.get(k), str)}
    return gateway.ToolCall(
        agent_id=agent_id, tool_id=name, resource_type=args["resource_type"], resource_id=args["resource_id"], arguments_digest=digest,
        value=(value["amount"], value["currency"]) if value else None, **extra,
    )  # fmt: skip


@router.post("/mcp")
async def mcp(body: McpRequest, request: Request, caller: CallerDep) -> Response:
    rid = body.id
    if body.jsonrpc != "2.0" or not isinstance(body.method, str):
        return _error(rid, -32600, "not a JSON-RPC 2.0 request")
    if "workload" not in caller.scope:
        raise ZidError("TOOL_CALL_FORBIDDEN", "Only an agent's own workload credential may use the tool gateway.", http_status=403)
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    if body.id is None and body.method.startswith("notifications/"):
        return Response(status_code=202)  # a notification has no answer
    method = body.method
    if method == "initialize":
        return _result(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER, "instructions": "Every tool call is decided at the Zoiko iD gateway. Listing a tool grants nothing."})
    if method == "ping":
        return _result(rid, {})
    try:
        if method == "tools/list":
            return _result(rid, {"tools": await _list_tools(request, caller)})
        if method != "tools/call":
            return _error(rid, -32601, "method not offered by this server")
        agent_id, tenant, engine = caller.ctx.principal_id or "", caller.ctx.tenant_id or "", request_engine(request)
        call = _call_from(agent_id, body.params or {})
        if isinstance(call, str):
            return _error(rid, -32602, call)

        async def decide(q: AccessQuery) -> dict[str, Any]:
            return await evaluate_access(engine, caller, q, catalog=catalog())

        async with tenant_transaction(engine, caller.ctx) as conn:
            result = await gateway.authorize_tool_call(
                conn, tenant, call, decide=decide, sign=lambda claims, typ: settings.keyring.sign(claims, typ=typ), issuer=settings.issuer, action_catalogued=lambda action: action in catalog(), receipt_sign=sign_evidence,
            )  # fmt: skip
    except MachineError as e:
        return _tool_error(rid, [e.code])
    if not result.allowed:
        return _tool_error(rid, list(result.codes), {"approval_context": result.approval_context} if result.approval_context else None)
    assert result.grant is not None and result.expires_at is not None
    return _result(
        rid,
        {
            "isError": False,
            "content": [{"type": "text", "text": "Authorized for the tool's own server. This is a grant, not the tool's result: the server redeems it, acts, and reports what happened."}],
            "structuredContent": {"decision": "ALLOW", "grant": result.grant, "expires_at": result.expires_at.isoformat(), "decision_id": result.decision_id, "agent_chain": list(result.chain)},
        },
    )


@metadata_router.get("/.well-known/oauth-protected-resource/v1/machine/mcp")
async def protected_resource_metadata(request: Request) -> JSONResponse:
    """OAuth 2.0 Protected Resource Metadata (RFC 9728) for the agent tool gateway: which resource this is, who issues the tokens it accepts, and how a token is presented. Public: it describes
    the interface and holds no data. The token must be the agent's own workload token, issued for this platform's audience; a token for another resource or tenant is refused by the gateway."""
    settings = getattr(request.app.state, "token_settings", None)
    base = str(getattr(settings, "public_base_url", "") or str(request.base_url)).rstrip("/")
    issuer = str(getattr(settings, "issuer", "")) if settings is not None else ""
    doc: dict[str, Any] = {
        "resource": f"{base}/v1/machine/mcp",
        "authorization_servers": [issuer] if issuer else [],
        "bearer_methods_supported": ["header"],
        "resource_name": "Zoiko iD agent tool gateway",
        "resource_documentation": f"{base}/docs/verify-api",
        "mcp_protocol_version": PROTOCOL,
        "notes": "Every tool call is decided at the gateway against the agent's authority chain, tool registry, mandate and policy. A token alone grants nothing; a listed tool grants nothing.",
    }
    return JSONResponse(doc, headers={"Cache-Control": "public, max-age=300"})
