"""Cryptography policy controls (ZID-CRYPTO-001 acceptance CRYP-02, 16, 17, 19-23, 30).

Each test feeds a control the input its acceptance row describes and asserts the refusal or the proof.
"""

from __future__ import annotations

import copy
import inspect
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
import zid_crypto
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from zid_crypto import CryptoError, LocalSigner, sign_jws, verify_jws
from zid_crypto.ceremony import (
    KeyCeremony,
    QuorumPolicy,
    enroll,
    verify_ceremony_evidence,
)
from zid_crypto.fips import require_fips
from zid_crypto.policy import DEFAULT_PATH, CryptoPolicyRegistry, default_registry
from zid_crypto.pq import MlDsaSigner, sign_dual_jws, verify_dual_jws, verify_mldsa_jws
from zid_crypto.providers import KeyProvider, LocalKeyProvider
from zid_crypto.purposes import KeyPurpose
from zid_crypto.tls import client_context, minimum_version, server_context
from zid_crypto.tls_policy import default_mode, evaluate_group, hybrid_groups

TYP = "test+jws"


# ---------------------------------------------------------------- TLS version (CRYP-17)


def _self_signed(directory: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_file), str(key_file)


def _handshake(server: ssl.SSLContext, client: ssl.SSLContext) -> str:
    """Run a real TLS handshake over a socket pair; returns the negotiated version or the failure."""
    import socket

    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    done: list[str] = []

    def serve() -> None:
        try:
            with server.wrap_socket(b, server_side=True) as tls:
                done.append(str(tls.version()))
        except (ssl.SSLError, OSError) as e:
            done.append(f"server:{type(e).__name__}")

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with client.wrap_socket(a, server_hostname="localhost") as tls:
            result = str(tls.version())
    except (ssl.SSLError, OSError) as e:
        result = f"client:{type(e).__name__}"
    thread.join(5)
    return result


def test_a_new_endpoint_rejects_tls_below_1_3_and_a_legacy_endpoint_is_a_separate_policy(
    tmp_path: Path,
) -> None:
    """CRYP-17: TLS 1.2 is refused on a new endpoint; only an explicit legacy profile accepts it."""
    cert, key = _self_signed(tmp_path)
    assert minimum_version("new") is ssl.TLSVersion.TLSv1_3
    assert minimum_version("legacy") is ssl.TLSVersion.TLSv1_2

    tls12_client = client_context(cafile=cert, profile="legacy")
    tls12_client.maximum_version = ssl.TLSVersion.TLSv1_2
    new_server = server_context(cert, key, profile="new")
    assert new_server.minimum_version is ssl.TLSVersion.TLSv1_3
    assert _handshake(new_server, tls12_client).startswith("client:")  # refused, not downgraded

    legacy_server = server_context(cert, key, profile="legacy")
    assert _handshake(legacy_server, tls12_client) == "TLSv1.2"
    assert _handshake(new_server, client_context(cafile=cert)) == "TLSv1.3"
    with pytest.raises(CryptoError):
        minimum_version("anything-else")  # type: ignore[arg-type]


def test_a_client_context_always_verifies_the_chain_and_hostname(tmp_path: Path) -> None:
    """CRYP-17: there is no profile that switches certificate verification off."""
    cert, _ = _self_signed(tmp_path)
    for profile in ("new", "legacy"):
        ctx = client_context(cafile=cert, profile=profile)  # type: ignore[arg-type]
        assert ctx.check_hostname is True and ctx.verify_mode is ssl.CERT_REQUIRED


# ---------------------------------------------------------------- hybrid TLS (CRYP-19, CRYP-20)


