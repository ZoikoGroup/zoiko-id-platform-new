#!/bin/sh
# Workload identity on the VMs: the same flow as local/spire/bootstrap.sh, split by the VM it runs on. Standard tools only.
#
#   SERVICES VM (where the SPIRE server runs; reads ./.env for ZID_SPIFFE_TRUST_DOMAIN):
#     ./workload-identity.sh server-init                 once: the server's data directory (owner 1000, mode 700)
#     ./workload-identity.sh token NODE_NAME [TTL_SECS]  a one-time join token for one agent; prints it once
#     ./workload-identity.sh register NODE_NAME SPIFFE_ID UID   let the workload running as UID on that node have SPIFFE_ID
#     ./workload-identity.sh bundle > spire_bundle.pem   the trust bundle (public): copy to each app VM as secrets/spire_bundle.pem
#   APP VM (where the cell and the attestor run):
#     ./workload-identity.sh attestor-init [KID]         the attestor's own key, and its PUBLIC key for the cell
#     ./workload-identity.sh edge-ca                     the root a workload must trust to reach the workload listener
#     ./workload-identity.sh docker-gid                  the group number that owns the engine socket (for ZID_DOCKER_GID)
#     ./workload-identity.sh preflight                   check this VM is ready for the profile; exit 1 on any FAIL
#     ./workload-identity.sh sync-attestor WORKSPACE [--write]   fill attestor/workloads.json's approved digests from approved builds
set -eu
cd "$(dirname "$0")"
cmd="${1:-}"
if [ $# -gt 0 ]; then shift; fi

env_value() { # env_value NAME  (from the environment, else from .env; never echoed back)
  eval "v=\${$1:-}"
  if [ -z "$v" ] && [ -f .env ]; then v=$(grep -E "^$1=" .env | head -n1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//'); fi
  printf %s "$v"
}
trust_domain() {
  td=$(env_value ZID_SPIFFE_TRUST_DOMAIN)
  [ -n "$td" ] || { echo "set ZID_SPIFFE_TRUST_DOMAIN (<cell>.<environment>.id.zoiko) in .env first" >&2; exit 2; }
  printf %s "$td"
}
server() { # run the spire-server CLI inside the running server container
  docker compose -f docker-compose.services.yml --profile workload-identity exec -T spire-server /opt/spire/bin/spire-server "$@"
}

case "$cmd" in
  server-init)
    dir=$(env_value ZID_SPIRE_DATA_DIR); dir=${dir:-/var/lib/zid/spire-server}
    mkdir -p "$dir" && chown 1000:1000 "$dir" && chmod 700 "$dir"
    echo "created $dir. Put it on the snapshotted disk, then: docker compose -f docker-compose.services.yml --profile workload-identity up -d spire-server"
    ;;
  token)
    node="${1:?usage: token NODE_NAME [TTL_SECS]}"; ttl="${2:-600}"
    td=$(trust_domain)
    # the agent's identity is spiffe://<td>/node/<NODE_NAME>, so registrations name a stable parent, not a random token id
    server token generate -spiffeID "spiffe://$td/node/$node" -ttl "$ttl" | awk '/^Token:/ {print $2}'
    echo "one-time token above (valid ${ttl}s). On the app VM: put ZID_SPIRE_JOIN_TOKEN=<it> in .env, start spire-agent, then REMOVE the line." >&2
    ;;
  register)
    node="${1:?usage: register NODE_NAME SPIFFE_ID UID}"; sid="${2:?SPIFFE_ID}"; uid="${3:?UID}"
    td=$(trust_domain)
    case "$sid" in "spiffe://$td/"*) ;; *) echo "SPIFFE_ID must be in spiffe://$td/ (the tenant is named in its path: /t/<tenant-id>/...)" >&2; exit 2;; esac
    case "$uid" in ''|*[!0-9]*) echo "UID must be a number" >&2; exit 2;; esac
    server entry create -parentID "spiffe://$td/node/$node" -spiffeID "$sid" -selector "unix:uid:$uid" -x509SVIDTTL 3600
    ;;
  bundle)
    server bundle show
    ;;
  attestor-init)
    kid="${1:-attestor-1}"
    image=$(env_value ZID_BACKEND_IMAGE); [ -n "$image" ] || { echo "set ZID_BACKEND_IMAGE in .env" >&2; exit 2; }
    [ ! -e attestor-secrets/attestor.pem ] || { echo "attestor-secrets/attestor.pem exists: refusing to overwrite a signing key" >&2; exit 1; }
    mkdir -p attestor-secrets secrets && chmod 700 attestor-secrets
    # the key is written by the image's own user (10001) into a directory only that user and root can read
    chown 10001:10001 attestor-secrets
    docker run --rm -u 10001:10001 -v "$PWD/attestor-secrets:/out" "$image" \
      python -m zid_platform.entrypoints.attestor keygen --out /out/attestor.pem --kid "$kid" > secrets/attestor_keys.json
    chmod 400 attestor-secrets/attestor.pem; chown 10001:10001 secrets/attestor_keys.json; chmod 444 secrets/attestor_keys.json
    echo "private key: attestor-secrets/attestor.pem (mounted only into the attestor). Public key for the cell: secrets/attestor_keys.json" >&2
    echo "set ZID_ATTESTOR_KEYS_FILE=/run/secrets/attestor_keys.json in .env, then restart cell." >&2
    ;;
  docker-gid)
    stat -c %g /var/run/docker.sock
    ;;
  preflight)
    # Everything the profile needs from THIS machine, checked before it starts. A FAIL names the fix; nothing is changed here.
    fails=0
    ok()   { echo "OK    $*"; }
    warn() { echo "WARN  $*"; }
    fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
    td=$(env_value ZID_SPIFFE_TRUST_DOMAIN)
    case "$td" in
      ?*.?*.id.zoiko) ok "trust domain $td" ;;
      *) fail "ZID_SPIFFE_TRUST_DOMAIN must look like <cell>.<environment>.id.zoiko (is '$td')" ;;
    esac
    if grep -q "BEGIN CERTIFICATE" secrets/spire_bundle.pem 2>/dev/null; then ok "secrets/spire_bundle.pem is a certificate bundle"; else fail "secrets/spire_bundle.pem missing or not a certificate: run 'bundle' on the services VM and copy it here"; fi
    case "$(env_value ZID_SPIFFE_BUNDLE_FILES)" in *"$td"*spire_bundle.pem*) ok "the cell is pointed at the bundle" ;; *) fail "ZID_SPIFFE_BUNDLE_FILES must map $td to /run/secrets/spire_bundle.pem" ;; esac
    case "$(env_value ZID_MESH_TRUSTED_PROXIES)" in *172.30.0.250*) ok "the cell trusts the workload listener's address" ;; *) fail "ZID_MESH_TRUSTED_PROXIES must be [\"172.30.0.250\"]" ;; esac
    if [ -S /var/run/docker.sock ]; then
      have=$(stat -c %g /var/run/docker.sock); want=$(env_value ZID_DOCKER_GID)
      if [ "$have" = "$want" ]; then ok "ZID_DOCKER_GID=$want matches the engine socket's group"; else fail "set ZID_DOCKER_GID=$have in .env (the engine socket's group; it is '${want:-unset}')"; fi
    else
      fail "/var/run/docker.sock is not a socket on this machine"
    fi
    if [ -d attestor-secrets ]; then
      own=$(stat -c %u attestor-secrets); mode=$(stat -c %a attestor-secrets)
      [ "$own" = 10001 ] && [ "$mode" = 700 ] && ok "attestor-secrets is owner 10001, mode 700" || fail "attestor-secrets must be owner 10001, mode 700 (is $own, $mode): run attestor-init as root"
      if [ -f attestor-secrets/attestor.pem ]; then
        own=$(stat -c %u attestor-secrets/attestor.pem); mode=$(stat -c %a attestor-secrets/attestor.pem)
        [ "$own" = 10001 ] && [ "$mode" = 400 ] && ok "the attestor's private key is owner 10001, mode 400" || fail "attestor-secrets/attestor.pem must be owner 10001, mode 400 (is $own, $mode)"
      else
        fail "attestor-secrets/attestor.pem missing: run attestor-init"
      fi
    else
      fail "attestor-secrets missing: run attestor-init"
    fi
    if ls secrets/*.pem 2>/dev/null | grep -qv spire_bundle.pem; then fail "a private key (*.pem) other than the bundle is in secrets/, which the cell can read: the attestor's key must live only in attestor-secrets/"; else ok "no attestor private key in secrets/"; fi
    grep -q '"kid"' secrets/attestor_keys.json 2>/dev/null && ok "secrets/attestor_keys.json holds the attestor's public key" || fail "secrets/attestor_keys.json missing: run attestor-init"
    [ -f attestor/workloads.json ] && ok "attestor/workloads.json exists" || fail "attestor/workloads.json missing: copy attestor/workloads.example.json and edit it (docker would create a directory in its place)"
    sock=$(env_value ZID_SPIRE_SOCKET_DIR); sock=${sock:-/run/zid/spire-sockets}
    [ -d "$sock" ] && ok "$sock exists" || fail "mkdir -p $sock (the agent's socket directory)"
    domain=$(env_value ZID_DOMAIN)
    if getent hosts "workload.$domain" >/dev/null 2>&1; then ok "workload.$domain resolves to $(getent hosts "workload.$domain" | cut -d' ' -f1 | head -n1)"; else warn "workload.$domain does not resolve here: add it to private DNS, pointing at this VM's private address"; fi
    host=$(env_value ZID_SERVICES_HOST)
    if command -v nc >/dev/null 2>&1; then
      nc -z -w 3 "$host" 8081 2>/dev/null && ok "the SPIRE server answers on $host:8081" || fail "cannot reach the SPIRE server at $host:8081 (is it up? is the firewall rule for 8081 in place?)"
    else
      warn "nc is not installed: the SPIRE server at $host:8081 was not checked"
    fi
    [ -z "$(env_value ZID_SPIRE_JOIN_TOKEN)" ] && ok "no join token left in .env" || warn "ZID_SPIRE_JOIN_TOKEN is still set: remove it once the agent has joined"
    [ "$fails" -eq 0 ] && echo "ready." || { echo "$fails problem(s) to fix before starting the profile." >&2; exit 1; }
    ;;
  sync-attestor)
    ws="${1:?usage: sync-attestor WORKSPACE [--write]}"; shift
    [ -f attestor/workloads.json ] || { echo "attestor/workloads.json missing: copy attestor/workloads.example.json and write its selectors first" >&2; exit 2; }
    # runs in the cell's own image and reads the company's records the way the cell does; it changes only the file it is given (with --write)
    docker compose run --rm --no-deps -v "$PWD/attestor:/work" cell \
      python -m zid_platform.entrypoints.attestor_sync --workspace "$ws" --registrations /work/workloads.json "$@"
    ;;
  edge-ca)
    docker compose --profile workload-identity exec -T workload-edge cat /data/caddy/pki/authorities/local/root.crt
    ;;
  *)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
