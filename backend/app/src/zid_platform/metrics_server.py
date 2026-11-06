"""A scrape endpoint for the background processes (worker, relay, consumer).

The cell and global APIs expose `/internal/metrics`; the worker has no HTTP server, so the counters and gauges it keeps (evidence integrity
checks, sealing lag, unanchored checkpoints) would otherwise never be seen. Set `ZID_METRICS_PORT` and the process answers
`GET /metrics` in the Prometheus text format on `ZID_METRICS_BIND` (default 127.0.0.1; use 0.0.0.0 inside a container network and never publish it
beyond the private network). Off unless the port is set.
"""

from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zid_telemetry.metrics import REGISTRY

log = logging.getLogger("zid.metrics")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/metrics":
            self.send_error(404)
            return
        body = REGISTRY.exposition().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_metrics_server(port: int, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), _Handler)
    threading.Thread(target=server.serve_forever, name="metrics", daemon=True).start()
    log.info("metrics endpoint listening", extra={"bind": bind, "port": port})
    return server


def start_from_environment() -> ThreadingHTTPServer | None:
    raw = os.environ.get("ZID_METRICS_PORT")
    if not raw:
        return None
    return start_metrics_server(int(raw), os.environ.get("ZID_METRICS_BIND", "127.0.0.1"))
