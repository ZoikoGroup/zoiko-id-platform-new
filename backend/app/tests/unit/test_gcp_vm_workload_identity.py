"""Workload identity on the GCP VM deployment (deploy/gcp-vm, README section 9): the properties that must survive later edits.

The flow itself was run end to end against real SPIRE, Caddy and Docker (see CR-063); these tests pin the parts a careless edit could
quietly weaken: what the attestor can read, what the workload listener demands, which header is trusted, what is pinned."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[4] / "deploy" / "gcp-vm"
PROFILE = ["workload-identity"]


def _compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))["services"]


APP = _compose("docker-compose.yml")
SERVICES = _compose("docker-compose.services.yml")
NEW = {
    "spire-agent": APP["spire-agent"],
    "attestor": APP["attestor"],
    "workload-edge": APP["workload-edge"],
    "spire-server": SERVICES["spire-server"],
}


def _text(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_nothing_new_starts_unless_the_profile_is_asked_for() -> None:
    for name, svc in NEW.items():
        assert svc.get("profiles") == PROFILE, name


def test_every_new_image_is_an_exact_version_never_latest() -> None:
    for name, svc in NEW.items():
        image = str(svc["image"])
        if image.startswith("${"):  # the platform image: a signed digest chosen by the release workflow
            assert image == "${ZID_BACKEND_IMAGE}", name
            continue
        assert re.search(r":\d+\.\d+", image) and ":latest" not in image, f"{name}: {image}"


def test_the_attestor_holds_none_of_the_cells_secrets() -> None:
    a = NEW["attestor"]
    assert "env_file" not in a and "ports" not in a
    mounts = [str(v) for v in a["volumes"]]
    assert not any(m.startswith("./secrets") or "/run/secrets" in m for m in mounts), mounts  # no master key, token or SMTP password
    assert "./attestor-secrets:/run/attestor:ro" in mounts
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in mounts  # read-only socket: it can ask, not start, stop or change
    assert set(a.get("environment", {})) == set(), "the attestor takes no settings from the environment"
    cmd = [str(c) for c in a["command"]]
    assert "--docker-socket=/var/run/docker.sock" in cmd and "--registered-docker" in cmd
    assert not any("kms" in c.lower() for c in cmd), "VM-only: no KMS"


def test_the_cell_gets_the_attestors_public_key_and_never_its_private_key() -> None:
    env = _text("docker-compose.yml")
    assert "ZID_ATTESTOR_KEYS_FILE: ${ZID_ATTESTOR_KEYS_FILE:-}" in env
    assert "attestor.pem" not in env.replace("/run/attestor/attestor.pem", "").replace("--key=", "")  # only the attestor's own command names it
    assert not any("attestor-secrets" in str(v) for v in APP["cell"].get("volumes", []))


def test_the_workload_listener_needs_a_certificate_signed_by_this_cells_spire_ca() -> None:
    caddy = _text("Caddyfile.workload")
    assert "mode require_and_verify" in caddy
    assert "trust_pool file /run/secrets/spire_bundle.pem" in caddy
    assert caddy.count("https://") == 1 and ":9443" in caddy, "one listener, one port"


def test_the_listener_sets_the_forwarded_certificate_itself_and_never_passes_the_clients() -> None:
    caddy = _text("Caddyfile.workload")
    # header_up with a value REPLACES whatever the client sent; there is no pass-through
    assert 'header_up X-Forwarded-Client-Cert "Cert=\\"{http.request.tls.client.certificate_der_base64}\\""' in caddy
    assert "certificate_pem" not in caddy, "a PEM holds raw newlines a header cannot carry (it made the proxy answer 502)"
    assert "/v1/machine/attestations:verify" in caddy and "respond 404" in caddy


def test_every_public_site_drops_the_forwarded_certificate_header() -> None:
    caddy = _text("Caddyfile")
    assert re.search(r"^\s*request_header -X-Forwarded-Client-Cert\s*$", caddy, flags=re.M)
    sites = re.findall(r"^([a-z]+\.\{\$ZID_DOMAIN\}) \{", caddy, flags=re.M)
    assert sites == ["id.{$ZID_DOMAIN}", "api.{$ZID_DOMAIN}", "global.{$ZID_DOMAIN}", "operator.{$ZID_DOMAIN}"]
    for site in sites:
        body = caddy.split(f"{site} {{", 1)[1].split("\n}\n", 1)[0]
        assert re.search(r"^\s*import no_forwarded_cert\s*$", body, flags=re.M), f"{site} would pass a client-set certificate header to the cell"
    api = caddy.split("api.{$ZID_DOMAIN} {", 1)[1].split("\n}\n", 1)[0]
    assert "/v1/machine/attestations:verify" in api and "respond 404" in api  # attestations come from the attestor, not the internet


def test_the_listener_has_the_one_address_the_cell_trusts_and_is_private_by_default() -> None:
    edge = NEW["workload-edge"]
    assert edge["networks"]["default"]["ipv4_address"] == "172.30.0.250"
    assert APP["cell"] is not None and "172.30.0.250" in _text("env.example")
    assert edge["ports"] == ["${ZID_WORKLOAD_BIND:-127.0.0.1}:9443:9443"]
    subnet = yaml.safe_load(_text("docker-compose.yml"))["networks"]["default"]["ipam"]["config"][0]["subnet"]
    assert subnet == "172.30.0.0/24"


def test_the_cell_defaults_are_valid_json_so_an_empty_setting_cannot_stop_it_starting() -> None:
    env = _text("docker-compose.yml")
    assert "ZID_SPIFFE_BUNDLE_FILES: ${ZID_SPIFFE_BUNDLE_FILES:-{}}" in env
    assert "ZID_MESH_TRUSTED_PROXIES: ${ZID_MESH_TRUSTED_PROXIES:-[]}" in env


def test_the_agent_pins_the_servers_bundle_and_does_not_trust_the_first_server_it_meets() -> None:
    agent = _text("spire/agent.conf")
    assert not re.search(r"^\s*insecure_bootstrap\s*=\s*true", agent, flags=re.M)
    assert re.search(r'^\s*trust_bundle_path\s*=\s*"/run/secrets/spire_bundle.pem"', agent, flags=re.M)
    assert NEW["spire-agent"]["pid"] == "host"
    assert "-expandEnv" in NEW["spire-agent"]["command"]


def test_the_server_keeps_its_ca_across_restarts_and_names_its_trust_domain_from_the_environment() -> None:
    server = _text("spire/server.conf")
    assert 'KeyManager "disk"' in server and 'KeyManager "memory"' not in server
    assert 'trust_domain = "${ZID_SPIFFE_TRUST_DOMAIN}"' in server
    assert "-expandEnv" in NEW["spire-server"]["command"]
    assert NEW["spire-server"]["ports"] == ["${ZID_SERVICES_BIND:-0.0.0.0}:8081:8081"]


def test_no_secret_value_is_written_into_any_new_file() -> None:
    files = ["Caddyfile.workload", "spire/server.conf", "spire/agent.conf", "workload-identity.sh", "attestor/workloads.example.json"]
    for f in files:
        text = _text(f)
        assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", text), f
        assert not re.search(r"(?i)(password|token|secret)\s*[:=]\s*[\"']?[A-Za-z0-9+/]{16,}", text), f
    assert _text("spire/agent.conf").count("${ZID_SPIRE_JOIN_TOKEN}") == 1  # the token is read from the environment, not stored


def test_the_registration_example_is_valid_for_the_attestors_own_parser() -> None:
    from zid_platform.attestor import registration

    entries = registration.load(DEPLOY / "attestor" / "workloads.example.json")
    assert entries and entries[0].selectors["docker.container"] == "payments-agent"
    assert "/t/" in entries[0].spiffe_id  # the tenant is named in the identity


def test_the_deployed_keys_and_registrations_cannot_be_committed_by_accident() -> None:
    assert (DEPLOY / "attestor-secrets" / ".gitignore").read_text().split() == ["*", "!.gitignore"]
    assert "workloads.json" in (DEPLOY / "attestor" / ".gitignore").read_text()


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell here")
def test_the_operator_script_parses_and_refuses_bad_input_before_touching_anything() -> None:
    script = DEPLOY / "workload-identity.sh"
    assert subprocess.run(["sh", "-n", str(script)], capture_output=True, check=False).returncode == 0
    assert "set -eu" in script.read_text(encoding="utf-8")
    nothing = subprocess.run(["sh", str(script)], capture_output=True, text=True, check=False, cwd=DEPLOY)
    assert nothing.returncode == 2 and "register" in nothing.stdout  # usage, not an action


def test_the_preflight_checks_what_a_vm_must_get_right_and_changes_nothing() -> None:
    script = (DEPLOY / "workload-identity.sh").read_text(encoding="utf-8")
    body = script.split("  preflight)", 1)[1].split("  edge-ca)", 1)[0]
    for needle in (
        "ZID_DOCKER_GID",  # the engine socket's group
        "attestor-secrets",  # owner 10001, mode 700
        "attestor.pem",  # owner 10001, mode 400
        "secrets/*.pem",  # a private key where the cell can read it
        "workloads.json",  # docker would create a directory in its place
        "workload.$domain",  # private DNS
        "8081",  # the SPIRE server
    ):
        assert needle in body, needle
    assert not re.search(r"(chown|chmod|mkdir|rm|docker run|docker compose)", body.replace("mkdir -p $sock", "")), "preflight only looks"


def test_the_digest_is_entered_once_and_the_deployment_says_how_builds_are_required() -> None:
    script = _text("workload-identity.sh")
    assert "sync-attestor)" in script and "zid_platform.entrypoints.attestor_sync" in script
    assert "--registrations /work/workloads.json" in script
    readme = _text("README.md")
    assert "sync-attestor" in readme and "ZID_AGENT_BUILDS" in readme
    assert "ZID_AGENT_BUILDS" in _text("env.example")
    assert "approved_digests" in readme and "do **not** type" in readme


def test_the_listener_proves_itself_to_the_cell_and_public_sites_drop_that_header() -> None:
    assert "header_up X-Zid-Proxy-Auth {file./run/secrets/mesh_proxy_secret}" in _text("Caddyfile.workload")
    assert re.search(r"^\s*request_header -X-Zid-Proxy-Auth\s*$", _text("Caddyfile"), flags=re.M)
    assert "./secrets/mesh_proxy_secret:/run/secrets/mesh_proxy_secret:ro" in [str(v) for v in NEW["workload-edge"]["volumes"]]
    assert "ZID_MESH_PROXY_SECRET_FILE: ${ZID_MESH_PROXY_SECRET_FILE:-}" in _text("docker-compose.yml")
    # the attestor holds none of the cell's or the listener's secrets
    assert not any("mesh_proxy_secret" in str(v) for v in NEW["attestor"]["volumes"])
    script = _text("workload-identity.sh")
    assert "proxy-secret)" in script and "end=''" in script and "mesh_proxy_secret" in script.split("  preflight)", 1)[1].split("  edge-ca)", 1)[0]
    assert "ZID_MESH_PROXY_SECRET_FILE" in _text("env.example") and "proxy-secret" in _text("README.md")
