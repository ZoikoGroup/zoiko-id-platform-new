"""Hybrid TLS enforcement and downgrade telemetry at the application edge (ZID-CRYPTO-001 CRYP-19, CRYP-20).

The terminator reports the key-exchange group it negotiated in a header. Paths marked required are refused
without a hybrid group; elsewhere a classical fallback is served, counted and attributable.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from zid_platform.main import create_app
from zid_platform.settings import Settings
from zid_platform.tls_guard import (
    FALLBACKS,
    REFUSED,
    client_agent,
    endpoint_label,
    install_tls_group_guard,
    judge,
)


@pytest.fixture
def client() -> TestClient:
    settings = Settings(tls_hybrid_required_prefixes=("/oauth2", "/admin"))
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_a_required_endpoint_refuses_every_connection_without_a_hybrid_group(
    client: TestClient,
) -> None:
    """CRYP-19: classical, pre-standard and unreported key exchanges get 426 on a HYBRID_REQUIRED path."""
    before = REFUSED.value(endpoint="oauth2")
    for group in ("x25519", "secp256r1", "X25519Kyber768Draft00", None):
        headers = {"x-tls-group": group} if group else {}
        r = client.get("/oauth2/authorize", headers=headers)
        assert r.status_code == 426, group
        assert r.json()["error"]["code"] in {"CRYPTO_HYBRID_REQUIRED", "CRYPTO_GROUP_PRE_STANDARD"}
        assert r.headers["upgrade"] == "TLS/1.3"
    assert REFUSED.value(endpoint="oauth2") == before + 4
    served = client.get("/oauth2/authorize", headers={"x-tls-group": "X25519MLKEM768"})
    assert served.status_code != 426  # the hybrid group reaches the application


def test_a_fallback_on_a_preferred_endpoint_is_served_counted_and_attributed(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """CRYP-20: HYBRID_PREFERRED serves a classical group and records which endpoint and client used it."""
    before = FALLBACKS.value(endpoint="healthz", client_agent="curl")
    with caplog.at_level(logging.WARNING, logger="zid.tls"):
        r = client.get("/healthz", headers={"x-tls-group": "x25519", "user-agent": "curl/8.5.0"})
    assert r.status_code != 426
    assert FALLBACKS.value(endpoint="healthz", client_agent="curl") == before + 1
    record = next(r for r in caplog.records if r.name == "zid.tls")
    assert (record.endpoint, record.client_agent, record.tls_group) == ("healthz", "curl", "x25519")  # type: ignore[attr-defined]
    # a hybrid connection, and one that did not report a group, are not fallbacks
    client.get("/healthz", headers={"x-tls-group": "x25519mlkem768", "user-agent": "curl/8.5.0"})
    client.get("/healthz", headers={"user-agent": "curl/8.5.0"})
    assert FALLBACKS.value(endpoint="healthz", client_agent="curl") == before + 1


def test_without_required_prefixes_or_a_group_header_nothing_changes() -> None:
    """CRYP-19: the guard is inert until the deployment names required endpoints."""
    plain = TestClient(create_app(Settings()), raise_server_exceptions=False)
    assert plain.get("/oauth2/authorize").status_code != 426
    assert judge("/oauth2/x", None, (), "HYBRID_PREFERRED").allowed


def test_labels_are_bounded_and_carry_no_identifier() -> None:
    """CRYP-20: the metric labels are a path segment and a product token, never a URL identifier or full agent."""
    assert endpoint_label("/oauth2/token") == "oauth2" and endpoint_label("/") == "root"
    assert endpoint_label("/" + "x" * 80) == "x" * 32
    assert client_agent("curl/8.5.0 (linux)") == "curl"
    assert client_agent("Mozilla/5.0 (X11)") == "mozilla"
    assert client_agent(None) == "unknown" and client_agent("") == "unknown"
    assert install_tls_group_guard  # exported for the app factory
