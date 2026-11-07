"""SPIFFE ID parsing, X.509-SVID verification and mesh peer identity (DEC-F7)."""

from __future__ import annotations

import base64
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from zid_context.spiffe import SpiffeError, SpiffeId, TrustPolicy, peer_spiffe_id, verify_x509_svid

NOW = datetime.now(UTC)


def ca(name: str) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def svid(
    issuer: tuple[x509.Certificate, ec.EllipticCurvePrivateKey], uris: list[str], *, hours: int = 1
) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "payments-admin")]))
        .issuer_name(issuer[0].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(minutes=5))
        .not_valid_after(NOW + timedelta(hours=hours))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if uris:
        b = b.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(u) for u in uris]),
            critical=False,
        )
    return b.sign(issuer[1], hashes.SHA256()).public_bytes(Encoding.DER)


UK = ca("uk-1.prod.id.zoiko")
EU = ca("eu-1.prod.id.zoiko")
POLICY = TrustPolicy(
    "uk-1.prod.id.zoiko",
    bundles={
        "uk-1.prod.id.zoiko": [UK[0].public_bytes(Encoding.DER)],
        "eu-1.prod.id.zoiko": [EU[0].public_bytes(Encoding.DER)],
    },
    trusted_proxies=frozenset({"127.0.0.1"}),
)
WORKLOAD = "spiffe://uk-1.prod.id.zoiko/ns/prod/sa/payments"


@pytest.mark.parametrize(
    "bad",
    [
        "https://uk-1/ns/x",
        "spiffe://UK-1/ns/x",
        "spiffe://uk-1:443/ns/x",
        "spiffe://uk-1",
        "spiffe://uk-1/ns//x",
        "spiffe://uk-1/ns/../x",
        "spiffe://uk-1/ns/x?y=1",
        "spiffe://user@uk-1/ns/x",
    ],
)
def test_spiffe_ids_are_strict(bad: str) -> None:
    with pytest.raises(SpiffeError):
        SpiffeId.parse(bad)


def test_svid_verifies_under_its_own_bundle_only() -> None:
    sid = verify_x509_svid([svid(UK, [WORKLOAD])], POLICY)
    assert str(sid) == WORKLOAD and sid.trust_domain == "uk-1.prod.id.zoiko"
    # compromise of another cell's CA doesn't authenticate here (trust domain not federated)
    with pytest.raises(SpiffeError, match="SPIFFE_TRUST_DOMAIN_UNTRUSTED"):
        verify_x509_svid([svid(EU, ["spiffe://eu-1.prod.id.zoiko/ns/prod/sa/x"])], POLICY)
    # a certificate claiming this trust domain but signed by the other CA
    with pytest.raises(SpiffeError, match="SVID_CHAIN_INVALID"):
        verify_x509_svid([svid(EU, [WORKLOAD])], POLICY)
    with pytest.raises(SpiffeError, match="exactly one URI SAN"):
        verify_x509_svid([svid(UK, [WORKLOAD, WORKLOAD + "2"])], POLICY)
    with pytest.raises(SpiffeError, match="no SAN"):
        verify_x509_svid([svid(UK, [])], POLICY)  # never authorize from the CN
    with pytest.raises(SpiffeError, match="SVID_EXPIRED"):
        verify_x509_svid([svid(UK, [WORKLOAD])], POLICY, now=NOW + timedelta(hours=2))
    federated = TrustPolicy(
        POLICY.own_domain, POLICY.bundles, federated=frozenset({"eu-1.prod.id.zoiko"})
    )
    assert (
        verify_x509_svid([svid(EU, ["spiffe://eu-1.prod.id.zoiko/ns/a"])], federated).trust_domain
        == "eu-1.prod.id.zoiko"
    )


def test_peer_identity_comes_from_the_mesh_never_from_a_caller_header() -> None:
    der = svid(UK, [WORKLOAD])
    pem = x509.load_der_x509_certificate(der).public_bytes(Encoding.PEM).decode()
    direct = {"extensions": {"tls": {"client_cert_chain": [pem]}}, "client": ("10.0.0.9", 1234)}
    assert str(peer_spiffe_id(direct, {}, POLICY)) == WORKLOAD
    xfcc = {
        "x-forwarded-client-cert": f'By=spiffe://uk-1.prod.id.zoiko/ns/prod/sa/zid;Cert="{urllib.parse.quote(pem)}"'
    }
    assert str(peer_spiffe_id({"client": ("127.0.0.1", 1)}, xfcc, POLICY)) == WORKLOAD
    with pytest.raises(SpiffeError, match="SVID_MISSING"):  # not from the local mesh proxy
        peer_spiffe_id({"client": ("10.0.0.9", 1)}, xfcc, POLICY)
    with pytest.raises(SpiffeError, match="SVID_MISSING"):
        peer_spiffe_id({"client": ("127.0.0.1", 1)}, {"x-spiffe-id": WORKLOAD}, POLICY)


def test_a_proxy_may_forward_the_certificate_as_base64_der_and_it_is_verified_just_the_same() -> None:
    """Caddy cannot put a PEM in a header (raw newlines), so it forwards base64 DER. The form changes how bytes are read, not what is trusted."""
    der = svid(UK, [WORKLOAD])
    proxy = {"client": ("127.0.0.1", 1)}
    ok = {"x-forwarded-client-cert": f'Cert="{base64.b64encode(der).decode()}"'}
    assert str(peer_spiffe_id(proxy, ok, POLICY)) == WORKLOAD
    other = ca("other")
    foreign = {"x-forwarded-client-cert": f'Cert="{base64.b64encode(svid(other, [WORKLOAD])).decode()}"'}
    with pytest.raises(SpiffeError, match="SVID_CHAIN_INVALID"):  # a readable certificate from a CA we do not trust
        peer_spiffe_id(proxy, foreign, POLICY)
    with pytest.raises(SpiffeError, match="SVID_MISSING"):  # not from the proxy
        peer_spiffe_id({"client": ("10.0.0.9", 1)}, ok, POLICY)


@pytest.mark.parametrize("junk", ['Cert="forged"', 'Cert=""', 'Cert="AAAA"', 'Cert="%%%"', "Cert=-----BEGIN CERTIFICATE-----junk"])
def test_an_unreadable_forwarded_certificate_is_no_certificate_never_a_server_error(junk: str) -> None:
    with pytest.raises(SpiffeError, match="SVID_MISSING"):
        peer_spiffe_id({"client": ("127.0.0.1", 1)}, {"x-forwarded-client-cert": junk}, POLICY)
