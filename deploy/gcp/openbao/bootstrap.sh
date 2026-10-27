#!/bin/sh
# One-time OpenBao setup, run once after the first `docker compose up -d openbao`:
#   docker compose exec -e SERVICE_ACCOUNT_EMAIL=zid-vm@<project>.iam.gserviceaccount.com -T openbao sh -s < openbao/bootstrap.sh
# 1. initialise (prints recovery keys: store them offline with two people, never in the repo or on the VM)
# 2. KV v2 at secret/, a read-only policy, and a JWT auth role bound to the VM's service account.
set -eu
export BAO_ADDR=http://127.0.0.1:8200
bao operator init -recovery-shares=5 -recovery-threshold=3 | tee /tmp/init.txt
echo "Record the recovery keys and root token above, then this script wipes /tmp/init.txt." >&2
BAO_TOKEN="$(sed -n 's/^Initial Root Token: //p' /tmp/init.txt)"
export BAO_TOKEN
bao secrets enable -path=secret -version=2 kv
bao policy write zid-platform - <<'POLICY'
path "secret/data/*"     { capabilities = ["read"] }
path "secret/metadata/*" { capabilities = ["read", "list"] }
POLICY
bao auth enable jwt
bao write auth/jwt/config oidc_discovery_url="https://accounts.google.com" bound_issuer="https://accounts.google.com"
bao write auth/jwt/role/zid-platform - <<ROLE
{
  "role_type": "jwt",
  "user_claim": "sub",
  "bound_audiences": ["openbao"],
  "bound_claims": {"email": "${SERVICE_ACCOUNT_EMAIL}"},
  "policies": ["zid-platform"],
  "ttl": "15m"
}
ROLE
rm -f /tmp/init.txt
echo "OpenBao ready. Add secrets with: bao kv put secret/<ref> value=..." >&2
