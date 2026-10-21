"""Workload identity wiring (DEC-F7): the cell's SPIFFE trust policy and the peer dependency.

    policy = trust_policy_from_settings(settings)          # app.state.trust_policy
    peer: SpiffeId = Depends(workload_peer)                # mesh-verified, never a header/body

`workload_peer` reads the verified peer of the connection (direct mTLS, or the local mesh proxy's
forwarded certificate when the TCP peer is a configured mesh proxy). Mapping it to a principal is
`machine.ports.resolve_workload`; an SVID alone authorizes nothing.
"""

from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi import Request
from zid_context.spiffe import SpiffeError, SpiffeId, TrustPolicy, peer_spiffe_id
from zid_errors import ZidError

from zid_platform.settings import Settings


def _bundle(path: str) -> list[bytes]:
    pem = Path(path).read_bytes()
    return [c.public_bytes(Encoding.DER) for c in x509.load_pem_x509_certificates(pem)]


def trust_policy_from_settings(settings: Settings) -> TrustPolicy:
    own = settings.spiffe_trust_domain or f"{settings.cell_id}.{settings.environment}.id.zoiko"
    return TrustPolicy(
        own_domain=own,
        bundles={domain: _bundle(path) for domain, path in settings.spiffe_bundle_files.items()},
        federated=frozenset(settings.spiffe_federated_domains),
        trusted_proxies=frozenset(settings.mesh_trusted_proxies),
    )


async def workload_peer(request: Request) -> SpiffeId:
    policy: TrustPolicy | None = getattr(request.app.state, "trust_policy", None)
    if policy is None:
        raise ZidError("WORKLOAD_IDENTITY_UNAVAILABLE", "Workload identity is not configured.", http_status=503)
    try:
        return peer_spiffe_id(request.scope, request.headers, policy)
    except SpiffeError as e:
        raise ZidError(e.code, "A mesh-verified workload identity is required.", http_status=401) from e


__all__ = ["trust_policy_from_settings", "workload_peer"]
