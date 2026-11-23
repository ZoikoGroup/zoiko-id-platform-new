"""A small RFC 3161 timestamp authority over HTTP, built on `openssl ts` (see the Dockerfile for what it is and is not).

    POST /          body: DER TimeStampReq, Content-Type: application/timestamp-query  ->  DER TimeStampResp (application/timestamp-reply)
    GET  /ca.pem    the root a token chains to (what a cell's ZID_EVIDENCE_TSA_TRUST_FILE holds)
    GET  /health    200 once the authority has its keys

On first start it makes a root and a signing certificate (timeStamping, critical) in /data, then removes the root's private key.
A request is answered under a lock because `openssl ts` increments one serial file; each answer gets the next serial.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("TSA_DATA", "/data"))
CONF = Path(os.environ.get("TSA_CONF", "/app/tsa.cnf"))
MAX_REQUEST = 4096  # a TimeStampReq is a few dozen bytes
LOCK = threading.Lock()


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def ensure_pki() -> None:
    if (DATA / "tsa.crt").exists() and (DATA / "ca.pem").exists():
        return
    ca_key, ca_crt = DATA / "ca.key", DATA / "ca.pem"
    run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(ca_key), "-out", str(ca_crt),
        "-days", "3650", "-subj", "/CN=Zoiko iD rehearsal timestamp root", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")  # fmt: skip
    run("openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(DATA / "tsa.key"), "-out", str(DATA / "tsa.csr"),
        "-subj", "/CN=Zoiko iD rehearsal timestamp authority")  # fmt: skip
    ext = DATA / "tsa.ext"
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,timeStamping\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n"
    )
    run("openssl", "x509", "-req", "-in", str(DATA / "tsa.csr"), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(DATA / "tsa.crt"),
        "-days", "825", "-extfile", str(ext))  # fmt: skip
    (DATA / "chain.pem").write_bytes(ca_crt.read_bytes())
    if not (DATA / "tsaserial").exists():
        (DATA / "tsaserial").write_text("01\n")
    for leftover in (ca_key, DATA / "tsa.csr", ext, DATA / "ca.srl"):
        leftover.unlink(missing_ok=True)  # the root's private key does not stay on the machine that signs timestamps
    (DATA / "tsa.key").chmod(0o600)


def reply_to(query: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp, LOCK:
        q, r = Path(tmp) / "q.tsq", Path(tmp) / "r.tsr"
        q.write_bytes(query)
        run("openssl", "ts", "-reply", "-config", str(CONF), "-section", "tsa_config1", "-queryfile", str(q), "-out", str(r))
        return r.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "zid-rehearsal-tsa"

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the http.server method name
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        elif self.path == "/ca.pem":
            self._send(200, (DATA / "ca.pem").read_bytes(), "application/x-pem-file")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/timestamp-query":
            return self._send(415, b"expected application/timestamp-query", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, b"bad length", "text/plain")
        if not 0 < length <= MAX_REQUEST:
            return self._send(413, b"request too large", "text/plain")
        try:
            body = reply_to(self.rfile.read(length))
        except subprocess.CalledProcessError:
            return self._send(400, b"not a time-stamp request this authority will sign", "text/plain")
        self._send(200, body, "application/timestamp-reply")

    def log_message(self, fmt: str, *args: object) -> None:  # one line per request, no body
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    ensure_pki()
    print("timestamp authority ready; root at /ca.pem", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8318), Handler).serve_forever()
