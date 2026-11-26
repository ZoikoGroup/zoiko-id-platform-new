"""Client-certificate sign-in against the real database and the real token endpoint (CR-165), with a simulated trusted edge proxy.

What is held: a registered certificate is pinned by fingerprint and signs in only when the cell is configured for it AND the request comes from a trusted proxy with its secret; every other path fails the same way any
wrong credential does; the certificate credential never outlives its certificate; rotation moves to a new pin; the IP allowlist and revocation still apply; a production certificate credential needs the approval that names
that exact certificate; and a cell with nothing configured refuses to register one.
"""

from __future__ import annotations

import asyncio
import hashlib
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from test_commercial_pg import settings
from test_verify_api_approvals_pg import production_app, refused
from test_verify_api_go_live_pg import Company, code
from test_verify_api_pg import make_app, uid
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.application import approvals_service as appr
from zid_platform.modules.verify_api.application import observe, service

SECRET = "e" * 48
TOKEN = "/v1/verify-api/oauth2/token"


def make_cert(*, days: int = 30, cn: str = "partner-server") -> tuple[str, bytes]:
    """(PEM, SHA-256 of the DER) of a self-signed certificate that ends `days` from now."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    built = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return built.public_bytes(serialization.Encoding.PEM).decode(), hashlib.sha256(
        built.public_bytes(serialization.Encoding.DER)
    ).digest()


def edge(pem: str | None, *, secret: str | None = SECRET) -> dict[str, str]:
    """What a trusted proxy adds to a request: its secret and the certificate it verified in the TLS handshake."""
    h: dict[str, str] = {}
    if secret is not None:
        h["x-zoiko-edge-secret"] = secret
    if pem is not None:
        h["x-forwarded-client-cert"] = urllib.parse.quote(pem)
    return h


def configured_app(rt_url: str, tmp_path: Path) -> TestClient:
    secret_file = tmp_path / "edge-secret"
    secret_file.write_text(SECRET + "\n", encoding="utf-8")
    app = create_app(
        settings(
            rt_url,
            partner_mtls_proxies=("testclient",),
            partner_mtls_proxy_secret_file=str(secret_file),
        ),
        keyring=onboard(rt_url).ring,
    )
    return TestClient(app, base_url=ISS)


def seed(
    rt_url: str, tenant: str, pem: str, *, allowlist: list[str] | None = None
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with engine(rt_url) as eng:
            app = await make_app(
                eng, tenant, "Cert app", scopes=["verifications:create", "decisions:read"]
            )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                cred = await service.issue_credential(
                    t, tenant, uid(), app["application_id"], certificate_pem=pem
                )
            if allowlist is not None:
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    await service.update_settings(
                        t, app["application_id"], {"ip_allowlist": allowlist}
                    )
            return {"app": app, "cred": cred}

    return asyncio.run(run())


def test_a_certificate_credential_signs_in_only_through_a_trusted_proxy_that_vouches_for_the_pinned_certificate(
    rt_url: str, owner_url: str, tmp_path: Path
) -> None:
    tenant = uid()
    pem, sha = make_cert(days=40, cn="acme-verify-server")
    made = seed(rt_url, tenant, pem)
    cred = made["cred"]
    assert (
        cred["auth_method"] == "MTLS"
        and cred["key_id"] == sha.hex()[:16]
        and "client_secret" not in cred
        and "acme-verify-server" in cred["certificate_subject"]
    )
    # the credential never outlives the certificate it pins (40 days, not the usual year)
    assert datetime.fromisoformat(cred["expires_at"]) <= datetime.now(UTC) + timedelta(days=41)

    issued0, denied0, none0 = (
        observe.AUTH.value(method="mtls", result="issued"),
        observe.AUTH.value(method="mtls", result="denied"),
        observe.AUTH.value(method="none", result="denied"),
    )
    with configured_app(rt_url, tmp_path) as http:
        ok = http.post(TOKEN, json={"client_id": cred["client_id"]}, headers=edge(pem))
        assert ok.status_code == 200, ok.text
        assert ok.headers["cache-control"] == "no-store" and ok.json()["environment"] == "sandbox"
        me = http.get(
            "/v1/verify-api/whoami",
            headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
        )
        assert me.status_code == 200 and set(me.json()["scopes"]) == {
            "verifications:create",
            "decisions:read",
        }

        other_pem, _ = make_cert()
        for label, headers in (
            ("no certificate", edge(None)),
            ("the proxy's secret missing", edge(pem, secret=None)),
            ("the proxy's secret wrong", edge(pem, secret="f" * 48)),
            ("another certificate", edge(other_pem)),
            (
                "an unreadable certificate",
                {**edge(None), "x-forwarded-client-cert": "not-a-certificate"},
            ),
        ):
            r = http.post(TOKEN, json={"client_id": cred["client_id"]}, headers=headers)
            assert r.status_code == 401 and code(r) == "AUTH_INVALID", label
        # a secret cannot be used against a certificate credential, and an unknown client gets the same answer
        assert (
            code(
                http.post(
                    TOKEN,
                    json={"client_id": cred["client_id"], "client_secret": "zps_guess"},
                    headers=edge(pem),
                )
            )
            == "AUTH_INVALID"
        )
        assert (
            code(http.post(TOKEN, json={"client_id": "zpc_" + "0" * 20}, headers=edge(pem)))
            == "AUTH_INVALID"
        )
    assert observe.AUTH.value(method="mtls", result="issued") == issued0 + 1
    # a certificate the edge vouched for but that is not the pinned one, and an unknown client, are mtls denials; a request the edge did not vouch for (no certificate, no secret, a wrong secret, unreadable) is
    # counted as having presented nothing the cell could check
    assert observe.AUTH.value(method="mtls", result="denied") == denied0 + 2
    assert observe.AUTH.value(method="none", result="denied") == none0 + 4


def test_a_cell_with_nothing_configured_refuses_to_register_a_certificate_and_cannot_sign_one_in(
    rt_url: str, owner_url: str
) -> None:
    pem, _ = make_cert()
    co = Company(rt_url)  # no trusted proxy and no secret
    try:
        app = co.http.post(
            "/v1/verify-api/applications", headers=co.h, json={"name": "No edge"}
        ).json()["application_id"]
        refused_ = co.http.post(
            f"/v1/verify-api/applications/{app}/credentials",
            headers=co.h,
            json={"certificate_pem": pem},
        )
        assert refused_.status_code == 409 and code(refused_) == "MTLS_NOT_CONFIGURED"
        # and even a credential somehow registered signs nobody in: the cell is not configured to believe a forwarded certificate
        seeded = seed(rt_url, co.o.tenant, pem)
        r = co.http.post(TOKEN, json={"client_id": seeded["cred"]["client_id"]}, headers=edge(pem))
        assert r.status_code == 401 and code(r) == "AUTH_INVALID"
        # a signed-in owner can still make an ordinary credential
        assert (
            co.http.post(f"/v1/verify-api/applications/{app}/credentials", headers=co.h).status_code
            == 201
        )
    finally:
        co.close()


def test_the_allowlist_revocation_and_rotation_still_apply_to_a_certificate_credential(
    rt_url: str, owner_url: str, tmp_path: Path
) -> None:
    tenant = uid()
    first_pem, _ = make_cert()
    second_pem, _ = make_cert()
    made = seed(
        rt_url, tenant, first_pem, allowlist=["203.0.113.0/24"]
    )  # the test client is not inside it
    cid, aid = made["cred"]["client_id"], made["app"]["application_id"]
    with configured_app(rt_url, tmp_path) as http:
        blocked = http.post(TOKEN, json={"client_id": cid}, headers=edge(first_pem))
        assert (
            blocked.status_code == 403 and code(blocked) == "IP_NOT_ALLOWED"
        )  # proved first, then refused by network

        async def clear() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as t:
                await service.update_settings(t, aid, {"ip_allowlist": []})

        asyncio.run(clear())
        assert http.post(TOKEN, json={"client_id": cid}, headers=edge(first_pem)).status_code == 200

        async def rotate(**kw: Any) -> dict[str, Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as t:
                return await service.rotate_credential(
                    t, tenant, uid(), aid, made["cred"]["credential_id"], **kw
                )

        async def refuse(**kw: Any) -> str:
            return await refused(rotate(**kw))

        assert (
            asyncio.run(refuse()) == "VALIDATION_FAILED"
        )  # a certificate credential is rotated to a new certificate
        assert (
            asyncio.run(refuse(public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "AAAA"}))
            == "VALIDATION_FAILED"
        )
        fresh = asyncio.run(rotate(certificate_pem=second_pem))
        assert fresh["auth_method"] == "MTLS" and fresh["replaces"] == made["cred"]["credential_id"]
        # each client id answers only to its own pinned certificate
        assert (
            http.post(
                TOKEN, json={"client_id": fresh["client_id"]}, headers=edge(second_pem)
            ).status_code
            == 200
        )
        assert (
            code(http.post(TOKEN, json={"client_id": fresh["client_id"]}, headers=edge(first_pem)))
            == "AUTH_INVALID"
        )
        assert (
            http.post(TOKEN, json={"client_id": cid}, headers=edge(first_pem)).status_code == 200
        )  # the old one, inside its overlap

        async def revoke() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as t:
                await service.revoke_credential(t, aid, fresh["credential_id"])

        asyncio.run(revoke())
        assert (
            code(http.post(TOKEN, json={"client_id": fresh["client_id"]}, headers=edge(second_pem)))
            == "AUTH_INVALID"
        )


def test_a_production_certificate_credential_needs_the_approval_that_names_that_exact_certificate(
    rt_url: str, owner_url: str
) -> None:
    tenant, op1, op2 = uid(), uid(), uid()
    pem_a, _ = make_cert(cn="approved-server")
    pem_b, _ = make_cert(cn="another-server")

    async def run() -> None:
        async with engine(rt_url) as eng:
            app = await production_app(eng, owner_url, tenant, "Cert live")
            aid = app["application_id"]

            async def approved(body: dict[str, Any]) -> str:
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    asked = await appr.request(t, tenant, op1, aid, body)
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    await appr.approve(t, asked["approval_id"], op2, None)
                return str(asked["approval_id"])

            pid = await approved({"action": "ISSUE", "certificate_pem": pem_a})
            async with (
                tenant_transaction(eng, ctx(tenant)) as t
            ):  # a different certificate, no certificate and a key are all refused, and the approval stays unspent
                assert (
                    await refused(
                        service.issue_credential(
                            t, tenant, op1, aid, certificate_pem=pem_b, approval_id=pid
                        )
                    )
                    == "APPROVAL_REQUIRED"
                )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (
                    await refused(service.issue_credential(t, tenant, op1, aid, approval_id=pid))
                    == "APPROVAL_REQUIRED"
                )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                made = await service.issue_credential(
                    t, tenant, op1, aid, certificate_pem=pem_a, approval_id=pid
                )
                assert made["auth_method"] == "MTLS"
            async with tenant_transaction(
                eng, ctx(tenant)
            ) as t:  # naming a key and a certificate together is not a request
                assert (
                    await refused(
                        appr.request(
                            t,
                            tenant,
                            op1,
                            aid,
                            {
                                "action": "ISSUE",
                                "certificate_pem": pem_a,
                                "public_jwk": {"kty": "OKP", "crv": "Ed25519", "x": "AAAA"},
                            },
                        )
                    )
                    == "VALIDATION_FAILED"
                )

    asyncio.run(run())
