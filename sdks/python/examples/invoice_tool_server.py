"""A tiny tool server: POST /invoices/send with a JSON body, guarded by an action grant.

The grant travels in `Authorization: ActionGrant <jwt>`. The server digests the body it is about to execute, asks the
SDK to accept the grant (verify offline, then redeem once at the platform), and only then acts. Any failure refuses
with an error body and nothing happens.

Run (values come from your tool registration):
    ZID_BASE_URL=https://zid.example ZID_TOOL_TOKEN=... ZID_AUDIENCE=<server principal id> ZID_TENANT=<tenant id> \
    ZID_JWKS_URL=https://zid.example/.well-known/jwks.json python invoice_tool_server.py
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from zoiko_tool_server import GrantError, ToolServer, fetch_jwks

MAX_BODY = 64 * 1024


def make_handler(tool: ToolServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/invoices/send":
                return self._reply(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._reply(413, {"error": "too large"})
            try:
                arguments = json.loads(self.rfile.read(length))
                if not isinstance(arguments, dict):
                    raise TypeError("body must be an object")
            except (ValueError, TypeError):
                return self._reply(400, {"error": "invalid body"})
            try:
                # Digest and execute the same parsed object: what was approved is what runs.
                claims = tool.accept(self.headers.get("Authorization"), arguments)
            except GrantError as e:
                return self._reply(403 if e.code != "TOOL_GRANT_USED" else 409, {"error": e.code})
            # Only now is it safe to act. This example just reports what it would do.
            self._reply(
                200,
                {
                    "sent": arguments.get("invoice_id"),
                    "to": arguments.get("recipient"),
                    "on_behalf_of": claims.get("on_behalf_of"),
                    "decision_id": claims.get("decision_id"),
                },
            )

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def main() -> None:
    tool = ToolServer(
        base_url=os.environ["ZID_BASE_URL"],
        access_token=os.environ["ZID_TOOL_TOKEN"],
        jwks=fetch_jwks(os.environ["ZID_JWKS_URL"]),
        audience=os.environ["ZID_AUDIENCE"],
        tenant_id=os.environ["ZID_TENANT"],
    )
    HTTPServer(("127.0.0.1", 8081), make_handler(tool)).serve_forever()


if __name__ == "__main__":
    main()
