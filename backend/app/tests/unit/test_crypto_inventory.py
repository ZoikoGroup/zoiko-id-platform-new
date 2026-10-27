"""PQC-0 key and algorithm inventory is complete (DEC-F6 §2) and the broker wiring (DEC-F5)."""

import asyncio
from pathlib import Path

import pytest
import yaml
from zid_crypto import CryptoError
from zid_crypto.envelope import Aad, open_envelope, seal
from zid_crypto.handles import handle
from zid_crypto.policy import default_registry
from zid_crypto.providers import LocalKeyProvider
from zid_crypto.purposes import KeyPurpose
from zid_crypto.secret_broker import EnvSecretBroker
from zid_platform import keys
from zid_platform.crypto_runtime import (
    kek_ref,
    key_provider_from_settings,
    secret_broker_from_settings,
)
from zid_platform.settings import Settings

INVENTORY = Path(__file__).resolve().parents[4] / "contracts" / "crypto" / "key-inventory.yaml"
WAVES = {f"PQC-{n}" for n in range(6)}


def entries() -> list[dict[str, object]]:
    doc = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    return list(doc["entries"])


def test_every_key_purpose_and_signing_ring_is_inventoried() -> None:
    inv = entries()
    purposes = {e["key_purpose"] for e in inv}
    missing = {p.value for p in KeyPurpose} - purposes
    assert not missing, f"key purposes missing from {INVENTORY.name}: {sorted(missing)}"
    rings = {e["keyring"] for e in inv}
    assert not set(keys.PURPOSES) - rings, (
        f"signing key rings missing: {set(keys.PURPOSES) - rings}"
    )


def test_inventory_algorithms_are_in_the_registry() -> None:
    reg = default_registry()
    known = reg.known_algorithms()
    ids = [e["id"] for e in entries()]
    assert len(ids) == len(set(ids)), "duplicate inventory ids"
    for e in entries():
        algs = list(e["algorithms"]) + list(e.get("planned") or [])  # type: ignore[call-overload]
        assert algs, e["id"]
        unknown = [a for a in algs if a not in known]
        assert not unknown, f"{e['id']}: not in the Crypto Policy Registry: {unknown}"
        if e["key_purpose"]:
            purpose = KeyPurpose(str(e["key_purpose"]))
            assert all(reg.allowed(purpose, a) for a in algs), e["id"]
            assert e["profile"] == reg.profile_for(purpose), e["id"]
        assert isinstance(e["pq_ready"], bool), e["id"]
        assert e["migration_wave"] in WAVES, e["id"]
        for field in ("used_by", "custody", "retention_horizon"):
            assert e.get(field), f"{e['id']}: {field}"


def test_local_runtime_provider_and_secrets() -> None:
    settings = Settings(environment="test", cell_id="t-1")
    provider = key_provider_from_settings(settings)
    assert isinstance(provider, LocalKeyProvider)
    ref = kek_ref(settings, KeyPurpose.DATA_KEK)
    assert ref.key_id == "dev-data_kek-t-1"
    aad = Aad("tenant", "principal", "p1", "email", "1")
    envelope = asyncio.run(seal(b"x", aad, provider, ref))
    assert asyncio.run(open_envelope(envelope, aad, provider)) == b"x"
    mac = provider.hmac_key(kek_ref(settings, KeyPurpose.HMAC_INTERNAL))
    assert handle(mac, "tenant", "authn", "v").startswith("ph1.1.")
    assert isinstance(secret_broker_from_settings(settings), EnvSecretBroker)


def test_deployed_environments_refuse_local_keys_and_env_secrets() -> None:
    settings = Settings(environment="production")
    with pytest.raises(CryptoError, match="CRYPTO_LOCAL_PROVIDER_FORBIDDEN"):
        key_provider_from_settings(settings)
    with pytest.raises(CryptoError, match="CRYPTO_LOCAL_PROVIDER_FORBIDDEN"):
        secret_broker_from_settings(settings)


def test_kms_key_ids_come_from_settings() -> None:
    settings = Settings(
        environment="production",
        cell_id="eu-1",
        key_provider="aws-kms",
        kms_key_ids={"DATA_KEK": "arn:aws:kms:eu-west-2:1:key/data"},
    )
    assert kek_ref(settings, KeyPurpose.DATA_KEK).key_id == "arn:aws:kms:eu-west-2:1:key/data"
    assert kek_ref(settings, KeyPurpose.BACKUP_KEK).key_id == "alias/zid/eu-1/backup_kek"

    class NoKms:
        pass

    provider = key_provider_from_settings(settings, kms_client=NoKms())  # type: ignore[arg-type]
    assert type(provider).__name__ == "AwsKmsKeyProvider"


