# CR-063: Workload identity and agents on the GCP VM deployment (SPIRE, attestor, workload listener)

Status: open (built and run end to end on one machine; the first run on the real VMs is still to do). Extends [CR-060](CR-060-software-keys-in-the-database.md) (VM-only deployment) and [CR-042](CR-042-verified-workload-attestation.md) (verified attestation).

## What changes
The local flow for agents and workloads (`local/spire/`, the attestor) is now part of `deploy/gcp-vm`, behind the compose profile `workload-identity`. Nothing starts without it; without it the cell answers 503 to workload requests and 404 to the attestation endpoint.

- **SPIRE server** on the services VM (`docker-compose.services.yml`, `spire/server.conf`): trust domain `<cell>.<environment>.id.zoiko` from `ZID_SPIFFE_TRUST_DOMAIN` (never a default); the CA key is kept on disk (`KeyManager "disk"`) so a restart keeps every issued identity valid; datastore is a sqlite file in the same directory. No KMS or HSM: that directory is the CA's secret.
- **SPIRE agent** on the app VM (`spire/agent.conf`): pins the server's trust bundle instead of trusting the first server it meets (the local profile used `insecure_bootstrap`); joins once with a one-time token from the environment; recognises a workload by the unix user it runs as (`pid: host`).
- **Workload listener** (`Caddyfile.workload`, service `workload-edge`, port 9443, private address only): mutual TLS against the SPIRE bundle; forwards the verified certificate to the cell as base64 DER in `X-Forwarded-Client-Cert`, replacing anything the client sent; the attestation endpoint is not served there. A separate Caddy from the public edge, so a missing bundle cannot stop the console.
- **Public edge** (`Caddyfile`): every site drops a client-supplied `X-Forwarded-Client-Cert` (a certificate is public, so copying one into a header must never be an identity), and the attestation endpoint is 404 from the internet.
- **Attestor** (service `attestor`, same platform image): its own key (`attestor-secrets/`, mounted only into it), none of the cell's secrets, the Docker socket read-only. Code: `engine_api_runner` reads the two inspect calls from the Docker Engine API (the platform image has no docker command) and refuses any other call; `--registered-docker` attests every registration that names a container, so the reviewed registration file is the whole list.
- **Cell**: `zid_context.spiffe._xfcc_cert` also reads a forwarded certificate as base64 DER (Caddy's PEM placeholder holds raw newlines a header cannot carry). Only how bytes are read changed; the certificate is verified against the bundle afterwards, and an unreadable value is "no certificate", never a server error.
- **Operator script** `workload-identity.sh` (`server-init`, `token`, `register`, `bundle`, `attestor-init`, `edge-ca`) and README section 9.

## Run against the real components (one machine, Docker)
SPIRE server and agent from these files; a registered workload fetched its identity as uid 1000 from a separate container; Caddy accepted that identity and refused no certificate and another CA's; the cell accepted the forwarded identity, derived the tenant and answered `WORKLOAD_IDENTITY_UNREGISTERED` (no binding exists: an identity alone authorizes nothing); a forged forwarded header changed nothing; the attestor read a real container through the read-only engine socket and the cell recorded a level 2 attestation (level 1 for a different image, none for a stopped container).

## Also run on a real Linux filesystem and with a real token
- `attestor-init` as root on the Docker VM's native filesystem (not a Windows mount): the directory came out owner 10001 mode 700, the key owner 10001 mode 400, the public key 444; the key's own user could read it, another user could not; a second run refused to overwrite it. `docker-gid` reads the engine socket's group. `preflight` (new) checks ownership, modes, the Docker group, the bundle, a private key left where the cell can read it, the workload registration file, the socket directory, private DNS and the SPIRE server, and printed a fix for each failure.
- A real token: a real cell on a fresh migrated database with one agent and one workload binding; the real attestor recorded a level 2 attestation for a real container; a real SPIRE identity (uid 2000) went through the real workload listener and received a token (subject the agent principal, tenant from the signed SPIFFE ID, five minutes, bound to the certificate's thumbprint). The token worked with the same identity and was refused with another valid SPIRE identity, with no certificate, and presented straight to the cell.

## Not run, and why
- Real GCP VMs: there is no `gcloud` or project access here, and I did not create cloud resources. The firewall rules and the private DNS zone in README section 9 are written but unrun.
- The SPIRE server reachability check and the workload-listener DNS name can only pass on the real network.
- A workload on a VM other than the app VM.

## Limits (stated, not hidden)
Software keys for the SPIRE CA and the attestor (modes 600 and 400 on VM disks); the agent container shares the host process table and is privileged; the listener's own certificate comes from Caddy's internal CA (a private name cannot get a public one) and workloads must pin that root; no hardware or TEE attestation, so no workload reaches level 3; one SPIRE server (no HA).

## Tests
`test_gcp_vm_workload_identity.py` (15: profile gating, pinned images, the attestor holds no cell secret and a read-only socket, mutual TLS required, the forwarded header replaced or dropped on every site, trusted-proxy address, valid JSON defaults, no secret value in any file; two deliberate breakages were checked to fail), `test_attestor_engine_api.py` (6), `test_spiffe.py` (+7 for the DER form and unreadable values).

## Acceptance rows
None linked: MACH-26 and the VM rows stay IN_PROGRESS or NOT_STARTED until the first run on the VMs.
