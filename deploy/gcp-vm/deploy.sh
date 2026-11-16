#!/bin/sh
# Roll the APP VM to a new release: verify the signed images by digest, migrate as the owner role, restart, wait for readiness.
#   ./deploy.sh sha256:<backend-digest> [sha256:<web-digest>]
# Digests come from the release workflow (build-image.yml, build-frontend-image.yml). Each image must carry the keyless cosign signature of exactly
# that workflow on main (DEC-G4); an unsigned or differently built image is refused before anything restarts. ZID_PROFILES (default "edge events")
# picks the compose profiles. After a release that adds a schema, also run `./db-init.sh post` from a machine that reaches the database VM.
set -eu
cd "$(dirname "$0")"
DIGEST="${1:?usage: deploy.sh sha256:<backend-digest> [sha256:<web-digest>]}"
WEB_DIGEST="${2:-}"
REPO="${GITHUB_REPOSITORY:-ZoikoGroup/zoiko-id-platform-new}"
ISSUER=https://token.actions.githubusercontent.com
PROFILES="${ZID_PROFILES:-edge events}"

verify() { # image-ref workflow-file
  cosign verify "$1" --certificate-identity "https://github.com/${REPO}/.github/workflows/$2@refs/heads/main" --certificate-oidc-issuer "$ISSUER" >/dev/null
  echo "signature verified: $1"
}

BACKEND="${ZID_BACKEND_REPO:-ghcr.io/zoikogroup/zoiko-id-platform}@${DIGEST}"
verify "$BACKEND" build-image.yml
sed -i "s|^ZID_BACKEND_IMAGE=.*|ZID_BACKEND_IMAGE=${BACKEND}|" .env
if [ -n "$WEB_DIGEST" ]; then
  WEB="${ZID_WEB_REPO:-ghcr.io/zoikogroup/zoiko-id-web}@${WEB_DIGEST}"
  verify "$WEB" build-frontend-image.yml
  sed -i "s|^ZID_WEB_IMAGE=.*|ZID_WEB_IMAGE=${WEB}|" .env
fi

docker compose pull
docker compose run --rm migrate                      # schema first, as the owner role
FLAGS=""; for p in $PROFILES; do FLAGS="$FLAGS --profile $p"; done
# shellcheck disable=SC2086
docker compose $FLAGS up -d --remove-orphans
for svc in cell; do                           # wait for readiness before declaring success
  port=8000
  i=0
  until docker compose exec -T "$svc" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$port/health/ready',timeout=3).status==200 else 1)" 2>/dev/null; do
    i=$((i+1)); [ "$i" -gt 40 ] && { echo "$svc did not become ready" >&2; exit 1; }; sleep 3
  done
done
echo "deployed ${DIGEST}"
