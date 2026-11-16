#!/bin/sh
# Roll the stack to a new release on the VM: verify the signed backend image by digest, then restart.
#   ./deploy.sh sha256:<backend-digest> [sha256:<web-digest>]
# Digests come from the release workflow (build-image.yml, build-frontend-image.yml). Each image must carry the keyless
# cosign signature of exactly that workflow on main (DEC-G4); an unsigned or differently built image is refused
# before anything restarts. ZID_EDGE=lb selects the load-balancer edge (default direct). The operator image, if used, is pinned in .env by hand the same way.
set -eu
DIGEST="${1:?usage: deploy.sh sha256:<backend-digest> [sha256:<web-digest>]}"
WEB_DIGEST="${2:-}"
REPO="${GITHUB_REPOSITORY:-ZoikoGroup/zoiko-id-platform-new}"
ISSUER=https://token.actions.githubusercontent.com

verify() { # image-ref workflow-file
  cosign verify "$1" --certificate-identity "https://github.com/${REPO}/.github/workflows/$2@refs/heads/main"     --certificate-oidc-issuer "$ISSUER" >/dev/null
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
docker compose --profile "${ZID_EDGE:-direct}" up -d --remove-orphans
for svc in cell; do                           # wait for readiness before declaring success
  port=8000
  i=0
  until docker compose exec -T "$svc" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$port/health/ready',timeout=3).status==200 else 1)" 2>/dev/null; do
    i=$((i+1)); [ "$i" -gt 30 ] && { echo "$svc did not become ready" >&2; exit 1; }; sleep 2
  done
done
echo "deployed ${DIGEST}"
