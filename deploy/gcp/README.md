# Zoiko iD on GCP (production-style)

Keys never leave Cloud KMS (HSM), secrets live in OpenBao (auto-unsealed by Cloud KMS), PostgreSQL is Cloud SQL on a private IP, and the application runs in Docker on a VM behind Caddy (automatic TLS). The older [deploy/vm](../vm/README.md) recipe is dev/demo only (development keys, no TLS).

```
Internet ─443─▶ Caddy ─▶ web (Next.js)  ─▶ cell API ─▶ Cloud SQL (zid_cell, row-level security)
                     ├─▶ cell API  (api.<domain>)       ├─▶ Cloud KMS (gcp-kms provider, signers, HMAC)
                     └─▶ global    (global.<domain>)    └─▶ OpenBao (secrets; JWT login with the VM identity)
                                   worker ─▶ Cloud SQL (zid_worker role)        valkey (web sessions)
```

## 1. Create the infrastructure (OpenTofu)

`deploy/tofu/modules/gcp-platform` creates the service account (least privilege), the HSM key ring `zid-<cell>` with every key the code asks for, the OpenBao unseal key, a private VPC, Cloud SQL PostgreSQL 17 (private IP only, point-in-time recovery, pgaudit), the firewall and the VM.

```hcl
module "cell" {
  source      = "../../modules/gcp-platform"
  project     = "zoiko-prod"
  cell_id     = "eu-1"
  admin_cidrs = ["35.235.240.0/20"] # IAP SSH
}
```

Set `enable_load_balancer = true` (with `domain`) for the production setup: a global HTTPS load balancer with Google-managed certificates, TLS 1.2+, Cloud Armor (OWASP SQLi/XSS/LFI/RCE rules and a per-IP rate limit) and an HTTP-to-HTTPS redirect, and the VM then accepts web traffic only from the load balancer. Start the stack with `docker compose --profile lb up -d` (Caddy then serves plain HTTP; TLS ends at the load balancer) and point DNS at `lb_ip`. Without it the VM terminates TLS itself and you must give `direct_web_cidrs` (the IaC policy rejects `0.0.0.0/0`).

Add `domain = "example.com"` and `alert_email = "oncall@example.com"` to get uptime checks on `id.`, `api.` and `global.` plus alerts for downtime and database disk, and the module also snapshots the VM disk daily (OpenBao data, certificates). `tofu init && tofu apply`, then use `tofu output backend_settings` for the key settings. Point DNS `id.<domain>`, `api.<domain>` and `global.<domain>` at `vm_ip`.

## 2. Build and push the images

```bash
gcloud artifacts repositories create zid --repository-format=docker --location=europe-west2
docker build -t europe-west2-docker.pkg.dev/<project>/zid/platform:<tag> backend
docker build -t europe-west2-docker.pkg.dev/<project>/zid/web:<tag> frontend
docker push ...   # both
```

`google-cloud-kms` is a dependency of the platform package, so the backend image already contains it.

## 3. Database roles and schemas

`db/` holds the SQL. Run from `deploy/gcp` on a machine that can reach the Cloud SQL private IP (the VM):

```bash
# 1. roles: owner (migrations), runtime (RLS applies), worker (BYPASSRLS)
psql "host=<private-ip> dbname=zid_cell user=postgres sslmode=require" -v owner_pw="$OWNER_PW"        -v runtime_pw="$RUNTIME_PW" -v worker_pw="$WORKER_PW" -f db/01_roles.sql
# 2. namespaces, as the owner
psql "host=<private-ip> dbname=zid_cell   user=zid_owner sslmode=require" -f ../../backend/database/schemas/001_cell_schemas.sql
psql "host=<private-ip> dbname=zid_cell   user=zid_owner sslmode=require" -f ../../backend/database/schemas/001_global_schemas.sql
docker compose run --rm migrate     # 3. tables, as the owner role
# 4. privileges for the runtime and worker roles (re-run after new schemas)
psql "host=<private-ip> dbname=zid_cell user=postgres sslmode=require" -f db/02_grants.sql
```

Put the same three passwords in `.env`. The worker's `BYPASSRLS` is deliberate: it sweeps every tenant, so it never serves requests.

