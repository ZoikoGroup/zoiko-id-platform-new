# Local workload identity (development only)

Lets a real container get a real token from the cell that runs from source on this machine: a SPIRE identity at the listener, the attestor looking at the
running container, and the cell deciding. The same design as `deploy/gcp-vm` (README section 9), with the cell on the host instead of in a container.

## One-time setup
```bash
docker compose --profile spire up -d              # then: sh local/spire/bootstrap.sh   (SPIRE server and agent)
sh local/workload/setup.sh                        # the attestor's key and local/workload/workloads.json
python ~/.zoiko/launch_stack.py cell              # restart the cell: it now trusts the attestor's public key and the listener
docker compose --profile workload up -d workload-edge attestor
```

## The flow, with a real container
1. **Build something to run** and read its digest: `docker build -t acme/invoice-agent:1.0.0 .` then `docker inspect --format '{{.Id}}' acme/invoice-agent:1.0.0`.
2. **Console:** Agents & tools → *Builds* → register that digest; a second person approves it. *Tools*, *Blueprints* (pick the approved build), *Agents*.
3. **Workload identity** → register a binding for the agent. The SPIFFE ID is built for you; note it.
4. **SPIRE:** let the workload have that identity: `docker exec zoiko-id-spire-server-1 /opt/spire/bin/spire-server entry create -parentID spiffe://local-1.local.id.zoiko/node/local -spiffeID <the id> -selector unix:uid:<the container's user id>`.
5. **Registration file:** in `local/workload/workloads.json` put that SPIFFE ID, `"environment_id": "prod"` and the container's selectors. Do not type the digest: run
   `python -m zid_platform.entrypoints.attestor_sync --workspace <company> --registrations local/workload/workloads.json --write` (needs `ZID_CELL_DATABASE_URL`, as the launcher sets it).
6. **Run it:** `docker run -d --name payments-agent --label team=finance -u <uid> acme/invoice-agent:1.0.0`. The attestor logs `attested … level 2: 200 OK`.
7. The workload presents its SPIRE identity to `https://workload.localhost:9443/v1/machine/workload-token` and receives a token bound to that identity.

## Limits
- The listener trusts nothing the client sends and replaces the forwarded certificate, but the cell trusts a certificate forwarded from `127.0.0.1`, so any process on this machine
  could present one it only copied. That is why this is development only; a deployed cell trusts one fixed private address.
- `workloads.json` and `.keys/` are git-ignored: they hold your digests, container names and the attestor's private key.
- A local image's digest is its image ID. After pushing to a registry the registry's digest is also accepted.