def test_hybrid_group_negotiates_and_classical_fallback_is_blocked_when_required() -> None:
    """CRYP-19: the registry's hybrid groups pass; classical, unreported and pre-standard groups do not."""
    groups = hybrid_groups()
    assert {"x25519mlkem768", "secp256r1mlkem768"} <= groups
    ok = evaluate_group("X25519MLKEM768", mode="HYBRID_REQUIRED")
    assert ok.allowed and ok.hybrid and not ok.fallback
    for classical in ("x25519", "secp256r1", "", None):
        d = evaluate_group(classical, mode="HYBRID_REQUIRED")
        assert not d.allowed and d.code == "CRYPTO_HYBRID_REQUIRED", classical
    d = evaluate_group("X25519Kyber768Draft00", mode="HYBRID_REQUIRED")
    assert not d.allowed and d.code == "CRYPTO_GROUP_PRE_STANDARD"
    assert (
        evaluate_group("X25519Kyber768Draft00", mode="CLASSICAL").code
        == "CRYPTO_GROUP_PRE_STANDARD"
    )
    with pytest.raises(CryptoError):
        evaluate_group("x25519", mode="WHATEVER")


def test_a_preferred_mode_fallback_is_flagged_so_it_can_be_attributed() -> None:
    """CRYP-20: under HYBRID_PREFERRED a classical group is served and marked as a fallback; hybrid is not."""
    assert default_mode() == "HYBRID_PREFERRED"
    fallback = evaluate_group("x25519", mode="HYBRID_PREFERRED")
    assert fallback.allowed and fallback.fallback and not fallback.hybrid
    assert not evaluate_group("x25519mlkem768", mode="HYBRID_PREFERRED").fallback
    assert not evaluate_group(
        None, mode="HYBRID_PREFERRED"
    ).fallback  # unreported is not a counted fallback
    assert not evaluate_group("x25519", mode="CLASSICAL").fallback


# ---------------------------------------------------------------- ML-DSA and dual signatures (CRYP-21, CRYP-22)


def test_an_ml_dsa_evidence_signature_verifies_through_jose_and_tampering_does_not() -> None:
    """CRYP-21: an ML-DSA-65 JWS with an RFC 9964 AKP key verifies; a changed payload, key or header does not."""
    signer = MlDsaSigner("ev-pq-1", purpose=KeyPurpose.EVIDENCE_SIGNING)
    jwk = signer.public_jwk()
    assert jwk["kty"] == "AKP" and jwk["alg"] == "ML-DSA-65" and "priv" not in jwk
    token = sign_jws_pq(signer, {"receipt": "r1"})
    header, payload = verify_mldsa_jws(token, keys=[jwk], typ=TYP)
    assert header["alg"] == "ML-DSA-65" and payload == {"receipt": "r1"}
    head, body, sig = token.split(".")
    other = MlDsaSigner("ev-pq-1")
    for bad_keys, bad_token in (
        ([other.public_jwk()], token),  # another key under the same kid
        ([jwk], f"{head}.{body[:-2]}AA.{sig}"),  # altered payload
        ([{**jwk, "priv": "x"}], token),  # a public key must carry no private member
    ):
        with pytest.raises(CryptoError):
            verify_mldsa_jws(bad_token, keys=bad_keys, typ=TYP)
    with pytest.raises(CryptoError):
        verify_mldsa_jws(token, keys=[jwk], typ="other+jws")  # explicit typ is required


def sign_jws_pq(signer: MlDsaSigner, payload: dict[str, Any]) -> str:
    return sign_jws(signer, payload, typ=TYP)  # type: ignore[arg-type]


