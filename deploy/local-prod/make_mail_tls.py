"""Make the throw-away CA and the mail relay's certificate for the local rehearsal (run inside the backend image).

Writes /zid/mail-ca.pem (what the backend trusts, through SSL_CERT_FILE), /zid/mail.crt and /zid/mail.key (what Mailpit serves).
A real deployment uses its SMTP provider's certificate and the system trust store instead.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

OUT = Path("/zid")
now = datetime.now(UTC)


def name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


ca_key = ec.generate_private_key(ec.SECP256R1())
ca = (
    x509.CertificateBuilder()
    .subject_name(name("Zoiko rehearsal mail CA"))
    .issuer_name(name("Zoiko rehearsal mail CA"))
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=365))
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
    .sign(ca_key, hashes.SHA256())
)
key = ec.generate_private_key(ec.SECP256R1())
cert = (
    x509.CertificateBuilder()
    .subject_name(name("mailpit"))
    .issuer_name(ca.subject)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("mailpit")]), critical=False)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256())
)
pem = serialization.Encoding.PEM
(OUT / "mail-ca.pem").write_bytes(ca.public_bytes(pem))
(OUT / "mail.crt").write_bytes(cert.public_bytes(pem))
(OUT / "mail.key").write_bytes(
    key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
)
for f in ("mail-ca.pem", "mail.crt", "mail.key"):
    (OUT / f).chmod(0o644)  # Mailpit runs as another user; this volume is a throw-away rehearsal volume
print("mail CA and certificate written")
