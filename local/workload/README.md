# Local workload identity (development only)

Lets a real container get a real token from the cell (the `backend` container, port 8000, the only port): a SPIRE identity at the listener, the attestor looking at the
running container, and the cell deciding. The same design as `deploy/gcp-vm` (README section 9). The listener has the fixed address 172.30.0.250 in the compose network and the
attestor posts to `http://backend:8000`.

## One-time setup
```bash
docker compose --profile spire up -d              # then: sh local/spire/bootstrap.sh   (SPIRE server and agent)
                                                  # bootstrap.sh rewrites local/spire/bundle.pem: the listener and the backend read it only at start-up, so RESTART
                                                  # them after it (docker compose restart workload-edge backend), or the token request fails with SVID_CHAIN_INVALID
sh local/workload/setup.sh                        # the attestor's key and local/workload/workloads.json
# Tell the cell about them: add these to the backend's `environment` in docker-compose.override.yml (not in the repository), then restart the backend:
#   ZID_MESH_TRUSTED_PROXIES: '["172.30.0.250"]'                 # the listener's fixed address
#   ZID_MESH_PROXY_SECRET_FILE: /run/proxy/proxy_auth
#   ZID_ATTESTOR_KEYS_FILE: /run/attestor/attestor_keys.json
#   ZID_SPIFFE_TRUST_DOMAIN: local-1.local.id.zoiko
#   ZID_SPIFFE_BUNDLE_FILES: '{"local-1.local.id.zoiko": "/app/spire/bundle.pem"}'
docker compose --profile workload up -d workload-edge attestor
```

## The flow, with a real container
The whole chain (who holds which grant, what each step refuses without) is in [docs/runbooks/agent-go-live.md](../../docs/runbooks/agent-go-live.md); this page is its last mile.

1. **The stand-in agent builds and runs with the stack** (`local/workload/agent/`, container `payments-agent`, label `team=finance`, user id 10001; `docker compose up -d` starts it). Read its digest: `docker inspect --format '{{.Id}}' acme/invoice-agent:1.0.0`. Rebuild with `docker compose build invoice-agent`: a rebuilt image can have a new digest, which must be registered and approved again.
2. **Console:** Agents & tools → *Builds* → register that digest; a second person approves it. *Tools*, *Blueprints* (pick the approved build), *Agents*.
3. **Workload identity** → register a binding for the agent. The SPIFFE ID is built for you; note it.
4. **SPIRE:** let the workload have that identity: `docker exec zoiko-id-spire-server-1 /opt/spire/bin/spire-server entry create -parentID spiffe://local-1.local.id.zoiko/node/local -spiffeID <the id> -selector unix:uid:<the container's user id>`.
5. **Registration file:** in `local/workload/workloads.json` put that SPIFFE ID, `"environment_id": "prod"` and the container's selectors. Do not type the digest: run
   `python -m zid_platform.entrypoints.attestor_sync --workspace <company> --registrations local/workload/workloads.json --write` (needs `ZID_CELL_DATABASE_URL`: the compose Postgres on `localhost:5432`, database `zid_cell`).
6. **Run it:** it is already running as `payments-agent` (user id 10001, so the SPIRE selector is `unix:uid:10001`). The attestor logs `attested … level 2: 200 OK`. To run your own software instead, replace the image or start your own container with the name, label and user id the registration file names.
7. The workload presents its SPIRE identity to `https://workload.localhost:9443/v1/machine/workload-token` and receives a token bound to that identity.

## Limits
- The listener trusts nothing the client sends and replaces the forwarded certificate, but the cell trusts a certificate forwarded from `127.0.0.1`, so any process on this machine
  could present one it only copied. That is why this is development only; a deployed cell trusts one fixed private address.
- `workloads.json` and `.keys/` are git-ignored: they hold your digests, container names and the attestor's private key.
- A local image's digest is its image ID. After pushing to a registry the registry's digest is also accepted.
