#!/bin/sh
# Local workload identity: the attestor's own key, the registration file, and the settings the cell needs (development only).
#
#   sh local/workload/setup.sh            # once: creates local/workload/.keys/ and local/workload/workloads.json
#   docker compose --profile spire up -d  # SPIRE (see local/spire/bootstrap.sh)
#   docker compose --profile workload up -d workload-edge attestor
#   python launch_stack.py cell           # restarts the cell with the attestor key and the trusted listener address
#
# The listener's secret is in .proxy/. The attestor's PRIVATE key stays in .keys/ and only the attestor container mounts it; the cell gets the PUBLIC key (attestor_keys.json).
# Both .keys/ and workloads.json are git-ignored: workloads.json holds your digests and container names.
set -eu
cd "$(dirname "$0")"
image="${ZID_BACKEND_IMAGE:-zid/platform:local}"
# the secret the listener proves itself with: its own directory, mounted only into the listener (the attestor never sees it)
mkdir -p .proxy
[ -s .proxy/proxy_auth ] || docker run --rm "$image" python -c "import secrets; print(secrets.token_hex(32), end='')" > .proxy/proxy_auth  # no newline: it goes into an HTTP header
# (safe to run again: an existing secret is kept)
[ ! -e .keys/attestor.pem ] || { echo ".keys/attestor.pem exists: refusing to overwrite a signing key" >&2; exit 1; }
mkdir -p .keys
# written as root inside the container, then made readable by the attestor's own user (Docker Desktop mounts ignore ownership)
docker run --rm -u 0 -v "$PWD/.keys:/out" "$image" \
  python -m zid_platform.entrypoints.attestor keygen --out /out/attestor.pem --kid attestor-1 > .keys/attestor_keys.json
docker run --rm -u 0 -v "$PWD/.keys:/d" --entrypoint chmod "$image" 644 /d/attestor.pem
[ -f workloads.json ] || cp workloads.example.json workloads.json
echo "attestor key created. Edit workloads.json: the SPIFFE id (your company id is in the console URL or Settings), the container name and its labels." >&2
echo "Then, after a person has bound the workload in the console:  python -m zid_platform.entrypoints.attestor_sync --workspace <company> --registrations local/workload/workloads.json --write" >&2
