#!/bin/sh
# Operate the software key store from the APP VM (CR-060, docs/runbooks/software-keys.md).
#   ./keys.sh init-master             # once, before the first start: creates ./secrets/master_key (mode 400, owner 10001); refuses to overwrite
#   ./keys.sh status | public | rotate-signing --key-id signing_token-eu-1 | rewrap
# Everything except init-master runs the platform's own tool in the cell image with the cell's settings, so it reads the same database and
# master key as the cell.
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" = "init-master" ]; then
  IMAGE="$(sed -n 's/^ZID_BACKEND_IMAGE=//p' .env | head -1)"
  [ -n "$IMAGE" ] || { echo "set ZID_BACKEND_IMAGE in .env first" >&2; exit 2; }
  mkdir -p secrets
  docker run --rm --user 0 -v "$PWD/secrets:/out" --entrypoint sh "$IMAGE" -c \
    'python -m zid_platform.entrypoints.keys init-master --file /out/master_key && chown 10001:10001 /out/master_key && chmod 400 /out/master_key'
  echo "Now copy secrets/master_key OFFLINE, split between two people. Without it every stored key is unrecoverable."
  exit 0
fi
exec docker compose run --rm --no-deps cell python -m zid_platform.entrypoints.keys "$@"
