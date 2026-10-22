#!/bin/sh
# Local SPIRE bootstrap (DEC-F7 development profile). Run once after `docker compose --profile spire up -d`:
#   sh local/spire/bootstrap.sh
# 1. mints a one-time join token for the agent and starts it;
# 2. registers the backend workload: spiffe://local-1.local.id.zoiko/ns/local/sa/zid-platform
#    (selector unix:uid:1000 = the `zid` user of the platform image);
# 3. exports the trust bundle to local/spire/bundle.pem for ZID_SPIFFE_BUNDLE_FILES.
set -eu
TD="local-1.local.id.zoiko"
TOKEN=$(docker compose exec -T spire-server /opt/spire/bin/spire-server token generate \
    -spiffeID "spiffe://$TD/node/local" | awk '{print $2}')
docker compose run -d --name zid-spire-agent --rm spire-agent -joinToken "$TOKEN"
docker compose exec -T spire-server /opt/spire/bin/spire-server entry create \
    -parentID "spiffe://$TD/node/local" \
    -spiffeID "spiffe://$TD/ns/local/sa/zid-platform" \
    -selector unix:uid:1000 || true
docker compose exec -T spire-server /opt/spire/bin/spire-server bundle show > "$(dirname "$0")/bundle.pem"
echo "trust bundle written to local/spire/bundle.pem"
echo "set ZID_SPIFFE_TRUST_DOMAIN=$TD and ZID_SPIFFE_BUNDLE_FILES='{\"$TD\": \"/app/spire/bundle.pem\"}'"