def test_a_dual_signed_record_verifies_for_a_classical_only_and_a_full_verifier() -> None:
    """CRYP-22: ES256 + ML-DSA evidence stays verifiable when the verifier knows only ES256, and for both."""
    classical, pq = (
        LocalSigner("ev-es-1", purpose=KeyPurpose.EVIDENCE_SIGNING),
        MlDsaSigner("ev-pq-1"),
    )
    keys = [classical.public_jwk(), pq.public_jwk()]
    doc = sign_dual_jws([classical, pq], {"receipt": "r1"}, typ=TYP)
    payload, algs = verify_dual_jws(
        doc, keys=keys, typ=TYP, supported=("ES256",), required=("ES256",)
    )
    assert payload == {"receipt": "r1"} and algs == ("ES256",)
    payload, algs = verify_dual_jws(
        doc, keys=keys, typ=TYP, supported=("ES256", "ML-DSA-65"), required=("ES256", "ML-DSA-65")
    )
    assert algs == ("ES256", "ML-DSA-65")
    # tampering the PQ signature is fatal for a verifier that supports it, and invisible to one that does not
    forged = copy.deepcopy(doc)
    sig = forged["signatures"][1]["signature"]
    forged["signatures"][1]["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(CryptoError):
        verify_dual_jws(
            forged, keys=keys, typ=TYP, supported=("ES256", "ML-DSA-65"), required=("ES256",)
        )
    verify_dual_jws(forged, keys=keys, typ=TYP, supported=("ES256",), required=("ES256",))
    with pytest.raises(CryptoError):  # a full verifier demands every algorithm its profile names
        verify_dual_jws(
            {**doc, "signatures": doc["signatures"][:1]},
            keys=keys,
            typ=TYP,
            supported=("ES256", "ML-DSA-65"),
            required=("ES256", "ML-DSA-65"),
        )


# ---------------------------------------------------------------- non-final algorithms (CRYP-23)


def test_a_non_final_algorithm_cannot_be_activated_in_the_production_baseline() -> None:
    """CRYP-23: HQC and FN-DSA are refused by the baseline registry loader; only a lab registry may name them."""
    document = yaml.safe_load(Path(DEFAULT_PATH).read_text(encoding="utf-8"))
    assert default_registry().known_algorithms().isdisjoint({"HQC", "FN-DSA", "FALCON-512"})
    for name in ("HQC-128", "FN-DSA-512", "FALCON-1024"):
        doc = copy.deepcopy(document)
        doc["profiles"]["evidence-signing.v1"]["allowed_algorithms"].append(name)
        with pytest.raises(CryptoError) as e:
            CryptoPolicyRegistry(doc)
        assert e.value.code == "CRYPTO_ALGORITHM_NOT_FINAL"
        lab = CryptoPolicyRegistry(
            doc, allow_non_final=True
        )  # lab only; never the production baseline
        assert lab.allowed(KeyPurpose.EVIDENCE_SIGNING, name)


# ---------------------------------------------------------------- FIPS-required operations (CRYP-16)


def test_a_fips_required_operation_runs_only_on_a_validated_module_with_a_fips_algorithm() -> None:
    """CRYP-16: a software provider, or an ecosystem-only algorithm, is refused when FIPS is required."""
    registry = default_registry()
    software = LocalKeyProvider(environment="test")

    class ValidatedKms:
        fips_validated = True

    assert require_fips(ValidatedKms(), KeyPurpose.TOKEN_SIGNING, "ES256", registry) == "ES256"
    with pytest.raises(CryptoError) as e:  # a validated module is needed, whatever the algorithm
        require_fips(software, KeyPurpose.TOKEN_SIGNING, "ES256", registry)
    assert e.value.code == "CRYPTO_FIPS_MODULE_REQUIRED"
    with pytest.raises(CryptoError) as e:  # EdDSA is listed only for non-FIPS ecosystems
        require_fips(ValidatedKms(), KeyPurpose.TOKEN_SIGNING, "Ed25519", registry)
    assert e.value.code == "CRYPTO_ALGORITHM_NOT_ALLOWED"
    assert registry.allowed(
        KeyPurpose.TOKEN_SIGNING, "Ed25519"
    )  # still fine when FIPS is not required
    assert not registry.allowed(KeyPurpose.TOKEN_SIGNING, "Ed25519", fips_required=True)
    assert not getattr(object(), "fips_validated", False)  # nothing is validated unless it says so


def test_a_kms_provider_is_validated_only_when_its_deployment_says_the_endpoint_is() -> None:
    """CRYP-16: the flag defaults to false, so a provider cannot be FIPS-validated by accident."""
    from zid_crypto.aws_kms import AwsKmsKeyProvider

    assert AwsKmsKeyProvider(object(), {}).fips_validated is False  # type: ignore[arg-type]
    assert AwsKmsKeyProvider(object(), {}, fips_endpoint=True).fips_validated is True  # type: ignore[arg-type]


# ---------------------------------------------------------------- key ceremony quorum (CRYP-30)


def _ceremony(required: int = 2) -> tuple[KeyCeremony, dict[str, LocalSigner]]:
    signers = {n: LocalSigner(f"cust-{n}") for n in ("a", "b", "c")}
    custodians = enroll([(n, "ROOT_CUSTODIAN", s) for n, s in signers.items()])
    ceremony = KeyCeremony(
        ceremony_id="cer-1",
        key_purpose="EVIDENCE",
        key_id="kms-evidence-1",
        environment="prod-uk-1",
        procedure_version="2026.1",
        custodians=custodians,
        quorum=QuorumPolicy(required, ("a", "b", "c")),
        started_at="2026-10-25T09:00:00Z",
        ended_at="2026-10-25T10:00:00Z",
        public_key_jwk=LocalSigner("generated").public_jwk(),
    )
    return ceremony, signers


def test_a_root_ceremony_needs_its_quorum_and_the_record_verifies_independently() -> None:
    """CRYP-30: no sealed record before M-of-N custodians attest; anyone can re-verify it afterwards."""
    ceremony, signers = _ceremony(2)
    ceremony.attest("a", signers["a"])
    with pytest.raises(CryptoError) as e:
        ceremony.finalize()
    assert e.value.code == "CRYPTO_CEREMONY_QUORUM_NOT_MET" or "QUORUM" in e.value.code
    ceremony.attest("b", signers["b"])
    evidence = ceremony.finalize()
    keys = {n: s.public_jwk() for n, s in signers.items()}
    assert verify_ceremony_evidence(evidence, keys) == ("a", "b")

    tampered = copy.deepcopy(evidence)
    tampered["record"]["key_id"] = "kms-other-key"
    with pytest.raises(CryptoError):
        verify_ceremony_evidence(tampered, keys)  # the digest no longer matches the record
    with pytest.raises(
        CryptoError
    ):  # one custodian's key unknown: only one attestation can be checked
        verify_ceremony_evidence(evidence, {"a": keys["a"]})
    stranger = LocalSigner("stranger")
    forged = copy.deepcopy(evidence)
    forged["attestations"] = {"a": evidence["attestations"]["a"], "x": ceremony._attestations["b"]}
    with pytest.raises(CryptoError):  # an attestation by someone outside the quorum does not count
        verify_ceremony_evidence(forged, {**keys, "x": stranger.public_jwk()})


def test_no_single_person_can_complete_a_ceremony() -> None:
    """CRYP-30: the quorum policy refuses M < 2, M > N and repeated custodians."""
    for required, ids in ((1, ("a", "b")), (3, ("a", "b")), (2, ("a", "a", "b"))):
        with pytest.raises(CryptoError):
            QuorumPolicy(required, ids)


# ---------------------------------------------------------------- private key material is not exportable (CRYP-02)


def test_no_interface_exports_private_key_material() -> None:
    """CRYP-02: signers publish public keys only; the key provider has no export; non-exportable is policy."""
    for signer in (LocalSigner("k"), MlDsaSigner("k")):
        jwk = signer.public_jwk()
        assert not {"d", "p", "q", "priv", "k"} & set(jwk)
    members = {name for name, _ in inspect.getmembers(KeyProvider) if not name.startswith("_")}
    assert not [m for m in members if "export" in m or "private" in m]
    public = {n for n in dir(zid_crypto) if not n.startswith("_")}
    assert not [n for n in public if "export" in n.lower() or "private" in n.lower()]
    document = yaml.safe_load(Path(DEFAULT_PATH).read_text(encoding="utf-8"))
    exports = {
        k: v.get("key_export_policy")
        for k, v in document["profiles"].items()
        if "key_export_policy" in v
    }
    assert exports and set(exports.values()) == {"NON_EXPORTABLE"}
    assert exports.keys() >= {"ca.v1"}


def test_the_signed_token_of_one_key_is_not_accepted_for_another_purpose() -> None:
    """CRYP-02 (companion): a public key set verifies only what its own private key signed."""
    a, b = LocalSigner("k1"), LocalSigner("k2")
    token = sign_jws(a, {"x": 1}, typ=TYP)
    verify_jws(token, keys=[a.public_jwk()], typ=TYP)
    with pytest.raises(CryptoError):
        verify_jws(token, keys=[b.public_jwk()], typ=TYP)


# ---------------------------------------------------------------- certificate automation (CRYP-27)


def _leaf_serial(server: ssl.SSLContext, cafile: str) -> int:
    """Handshake against `server` and return the serial of the certificate it presented."""
    import socket

    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    client = client_context(cafile=cafile)
    client.load_verify_locations(cafile)
    thread = threading.Thread(target=lambda: _serve_once(server, b))
    thread.start()
    with client.wrap_socket(a, server_hostname="localhost") as tls:
        der = tls.getpeercert(binary_form=True)
    thread.join(5)
    assert der is not None
    return x509.load_der_x509_certificate(der).serial_number


def _serve_once(server: ssl.SSLContext, sock: Any) -> None:
    try:
        with server.wrap_socket(sock, server_side=True):
            pass
    except (ssl.SSLError, OSError):
        pass


def test_a_renewed_certificate_is_served_without_a_restart_and_a_bad_pair_is_refused(
    tmp_path: Path,
) -> None:
    """CRYP-27: the renewed certificate and key are applied to the running context and serve the next handshake;
    a mismatched pair is refused and the serving pair stays; the unchanged files cause no reload."""
    from zid_crypto.tls import ReloadingServerContext, needs_renewal

    cert, key = _self_signed(tmp_path)
    first_serial = x509.load_pem_x509_certificate(Path(cert).read_bytes()).serial_number
    live = ReloadingServerContext(cert, key)
    context_before = live.context
    assert live.reload_if_changed() is False
    assert _leaf_serial(live.context, cert) == first_serial

    renewed = tmp_path / "renewed"
    renewed.mkdir()
    new_cert, new_key = _self_signed(renewed)  # the issuer rewrites the mounted files
    (tmp_path / "cert.pem").write_bytes(Path(new_cert).read_bytes())
    (tmp_path / "key.pem").write_bytes(Path(new_key).read_bytes())
    assert live.reload_if_changed() is True
    assert live.context is context_before  # the same listener context: no restart
    second_serial = x509.load_pem_x509_certificate(Path(new_cert).read_bytes()).serial_number
    assert second_serial != first_serial
    # the new certificate verifies against itself (it is self-signed), so trust it for the second handshake
    assert _leaf_serial(live.context, new_cert) == second_serial

    other_cert, _ = _self_signed(
        tmp_path / ".."
    )  # a certificate whose key does not match the key file
    (tmp_path / "cert.pem").write_bytes(Path(other_cert).read_bytes())
    with pytest.raises(ssl.SSLError):
        live.reload_if_changed()
    assert _leaf_serial(live.context, new_cert) == second_serial  # still serving the last good pair

    soon = datetime.now(UTC) + timedelta(minutes=50)
    assert (
        needs_renewal(new_cert, timedelta(minutes=30), now=soon) is True
    )  # inside the renewal window
    assert needs_renewal(new_cert, timedelta(minutes=30)) is False  # an hour of life left


def test_the_chart_renews_certificates_ahead_of_expiry_rotates_the_key_and_keeps_a_replica_up() -> (
    None
):
    """CRYP-27: cert-manager renews a month before expiry and replaces the private key every time; a
    disruption budget keeps one replica serving while pods take the new secret."""
    chart = Path(__file__).resolve().parents[4] / "deploy/helm/charts/zid-platform"
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    hours = {k: int(str(values["tls"][k]).rstrip("h")) for k in ("duration", "renewBefore")}
    assert hours["renewBefore"] >= 24 * 14 and hours["duration"] > 2 * hours["renewBefore"]
    certificate = (chart / "templates/certificate.yaml").read_text(encoding="utf-8")
    assert "kind: Certificate" in certificate and "rotationPolicy: Always" in certificate
    assert "renewBefore: {{ .Values.tls.renewBefore }}" in certificate
    assert (
        'required "tls.issuerRef.name' in certificate
    )  # no issuer, no certificate: it never renders silently
    budget = (chart / "templates/poddisruptionbudget.yaml").read_text(encoding="utf-8")
    assert "kind: PodDisruptionBudget" in budget and values["pdb"]["minAvailable"] >= 1
    assert values["replicas"] >= 2  # a budget of 1 is only meaningful with a second replica