def test_evidence_signer_and_handle_key_from_settings() -> None:
    from zid_crypto import sign_jws, verify_jws
    from zid_platform.crypto_runtime import evidence_signer_from_settings, handle_key_from_settings

    settings = Settings(environment="test", cell_id="t-1")
    signer = evidence_signer_from_settings(settings)
    assert signer.kid == "dev-evidence-t-1"
    token = sign_jws(signer, {"x": 1}, typ="t+jws")
    assert verify_jws(token, keys=[signer.public_jwk()], typ="t+jws")[1] == {"x": 1}
    mac = handle_key_from_settings(settings)
    assert handle(mac, "tenant", "evidence.actor", "p").startswith("ph1.")
    with pytest.raises(RuntimeError, match="KMS outside local/test"):
        evidence_signer_from_settings(Settings(environment="production"))


def test_kms_evidence_signer_uses_the_configured_key() -> None:
    from zid_crypto.aws_kms import KmsMacKey, KmsSigner
    from zid_platform.crypto_runtime import evidence_signer_from_settings, handle_key_from_settings

    settings = Settings(environment="production", cell_id="eu-1", key_provider="aws-kms")
    signer = evidence_signer_from_settings(settings, kms_client=object())
    assert isinstance(signer, KmsSigner) and signer.kid == "evidence-eu-1"
    assert isinstance(handle_key_from_settings(settings, kms_client=object()), KmsMacKey)


def test_gcp_kms_key_names_and_classes_come_from_settings() -> None:
    from zid_crypto.gcp_kms import GcpKmsMacKey, GcpKmsSigner
    from zid_platform.crypto_runtime import evidence_signer_from_settings, handle_key_from_settings

    settings = Settings(
        environment="production",
        cell_id="eu-1",
        key_provider="gcp-kms",
        gcp_kms_project="zoiko-prod",
        gcp_kms_location="europe-west2",
        kms_key_ids={"DATA_KEK": "projects/other/locations/l/keyRings/r/cryptoKeys/data"},
    )
    assert kek_ref(settings, KeyPurpose.DATA_KEK).key_id.startswith("projects/other/")
    assert kek_ref(settings, KeyPurpose.BACKUP_KEK).key_id == (
        "projects/zoiko-prod/locations/europe-west2/keyRings/zid-eu-1/cryptoKeys/backup_kek"
    )
    provider = key_provider_from_settings(settings, kms_client=object())  # type: ignore[arg-type]
    assert type(provider).__name__ == "GcpKmsKeyProvider"
    signer = evidence_signer_from_settings(settings, kms_client=object())
    assert isinstance(signer, GcpKmsSigner) and signer.kid == "evidence-eu-1"
    assert isinstance(handle_key_from_settings(settings, kms_client=object()), GcpKmsMacKey)
    with pytest.raises(RuntimeError, match="ZID_GCP_KMS_PROJECT"):
        kek_ref(Settings(environment="production", key_provider="gcp-kms"), KeyPurpose.DATA_KEK)


@pytest.mark.parametrize("provider", ["gcp-kms", "aws-kms"])
def test_deployed_signing_key_rings_are_distinct_kms_keys(provider: str) -> None:
    """A deployed cell issues tokens only with KMS-backed keys, one per purpose, none of them local."""
    from zid_platform.crypto_runtime import kms_keyrings_from_settings

    settings = Settings(
        environment="production",
        cell_id="eu-1",
        key_provider=provider,  # type: ignore[arg-type]
        gcp_kms_project="zoiko-prod",
        gcp_kms_location="europe-west2",
        kms_key_ids={"SIGNING_TOKEN": "explicit-token-key"},
        signing_retiring_keys={"token": ({"kty": "EC", "kid": "old"},)},
    )
    rings = kms_keyrings_from_settings(settings, kms_client=object())
    assert rings.distinct()
    assert {r.active.kid for r in (rings.token, rings.set)} == {"token-eu-1", "set-eu-1"}
    assert rings.token.retiring == [{"kty": "EC", "kid": "old"}] and rings.set.retiring == []
    assert all(type(r.active).__name__ != "LocalSigner" for r in (rings.token, rings.credential))
    with pytest.raises(RuntimeError, match="outside local/test"):
        kms_keyrings_from_settings(Settings(environment="production"))
