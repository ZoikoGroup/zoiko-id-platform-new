#!/bin/sh
# Roll the APP VM to a new release: verify the signed images by digest, migrate as the owner role, restart, wait for readiness.
#   ./deploy.sh sha256:<backend-digest> [sha256:<web-digest> [sha256:<operator-digest> [sha256:<ocr-digest> [sha256:<face-digest>]]]]
# Digests come from the release workflow (build-image.yml, build-frontend-image.yml, build-sidecar-image.yml). Each image must carry the keyless cosign signature of exactly
# that workflow on main (DEC-G4); an unsigned or differently built image is refused before anything restarts. ZID_PROFILES (default "edge events")
# picks the compose profiles. After a release that adds a schema, also run `./db-init.sh post` from a machine that reaches the database VM.
set -eu
cd "$(dirname "$0")"
DIGEST="${1:?usage: deploy.sh sha256:<backend> [sha256:<web> [sha256:<operator> [sha256:<ocr> [sha256:<face>]]]]}"
WEB_DIGEST="${2:-}"
OPERATOR_DIGEST="${3:-}"
OCR_DIGEST="${4:-}"
FACE_DIGEST="${5:-}"
REPO="${GITHUB_REPOSITORY:-ZoikoGroup/zoiko-id-platform-new}"
ISSUER=https://token.actions.githubusercontent.com
PROFILES="${ZID_PROFILES:-edge events}"

verify() { # image-ref workflow-file
  cosign verify "$1" --certificate-identity "https://github.com/${REPO}/.github/workflows/$2@refs/heads/main" --certificate-oidc-issuer "$ISSUER" >/dev/null
  echo "signature verified: $1"
}

# Optional release-token gate (ZID-ENG-REL-002). When ZID_REQUIRE_RELEASE_TOKEN=1 the roll is refused unless tools/scripts/release_cell.py verifies a signed token for THIS release cell:
# the approved fingerprint, an unchanged evidence ledger, not expired, same market and region. Needs python3 with `cryptography` on the machine running this script.
if [ "${ZID_REQUIRE_RELEASE_TOKEN:-0}" = "1" ]; then
  : "${ZID_RELEASE_TOKEN:?set ZID_RELEASE_TOKEN, ZID_RELEASE_CELL, ZID_RELEASE_LEDGER and ZID_RELEASE_PUBLIC_KEY}"
  python3 ../../tools/scripts/release_cell.py verify --token "$ZID_RELEASE_TOKEN" --cell "${ZID_RELEASE_CELL:?}" --ledger "${ZID_RELEASE_LEDGER:?}" --public "${ZID_RELEASE_PUBLIC_KEY:?}"     || { echo "release token refused: nothing was changed" >&2; exit 1; }
  echo "release token verified"
fi

BACKEND="${ZID_BACKEND_REPO:-ghcr.io/zoikogroup/zoiko-id-platform}@${DIGEST}"
verify "$BACKEND" build-image.yml
sed -i "s|^ZID_BACKEND_IMAGE=.*|ZID_BACKEND_IMAGE=${BACKEND}|" .env
if [ -n "$WEB_DIGEST" ]; then
  WEB="${ZID_WEB_REPO:-ghcr.io/zoikogroup/zoiko-id-web}@${WEB_DIGEST}"
  verify "$WEB" build-frontend-image.yml
  sed -i "s|^ZID_WEB_IMAGE=.*|ZID_WEB_IMAGE=${WEB}|" .env
fi

if [ -n "$OPERATOR_DIGEST" ]; then            # the operator console is built and signed by the same workflow as the web image
  OPERATOR="${ZID_OPERATOR_REPO:-ghcr.io/zoikogroup/zoiko-id-operator}@${OPERATOR_DIGEST}"
  verify "$OPERATOR" build-frontend-image.yml
  sed -i "s|^ZID_OPERATOR_IMAGE=.*|ZID_OPERATOR_IMAGE=${OPERATOR}|" .env
fi

if [ -n "$OCR_DIGEST" ]; then                 # the identity-check sidecars are built and signed by build-sidecar-image.yml
  OCR="${ZID_OCR_REPO:-ghcr.io/zoikogroup/zoiko-id-ocr}@${OCR_DIGEST}"
  verify "$OCR" build-sidecar-image.yml
  sed -i "s|^ZID_OCR_IMAGE=.*|ZID_OCR_IMAGE=${OCR}|" .env
fi
if [ -n "$FACE_DIGEST" ]; then
  FACE="${ZID_FACE_REPO:-ghcr.io/zoikogroup/zoiko-id-face}@${FACE_DIGEST}"
  verify "$FACE" build-sidecar-image.yml
  sed -i "s|^ZID_FACE_IMAGE=.*|ZID_FACE_IMAGE=${FACE}|" .env
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
