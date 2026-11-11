"""The local workload identity profile (local/workload, compose profile `workload`): development only, but the same design as the VM deployment.

These pin what a careless edit could weaken: the listener demands a SPIRE-signed client certificate and never passes the client's header on, the
attestor's private key and the engine socket are mounted read-only into the attestor alone, nothing is published beyond the loopback address, and
the private key and your digests can never be committed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[4]
COMPOSE = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
EDGE: dict[str, Any] = COMPOSE["workload-edge"]
ATTESTOR: dict[str, Any] = COMPOSE["attestor"]
CADDY = (REPO / "local" / "workload" / "Caddyfile").read_text(encoding="utf-8")


def test_nothing_starts_without_the_profile_and_nothing_is_published_beyond_loopback() -> None:
    assert EDGE["profiles"] == ["workload"] and ATTESTOR["profiles"] == ["workload"]
    assert EDGE["ports"] == ["127.0.0.1:9443:9443"]
    assert "ports" not in ATTESTOR


def test_the_listener_demands_a_spire_signed_certificate_and_replaces_the_forwarded_one() -> None:
    assert "mode require_and_verify" in CADDY and "trust_pool file /run/secrets/spire_bundle.pem" in CADDY
    assert 'header_up X-Forwarded-Client-Cert "Cert=\\"{http.request.tls.client.certificate_der_base64}\\""' in CADDY
    assert "certificate_pem" not in CADDY
    assert re.search(r"^\s*header_up -X-Forwarded-For\s*$", CADDY, flags=re.M), "uvicorn would otherwise rewrite the peer the cell trusts"
    assert "/v1/machine/attestations:verify" in CADDY and "respond 404" in CADDY
    assert "./local/spire/bundle.pem:/run/secrets/spire_bundle.pem:ro" in EDGE["volumes"]


def test_the_attestor_holds_its_own_key_and_a_read_only_engine_socket_and_no_other_secret() -> None:
    mounts = [str(v) for v in ATTESTOR["volumes"]]
    assert "./local/workload/.keys:/run/attestor:ro" in mounts
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in mounts
    assert "./local/workload/workloads.json:/etc/zid/workloads.json:ro" in mounts
    assert not any(".env" in m or "backend/.zid" in m or "secrets" in m for m in mounts), mounts
    assert "env_file" not in ATTESTOR
    cmd = [str(c) for c in ATTESTOR["command"]]
    assert "--registered-docker" in cmd and "--docker-socket=/var/run/docker.sock" in cmd
    assert any(c.startswith("--cell=http://backend:8000/") for c in cmd)  # the cell is the backend container, on its one port


def test_the_private_key_and_the_registration_with_your_digests_cannot_be_committed() -> None:
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "local/workload/.keys/" in ignore and "local/workload/workloads.json" in ignore


def test_the_example_registration_is_valid_for_the_attestor_and_names_the_tenant_in_the_identity() -> None:
    from zid_platform.attestor import registration

    entries = registration.load(REPO / "local" / "workload" / "workloads.example.json")
    assert entries and "/t/" in entries[0].spiffe_id and entries[0].approved_digests == frozenset(), "digests come from approved builds, not the example"


def test_the_setup_script_refuses_to_overwrite_a_signing_key() -> None:
    script = (REPO / "local" / "workload" / "setup.sh").read_text(encoding="utf-8")
    assert "refusing to overwrite a signing key" in script and "set -eu" in script


def test_the_listener_proves_itself_with_a_secret_only_it_and_the_cell_can_read() -> None:
    assert "header_up X-Zid-Proxy-Auth {file./run/secrets/proxy_auth}" in CADDY
    assert "./local/workload/.proxy/proxy_auth:/run/secrets/proxy_auth:ro" in EDGE["volumes"]
    # the attestor mounts the keys directory, never the secret's directory
    assert not any(".proxy" in str(v) for v in ATTESTOR["volumes"])
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "local/workload/.proxy/" in ignore
    script = (REPO / "local" / "workload" / "setup.sh").read_text(encoding="utf-8")
    assert "end=''" in script, "a trailing newline would make the header value invalid and the listener answer 502"