Run `db/00_check_cloudsql.sql` first. It says whether this Cloud SQL instance lets the admin user create a `BYPASSRLS` role. If it says NOT ALLOWED, pass `-v worker_attr=NOBYPASSRLS` to `01_roles.sql` and run `db/03_worker_policies.sql` after the migrations (and after each one that adds tables): it gives only `zid_worker` a permissive `USING (true)` policy on every row-level-security table, which is equivalent for the worker and leaves `zid_runtime` unchanged. Both paths were run against Postgres 17 (the worker sees every tenant's rows and the runtime role sees none without a tenant context); the Cloud SQL answer itself I could not check.

## 4. Start the stack

```bash
git clone <repo> && cd zoiko_ids/deploy/gcp
cp env.example .env && chmod 600 .env          # fill in (see the checklist below)
mkdir -m 700 secrets                            # smtp_password, stripe_key as root-owned 0400 files
docker compose up -d openbao vm-identity
docker compose exec -e SERVICE_ACCOUNT_EMAIL=<vm_service_account> -T openbao sh -s < openbao/bootstrap.sh
docker compose --profile direct up -d           # migrate runs first, then cell, global, worker, web, caddy (use --profile lb behind the load balancer)
```

`bootstrap.sh` prints the OpenBao recovery keys once. Keep them offline, split between two people.

## Operating modes in this deployment

`env.example` turns on two modes on purpose, each with the acknowledgement the cell requires outside local, test and dev:

| Setting | Effect | If you do not want it |
|---|---|---|
| `ZID_OPERATOR_MODE=direct` + `ZID_OPERATOR_DIRECT_ACK` | one registered operator account approves and changes commercial and platform settings with no access request and no second approver (customer-data access stays behind the customer's lockbox) | delete both lines: operators then request just-in-time access and a second person approves |
| `ZID_ONBOARDING_APPROVAL=owner` + `ZID_ONBOARDING_OWNER_ACK` | the company's owner approves plan, pilot and activation steps alone | delete both lines: a second administrator is required again |

Both remove a second pair of eyes. The compensating controls: passkey sign-in with a fresh step-up on every operator action, a priority-lane event and an audit record for each direct operator action (Security can alert on `operator_access.direct_action`), and the commercial change log. Protect the operator console with the IP allow-list or Identity-Aware Proxy, give each operator their own device-bound passkey, and revisit both settings when there is a second operator or administrator. Without the acknowledgement lines the cell refuses to start.

## First deploy checklist

1. `tofu apply`, DNS to `lb_ip` (or `vm_ip`), wait for the managed certificates to turn ACTIVE.
2. Database roles and schemas (step 3), then `docker compose run --rm migrate`.
3. Prove the keys against the real Cloud KMS, on the VM, before taking traffic:
   `ZID_ENVIRONMENT=production ZID_CELL_ID=eu-1 ZID_KEY_PROVIDER=gcp-kms ZID_GCP_KMS_PROJECT=<p> ZID_GCP_KMS_LOCATION=<l> python tools/scripts/gcp_kms_smoke.py`
   (round trips for every key purpose, a refused wrong-tenant context, a signed and verified token for each signing purpose, the evidence key and the HMAC key; it never destroys or rotates). Its logic was run against a fake Cloud KMS, so the first real run is the real test.
4. `docker compose --profile direct up -d` (or `--profile lb`), then `./deploy.sh` for later releases.

## Releases

The release workflow builds, scans, attests and signs three images by digest: `zoiko-id-platform` (backend), `zoiko-id-web` and `zoiko-id-operator` (`build-frontend-image.yml`, same hardening as the backend build). `./deploy.sh sha256:<backend-digest> [sha256:<web-digest>]` verifies each signature against its workflow identity, runs the migration as the owner role, restarts the stack and waits for `/health/ready` on the cell and global roles. Pull access: the images are in ghcr.io, so `docker login ghcr.io` on the VM with a read-only token, or make the packages public.

## Optional profiles

- `docker compose --profile operator up -d`: Operator Console on `operator.<domain>`, restricted to `OPERATOR_ALLOWED_CIDRS` in Caddy. Needs `ZID_OPERATOR_IMAGE`, `ZID_OPERATOR_TENANT_ID` and the origin added to `ZID_WEBAUTHN_ORIGINS`.
- `docker compose --profile operator-bootstrap run --rm operator-bootstrap`: creates the first operator account from the `ZID_OPERATOR_*` settings in `.env` (principal, console OAuth client with the https redirect, operator profile; idempotent) and prints one single-use passkey enrollment link in your terminal. The link is on the console itself (`https://operator.<domain>/enroll?ticket=…`): the operator opens it on their own device, registers a passkey and is signed in. The customer app cannot redeem it (a different client and tenant). Run it again with another `ZID_OPERATOR_BOOTSTRAP_PRINCIPAL_ID` for the second operator that approvals need.
- `docker compose --profile events up -d`: outbox relay and event consumer against a managed Kafka (`ZID_KAFKA_BOOTSTRAP`); add `ZID_SPICEDB_URL` and `ZID_SPICEDB_KEY` for the relationship projection.

## Secrets and keys checklist

| What | Where it lives | Notes |
|---|---|---|
| Token, set, federation, credential, verifier signing keys | Cloud KMS (`signing_*`) | Created by tofu, one key per purpose, never exported |
| Evidence, webhook, directory and discovery signing keys | Cloud KMS | Same |
| Data, erasure, backup and secret-encryption KEKs; HMAC key | Cloud KMS | KEKs rotate every 90 days; old ciphertext still decrypts |
| OpenBao unseal key | Cloud KMS (`zid-seal`) | No unseal keys to hand out |
| Verifier certificate chain (`ZID_VERIFIER_CERTIFICATE_CHAIN`) | env (public certificates) | Needed only for `x509_hash` (HAIP); the leaf must certify the `signing_verifier` key |
| Database passwords (owner, runtime, worker) | `.env` on the VM, mode 0600 | Move to Secret Manager or OpenBao when you automate deployment |
| Valkey password | `.env` | `openssl rand -hex 24` |
| SMTP host, user, password | `.env` + `secrets/smtp_password` | Any SMTP provider; the code reads the password from the file |
| Stripe secret key | `secrets/stripe_key` (`ZID_STRIPE_SECRET_KEY_FILE`) | Only if billing is on |
| Enterprise OIDC client secrets | OpenBao `secret/<ref>` | Per customer connection |
| ACME email, domain | `.env` | Public values |
| Kafka and SpiceDB | not deployed here | Needed only if you run the event relay and consumer (`ZID_KAFKA_BOOTSTRAP`, `ZID_SPICEDB_*`) |

No third-party AI or SaaS API key is required.

## Rotation and recovery

- **Signing key rotation:** add a key version in Cloud KMS and promote it, publish the old public key in `ZID_SIGNING_RETIRING_KEYS` (`{"token": [<public JWK>]}`), and set a new `kid`. Tokens signed by the old key stay verifiable for the window.
- **Backups:** Cloud SQL daily backups plus 7-day point-in-time recovery. OpenBao data is on the `openbao-data` volume; snapshot that disk.
- **Crypto-erasure:** destroying the `subject_erasure_key` versions makes the data unreadable after the pending window, so choose `key_destroy_days` knowingly.

## Not covered here

Kafka and SpiceDB provisioning, dashboards beyond the uptime and disk alerts, and promotion-gate wiring for the web and operator images (the gate in `deploy/release/promotion-policy.json` covers the backend image only).

## More than one cell

`deploy/tofu/live/gcp-prod` instantiates the module once per cell, each in its own project and region (uk-1 in europe-west2, eu-1 in europe-west4) with its own state key ring, database and load balancer. Each cell runs this compose stack on its own VM with its own `ZID_CELL_ID`; the cell must also exist in `backend/config/cells/cells.yaml` (`provider: gcp`, the region, jurisdiction and subsystem placements), which the code reads but does not restrict to AWS. A plan of that two-cell root (178 resources, created offline with a dummy token) passes `tools/scripts/iac_policy.py` for the approved regions europe-west2 and europe-west4, and is correctly rejected when a region is not approved. The drills (zone loss, failover, DR, PITR) still have to be run on real infrastructure.
