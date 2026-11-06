#!/usr/bin/env bash
# OpenBao on the services VM: initialise it once, unseal it after every restart, and issue the application's token.
#   ./openbao.sh init                 # once: prints the unseal shares and the root token ONCE, to the terminal only
#   ./openbao.sh unseal               # after every start: asks for shares (hidden input) until it is unsealed
#   ./openbao.sh app-token [file]     # once: policy + a periodic token for the platform, written to <file> (default ./app_token)
# OpenBao keeps its data in PostgreSQL and starts SEALED, by design: nothing on this machine can open it. The shares belong to different
# people (5 shares, any 3 open it). Do NOT save them on this VM, in a ticket or in a chat. Run from the folder with docker-compose.services.yml.
set -euo pipefail
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1
SHARES="${BAO_SHARES:-5}" THRESHOLD="${BAO_THRESHOLD:-3}"
bao() { docker compose -f docker-compose.services.yml exec -T -e BAO_ADDR=http://127.0.0.1:8200 ${BAO_TOKEN:+-e BAO_TOKEN="$BAO_TOKEN"} openbao bao "$@"; }
status() { bao status -format=json 2>/dev/null || true; }   # exits 2 while sealed

case "${1:-}" in
  init)
    if status | grep -q '"initialized": *true'; then echo "already initialised" >&2; exit 1; fi
    out="$(bao operator init -key-shares="$SHARES" -key-threshold="$THRESHOLD")"
    printf '%s\n' "$out"
    echo
    echo "Copy each unseal share to a DIFFERENT custodian now, and the root token to the person who will run app-token."
    echo "This is the only time they are shown. Then clear this terminal's scrollback."
    ;;
  unseal)
    while status | grep -q '"sealed": *true'; do
      printf 'unseal share (hidden): '
      read -rs share; echo
      printf '%s\n' "$share" | bao operator unseal - >/dev/null || echo "that share was not accepted"
    done
    echo "unsealed"
    ;;
  app-token)
    out="${2:-./app_token}"
    printf 'root token (hidden): '
    read -rs BAO_TOKEN; echo
    export BAO_TOKEN
    bao secrets list -format=json | grep -q '"secret/"' || bao secrets enable -path=secret kv-v2 >/dev/null
    printf 'path "secret/data/*" { capabilities = ["create","read","update","delete","list"] }\npath "secret/metadata/*" { capabilities = ["list","read","delete"] }\n' \
      | bao policy write zid - >/dev/null
    umask 077
    bao token create -policy=zid -period=720h -orphan -format=json | tr -d '\n ' | sed -n 's/.*"client_token":"\([^"]*\)".*/\1/p' > "$out"
    [ -s "$out" ] || { echo "no token was issued" >&2; rm -f "$out"; exit 1; }
    echo "wrote $out (a periodic token, 30 days, renewed while the platform uses it; policy zid: secret/* only)."
    echo "Copy it to the APP VM as ./secrets/openbao_token (owner 10001, mode 400), then delete this copy and revoke the root token."
    ;;
  *) sed -n 2,6p "$0"; exit 2 ;;
esac
