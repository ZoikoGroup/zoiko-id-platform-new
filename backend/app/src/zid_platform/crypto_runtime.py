"""Crypto broker wiring from settings (DEC-F5). Services get keys and secrets only through here.

    provider = key_provider_from_settings(settings)
    envelope = await seal(data, aad, provider, kek_ref(settings, KeyPurpose.DATA_KEK))
    secrets = secret_broker_from_settings(settings)

Local/test: development KEKs (`LocalKeyProvider`, one per symmetric purpose, `dev-<purpose>-<cell>`)
and ZID_SECRET__<REF> environment secrets. Any other environment must configure aws-kms and
openbao; the local implementations refuse to start there.

    signer = evidence_signer_from_settings(settings)     # EVIDENCE_SIGNING (checkpoints, exports)
    mac = handle_key_from_settings(settings, provider)   # HMAC_INTERNAL (pseudonymous handles)
"""

from __future__ import annotations

from pathlib import Path

import httpx
from zid_crypto import LocalSigner, Signer
from zid_crypto.aws_kms import AwsKmsKeyProvider, KmsClient, KmsMacKey, KmsSigner
from zid_crypto.handles import MacKey
from zid_crypto.providers import KeyProvider, KeyRef, LocalKeyProvider
from zid_crypto.purposes import WRAPPING_PURPOSES, KeyPurpose
from zid_crypto.secret_broker import EnvSecretBroker, OpenBaoSecretBroker, SecretBroker

from .settings import Settings

# Symmetric purposes the provider holds (signing keys stay behind keys.KeyRings / KMS signers).
PROVIDER_PURPOSES = (*sorted(WRAPPING_PURPOSES), KeyPurpose.HMAC_INTERNAL)


def kek_ref(settings: Settings, purpose: KeyPurpose) -> KeyRef:
    """The configured key for `purpose` in this cell (current version)."""
    if settings.key_provider == "local":
        return KeyRef(purpose, f"dev-{purpose.value.lower()}-{settings.cell_id}")
    key_id = settings.kms_key_ids.get(purpose.value) or settings.kms_key_alias_pattern.format(
        cell=settings.cell_id, purpose=purpose.value.lower()
    )
    return KeyRef(purpose, key_id)


def key_provider_from_settings(
    settings: Settings, *, kms_client: KmsClient | None = None
) -> KeyProvider:
    if settings.key_provider == "local":
        local = LocalKeyProvider(environment=settings.environment, keystore=settings.local_keystore)
        for purpose in PROVIDER_PURPOSES:
            local.ensure_key(purpose, kek_ref(settings, purpose).key_id)
        return local
    key_ids = {p: kek_ref(settings, p).key_id for p in PROVIDER_PURPOSES}
    if kms_client is not None:
        return AwsKmsKeyProvider(
            kms_client, key_ids, pending_window_days=settings.kms_pending_window_days
        )
    return AwsKmsKeyProvider.from_boto3(settings.kms_region, key_ids)


def _kms_client(settings: Settings) -> object:
    try:
        import boto3  # type: ignore[import-not-found]  # optional: deployed images only
    except ImportError as e:
        raise RuntimeError("ZID_KEY_PROVIDER=aws-kms needs boto3 in the image (DEC-F5)") from e
    return boto3.client("kms", region_name=settings.kms_region)


def evidence_signer_from_settings(
    settings: Settings, *, kms_client: object | None = None
) -> Signer:
    """The EVIDENCE_SIGNING key (DEC-F2 §5): separate from every token/credential key.

    Local/test: a development key, shared between processes through ZID_DEV_SIGNING_KEYS
    ("evidence" -> PEM) so the API, worker and verifier agree; deployed: the cell's KMS key."""
    kid = f"evidence-{settings.cell_id}"
    if settings.key_provider == "local":
        if settings.environment not in ("local", "test"):
            raise RuntimeError(
                "evidence signing keys must come from KMS outside local/test (DEC-F5)"
            )
        pem = settings.dev_signing_keys.get("evidence")
        kid = f"dev-evidence-{settings.cell_id}"
        if pem:
            return LocalSigner.from_pem(kid, pem, purpose=KeyPurpose.EVIDENCE_SIGNING)
        return LocalSigner(kid, purpose=KeyPurpose.EVIDENCE_SIGNING)
    return KmsSigner(
        kms_client or _kms_client(settings),
        kek_ref(settings, KeyPurpose.EVIDENCE_SIGNING).key_id,
        kid=settings.evidence_signing_kid or kid,
        purpose=KeyPurpose.EVIDENCE_SIGNING,
    )


def handle_key_from_settings(
    settings: Settings, provider: KeyProvider | None = None, *, kms_client: object | None = None
) -> MacKey:
    """The HMAC_INTERNAL key that makes pseudonymous evidence handles (DEC-F1 §2)."""
    ref = kek_ref(settings, KeyPurpose.HMAC_INTERNAL)
    if settings.key_provider == "local":
        local = (
            provider
            if isinstance(provider, LocalKeyProvider)
            else key_provider_from_settings(settings)
        )
        assert isinstance(local, LocalKeyProvider)
        return local.hmac_key(ref)
    return KmsMacKey(kms_client or _kms_client(settings), ref.key_id)


def _file_reader(path: str) -> str:
    # Re-read on every login so a rotated JWT-SVID/token is picked up without a restart.
    return Path(path).read_text(encoding="utf-8").strip()


def secret_broker_from_settings(
    settings: Settings, *, http_client: httpx.Client | None = None
) -> SecretBroker:
    if settings.secret_broker == "env":
        return EnvSecretBroker(environment=settings.environment)
    if not settings.openbao_addr:
        raise RuntimeError("ZID_SECRET_BROKER=openbao needs ZID_OPENBAO_ADDR (DEC-F5)")
    source = (
        settings.openbao_jwt_file if settings.openbao_auth == "jwt" else settings.openbao_token_file
    )
    if not source:
        raise RuntimeError(f"ZID_OPENBAO_AUTH={settings.openbao_auth} needs its credential file")
    reader = lambda: _file_reader(source)
    return OpenBaoSecretBroker(
        settings.openbao_addr,
        mount=settings.openbao_kv_mount,
        namespace=settings.openbao_namespace,
        token=reader if settings.openbao_auth == "token" else None,
        jwt=reader if settings.openbao_auth == "jwt" else None,
        jwt_role=settings.openbao_jwt_role,
        jwt_mount=settings.openbao_jwt_mount,
        client=http_client,
    )


__all__ = [
    "evidence_signer_from_settings",
    "handle_key_from_settings",
    "kek_ref",
    "key_provider_from_settings",
    "secret_broker_from_settings",
]
