"""Threat-model controls provable without a database (ZID-SEC-001 §15, §28: SEC-17, SEC-19).

SEC-17 secret leakage: no production-shaped private key, token or credential is committed, and the
telemetry path scrubs one if it ever reaches a log line or span attribute.
SEC-19 SSRF: every server-side fetch refuses metadata, loopback, private, reserved and other
non-public destinations, however the address is spelled, and never follows a redirect.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import socket
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from zid_platform import egress
from zid_platform.modules.credential.application import verifier
from zid_platform.modules.federation.application import oidc_login
from zid_platform.modules.federation.domain.connection import Connection, FederationError
from zid_platform.modules.tenant.application.domains import https_fetcher
from zid_platform.modules.webhook.domain.webhooks import (
    WebhookError,
    check_resolution,
    validate_url,
)
from zid_telemetry import JsonFormatter
from zid_telemetry.redaction import REDACTED, redact

REPO = Path(__file__).resolve().parents[4]

# --------------------------------------------------------------------------- SEC-17 secret scan

# A secret has to be built at runtime in the self-test: a literal here would be a finding itself.
_PEM_HEAD = "-----BEGIN " + "PRIVATE KEY-----"
PATTERNS = {
    "pem-private-key": re.compile(
        r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{40,}"
    ),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "stripe-live-key": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.(eyJ[A-Za-z0-9_-]{10,})\.[A-Za-z0-9_-]{10,}"),
    "quoted-credential": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*"
        r"['\"](?![A-Za-z_]*(?:test|example|dummy|changeme|placeholder|local)[A-Za-z_-]*['\"])"
        r"[A-Za-z0-9/+_-]{24,}['\"]"
    ),
}
SKIP_PARTS = {"node_modules", ".venv", ".git", "__pycache__", ".next"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf", ".lock", ".sum"}
# A JWT is a finding only when its payload carries real-token claims; a synthetic fixture that
# decodes to `{"sub": "1234567890"}` is documentation, not a credential.
TOKEN_CLAIMS = ("iss", "exp", "aud", "tenant_id", "cnf", "scope", "client_id")


def _jwt_has_claims(payload_b64: str) -> bool:
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    except ValueError:
        return True  # undecodable: treat as a finding
    return isinstance(claims, dict) and any(c in claims for c in TOKEN_CLAIMS)


def scan_text(text: str) -> list[str]:
    found = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            line = text[: m.start()].count("\n") + 1
            if "zid-secret-scan: allow" in text.splitlines()[line - 1]:
                continue
            if name == "jwt" and not _jwt_has_claims(m.group(1)):
                continue
            found.append(f"{name} at line {line}")
    return found


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "--cached"],
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available to enumerate tracked files")
    return [REPO / p for p in out.split("\0") if p and (REPO / p).is_file()]


def test_no_production_secret_is_committed() -> None:
    """SEC-17: repository scan finds no production private key, token or secret fixture."""
    findings: list[str] = []
    scanned = 0
    for path in tracked_files():
        rel = path.relative_to(REPO)
        if path.suffix.lower() in SKIP_SUFFIX or SKIP_PARTS & set(rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        findings += [f"{rel.as_posix()}: {f}" for f in scan_text(text)]
    assert scanned > 500, "the scan must cover the repository"
    assert not findings, (
        "credential-shaped strings are committed (treat as COMPROMISED: rotate, then remove):\n"
        + "\n".join(findings)
    )


def test_the_scanner_recognises_each_secret_shape() -> None:
    """The scan is not vacuous: every class it hunts for is detected when present."""
    body = base64.b64encode(b"k" * 60).decode()
    claims = base64.urlsafe_b64encode(b'{"iss":"https://id","exp":4102444800}').decode().rstrip("=")
    samples = {
        "pem-private-key": f"{_PEM_HEAD}\n{body}\n-----END PRIVATE KEY-----",
        "aws-access-key": "AKIA" + "ABCDEFGHIJKLMNOP",
        "github-token": "ghp_" + "a" * 36,
        "slack-token": "xoxb-" + "1" * 12 + "-abc",
        "stripe-live-key": "sk_live_" + "a" * 20,
        "google-api-key": "AIza" + "a" * 35,
        "jwt": f"eyJhbGciOiJFUzI1NiJ9.{claims}.{'s' * 20}",
        "quoted-credential": 'api_key = "' + "Zx9" * 10 + '"',
    }
    assert set(samples) == set(PATTERNS)
    for name, sample in samples.items():
        assert any(f.startswith(name) for f in scan_text(sample)), name
    assert scan_text(f"{_PEM_HEAD}  # zid-secret-scan: allow (synthetic fixture)") == []


def test_secrets_are_scrubbed_from_logs_and_trace_attributes() -> None:
    """SEC-17: a token, key or credential that reaches a log line or span attribute is redacted."""
    jwt = (
        "eyJhbGciOiJFUzI1NiJ9." + "eyJzdWIiOiJ4IiwiZXhwIjo0MTAyNDQ0ODAwfQ" + ".c2lnbmF0dXJlLXZhbHVl"
    )
    secrets = [jwt, "abcdefghijklmnop123456", "hunter2-correct-horse", "s3cr3t-value"]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("svc", "uk-1"))
    log = logging.getLogger("sec17")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    log.info(
        f"auth failed Authorization: Bearer {secrets[1]} token={jwt} "
        f"password={secrets[2]} client_secret: {secrets[3]} {_PEM_HEAD} MIIE"
    )
    line = stream.getvalue()
    assert REDACTED in line
    for needle in (*secrets, "BEGIN PRIVATE"):
        assert needle not in line
    attrs = redact(
        {"http.route": "/v1/x", "authorization": f"Bearer {secrets[1]}", "zoiko.reason_code": jwt}
    )
    assert attrs["zoiko.reason_code"] == REDACTED and "authorization" not in attrs


# ------------------------------------------------------------------------------ SEC-19 SSRF

BLOCKED_ADDRESSES = [
    "127.0.0.1",
    "127.1.2.3",
    "10.0.0.5",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata
    "169.254.170.2",  # ECS task metadata
    "100.64.0.1",  # CGNAT
    "0.0.0.0",
    "224.0.0.1",
    "240.0.0.1",
    "198.18.0.1",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd00:ec2::254",  # AWS IPv6 metadata
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "ff02::1",
]
ALLOWED_ADDRESSES = ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"]
BLOCKED_URLS = [
    "http://example.com/hook",  # not https
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1/",
    "https://[::1]/",
    "https://[::ffff:127.0.0.1]/",
    "https://2130706433/",  # decimal 127.0.0.1
    "https://0x7f000001/",  # hex 127.0.0.1
    "https://0177.0.0.1/",  # octal
    "https://user:pass@example.com/",  # userinfo
    "https://example.com#frag",
    "https://example.com:22/",  # non-standard port
    "https://example.com:6379/",
    "ftp://example.com/",
    "file:///etc/passwd",
    "gopher://example.com/",
    "https:///no-host",
    "https://example.com\\@evil.test/",
    "https://exa mple.com/",
    "https://example.com/\r\nHost: evil.test",
    "https://metadata.google.internal/",
    "https://service.local/",
    "",
]
ALLOWED_URLS = ["https://hooks.example.com/zid", "https://idp.example.org:8443/token"]


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert not egress.address_allowed(address)
    with pytest.raises(egress.EgressError):
        egress.check_resolution([address])


@pytest.mark.parametrize("address", ALLOWED_ADDRESSES)
def test_public_addresses_are_allowed(address: str) -> None:
    assert egress.address_allowed(address)
    egress.check_resolution([address])


def test_one_private_record_poisons_the_whole_name() -> None:
    """DNS rebinding: a name with a public and a private record is refused, as is an empty answer."""
    with pytest.raises(egress.EgressError):
        egress.check_resolution(["93.184.216.34", "10.0.0.1"])
    with pytest.raises(egress.EgressError):
        egress.check_resolution([])


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_dangerous_urls_are_refused(url: str) -> None:
    with pytest.raises(egress.EgressError):
        egress.check_url(url)
    if url.startswith(("http://", "https://")) and "\\" not in url and " " not in url:
        with pytest.raises(WebhookError):  # the webhook domain applies the same rules
            validate_url(url)


@pytest.mark.parametrize("url", ALLOWED_URLS)
def test_ordinary_https_urls_pass(url: str) -> None:
    assert egress.check_url(url).startswith("https://")


def test_the_dns_step_resolves_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves to the metadata service is refused even though its name looks harmless."""
    answers: dict[str, list[str]] = {
        "rebind.example.com": ["169.254.169.254"],
        "mixed.example.com": ["93.184.216.34", "10.1.1.1"],
        "ok.example.com": ["93.184.216.34"],
    }

    def fake(host: str, *_a: Any, **_k: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in answers[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    async def run() -> None:
        for host in ("rebind.example.com", "mixed.example.com"):
            with pytest.raises(egress.EgressError, match="EGRESS_DESTINATION_BLOCKED"):
                await egress.guard(f"https://{host}/x")
        assert await egress.guard("https://ok.example.com/x") == "https://ok.example.com/x"
        # the name is looked up on every call (no cached verdict a rebinding record can outlive)
        answers["ok.example.com"] = ["127.0.0.1"]
        with pytest.raises(egress.EgressError):
            await egress.guard("https://ok.example.com/x")

    asyncio.run(run())


class Recorder(httpx.AsyncBaseTransport):
    """An origin that records every request: a blocked fetch must never reach it."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return httpx.Response(200, text="token", request=request)


def test_domain_verification_fetch_refuses_private_targets_and_follows_no_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claimed domain that resolves to the metadata service is never fetched."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )

    async def run() -> None:
        origin = Recorder()
        fetch = https_fetcher(None)  # the real network path: DNS guard applies
        assert await fetch("https://claimed.example.com/.well-known/x") is None
        assert origin.seen == []
        # an injected transport (tests, sovereign egress proxy) skips only DNS, never the URL rules
        proxied = https_fetcher(origin)
        assert await proxied("https://169.254.169.254/x") is None
        assert await proxied("http://claimed.example.com/x") is None
        assert origin.seen == []
        assert await proxied("https://claimed.example.com/x") == "token"

    asyncio.run(run())


def test_oidc_token_endpoint_to_the_metadata_service_is_refused_before_any_request() -> None:
    """SEC-19 / SEC-AC-10: the token POST (which carries client credentials) is never sent to a
    private or metadata address, whether the URL came from tenant config or remote metadata."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from zid_crypto import KeyRing, LocalSigner

    async def run() -> None:
        origin = Recorder()
        settings = oidc_login.FederationSettings(
            issuer="https://id.zoiko.test",
            keyring=KeyRing(LocalSigner("t")),
            home_cell="uk-1",
            secrets=lambda _ref: "client-secret",
            transport=origin,
        )
        base = Connection(
            connection_id="c",
            tenant_id="t",
            protocol="OIDC",
            display_name="x",
            issuer="https://idp.example.com",
            status="ACTIVE",
            config_revision=1,
            metadata_expires_at=datetime.now(UTC),
            acr_levels={},
            trust_amr=True,
            link_policy="EXISTING_ONLY",
            client_id="cid",
            client_auth="client_secret_basic",
            client_secret_ref="ref",
        )
        login: Any = SimpleNamespace(code_verifier="v")
        for endpoint in (
            "https://169.254.169.254/token",
            "http://idp.example.com/token",
            "https://127.0.0.1/token",
            "https://localhost:6379/token",
            "https://10.1.2.3/token",
        ):
            c = replace(base, token_endpoint=endpoint)
            with pytest.raises(FederationError) as e:
                await oidc_login._exchange(settings, c, login, "code", datetime.now(UTC))
            assert e.value.code == "FEDERATION_ENDPOINT_BLOCKED", endpoint
        assert origin.seen == []  # nothing, credentials included, left the process

        class Ok(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"id_token": "x"}, request=request)

        good = replace(base, token_endpoint="https://idp.example.com/token")
        out = await oidc_login._exchange(
            replace(settings, transport=Ok()), good, login, "code", datetime.now(UTC)
        )
        assert out == {"id_token": "x"}

    asyncio.run(run())


def test_status_list_fetches_refuse_private_hosts() -> None:
    """SEC-19: a wallet-supplied status-list URL (mdoc) is checked before any request is made."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    async def run() -> None:
        origin = Recorder()
        settings: Any = SimpleNamespace(transport=origin)
        real = verifier.status_reference
        try:
            for uri in (
                "https://169.254.169.254/statuslists/1",
                "https://127.0.0.1/statuslists/1",
                "http://issuer.example.com/statuslists/1",
                "https://issuer.example.com:6379/statuslists/1",
            ):
                verifier.status_reference = lambda _mso, uri=uri: (1, uri)  # type: ignore[assignment]
                out = await verifier._mdoc_status(settings, {}, [], datetime.now(UTC))
                assert out == "UNKNOWN", uri
        finally:
            verifier.status_reference = real  # type: ignore[assignment]
        assert origin.seen == []

    asyncio.run(run())


def test_webhook_resolution_rules_match_the_shared_guard() -> None:
    """The two implementations agree on every address (no drift between webhook and egress)."""
    for address in BLOCKED_ADDRESSES:
        with pytest.raises(WebhookError):
            check_resolution([address])
    for address in ALLOWED_ADDRESSES:
        check_resolution([address])


def test_p47_no_application_log_call_names_a_card_number_or_security_code() -> None:
    """ZID-UX-PRICE-004 P47: the card is entered on the processor's hosted page, so no log call in the application names one."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    call = re.compile(
        r"\b(?:log|logger|logging|_log|_logger)\.(?:debug|info|warning|warn|error|exception|critical)\("
    )
    card = re.compile(r"(?i)\b(cvc2?|cvv2?|card_?number|card_?no|pan|security_?code|card\[)\b")
    hits = [
        f"{p.relative_to(src)}:{n}"
        for p in src.rglob("*.py")
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if call.search(ln) and card.search(ln)
    ]
    assert hits == []


