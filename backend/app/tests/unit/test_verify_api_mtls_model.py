"""Client-certificate sign-in, the parts that need no database (CR-165): which certificates may be registered, and when the cell will believe a certificate the edge hands it. It fails closed."""

from __future__ import annotations

import base64
import hashlib
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from zid_platform.modules.verify_api.application import certificates as certs
from zid_platform.modules.verify_api.application import edge_cert as ec_mod
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW = datetime.now(UTC)
SECRET = "s" * 40


def cert(
    *, start: int = -1, end: int = 30, key: Any = None, cn: str = "partner-server"
) -> tuple[str, bytes, bytes]:
    """(PEM, DER, SHA-256 of the DER) of a self-signed certificate valid from `start` to `end` days from now."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    built = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW + timedelta(days=start))
        .not_valid_after(NOW + timedelta(days=end))
        .sign(key, hashes.SHA256())
    )
    der = built.public_bytes(serialization.Encoding.DER)
    return (
        built.public_bytes(serialization.Encoding.PEM).decode(),
        der,
        hashlib.sha256(der).digest(),
    )


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_current_public_certificate_is_pinned_by_its_fingerprint_and_nothing_else_is_kept() -> (
    None
):
    pem, _der, sha = cert(cn="acme-verify-server")
    facts = certs.check_certificate(pem, now=NOW)
    assert facts.sha256 == sha and "acme-verify-server" in facts.subject and facts.not_after > NOW
    for strong in (ec.generate_private_key(ec.SECP384R1()), rsa.generate_private_key(65537, 2048)):
        assert certs.check_certificate(cert(key=strong)[0], now=NOW).sha256


def test_a_certificate_that_is_a_chain_expired_not_yet_valid_weak_or_carries_a_private_key_is_refused() -> (
    None
):
    good = cert()[0]
    private = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    for label, bad in (
        ("a chain", good + cert()[0]),
        ("private key material", good + private),
        ("expired", cert(start=-10, end=-1)[0]),
        ("not yet valid", cert(start=5, end=30)[0]),
        ("a weak RSA key", cert(key=rsa.generate_private_key(65537, 1024))[0]),
        (
            "not a certificate",
            "-----BEGIN CERTIFICATE-----\n" + "A" * 200 + "\n-----END CERTIFICATE-----",
        ),
        ("empty", ""),
        ("not text", 12345),
        ("too large", "x" * 9000),
    ):
        assert code(certs.check_certificate, bad, now=NOW) == "VALIDATION_FAILED", label


def test_the_cell_believes_a_forwarded_certificate_only_from_a_trusted_proxy_with_its_secret() -> (
    None
):
    pem, der, sha = cert()
    cfg = ec_mod.EdgeConfig(frozenset({"10.0.0.7"}), SECRET.encode())
    good = {"x-zoiko-edge-secret": SECRET, "x-forwarded-client-cert": urllib.parse.quote(pem)}
    assert ec_mod.presented_fingerprint(cfg, peer="10.0.0.7", headers=good) == sha
    # base64 of the DER is accepted too, and header names are case-insensitive
    other = {
        "X-Zoiko-Edge-Secret": SECRET,
        "X-Forwarded-Client-Cert": base64.b64encode(der).decode(),
    }
    assert ec_mod.presented_fingerprint(cfg, peer="10.0.0.7", headers=other) == sha
    # everything else is nothing at all, never an error that says which part was wrong
    for label, kw in (
        ("an untrusted address", {"peer": "203.0.113.9", "headers": good}),
        ("no address", {"peer": None, "headers": good}),
        (
            "no secret",
            {"peer": "10.0.0.7", "headers": {"x-forwarded-client-cert": urllib.parse.quote(pem)}},
        ),
        (
            "a wrong secret",
            {"peer": "10.0.0.7", "headers": {**good, "x-zoiko-edge-secret": "t" * 40}},
        ),
        (
            "a near secret",
            {"peer": "10.0.0.7", "headers": {**good, "x-zoiko-edge-secret": SECRET + "x"}},
        ),
        ("no certificate", {"peer": "10.0.0.7", "headers": {"x-zoiko-edge-secret": SECRET}}),
        (
            "garbage",
            {
                "peer": "10.0.0.7",
                "headers": {**good, "x-forwarded-client-cert": "not a certificate"},
            },
        ),
        (
            "a chain",
            {
                "peer": "10.0.0.7",
                "headers": {**good, "x-forwarded-client-cert": urllib.parse.quote(pem + cert()[0])},
            },
        ),
        (
            "an expired certificate",
            {
                "peer": "10.0.0.7",
                "headers": {
                    **good,
                    "x-forwarded-client-cert": urllib.parse.quote(cert(start=-10, end=-1)[0]),
                },
            },
        ),
        (
            "a huge header",
            {"peer": "10.0.0.7", "headers": {**good, "x-forwarded-client-cert": "A" * 20000}},
        ),
    ):
        assert ec_mod.presented_fingerprint(cfg, **kw) is None, label  # type: ignore[arg-type]


def test_with_no_proxy_or_no_secret_configured_it_is_off_and_a_half_configuration_stops_the_cell(
    tmp_path: Path,
) -> None:
    pem, _der, _sha = cert()
    headers = {"x-zoiko-edge-secret": SECRET, "x-forwarded-client-cert": urllib.parse.quote(pem)}
    assert not ec_mod.EdgeConfig().enabled
    assert (
        ec_mod.presented_fingerprint(ec_mod.EdgeConfig(), peer="10.0.0.7", headers=headers) is None
    )
    assert (
        ec_mod.presented_fingerprint(
            ec_mod.EdgeConfig(frozenset({"10.0.0.7"}), None), peer="10.0.0.7", headers=headers
        )
        is None
    )
    assert (
        ec_mod.presented_fingerprint(
            ec_mod.EdgeConfig(frozenset(), SECRET.encode()), peer="10.0.0.7", headers=headers
        )
        is None
    )

    class S:
        partner_mtls_proxies = ("10.0.0.7",)
        partner_mtls_proxy_secret_file = None
        partner_mtls_header = "X-Forwarded-Client-Cert"
        partner_mtls_secret_header = "X-Zoiko-Edge-Secret"

    with pytest.raises(
        RuntimeError, match="without"
    ):  # proxies named but no secret: believing the address alone is refused at start-up
        ec_mod.config_from_settings(S())
    short = tmp_path / "short"
    short.write_text("too-short", encoding="utf-8")
    S.partner_mtls_proxy_secret_file = str(short)  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="at least 32"):
        ec_mod.config_from_settings(S())
    ok = tmp_path / "ok"
    ok.write_text(SECRET + "\n", encoding="utf-8")
    S.partner_mtls_proxy_secret_file = str(ok)  # type: ignore[assignment]
    built = ec_mod.config_from_settings(S())
    assert (
        built.enabled
        and built.header == "x-forwarded-client-cert"
        and built.secret == SECRET.encode()
    )

    class Off:  # nothing configured at all
        pass

    assert not ec_mod.config_from_settings(Off()).enabled
