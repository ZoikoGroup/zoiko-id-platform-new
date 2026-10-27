"""Hybrid TLS enforcement and downgrade telemetry at the application edge (ZID-CRYPTO-001 §11, §12.1).

The terminator forwards the key-exchange group it negotiated in `ZID_TLS_GROUP_HEADER` (default
`x-tls-group`) and must strip that header from inbound requests. For paths under
`ZID_TLS_HYBRID_REQUIRED_PREFIXES` the policy is HYBRID_REQUIRED: a connection that did not negotiate a
registered hybrid group is refused with 426, never served. Everywhere else the registry's mode applies
(HYBRID_PREFERRED): a classical fallback is served, counted by endpoint and client agent, and logged with
its endpoint so it can be attributed. Without required prefixes and without the header nothing changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from zid_crypto.tls_policy import GroupDecision, default_mode, evaluate_group
from zid_telemetry import get_logger
from zid_telemetry.metrics import REGISTRY

from .settings import Settings

FALLBACKS = REGISTRY.counter(
    "zid_tls_hybrid_fallback_total",
    "Connections that used a classical key exchange where hybrid was preferred",
    ("endpoint", "client_agent"),
)
REFUSED = REGISTRY.counter(
    "zid_tls_hybrid_refused_total",
    "Connections refused for lacking the required hybrid key exchange",
    ("endpoint",),
)
log = get_logger("zid.tls")


def endpoint_label(path: str) -> str:
    """The first path segment: a bounded label, never an identifier from the URL."""
    first = path.strip("/").split("/", 1)[0]
    return first[:32] or "root"


def client_agent(user_agent: str | None) -> str:
    """The product token of the User-Agent (`curl/8.5` -> `curl`): attribution without an identifier label."""
    token = (user_agent or "").split("/", 1)[0].split(" ", 1)[0].strip().lower()
    return token[:32] or "unknown"


def judge(
    path: str, group: str | None, required_prefixes: tuple[str, ...], preferred: str
) -> GroupDecision:
    required = bool(required_prefixes) and path.startswith(required_prefixes)
    return evaluate_group(group, mode="HYBRID_REQUIRED" if required else preferred)


def install_tls_group_guard(app: FastAPI, settings: Settings) -> None:
    preferred = default_mode()
    required = tuple(settings.tls_hybrid_required_prefixes)
    header = settings.tls_group_header.lower()

    @app.middleware("http")
    async def tls_group_guard(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        group = request.headers.get(header)
        decision = judge(request.url.path, group, required, preferred)
        endpoint = endpoint_label(request.url.path)
        if not decision.allowed:
            REFUSED.inc(endpoint=endpoint)
            return JSONResponse(
                {
                    "error": {
                        "code": decision.code,
                        "message": "This endpoint needs a hybrid post-quantum TLS key exchange.",
                    }
                },
                status_code=426,
                headers={"Upgrade": "TLS/1.3"},
            )
        if decision.fallback:
            agent = client_agent(request.headers.get("user-agent"))
            FALLBACKS.inc(endpoint=endpoint, client_agent=agent)
            log.warning(
                "classical TLS key exchange where hybrid is preferred",
                extra={"endpoint": endpoint, "client_agent": agent, "tls_group": group},
            )
        return await call_next(request)