def test_a_sealed_client_secret_is_the_one_sent_to_the_provider_and_the_store_is_not_asked() -> (
    None
):
    """CR-138: when the company entered the secret in the console, the exchange uses that sealed value; the operator's secret store (`settings.secrets`) is never consulted for it."""
    import base64
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from zid_crypto import KeyRing, LocalSigner

    async def run() -> None:
        seen: dict[str, str] = {}

        class Capture(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                seen["authorization"] = request.headers.get("authorization", "")
                return httpx.Response(200, json={"id_token": "x"}, request=request)

        def never(_ref: str) -> str:
            raise AssertionError(
                "the operator's secret store must not be asked for a sealed secret"
            )

        settings = oidc_login.FederationSettings(
            issuer="https://id.zoiko.test",
            keyring=KeyRing(LocalSigner("t")),
            home_cell="uk-1",
            secrets=never,
            transport=Capture(),
        )
        c = Connection(
            connection_id="c", tenant_id="t", protocol="OIDC", display_name="x", issuer="https://idp.example.com", status="ACTIVE", config_revision=1,
            metadata_expires_at=datetime.now(UTC), acr_levels={}, trust_amr=True, link_policy="EXISTING_ONLY", client_id="cid",
            client_auth="client_secret_basic", client_secret_ref="connector:abc", token_endpoint="https://idp.example.com/token",
        )  # fmt: skip
        login: Any = SimpleNamespace(code_verifier="v")
        out = await oidc_login._exchange(
            settings, c, login, "code", datetime.now(UTC), client_secret="sealed-secret"
        )
        assert out == {"id_token": "x"}
        assert seen["authorization"] == "Basic " + base64.b64encode(b"cid:sealed-secret").decode()

    asyncio.run(run())
