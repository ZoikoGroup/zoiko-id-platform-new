"""Evidence runtime: the keys the evidence module uses (DEC-F1 §2, DEC-F2 §5, DEC-F5).

    configure_runtime(EvidenceRuntime(...))   # once at start-up (main.py / worker)
    rt = current_runtime()

- `mac_key`       HMAC_INTERNAL: pseudonymous handles (tenant- and domain-scoped)
- `provider`      KeyProvider for the cell SUBJECT_ERASURE_KEY (wraps per-subject keys) and
                  DATA_KEK (supporting evidence that is not about one person)
- `signer`        EVIDENCE_SIGNING: checkpoints, E3 entry signatures, export packages
- `trusted_keys`  every evidence public key still needed to verify (historic ones included);
                  verification never trusts the key stored next to the signature

Local/test processes that never configure one get development keys on first use; any other
environment refuses (no silent fallback to in-memory keys outside local/test).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from zid_crypto import LocalSigner, Signer
from zid_crypto.handles import MacKey
from zid_crypto.providers import KeyProvider, KeyRef, LocalKeyProvider
from zid_crypto.purposes import KeyPurpose

from zid_platform.modules.evidence.domain.key_compromise import check_replacement
from zid_platform.modules.evidence.domain.ledger import Cadence


@dataclass(frozen=True, slots=True)
class EvidenceRuntime:
    cell_id: str
    mac_key: MacKey
    provider: KeyProvider
    subject_kek: KeyRef
    data_kek: KeyRef
    signer: Signer
    historic_keys: tuple[Mapping[str, Any], ...] = ()
    cadence: Cadence = field(default_factory=Cadence)
    # evidence class -> retention days (EVIDENCE §22; the DPO's numbers; empty = keep)
    retention_days: Mapping[str, int] = field(default_factory=dict)
    # tenant id -> KeyRef of a DEDICATED_TENANT_KEY / BYOK / HYOK KEK (DEC-F5 §4); absent = platform
    tenant_keks: Mapping[str, KeyRef] = field(default_factory=dict)
    # kid -> when the key was believed to be in other hands; signatures from then on are not trusted (EVID-22)
    compromised: Mapping[str, datetime] = field(default_factory=dict)
    # checks a stored RFC 3161 timestamp against the TSA trust list (`tsa_token.TimestampVerifier`); None = not verified here
    timestamp_verifier: Any = None

    def after_compromise(self, new_signer: Signer, *, compromised_at: datetime) -> EvidenceRuntime:
        """The runtime once the current signer is declared compromised: the new signer takes over, the old public
        key stays published so earlier history verifies, and signatures it made from `compromised_at` do not."""
        old = self.signer.public_jwk()
        check_replacement(
            str(old["kid"]),
            str(new_signer.public_jwk()["kid"]),
            {str(k.get("kid")) for k in self.historic_keys},
        )
        return replace(
            self,
            signer=new_signer,
            historic_keys=(*self.historic_keys, old),
            compromised={**self.compromised, str(old["kid"]): compromised_at},
        )

    def kek_for(self, tenant_id: str) -> KeyRef:
        """The KEK that wraps this tenant's subject keys. A customer-managed key never falls back
        to the platform key (HYOK fails closed when the customer's key store is unavailable)."""
        return self.tenant_keks.get(tenant_id, self.subject_kek)

    def trusted_keys(self) -> list[dict[str, Any]]:
        """The active evidence key plus historic keys, by kid (DEC-F2 §5: never re-signed)."""
        keys = {str(k.get("kid")): dict(k) for k in self.historic_keys}
        active = self.signer.public_jwk()
        keys[str(active["kid"])] = active
        return list(keys.values())


_runtime: EvidenceRuntime | None = None


def sign_evidence(claims: Mapping[str, Any], typ: str) -> str:
    """Sign a statement with the EVIDENCE signing key: never the key that signs access tokens, so a token-key compromise cannot forge a receipt."""
    from zid_crypto import sign_jws

    return sign_jws(current_runtime().signer, claims, typ=typ)


def verify_evidence(token: str, typ: str) -> Mapping[str, Any]:
    """The claims of a statement signed by the evidence key, historic keys included (rotation never invalidates the past). Raises when it does not verify."""
    from zid_crypto import verify_jws

    return verify_jws(token, keys=current_runtime().trusted_keys(), typ=typ)[1]


def configure_runtime(runtime: EvidenceRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def development_runtime(cell_id: str = "local-1", environment: str = "test") -> EvidenceRuntime:
    """In-memory development keys (local/test only: LocalKeyProvider refuses anything else)."""
    provider = LocalKeyProvider(environment=environment)
    refs = {
        p: KeyRef(p, f"dev-{p.value.lower()}-{cell_id}")
        for p in (KeyPurpose.SUBJECT_ERASURE_KEY, KeyPurpose.DATA_KEK, KeyPurpose.HMAC_INTERNAL)
    }
    for purpose, ref in refs.items():
        provider.ensure_key(purpose, ref.key_id)
    return EvidenceRuntime(
        cell_id=cell_id,
        mac_key=provider.hmac_key(refs[KeyPurpose.HMAC_INTERNAL]),
        provider=provider,
        subject_kek=refs[KeyPurpose.SUBJECT_ERASURE_KEY],
        data_kek=refs[KeyPurpose.DATA_KEK],
        signer=LocalSigner(f"dev-evidence-{cell_id}", purpose=KeyPurpose.EVIDENCE_SIGNING),
    )


def current_runtime() -> EvidenceRuntime:
    global _runtime
    if _runtime is None:
        environment = os.environ.get("ZID_ENVIRONMENT", "local")
        if environment not in ("local", "test"):
            raise RuntimeError("the evidence runtime is not configured (DEC-F5: no local keys)")
        _runtime = development_runtime(os.environ.get("ZID_CELL_ID", "local-1"), environment)
    return _runtime


def runtime_from_settings(settings: Any, *, kms_client: object | None = None) -> EvidenceRuntime:
    """Build the runtime from platform Settings through the crypto broker (crypto_runtime)."""
    from zid_platform.crypto_runtime import (
        evidence_signer_from_settings,
        handle_key_from_settings,
        kek_ref,
        key_provider_from_settings,
    )
    from zid_platform.modules.evidence.infrastructure.tsa_token import (
        timestamp_verifier_from_settings,
    )

    provider = key_provider_from_settings(settings)
    return EvidenceRuntime(
        cell_id=settings.cell_id,
        mac_key=handle_key_from_settings(settings, provider, kms_client=kms_client),
        provider=provider,
        subject_kek=kek_ref(settings, KeyPurpose.SUBJECT_ERASURE_KEY),
        data_kek=kek_ref(settings, KeyPurpose.DATA_KEK),
        signer=evidence_signer_from_settings(settings, kms_client=kms_client),
        historic_keys=tuple(settings.evidence_historic_keys),
        cadence=Cadence(
            interval=settings.evidence_checkpoint_interval,
            e3_interval=settings.evidence_checkpoint_e3_interval,
        ),
        retention_days=dict(settings.evidence_retention_days),
        timestamp_verifier=timestamp_verifier_from_settings(settings),
        tenant_keks={
            tenant: KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, key_id)
            for tenant, key_id in settings.tenant_kek_ids.items()
        },
    )


def parse_keys(keys: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    out = []
    for k in keys:
        if any(p in k for p in ("d", "p", "q", "k")):
            raise ValueError("historic evidence keys are public keys only")
        out.append(dict(k))
    return tuple(out)


__all__ = [
    "EvidenceRuntime",
    "configure_runtime",
    "current_runtime",
    "development_runtime",
    "parse_keys",
    "runtime_from_settings",
]
