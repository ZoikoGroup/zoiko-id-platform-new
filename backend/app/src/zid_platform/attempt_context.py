"""What a request tells us about where an attempt came from, for the fraud and velocity signals (CR-068; ZID-GAP-001 WP-14).

The network is the client address the platform already trusts (uvicorn applies `ZID_FORWARDED_ALLOW_IPS`, so behind the edge it is the real
client, not the proxy). The device is an opaque id the web app keeps for the browser (header `X-Zid-Device`); a client can change or omit it, so
it is advisory: the contact and network keys still count. Nothing here is stored raw; `screening.note_attempt` hashes each value.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

DEVICE_HEADER = "x-zid-device"


def attempt_inputs(request: Request) -> dict[str, Any]:
    """Keyword arguments for `note_attempt`: the client network, the device id if the client sent one, and the cell's hash pepper."""
    device = request.headers.get(DEVICE_HEADER)
    return {
        "network": request.client.host if request.client else None,
        "device": device[:128] if device else None,
        "pepper": getattr(request.app.state, "screening_signal_pepper", b""),
        "mode": getattr(request.app.state, "screening_velocity_mode", "shadow"),
    }
