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
set -eu
cd "$(dirname "$0")"
cmd="${1:-}"; shift || true

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
  edge-ca)
    docker compose --profile workload-identity exec -T workload-edge cat /data/caddy/pki/authorities/local/root.crt
    ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
