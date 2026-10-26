"""Development identity-verification provider simulator (ZID-VERIFY-001 §9, §22, §30 VERIFY-W2).

Local/test only (ZID_DEV_SIMULATORS; the app refuses to start with it elsewhere). It plays an
external provider through the real adapter contract, so nothing downstream is special-cased:
- the cell submits a session to the simulator's submit endpoint exactly as to a vendor (only the
  opaque session reference and the required claims leave Zoiko) and gets a transaction reference;
- the applicant "completes" the check on the simulator's page; the simulator then posts a signed
  `verification-result+jwt` to the cell's public provider-callback endpoint, where signature,
  mapping-version, deduplication, state-machine and decision rules apply as for any provider.
It holds no state of its own: transaction references are derived from the session reference.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from zid_crypto import LocalSigner, sign_jws

SIM_HOST = "verification-simulator.zoiko.local"
SIM_SUBMIT_URL = f"https://{SIM_HOST}/sessions"
SIM_ADAPTER_ID = "zid-simulator"
SIM_PROVIDER = "Zoiko development simulator"
SIM_MAPPING_VERSION = "sim-1"
SIM_GROUP = "dev-simulator"
SIM_EVIDENCE = ("GOVERNMENT_ID", "DIGITAL_CREDENTIAL", "BIOMETRIC_BINDING")
INTERNAL_SUBMIT_PATH = "/dev/verification-simulator/provider/sessions"


class ProviderSimulator:
    """The simulator's signing identity (one per process; the adapter's pinned key is refreshed
    from it before each simulated callback, so restarts never strand a check)."""

    def __init__(self) -> None:
        self.signer = LocalSigner(f"zid-sim-{uuid.uuid4().hex[:8]}")

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.signer.public_jwk()]}

    @staticmethod
    def transaction_ref(session_ref: str) -> str:
        return f"sim-{session_ref}"

    def result(
        self,
        *,
        transaction_ref: str,
        outcome: str,
        claims: Mapping[str, str],
        required: Sequence[str],
    ) -> bytes:
        """A signed provider result. `outcome`: VERIFIED | FAILED | FRAUD (fraud never passes:
        it fails or goes to manual review, as the profile says)."""
        verified = outcome != "FAILED"
        body: dict[str, Any] = {
            "status": "COMPLETED" if verified else "FAILED",
            "binding": "BIOMETRIC",
            "liveness_passed": True,
            "fraud_signals": ["DOCUMENT_TAMPERING"] if outcome == "FRAUD" else [],
            "claims": [
                {
                    "claim": c,
                    "verified": verified and bool(claims.get(c)),
                    "value": claims.get(c, ""),
                    "method": "document+biometric" if c == "legal_name" else "document",
                    "evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"]
                    if c == "legal_name"
                    else ["GOVERNMENT_ID"],
                }
                for c in required
            ],
            "evidence_commitment": f"sim-evidence-{uuid.uuid4().hex[:12]}",
        }
        return sign_jws(
            self.signer,
            {
                "iss": SIM_ADAPTER_ID,
                "mapping_version": SIM_MAPPING_VERSION,
                "event_id": f"sim-evt-{uuid.uuid4().hex}",
                "transaction_ref": transaction_ref,
                "iat": int(time.time()),
                "result": body,
            },
            typ="verification-result+jwt",
        ).encode()


class SimulatorTransport(httpx.AsyncBaseTransport):
    """Outbound provider HTTP: the simulator host goes to this app in-process; everything else
    to the real network."""

    def __init__(self, app: Any) -> None:
        self._local = httpx.ASGITransport(app=app)
        self._net = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == SIM_HOST:
            request.url = request.url.copy_with(
                scheme="http", host="internal", path=INTERNAL_SUBMIT_PATH
            )
            return await self._local.handle_async_request(request)
        return await self._net.handle_async_request(request)

    async def aclose(self) -> None:
        await self._net.aclose()
