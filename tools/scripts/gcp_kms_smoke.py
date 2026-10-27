"""Smoke test of a deployed cell's Cloud KMS keys. Run once after `tofu apply`, from the VM or any host whose
credentials may use the keys (Application Default Credentials). It never destroys or rotates anything.

    ZID_ENVIRONMENT=production ZID_CELL_ID=eu-1 ZID_KEY_PROVIDER=gcp-kms \\
    ZID_GCP_KMS_PROJECT=zoiko-prod ZID_GCP_KMS_LOCATION=europe-west2 \\
    python tools/scripts/gcp_kms_smoke.py

Checks, with the real Cloud KMS API (the unit tests only use fakes):
  1. envelope encryption round trip for every wrapping purpose, and a refusal when the tenant context is wrong
  2. one signed JWS per signing purpose (token, set, federation, credential, verifier), verified against the
     published public key, and distinct key ids
  3. the evidence signer and the HMAC key (stable, deterministic handles)
Exit code 0 only if every check passed.
"""

from __future__ import annotations

import asyncio
import sys

from zid_crypto import CryptoError, sign_jws, verify_jws
from zid_crypto.envelope import Aad, open_envelope, seal
from zid_crypto.handles import handle
from zid_crypto.purposes import WRAPPING_PURPOSES
from zid_platform.crypto_runtime import (
    evidence_signer_from_settings,
    handle_key_from_settings,
    kek_ref,
    key_provider_from_settings,
    kms_keyrings_from_settings,
)
from zid_platform.settings import Settings

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)


async def envelope_checks(settings: Settings) -> None:
    provider = key_provider_from_settings(settings)
    aad = Aad("smoke-tenant", "smoke", "obj-1", "field", "1")
    other = Aad("other-tenant", "smoke", "obj-1", "field", "1")
    for purpose in sorted(WRAPPING_PURPOSES, key=str):
        ref = kek_ref(settings, purpose)
        try:
            envelope = await seal(b"smoke-test", aad, provider, ref)
            check(f"{purpose} round trip", await open_envelope(envelope, aad, provider) == b"smoke-test")
            try:
                await open_envelope(envelope, other, provider)
                check(f"{purpose} refuses another tenant's context", False)
            except CryptoError:
                check(f"{purpose} refuses another tenant's context", True)
        except Exception as e:  # noqa: BLE001 - report every key, do not stop at the first
            check(f"{purpose} round trip", False, f"{type(e).__name__}: {e}")


def signing_checks(settings: Settings) -> None:
    rings = kms_keyrings_from_settings(settings)
    for name in ("token", "set", "federation", "credential", "verifier"):
        ring = getattr(rings, name)
        try:
            token = ring.sign({"smoke": name}, typ="smoke+jws")
            _, payload = verify_jws(token, keys=ring.jwks()["keys"], typ="smoke+jws")
            check(f"{name} signing key signs and verifies", payload == {"smoke": name})
        except Exception as e:  # noqa: BLE001
            check(f"{name} signing key signs and verifies", False, f"{type(e).__name__}: {e}")
    check("signing keys are distinct per purpose", rings.distinct())
    try:
        evidence = evidence_signer_from_settings(settings)
        token = sign_jws(evidence, {"smoke": "evidence"}, typ="smoke+jws")
        check("evidence key signs and verifies", verify_jws(token, keys=[evidence.public_jwk()], typ="smoke+jws")[1] == {"smoke": "evidence"})
    except Exception as e:  # noqa: BLE001
        check("evidence key signs and verifies", False, f"{type(e).__name__}: {e}")
    try:
        mac = handle_key_from_settings(settings)
        first = handle(mac, "smoke-tenant", "smoke.actor", "p1")
        check("HMAC handle key is deterministic", first == handle(mac, "smoke-tenant", "smoke.actor", "p1")
              and first != handle(mac, "other-tenant", "smoke.actor", "p1"))
    except Exception as e:  # noqa: BLE001
        check("HMAC handle key is deterministic", False, f"{type(e).__name__}: {e}")


def main() -> int:
    settings = Settings()
    if settings.key_provider != "gcp-kms":
        print("ZID_KEY_PROVIDER must be gcp-kms", file=sys.stderr)
        return 2
    asyncio.run(envelope_checks(settings))
    signing_checks(settings)
    print(f"\n{len(failures)} check(s) failed" if failures else "\nall Cloud KMS checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
